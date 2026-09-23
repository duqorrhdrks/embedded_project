"""
[하드웨어 제어 모듈 - motor_controller.py]
라즈베리파이 GPIO를 통한 좌/우 듀얼 모터 제어 클래스
- L298N / TB6612 등의 H-Bridge 드라이버 PWM 속도 제어
- 졸음 감지 시 점진적 감속(Deceleration) 기능
- 정상 속도 복귀(Recovery) 및 긴급 정지(E-Stop) 기능
- 하드웨어 미연결/권한 부재 시 가상 시뮬레이션 모드 자동 지원
"""

import time
import threading
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MotorController")

# RPi.GPIO 라이브러리 가용 여부 확인
HAS_GPIO = False
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except (ImportError, RuntimeError):
    HAS_GPIO = False


class MotorState:
    """모터 동작 상태 열거형 상수"""
    STOPPED = "STOPPED"           # 정지 상태
    FORWARD = "FORWARD"           # 정상 전진 주행
    DECELERATING = "DECELERATING" # 감속 중 (졸음 감지 등으로 인한 서행)
    RECOVERING = "RECOVERING"     # 정상 속도로 재가속 복귀 중


class DualMotorController:
    """
    라즈베리파이 듀얼 모터 컨트롤러 클래스
    좌/우 2개의 모터를 개별 또는 동기화하여 제어합니다.
    """

    def __init__(self,
                 left_in1: int = 17, left_in2: int = 27, left_ena: int = 22,
                 right_in3: int = 23, right_in4: int = 24, right_enb: int = 25,
                 pwm_freq: int = 1000,
                 default_speed: int = 70,
                 min_speed: int = 20,
                 decel_step: int = 10,
                 decel_interval: float = 0.3,
                 force_simulation: bool = False):
        """
        :param left_in1, left_in2, left_ena: 좌측 모터 제어 핀
        :param right_in3, right_in4, right_enb: 우측 모터 제어 핀
        :param pwm_freq: PWM 주파수(Hz)
        :param default_speed: 기본 주행 속도 (0~100%)
        :param min_speed: 최소 동작 유지 속도
        :param decel_step: 감속 단계별 감소량
        :param decel_interval: 감속 단계 사이 딜레이(초)
        :param force_simulation: 강제 시뮬레이션 모드 여부
        """
        self.left_in1 = left_in1
        self.left_in2 = left_in2
        self.left_ena = left_ena

        self.right_in3 = right_in3
        self.right_in4 = right_in4
        self.right_enb = right_enb

        self.pwm_freq = pwm_freq
        self.target_speed = default_speed
        self.current_speed = 0
        self.min_speed = min_speed
        self.decel_step = decel_step
        self.decel_interval = decel_interval

        self.state = MotorState.STOPPED
        self.is_simulated = force_simulation or (not HAS_GPIO)

        self._lock = threading.Lock()
        self._decel_thread = None
        self._stop_decel_event = threading.Event()

        # PWM 객체
        self.pwm_left = None
        self.pwm_right = None

        self._init_gpio()

    def _init_gpio(self):
        """GPIO 핀 초기화 및 PWM 설정"""
        if self.is_simulated:
            logger.info("⚡ [모터 컨트롤러] 가상 시뮬레이션 모드로 작동합니다. (Mock Motor)")
            return

        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)

            # 출력 핀 설정
            for pin in [self.left_in1, self.left_in2, self.left_ena,
                        self.right_in3, self.right_in4, self.right_enb]:
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.LOW)

            # PWM 설정
            self.pwm_left = GPIO.PWM(self.left_ena, self.pwm_freq)
            self.pwm_right = GPIO.PWM(self.right_enb, self.pwm_freq)

            self.pwm_left.start(0)
            self.pwm_right.start(0)
            logger.info("✅ [모터 컨트롤러] RPi.GPIO 모터 초기화 완료 (PWM 1kHz)")

        except Exception as e:
            logger.warning(f"⚠️ [모터 컨트롤러] GPIO 초기화 실패: {e}. 시뮬레이션 모드로 전환합니다.")
            self.is_simulated = True

    def start_forward(self, speed: int = None):
        """
        지정된 속도로 두 모터 정상 전진 시작
        """
        with self._lock:
            # 기존 감속 스레드가 있다면 중단
            self._stop_decel_event.set()

            if speed is None:
                speed = self.target_speed
            self.current_speed = max(0, min(100, speed))
            self.state = MotorState.FORWARD

            if not self.is_simulated:
                # 좌측 모터 전진
                GPIO.output(self.left_in1, GPIO.HIGH)
                GPIO.output(self.left_in2, GPIO.LOW)
                self.pwm_left.ChangeDutyCycle(self.current_speed)

                # 우측 모터 전진
                GPIO.output(self.right_in3, GPIO.HIGH)
                GPIO.output(self.right_in4, GPIO.LOW)
                self.pwm_right.ChangeDutyCycle(self.current_speed)

            logger.info(f"🚗 [모터] 정상 전진 주행 시작 (속도: {self.current_speed}%)")

    def set_speed(self, left_speed: int, right_speed: int):
        """
        좌/우 모터 개별 속도 제어 (차선 변경 및 갓길 주차 조향 시 활용)
        """
        with self._lock:
            left_speed = max(0, min(100, left_speed))
            right_speed = max(0, min(100, right_speed))
            self.current_speed = int((left_speed + right_speed) / 2)

            if not self.is_simulated:
                self.pwm_left.ChangeDutyCycle(left_speed)
                self.pwm_right.ChangeDutyCycle(right_speed)

    def trigger_drowsy_deceleration(self, target_slow_speed: int = 0):
        """
        [핵심 요구사항] 졸음 감지 시 점진적으로 속도를 줄이는 비동기 감속 프로세스 실행
        :param target_slow_speed: 최종 감속 목표 속도 (기본 0: 안전 정지)
        """
        with self._lock:
            # 모터가 돌고 있지 않으면 감속할 필요 없음
            if self.current_speed == 0 or self.state == MotorState.STOPPED:
                logger.info("ℹ️ [모터] 차량이 이미 정지 상태이므로 감속 로직을 건너뜁니다.")
                return

            if self.state == MotorState.DECELERATING:
                # 이미 감속 중이면 중복 실행 방지
                return

            self.state = MotorState.DECELERATING
            self._stop_decel_event.clear()

            # 백그라운드 스레드에서 점진적 감속 수행
            self._decel_thread = threading.Thread(
                target=self._deceleration_worker,
                args=(target_slow_speed,),
                daemon=True
            )
            self._decel_thread.start()
            logger.warning(f"⚠️ [모터] 졸음 감지! 비상 감속 스레드 가동 (현재 {self.current_speed}% -> {target_slow_speed}%)")

    def _deceleration_worker(self, target_slow_speed: int):
        """점진적 감속 워커 스레드"""
        while not self._stop_decel_event.is_set():
            with self._lock:
                new_speed = self.current_speed - self.decel_step
                if new_speed <= target_slow_speed:
                    new_speed = target_slow_speed
                    self.current_speed = new_speed
                    if not self.is_simulated:
                        self.pwm_left.ChangeDutyCycle(self.current_speed)
                        self.pwm_right.ChangeDutyCycle(self.current_speed)
                    if new_speed == 0:
                        self.state = MotorState.STOPPED
                    logger.info(f"🛑 [모터] 목표 감속 완료 (현재 속도: {self.current_speed}%)")
                    break
                else:
                    self.current_speed = new_speed
                    if not self.is_simulated:
                        self.pwm_left.ChangeDutyCycle(self.current_speed)
                        self.pwm_right.ChangeDutyCycle(self.current_speed)
                    logger.info(f"📉 [모터 감속 중] 속도: {self.current_speed}%")

            time.sleep(self.decel_interval)

    def recover_normal_speed(self, speed: int = None):
        """
        [핵심 요구사항] 운전자 상태 정상 확인 후 복구 버튼을 눌렀을 때
        모터 속도를 원래의 정상 주행 속도로 복구
        """
        logger.info("🔄 [모터] 운전자 상태 정상 확인 -> 정상 주행 속도로 복구 완료")
        self.start_forward(speed if speed is not None else self.target_speed)

    def toggle_drive(self) -> bool:
        """
        주행 시작 / 정지 토글
        :return: True면 주행 중, False면 정지 상태
        """
        if self.is_moving():
            self.emergency_stop()
            return False
        else:
            self.start_forward()
            return True

    def emergency_stop(self):
        """즉시 완전 정지 (E-Stop)"""
        with self._lock:
            self._stop_decel_event.set()
            self.current_speed = 0
            self.state = MotorState.STOPPED

            if not self.is_simulated:
                # 모터 브레이크 (LOW/LOW 또는 PWM 0)
                GPIO.output(self.left_in1, GPIO.LOW)
                GPIO.output(self.left_in2, GPIO.LOW)
                GPIO.output(self.right_in3, GPIO.LOW)
                GPIO.output(self.right_in4, GPIO.LOW)
                self.pwm_left.ChangeDutyCycle(0)
                self.pwm_right.ChangeDutyCycle(0)

            logger.info("🛑 [모터] 비상 긴급 정지 완료!")

    def is_moving(self) -> bool:
        """현재 모터가 회전하고 있는지 여부 반환"""
        return self.current_speed > 0 and self.state in [MotorState.FORWARD, MotorState.DECELERATING, MotorState.RECOVERING]

    def get_status(self) -> dict:
        """GUI 및 Web 전송용 모터 상태 딕셔너리"""
        return {
            "state": self.state,
            "speed": self.current_speed,
            "target_speed": self.target_speed,
            "is_moving": self.is_moving(),
            "is_simulated": self.is_simulated
        }

    def cleanup(self):
        """GPIO 리소스 해제"""
        self.emergency_stop()
        if not self.is_simulated:
            try:
                if self.pwm_left:
                    self.pwm_left.stop()
                if self.pwm_right:
                    self.pwm_right.stop()
                GPIO.cleanup([self.left_in1, self.left_in2, self.left_ena,
                              self.right_in3, self.right_in4, self.right_enb])
            except Exception as e:
                logger.error(f"모터 GPIO cleanup 실패: {e}")
