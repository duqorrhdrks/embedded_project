"""
[라즈베리파이 전용 - pi_agent.py]
라즈베리파이는 AI 추론을 하지 않고 다음 두 가지만 합니다. (torch/ultralytics 설치 불필요)

  1) 카메라 프레임을 JPEG로 압축해 PC 서버의 POST /api/frame 으로 계속 전송
  2) PC 서버의 GET /api/command 에 주기적으로 접속해 명령을 확인하고,
     명령 번호(seq)가 바뀌면 모터/LED GPIO를 실제로 동작시킴
       {"motor": "on"|"off", "speed": 0~100, "led": "on"|"off"|"blink", "seq": N}

안전장치: PC와 FAILSAFE_TIMEOUT_SEC 이상 통신이 끊기면 모터를 즉시 정지하고 LED를 점멸합니다.
          연결이 복구돼도 모터는 자동으로 다시 돌지 않으며, 웹에서 새 명령을 내려야 움직입니다.

사용법 (라즈베리파이에서):
    ./venv/bin/python3 pi_agent/pi_agent.py --server http://<PC의 IP>:8000
"""

import os
import sys
import time
import argparse
import logging
import threading

import cv2
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import HardwareConfig, VisionConfig, PCServerConfig  # noqa: E402
from hardware.motor_controller import DualMotorController  # noqa: E402
from hardware.led_controller import HazardLedController  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("PiAgent")

try:
    from picamera2 import Picamera2
    HAS_PICAM2 = True
except ImportError:
    HAS_PICAM2 = False


