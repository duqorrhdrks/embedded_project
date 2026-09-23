"""
[시스템 통합 및 객체 중앙 관리자 - system_manager.py]
프로젝트의 모든 동작 클래스(모터, LED, 졸음감지, 녹화기, 웹, 차선보조)의
객체(Instance)를 단일 파일에서 집중 생성하고 상호 결합하는 컨트롤 타워입니다.

★ 사용자는 이 파일에서 각 객체의 생성 파라미터를 한눈에 확인하고 직접 수정할 수 있습니다.
"""

import time
import threading
import logging
import cv2

# 중앙 설정 불러오기
from config import HardwareConfig, VisionConfig, WebConfig, AutonomousPullOverConfig

# 각 부서별 동작 클래스 임포트
from hardware.motor_controller import DualMotorController, MotorState
from hardware.led_controller import HazardLedController
from vision.drowsiness_detector import DrowsinessDetector
from vision.video_recorder import EventVideoRecorder
from vision.lane_assistant import LaneDeparturePullOver

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SystemManager")


class SystemManager:
    """
    모든 하드웨어 및 비전 객체를 통합 오케스트레이션하는 싱글톤 관리 클래스
    """

    def __init__(self):
        logger.info("🚀 [시스템 매니저] 전체 시스템 객체 초기화 및 배선(Wiring) 시작...")

        # ======================================================================
        # ★ [객체 집중 생성 영역] 사용자가 설정을 변경하고 싶을 때 이 부분을 수정합니다.
        # ======================================================================

        # 1. 듀얼 모터 제어 객체 생성
        self.motor_controller = DualMotorController(
            left_in1=HardwareConfig.LEFT_MOTOR_IN1,
            left_in2=HardwareConfig.LEFT_MOTOR_IN2,
            left_ena=HardwareConfig.LEFT_MOTOR_ENA,
            right_in3=HardwareConfig.RIGHT_MOTOR_IN3,
            right_in4=HardwareConfig.RIGHT_MOTOR_IN4,
            right_enb=HardwareConfig.RIGHT_MOTOR_ENB,
            pwm_freq=HardwareConfig.PWM_FREQUENCY,
            default_speed=HardwareConfig.DEFAULT_CRUISE_SPEED,
            min_speed=HardwareConfig.MIN_RUNNING_SPEED,
            decel_step=HardwareConfig.DECELERATION_STEP,
            decel_interval=HardwareConfig.DECELERATION_INTERVAL,
            force_simulation=HardwareConfig.USE_SIMULATION
        )

        # 2. 비상등 LED 제어 객체 생성
        self.led_controller = HazardLedController(
            pin=HardwareConfig.HAZARD_LED_PIN,
            blink_interval=HardwareConfig.HAZARD_BLINK_INTERVAL,
            force_simulation=HardwareConfig.USE_SIMULATION
        )

        # 3. YOLOv8 졸음 감지 객체 생성 (best.pt 미존재 시 Fallback)
        self.drowsiness_detector = DrowsinessDetector(
            camera_index=VisionConfig.CAMERA_INDEX,
            model_path=VisionConfig.MODEL_PATH,
            conf_threshold=VisionConfig.CONFIDENCE_THRESHOLD,
            drowsy_classes=VisionConfig.DROWSY_CLASS_NAMES,
            duration_threshold_sec=VisionConfig.DROWSY_DURATION_THRESHOLD_SEC,
            recovery_time_sec=VisionConfig.DROWSY_RECOVERY_TIME_SEC
        )

        # 4. 이벤트 영상 자동 녹화 객체 생성
        self.video_recorder = EventVideoRecorder(
            output_dir=VisionConfig.RECORDINGS_DIR,
            fourcc_str=VisionConfig.VIDEO_FOURCC,
            ext=VisionConfig.VIDEO_EXT
        )

        # 5. 향후 갓길 주차 및 차선 보조 확장 객체 생성
        self.lane_assistant = LaneDeparturePullOver(
            camera_index=AutonomousPullOverConfig.LANE_CAMERA_INDEX,
            pull_over_steer_ratio=AutonomousPullOverConfig.PULL_OVER_STEER_RATIO
        )

        # config.py의 ENABLE_PULL_OVER_EXTENSION 플래그가 켜져 있으면 보조 카메라 연결을
        # 미리 시도해 둡니다. 카메라가 아직 없어도 안전하게 대기 상태로 남습니다.
        self.pull_over_extension_enabled = AutonomousPullOverConfig.ENABLE_PULL_OVER_EXTENSION
        if self.pull_over_extension_enabled:
            self.lane_assistant.initialize_lane_camera()

        # ======================================================================
        # 시스템 상태 및 런타임 변수
        # ======================================================================
        self.is_running = False
        self.latest_frame = None
        self.latest_raw_frame = None
        self._frame_lock = threading.Lock()
        self.last_event_msg = "시스템 정상 대기 중"

        # 콜백 연결 (감지기와 시스템 매니저 간 통신)
        self.drowsiness_detector.add_drowsy_callback(self._on_drowsy_event)

        logger.info("✅ [시스템 매니저] 모든 객체 생성 및 상호 연결 완료!")

    def start_system(self):
        """백그라운드 비전 처리 루프 및 기본 주행 시작"""
        if self.is_running:
            return

        self.is_running = True
        # 주행 시작
        self.motor_controller.start_forward()

        # 비전 캡처 & 모니터링 백그라운드 스레드 시작
        self._vision_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._vision_thread.start()
        logger.info("🏁 [시스템 매니저] 차량 주행 및 실시간 비전 감시 루프 가동 시작")

    def _on_drowsy_event(self):
        """
        [핵심 요구사항 판정 로직]
        운전자 눈 감김(졸음) 감지 시:
        1) 이벤트 영상 자동 녹화 즉시 시작
        2) 비상등 점멸
        3) 주행 중일 경우 점진적 안전 감속
        """
        # 1. 졸음 감지 즉시 이벤트 영상 자동 녹화 시작 (주행 여부 무관하게 즉시 저장)
        if not self.video_recorder.is_recording():
            saved_filepath = self.video_recorder.start_recording(
                frame_width=VisionConfig.FRAME_WIDTH,
                frame_height=VisionConfig.FRAME_HEIGHT,
                fps=20.0
            )
            logger.info(f"📹 [이벤트 영상 녹화] 파일 저장 시작: {saved_filepath}")

        # 2. 비상등 LED 점멸 작동
        self.led_controller.start_blinking()

        # 3. 주행 중 여부에 따른 감속 제어
        is_moving = self.motor_controller.is_moving()
        if is_moving:
            self.last_event_msg = "🚨 운전자 졸음 감지! 차량 비상 감속 및 비상등 작동 중"
            logger.warning(f"🚨 [비상 대응] {self.last_event_msg}")
            self.motor_controller.trigger_drowsy_deceleration(target_slow_speed=0)
        else:
            self.last_event_msg = "⚠️ 운전자 졸음 감지! 비상등 점멸 및 이벤트 영상 녹화 중"
            logger.warning(f"⚠️ [비상 대응] {self.last_event_msg}")

    def recover_system(self):
        """
        [핵심 요구사항 복구 로직]
        운전자 상태 정상 확인 후 안전 시스템을 정상 주행 상태로 복구
        """
        logger.info("🟢 [시스템 복구] 운전자 상태 정상 확인 -> 안전 시스템 정상 복구 시작")

        # 1. 비상등 소등
        self.led_controller.stop_blinking()

        # 2. 모터 속도 정상 복구
        self.motor_controller.recover_normal_speed()

        # 3. 이벤트 영상 녹화 종료 및 파일 저장 완료
        if self.video_recorder.is_recording():
            saved_file = self.video_recorder.stop_recording()
            self.last_event_msg = f"복구 완료! 이벤트 영상 저장됨: {saved_file}"
        else:
            self.last_event_msg = "운전자 정상 확인: 정상 주행 복귀 완료"

        # 4. 비전 감지 상태 리셋
        self.drowsiness_detector.reset_drowsy_state()

    def toggle_drive(self) -> bool:
        """차량 주행 시작 / 정지 토글"""
        is_driving = self.motor_controller.toggle_drive()
        if is_driving:
            self.last_event_msg = "차량 정상 주행 시작"
        else:
            self.last_event_msg = "차량 주행 일시 정지"
        return is_driving

    def manual_stop(self):
        """수동 비상 정지"""
        self.motor_controller.emergency_stop()
        self.led_controller.start_blinking()
        self.last_event_msg = "수동 비상 정지 명령 발령"

    def _process_loop(self):
        """메인 비전 & 레코딩 루프"""
        while self.is_running:
            # 1. 카메라 원본 프레임 취득 (OSD 오버레이 그리기 전 원본 -> 외부(노트북) 추론용으로 보존)
            raw_frame = self.drowsiness_detector.read_frame()
            raw_frame_snapshot = raw_frame.copy()

            # 2. 프레임 분석 (process_frame이 raw_frame 위에 직접 OSD를 그려 넣음)
            frame, is_drowsy, info = self.drowsiness_detector.process_frame(raw_frame)

            # 3. 녹화 중이라면 프레임 저장
            if self.video_recorder.is_recording():
                self.video_recorder.write_frame(frame)

            # 4. 최신 프레임 캐싱 (GUI 및 Web 스트리밍용)
            with self._frame_lock:
                self.latest_frame = frame.copy()
                self.latest_raw_frame = raw_frame_snapshot

            time.sleep(0.03)  # 약 30 FPS 유지

    def get_latest_frame(self):
        """GUI 또는 Web 스트리밍을 위한 최신 시각화(OSD 오버레이 포함) 프레임 반환"""
        with self._frame_lock:
            if self.latest_frame is not None:
                return self.latest_frame.copy()
            return None

    def get_latest_raw_frame(self):
        """
        [노트북 등 외부 추론용] OSD 오버레이가 그려지기 전의 원본 카메라 프레임 반환
        (web/server.py의 /video_feed_raw 에서 사용)
        """
        with self._frame_lock:
            if self.latest_raw_frame is not None:
                return self.latest_raw_frame.copy()
            return None

    def apply_external_judgment(self, eyes_closed: bool):
        """
        [노트북 등 외부 AI 판단 반영]
        라즈베리파이 카메라 영상을 받아 노트북(또는 다른 서버)에서 YOLO 등으로 판단한
        결과를 그대로 적용합니다. 지속 시간 기반 확정 필터링(DROWSY_DURATION_THRESHOLD_SEC)과
        졸음 확정 시 콜백(_on_drowsy_event)은 로컬 판정과 동일하게 그대로 적용됩니다.
        (web/server.py의 /api/external_judgment 에서 사용)
        """
        self.drowsiness_detector.report_external_judgment(eyes_closed)

    def get_full_system_status(self) -> dict:
        """GUI 및 Web REST API에서 조회 가능한 전체 시스템 상태 종합"""
        motor_status = self.motor_controller.get_status()
        led_status = self.led_controller.get_status()
        rec_status = self.video_recorder.get_status()

        return {
            "motor": motor_status,
            "led": led_status,
            "recorder": rec_status,
            "drowsiness": {
                "is_drowsy": self.drowsiness_detector.is_drowsy_confirmed,
                "duration": round(self.drowsiness_detector.current_drowsy_duration, 1),
                "is_yolo": self.drowsiness_detector.is_yolo_active,
                "is_external": self.drowsiness_detector.external_judgment_mode,
                "camera_backend": getattr(self.drowsiness_detector, "camera_backend", "none"),
                "is_simulated": self.drowsiness_detector.simulated_eye_closed
            },
            "last_event_message": self.last_event_msg,
            "timestamp": time.time()
        }

    def shutdown(self):
        """전체 시스템 안전 종료"""
        logger.info("🛑 [시스템 매니저] 시스템 종료 절차 개시...")
        self.is_running = False
        if self.video_recorder.is_recording():
            self.video_recorder.stop_recording()
        self.motor_controller.cleanup()
        self.led_controller.cleanup()
        self.drowsiness_detector.release()
        self.lane_assistant.cleanup()
        logger.info("완전 종료 완료")
