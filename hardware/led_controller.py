"""
[하드웨어 제어 모듈 - led_controller.py]
라즈베리파이 GPIO를 통한 비상등(Hazard Warning LED) 점멸 제어 클래스
- 졸음 감지 시 비동기 스레드로 주기적 점멸 (Blinking)
- 정상 복귀 시 비상등 소등 및 정지
- 하드웨어 미연결 시 가상 시뮬레이션 모드 자동 지원
"""

import time
import threading
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("HazardLedController")

HAS_GPIO = False
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except (ImportError, RuntimeError):
    HAS_GPIO = False


class HazardLedController:
    """
    비상등 LED 제어기
    별도의 백그라운드 스레드를 통해 메인 루프를 블로킹하지 않고
    일정한 주기로 LED를 점멸합니다.
    """

    def __init__(self, pin: int = 18, blink_interval: float = 0.4, force_simulation: bool = False):
        """
        :param pin: 비상등 LED가 연결된 GPIO BCM 핀 번호
        :param blink_interval: 점멸 주기 (초)
        :param force_simulation: 강제 시뮬레이션 모드 여부
        """
        self.pin = pin
        self.blink_interval = blink_interval
        self.is_simulated = force_simulation or (not HAS_GPIO)

        self._is_blinking = False
        self._led_on = False
        self._stop_event = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

        self._init_gpio()

    def _init_gpio(self):
        """GPIO 핀 출력 설정"""
        if self.is_simulated:
            logger.info(f"⚡ [비상등 LED] 가상 시뮬레이션 모드로 작동합니다. (Pin: {self.pin})")
            return

        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(self.pin, GPIO.OUT)
            GPIO.output(self.pin, GPIO.LOW)
            logger.info(f"✅ [비상등 LED] GPIO {self.pin}번 출력 초기화 완료")
        except Exception as e:
            logger.warning(f"⚠️ [비상등 LED] GPIO 초기화 실패: {e}. 시뮬레이션 모드로 전환합니다.")
            self.is_simulated = True

    def start_blinking(self):
        """비상등 점멸 시작"""
        with self._lock:
            if self._is_blinking:
                return  # 이미 점멸 중

            self._is_blinking = True
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._blink_worker, daemon=True)
            self._thread.start()
            logger.warning("🚨 [비상등 LED] 비상등 점멸이 시작되었습니다!")

    def stop_blinking(self):
        """비상등 점멸 정지 및 소등"""
        with self._lock:
            if not self._is_blinking:
                return

            self._is_blinking = False
            self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

        self._set_led_hardware(False)
        logger.info("🟢 [비상등 LED] 비상등이 소등되었습니다. (정상 상태)")

    def turn_on(self):
        """점멸 없이 LED 상시 점등 (원격 명령 'led on' 용)"""
        self.stop_blinking()
        self._set_led_hardware(True)

    def turn_off(self):
        """LED 소등 (원격 명령 'led off' 용)"""
        self.stop_blinking()
        self._set_led_hardware(False)

    def _blink_worker(self):
        """비상등 점멸 백그라운드 루프"""
        state = False
        while not self._stop_event.is_set():
            state = not state
            self._set_led_hardware(state)
            time.sleep(self.blink_interval)

    def _set_led_hardware(self, state: bool):
        """실제 LED 핀 또는 가상 상태 업데이트"""
        self._led_on = state
        if not self.is_simulated:
            try:
                GPIO.output(self.pin, GPIO.HIGH if state else GPIO.LOW)
            except Exception as e:
                logger.error(f"LED 출력 에러: {e}")

    def is_blinking(self) -> bool:
        """현재 비상등이 작동 중인지 여부"""
        return self._is_blinking

    def get_status(self) -> dict:
        """GUI 및 Web 표시용 상태 딕셔너리"""
        return {
            "is_blinking": self._is_blinking,
            "led_on": self._led_on,
            "pin": self.pin,
            "is_simulated": self.is_simulated
        }

    def cleanup(self):
        """종료 시 리소스 해제"""
        self.stop_blinking()
        if not self.is_simulated:
            try:
                GPIO.cleanup(self.pin)
            except Exception as e:
                logger.error(f"LED GPIO cleanup 실패: {e}")