class Camera:
    """Picamera2(CSI 카메라) 우선, 실패 시 OpenCV(USB 웹캠)"""

    def __init__(self, width, height, index=0):
        self.picam2 = None
        self.cap = None
        if HAS_PICAM2:
            try:
                self.picam2 = Picamera2()
                self.picam2.configure(self.picam2.create_video_configuration(
                    main={"size": (width, height), "format": "BGR888"}))
                self.picam2.start()
                logger.info("✅ [카메라] CSI 카메라(Picamera2) 사용")
                return
            except Exception as e:
                logger.warning(f"⚠️ [카메라] Picamera2 실패: {e}. OpenCV로 전환")
                self.picam2 = None
        self.cap = cv2.VideoCapture(index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if not self.cap.isOpened():
            raise RuntimeError("사용 가능한 카메라가 없습니다.")
        logger.info(f"✅ [카메라] OpenCV 디바이스 {index}번 사용")

    def read(self):
        if self.picam2 is not None:
            # Picamera2 "BGR888"은 실제 메모리상 RGB 순서이므로 OpenCV용 BGR로 변환
            # (vision/drowsiness_detector.py의 read_frame과 동일한 처리)
            return cv2.cvtColor(self.picam2.capture_array(), cv2.COLOR_RGB2BGR)
        ret, frame = self.cap.read()
        return frame if ret else None

    def release(self):
        if self.picam2 is not None:
            self.picam2.stop()
            self.picam2.close()
        if self.cap is not None:
            self.cap.release()


def frame_uploader(camera, frame_url, fps, quality, stop_event):
    """카메라 프레임을 PC로 계속 전송하는 스레드"""
    session = requests.Session()
    interval = 1.0 / fps
    failing = False
    while not stop_event.is_set():
        t0 = time.time()
        frame = camera.read()
        if frame is not None:
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if ok:
                try:
                    session.post(frame_url, data=buf.tobytes(), headers={"Content-Type": "image/jpeg"}, timeout=1.0)
                    if failing:
                        logger.info("📷 [프레임 전송] PC 연결 복구")
                    failing = False
                except requests.exceptions.RequestException as e:
                    if not failing:
                        logger.error(f"❌ [프레임 전송 실패] {e}")
                    failing = True
        time.sleep(max(0.0, interval - (time.time() - t0)))


class CommandExecutor:
    """PC 명령을 실제 모터/LED 동작으로 변환"""

    def __init__(self):
        self.motor = DualMotorController(
            left_in1=HardwareConfig.LEFT_MOTOR_IN1, left_in2=HardwareConfig.LEFT_MOTOR_IN2,
            left_ena=HardwareConfig.LEFT_MOTOR_ENA, right_in3=HardwareConfig.RIGHT_MOTOR_IN3,
            right_in4=HardwareConfig.RIGHT_MOTOR_IN4, right_enb=HardwareConfig.RIGHT_MOTOR_ENB,
            pwm_freq=HardwareConfig.PWM_FREQUENCY, default_speed=HardwareConfig.DEFAULT_CRUISE_SPEED,
            min_speed=HardwareConfig.MIN_RUNNING_SPEED, decel_step=HardwareConfig.DECELERATION_STEP,
            decel_interval=HardwareConfig.DECELERATION_INTERVAL, force_simulation=HardwareConfig.USE_SIMULATION,
        )
        self.led = HazardLedController(
            pin=HardwareConfig.HAZARD_LED_PIN, blink_interval=HardwareConfig.HAZARD_BLINK_INTERVAL,
            force_simulation=HardwareConfig.USE_SIMULATION,
        )
        self.applied_seq = -1
        self.applied_boot = None

    def apply(self, cmd: dict):
        if cmd["seq"] == self.applied_seq and cmd.get("boot") == self.applied_boot:
            return
        logger.info(f"📥 [명령 #{cmd['seq']}] motor={cmd['motor']} speed={cmd['speed']} led={cmd['led']} (by {cmd.get('source')})")

        if cmd["motor"] == "on":
            self.motor.start_forward(cmd["speed"])
        else:
            self.motor.emergency_stop()

        if cmd["led"] == "on":
            self.led.turn_on()
        elif cmd["led"] == "blink":
            self.led.start_blinking()
        else:
            self.led.turn_off()

        self.applied_seq = cmd["seq"]
        self.applied_boot = cmd.get("boot")

    def failsafe(self):
        logger.warning("🛑 [페일세이프] PC 서버와 통신 두절 -> 모터 정지 + LED 점멸 (웹에서 새 명령을 내려야 재가동)")
        self.motor.emergency_stop()
        self.led.start_blinking()

    def cleanup(self):
        self.motor.cleanup()
        self.led.cleanup()


def main():
    parser = argparse.ArgumentParser(description="라즈베리파이 에이전트: 카메라 송출 + PC 명령 실행")
    parser.add_argument("--server", default=PCServerConfig.PC_SERVER_URL, help="PC 서버 주소 (예: http://192.168.0.10:8000)")
    parser.add_argument("--fps", type=float, default=PCServerConfig.FRAME_UPLOAD_FPS)
    parser.add_argument("--no-camera", action="store_true", help="카메라 전송 없이 명령 실행만 테스트")
    args = parser.parse_args()
    server = args.server.rstrip("/")

    executor = CommandExecutor()
    stop_event = threading.Event()
    camera = None
    if not args.no_camera:
        camera = Camera(VisionConfig.FRAME_WIDTH, VisionConfig.FRAME_HEIGHT, VisionConfig.CAMERA_INDEX)
        threading.Thread(
            target=frame_uploader,
            args=(camera, f"{server}/api/frame", args.fps, PCServerConfig.JPEG_QUALITY, stop_event),
            daemon=True,
        ).start()

    logger.info(f"🔄 [명령 폴링] {server}/api/command 확인 시작")
    session = requests.Session()
    last_ok = time.time()
    in_failsafe = False
    try:
        while True:
            try:
                r = session.get(f"{server}/api/command", params={"ack": executor.applied_seq}, timeout=1.0)
                r.raise_for_status()
                cmd = r.json()
                last_ok = time.time()
                if in_failsafe:
                    logger.info("✅ [명령 폴링] PC 연결 복구 (다음 새 명령부터 실행)")
                    in_failsafe = False
                    # 페일세이프 이전 명령(예: motor=on)으로 자동 재가동되지 않도록 현재 seq를 이미 반영된 것으로 처리
                    executor.applied_seq = cmd["seq"]
                    executor.applied_boot = cmd.get("boot")
                executor.apply(cmd)
            except (requests.exceptions.RequestException, ValueError, KeyError) as e:
                if not in_failsafe and time.time() - last_ok > PCServerConfig.FAILSAFE_TIMEOUT_SEC:
                    logger.error(f"❌ [명령 폴링 실패] {e}")
                    executor.failsafe()
                    in_failsafe = True
            time.sleep(PCServerConfig.COMMAND_POLL_INTERVAL_SEC)
    except KeyboardInterrupt:
        logger.info("종료합니다...")
    finally:
        stop_event.set()
        executor.cleanup()
        if camera is not None:
            camera.release()


if __name__ == "__main__":
    main()
