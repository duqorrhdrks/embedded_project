"""
[컴퓨터 비전 & AI 모듈 - drowsiness_detector.py]
실시간 운전자 졸음(눈 감김) 감지 및 카메라 영상 캡처 엔진
- Raspberry Pi CSI 카메라(Picamera2) 및 USB 웹캠(OpenCV) 하이브리드 지원
- 카메라가 비추는 실제 환경 실시간 영상 송출
- OpenCV Haar Cascade 안면/시선 분석 및 YOLOv8 모델(best.pt) 추론 지원
- 눈 감김 지속 시간 기반 졸음 확정 필터링
- 졸음 감지 시 콜백 호출 및 고화질 ADAS OSD 오버레이
"""

import os
import time
import logging
from datetime import datetime
import cv2
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DrowsinessDetector")

# Ultralytics 라이브러리 가용 여부 확인
HAS_YOLO = False
try:
    from ultralytics import YOLO
    HAS_YOLO = True
except ImportError:
    HAS_YOLO = False

# Picamera2 라이브러리 가용 여부 확인 (라즈베리파이 CSI 카메라 전용)
HAS_PICAM2 = False
try:
    from picamera2 import Picamera2
    HAS_PICAM2 = True
except ImportError:
    HAS_PICAM2 = False


class DrowsinessDetector:
    """
    운전자 졸음 감지 및 카메라 프레임 분석기
    """

    def __init__(self,
                 camera_index: int = 0,
                 model_path: str = "models/best.pt",
                 conf_threshold: float = 0.5,
                 drowsy_classes: list = None,
                 duration_threshold_sec: float = 1.2,
                 recovery_time_sec: float = 1.0,
                 frame_width: int = 640,
                 frame_height: int = 480):
        """
        :param camera_index: 비디오 캡처 장치 인덱스 (기본 0)
        :param model_path: YOLOv8 가중치 파일 경로
        :param conf_threshold: 인식 신뢰도 임계값
        :param drowsy_classes: 졸음 판정 클래스 명칭 목록
        :param duration_threshold_sec: 졸음 확정까지 눈 감고 있어야 하는 연속 시간 (초)
        :param recovery_time_sec: 정상 복귀까지 눈 뜨고 있어야 하는 연속 시간 (초)
        """
        self.camera_index = camera_index
        self.model_path = model_path
        self.conf_threshold = conf_threshold
        self.drowsy_classes = [c.lower() for c in (drowsy_classes or ["drowsy", "closed_eye", "closed_eyes", "sleep", "sleeping"])]
        self.duration_threshold_sec = duration_threshold_sec
        self.recovery_time_sec = recovery_time_sec
        self.frame_width = frame_width
        self.frame_height = frame_height

        # 카메라 관련 변수
        self.picam2 = None
        self.cap = None
        self.is_camera_open = False
        self.camera_backend = "none"  # "picamera2", "opencv", "standby"

        # AI 모델 관련 변수
        self.yolo_model = None
        self.is_yolo_active = False
        self.face_cascade = None
        self.eye_cascade = None

        # 감지 상태 타이머
        self.eye_closed_start_time = None
        self.eye_open_start_time = None
        self.is_drowsy_confirmed = False
        self.current_drowsy_duration = 0.0

        # 모의 시험용 플래그
        self.simulated_eye_closed = False

        # [외부(노트북 등) AI 판단 위임 모드]
        # /api/external_judgment 로 한 번이라도 판정 결과를 수신하면 True로 전환되며,
        # 이후로는 라즈베리파이 로컬 YOLO/Haar Cascade 판정보다 외부 판정이 우선 적용됩니다.
        self.external_judgment_mode = False
        self.external_eyes_closed = False
        self.external_last_update_time = 0.0

        # 콜백 함수 등록 목록
        self.on_drowsy_detected_callbacks = []
        self.on_drowsy_recovered_callbacks = []

        self._init_camera()
        self._init_detector()

    def _init_camera(self):
        """카메라 초기화: Picamera2 우선 시도 후 OpenCV VideoCapture 순으로 폴백"""
        # 1. Picamera2 (라즈베리파이 CSI 카메라 모듈) 우선 시도
        if HAS_PICAM2:
            try:
                logger.info("🔄 [카메라] Raspberry Pi CSI 카메라 (Picamera2) 초기화 시도...")
                self.picam2 = Picamera2()
                cam_config = self.picam2.create_video_configuration(
                    main={"size": (self.frame_width, self.frame_height), "format": "BGR888"}
                )
                self.picam2.configure(cam_config)
                self.picam2.start()
                self.is_camera_open = True
                self.camera_backend = "picamera2"
                logger.info("✅ [카메라] Raspberry Pi CSI 카메라 (Picamera2) 정상 구동 완료")
                return
            except Exception as e:
                logger.warning(f"⚠️ [카메라] Picamera2 시작 실패: {e}. USB 카메라/OpenCV로 전환합니다.")
                if self.picam2:
                    try:
                        self.picam2.close()
                    except Exception:
                        pass
                self.picam2 = None

        # 2. OpenCV VideoCapture (USB 웹캠 또는 V4L2) 시도
        try:
            logger.info(f"🔄 [카메라] OpenCV VideoCapture 디바이스 {self.camera_index}번 오픈 시도...")
            self.cap = cv2.VideoCapture(self.camera_index)
            if self.cap.isOpened():
                # 해상도 설정
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
                ret, test_frame = self.cap.read()
                if ret and test_frame is not None:
                    self.is_camera_open = True
                    self.camera_backend = "opencv"
                    logger.info(f"✅ [카메라] USB 웹캠 (디바이스 {self.camera_index}번) 정상 연결 완료")
                    return
                else:
                    self.cap.release()
                    self.cap = None
        except Exception as e:
            logger.warning(f"⚠️ [카메라] OpenCV 카메라 오픈 오류: {e}")
            self.cap = None

        # 3. 하드웨어 카메라 없음 -> 대기 화면 모드
        self.is_camera_open = False
        self.camera_backend = "standby"
        logger.warning("⚠️ [카메라] 감지된 하드웨어 카메라가 없어 실시간 영상 대기 화면으로 작동합니다.")

    def _init_detector(self):
        """YOLOv8 모델 로드 또는 OpenCV Haar Cascade 안면/시선 분석기 설정"""
        if HAS_YOLO and os.path.exists(self.model_path):
            try:
                logger.info(f"🔄 [AI 모델] YOLOv8 가중치 로드 시도: {self.model_path}")
                self.yolo_model = YOLO(self.model_path)
                self.is_yolo_active = True
                logger.info("✅ [AI 모델] YOLOv8 best.pt 모델 로드 완료 (실시간 딥러닝 추론 준비)")
                return
            except Exception as e:
                logger.error(f"❌ [AI 모델] YOLO 모델 로드 오류: {e}")
                self.is_yolo_active = False

        # Haar Cascade 경로 검색
        cascade_dirs = [
            "/usr/share/opencv4/haarcascades",
            "/usr/share/opencv/haarcascades",
            "/usr/local/share/opencv4/haarcascades",
            os.path.join(os.path.dirname(__file__), "..", "models")
        ]

        face_path = None
        eye_path = None
        for d in cascade_dirs:
            fp = os.path.join(d, "haarcascade_frontalface_default.xml")
            ep = os.path.join(d, "haarcascade_eye.xml")
            if os.path.exists(fp) and face_path is None:
                face_path = fp
            if os.path.exists(ep) and eye_path is None:
                eye_path = ep

        if face_path and eye_path:
            try:
                self.face_cascade = cv2.CascadeClassifier(face_path)
                self.eye_cascade = cv2.CascadeClassifier(eye_path)
                logger.info("✅ [AI 모델] 안면 및 시선 분석 엔진 (Haar Cascade) 로드 완료")
            except Exception as e:
                logger.warning(f"⚠️ Haar Cascade 로드 실패: {e}")
        else:
            logger.info("ℹ️ [AI 모델] Haar Cascade XML 미존재 -> 모의 시험 및 기본 필터로 대기합니다.")

    def add_drowsy_callback(self, callback):
        """졸음 확정 시 실행할 콜백 함수 등록"""
        self.on_drowsy_detected_callbacks.append(callback)

    def add_recovery_callback(self, callback):
        """정상 복귀 시 실행할 콜백 함수 등록"""
        self.on_drowsy_recovered_callbacks.append(callback)

    def toggle_simulation_drowsy(self, forced_state: bool = None) -> bool:
        """
        [테스트용] 졸음(눈 감김) 상태를 강제로 ON/OFF 토글
        """
        if forced_state is not None:
            self.simulated_eye_closed = forced_state
        else:
            self.simulated_eye_closed = not self.simulated_eye_closed

        logger.info(f"🧪 [모의 시험] 눈 감김 강제 상태 변경: {self.simulated_eye_closed}")
        return self.simulated_eye_closed

    def report_external_judgment(self, eyes_closed: bool):
        """
        [노트북 등 외부 AI 판단 수신]
        라즈베리파이가 스트리밍한 영상(/video_feed_raw)을 외부(노트북 등)에서 YOLO로
        판단한 결과를 전달받습니다. 최초 호출 시 external_judgment_mode가 True로 전환되며,
        이후 process_frame()은 로컬 YOLO/Haar Cascade 대신 이 값을 우선 사용합니다.
        """
        if not self.external_judgment_mode:
            logger.info("🌐 [외부 AI 판단 모드 전환] 이후 로컬 YOLO/Haar 대신 외부 판정 결과를 우선 사용합니다.")
        self.external_judgment_mode = True
        self.external_eyes_closed = bool(eyes_closed)
        self.external_last_update_time = time.time()

    def read_frame(self):
        """카메라에서 프레임을 읽어오거나 대기 화면 반환"""
        if self.is_camera_open:
            if self.camera_backend == "picamera2" and self.picam2 is not None:
                try:
                    frame = self.picam2.capture_array()
                    if frame is not None and frame.size > 0:
                        # Picamera2의 "BGR888" 포맷은 알려진 특이사항으로 실제 메모리상
                        # 채널 순서가 RGB이므로(R/B가 뒤바뀜), OpenCV가 기대하는 진짜
                        # BGR 순서로 맞춰주기 위해 R/B 채널을 교환합니다.
                        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                except Exception as e:
                    logger.error(f"Picamera2 프레임 캡처 오류: {e}")

            elif self.camera_backend == "opencv" and self.cap is not None:
                try:
                    ret, frame = self.cap.read()
                    if ret and frame is not None:
                        return frame
                except Exception as e:
                    logger.error(f"OpenCV 프레임 캡처 오류: {e}")

        return self._generate_standby_frame()

    def _generate_standby_frame(self):
        """카메라 미연결 시 환경 대기 화면 생성 (캐릭터 그래픽 배제, 깔끔한 관제 모니터)"""
        frame = np.zeros((self.frame_height, self.frame_width, 3), dtype=np.uint8)
        frame[:] = (22, 26, 31)

        # 미세 격자 가이드선
        for y in range(0, self.frame_height, 40):
            cv2.line(frame, (0, y), (self.frame_width, y), (30, 36, 44), 1)
        for x in range(0, self.frame_width, 40):
            cv2.line(frame, (x, 0), (x, self.frame_height), (30, 36, 44), 1)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(frame, f"[CAMERA STANDBY] {now_str}", (20, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (100, 160, 220), 2)
        cv2.putText(frame, "REAL-TIME ENVIRONMENT MONITORING", (20, 55),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (140, 150, 165), 1)

        # 중앙 안내
        cv2.putText(frame, "NO VIDEO SIGNAL", (210, 230),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 165, 255), 2)
        cv2.putText(frame, "Waiting for CSI Camera / USB Webcam Input...", (130, 265),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (160, 160, 160), 1)

        return frame

    def _analyze_and_annotate(self, frame):
        """
        프레임 1장에 대해 YOLOv8(best.pt) 또는 모의시험/Haar Cascade 기반으로
        눈 감김 여부를 판별하고 결과를 프레임 위에 시각화(annotate)합니다.
        process_frame()(연속 스트림)과 infer_single_image()(단발성 업로드 이미지)가
        공통으로 사용하는 순수 감지 로직입니다.
        :return: (eyes_detected_closed, detected_objects, driver_detected)
        """
        eyes_detected_closed = False
        detected_objects = []
        driver_detected = False

        # 0. [최우선] 노트북 등 외부에서 이미 판단한 결과가 있으면 그것을 그대로 사용
        #    (라즈베리파이는 카메라 캡처/모터-LED 제어만 담당하고, 무거운 YOLO 추론은
        #    노트북에서 수행하는 구조를 위한 경로. web/server.py의 /api/external_judgment 참고)
        if self.external_judgment_mode:
            eyes_detected_closed = self.external_eyes_closed
            driver_detected = True
            since = time.time() - self.external_last_update_time
            tag = "EYES CLOSED (EXTERNAL AI)" if eyes_detected_closed else "NORMAL (EXTERNAL AI)"
            color = (0, 0, 255) if eyes_detected_closed else (0, 200, 0)
            cv2.putText(frame, f"[LAPTOP JUDGMENT] {tag} (last update {since:.1f}s ago)", (20, 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
            return eyes_detected_closed, detected_objects, driver_detected

        # 1. YOLOv8 모델 추론 (best.pt 로드된 경우)
        if self.is_yolo_active and self.yolo_model is not None:
            results = self.yolo_model.predict(frame, conf=self.conf_threshold, verbose=False)
            if results and len(results) > 0:
                result = results[0]
                boxes = result.boxes
                for box in boxes:
                    cls_id = int(box.cls[0].item())
                    conf = float(box.conf[0].item())
                    cls_name = self.yolo_model.names.get(cls_id, str(cls_id)).lower()
                    xyxy = box.xyxy[0].cpu().numpy().astype(int)

                    detected_objects.append({"class": cls_name, "conf": conf, "box": xyxy})
                    driver_detected = True

                    is_drowsy_box = any(dc in cls_name for dc in self.drowsy_classes)
                    if is_drowsy_box:
                        eyes_detected_closed = True
                        color = (0, 0, 255)
                        tag = f"CLOSED EYE: {conf:.2f}"
                    else:
                        color = (0, 255, 0)
                        tag = f"{cls_name}: {conf:.2f}"

                    cv2.rectangle(frame, (xyxy[0], xyxy[1]), (xyxy[2], xyxy[3]), color, 2)
                    cv2.putText(frame, tag, (xyxy[0], max(20, xyxy[1] - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        # 2. YOLOv8 미사용 시: 모의 시험 또는 Haar Cascade 안면/시선 분석
        else:
            if self.simulated_eye_closed:
                eyes_detected_closed = True
                driver_detected = True
                detected_objects.append({"class": "simulated_drowsy", "conf": 1.0, "box": [100, 100, 300, 300]})
                cv2.putText(frame, "[TEST MODE] SIMULATED EYES CLOSED", (20, 70),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 140, 255), 2)
            elif self.face_cascade and self.eye_cascade:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                # 안면 검출
                faces = self.face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=4, minSize=(80, 80))
                if len(faces) > 0:
                    driver_detected = True
                    # 가장 큰 얼굴 영역을 운전자로 지정
                    fx, fy, fw, fh = max(faces, key=lambda b: b[2] * b[3])
                    cv2.rectangle(frame, (fx, fy), (fx + fw, fy + fh), (200, 200, 0), 2)

                    # 얼굴 상단 55% 영역(눈 부위)에서 눈 검출
                    eye_roi_gray = gray[fy:fy + int(fh * 0.55), fx:fx + fw]
                    eyes = self.eye_cascade.detectMultiScale(eye_roi_gray, scaleFactor=1.1, minNeighbors=3, minSize=(18, 18))

                    for (ex, ey, ew, eh) in eyes:
                        cv2.rectangle(frame, (fx + ex, fy + ey), (fx + ex + ew, fy + ey + eh), (255, 200, 0), 1)
                        cv2.putText(frame, "Eye", (fx + ex, fy + ey - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 200, 0), 1)

                    if len(eyes) == 0:
                        # 얼굴은 식별되나 눈이 감겨 있는 상태
                        eyes_detected_closed = True
                        cv2.putText(frame, "EYES CLOSED DETECTED", (fx, max(25, fy - 10)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                    else:
                        cv2.putText(frame, "DRIVER NORMAL", (fx, max(25, fy - 10)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        return eyes_detected_closed, detected_objects, driver_detected

    def process_frame(self, frame=None):
        """
        1개 프레임을 분석하여 졸음 여부를 판별하고 시각화된 프레임과 결과를 반환
        :return: (annotated_frame, is_drowsy_confirmed, detection_info)
        """
        if frame is None:
            frame = self.read_frame()

        h, w, _ = frame.shape
        now = time.time()

        eyes_detected_closed, detected_objects, driver_detected = self._analyze_and_annotate(frame)

        # 3. 연속 시간 기반 졸음 확정 필터링 (일시적 눈 깜빡임 오작동 방지)
        if eyes_detected_closed:
            self.eye_open_start_time = None
            if self.eye_closed_start_time is None:
                self.eye_closed_start_time = now

            self.current_drowsy_duration = now - self.eye_closed_start_time

            # 임계 시간 이상 눈을 감고 있을 때 졸음 확정
            if self.current_drowsy_duration >= self.duration_threshold_sec:
                if not self.is_drowsy_confirmed:
                    self.is_drowsy_confirmed = True
                    logger.warning(f"🚨 [졸음 확정] 운전자 눈 감김 {self.current_drowsy_duration:.1f}초 지속 -> 비상 대응 발령!")
                    for cb in self.on_drowsy_detected_callbacks:
                        try:
                            cb()
                        except Exception as e:
                            logger.error(f"졸음 콜백 실행 오류: {e}")
        else:
            self.eye_closed_start_time = None
            self.current_drowsy_duration = 0.0

            if self.eye_open_start_time is None:
                self.eye_open_start_time = now

            # 눈을 뜬 상태가 지속되면 자동 정상 복귀
            if (now - self.eye_open_start_time) >= self.recovery_time_sec:
                if self.is_drowsy_confirmed:
                    self.is_drowsy_confirmed = False
                    for cb in self.on_drowsy_recovered_callbacks:
                        try:
                            cb()
                        except Exception as e:
                            logger.error(f"복귀 콜백 실행 오류: {e}")

        # 4. 프레임 상단 ADAS 안전 관제 OSD 오버레이
        if self.is_drowsy_confirmed:
            status_text = f"CRITICAL: DROWSINESS DETECTED! ({self.current_drowsy_duration:.1f}s)"
            banner_color = (0, 0, 200)   # 위험 경보 (빨간색)
        elif eyes_detected_closed:
            status_text = f"WARNING: EYE CLOSED ({self.current_drowsy_duration:.1f}s)"
            banner_color = (0, 140, 255) # 주의 (주황색)
        else:
            status_text = "STATUS: DRIVER MONITORING ACTIVE (NORMAL)"
            banner_color = (0, 150, 40)  # 정상 주행 (초록색)

        # 상단 상태 배너 바
        cv2.rectangle(frame, (0, 0), (w, 36), banner_color, -1)
        cv2.putText(frame, status_text, (16, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)

        # 하단 정보 바
        cam_label = "CSI Camera (Picamera2)" if self.camera_backend == "picamera2" else (
            f"USB Cam (Dev {self.camera_index})" if self.camera_backend == "opencv" else "Standby Mode"
        )
        ai_label = "AI: Laptop (External)" if self.external_judgment_mode else (
            "AI: YOLOv8" if self.is_yolo_active else (
                "AI: Haar Vision" if self.face_cascade else "AI: Test Mode"
            )
        )
        sub_info = f"CAM: {cam_label} | {ai_label}"
        cv2.putText(frame, sub_info, (15, h - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 210, 220), 1)

        detection_info = {
            "is_drowsy": self.is_drowsy_confirmed,
            "eyes_closed": eyes_detected_closed,
            "duration": self.current_drowsy_duration,
            "is_yolo": self.is_yolo_active,
            "is_external": self.external_judgment_mode,
            "camera_backend": self.camera_backend,
            "objects_count": len(detected_objects)
        }

        return frame, self.is_drowsy_confirmed, detection_info

    def infer_single_image(self, frame) -> dict:
        """
        [외부 업로드 프레임 전용 API용]
        process_frame()과 달리 연속 스트림의 졸음 지속시간 상태(eye_closed_start_time 등)를
        변경하지 않고, 업로드된 단일 이미지 1장에 대한 즉시 판정 결과만 반환합니다.
        (web/server.py의 /api/inference 에서 사용)
        :return: {"eyes_closed": bool, "driver_detected": bool, "is_yolo": bool, "objects": [...]}
        """
        eyes_detected_closed, detected_objects, driver_detected = self._analyze_and_annotate(frame)
        return {
            "eyes_closed": eyes_detected_closed,
            "driver_detected": driver_detected,
            "is_yolo": self.is_yolo_active,
            "objects": [
                {"class": obj["class"], "conf": round(float(obj["conf"]), 3)}
                for obj in detected_objects
            ],
        }

    def reset_drowsy_state(self):
        """복구 버튼 등으로 졸음 감지 상태 초기화"""
        self.eye_closed_start_time = None
        self.current_drowsy_duration = 0.0
        self.is_drowsy_confirmed = False
        self.simulated_eye_closed = False
        logger.info("🟢 [졸음 감지기] 감지 상태가 정상으로 초기화되었습니다.")

    def release(self):
        """카메라 및 시스템 리소스 안전 해제"""
        if self.picam2:
            try:
                self.picam2.stop()
                self.picam2.close()
            except Exception as e:
                logger.warning(f"Picamera2 해제 오류: {e}")
            self.picam2 = None

        if self.cap:
            try:
                self.cap.release()
            except Exception as e:
                logger.warning(f"OpenCV 카메라 해제 오류: {e}")
            self.cap = None

        self.is_camera_open = False
        logger.info("✅ [카메라] 모든 비디오 리소스가 안전하게 해제되었습니다.")

