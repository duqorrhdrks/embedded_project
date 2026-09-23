"""
[시스템 중앙 설정 파일 - config.py]
라즈베리파이 핀 맵, 모터 제어 파라미터, 비전/AI 설정, 웹 서버 포트 등
프로젝트의 모든 설정값을 한곳에서 통합 관리합니다.
"""

import os

# ==============================================================================
# 1. 하드웨어 설정 (GPIO Pin Mapping & Motor Parameters)
# ==============================================================================
class HardwareConfig:
    # GPIO 모드: BCM 핀 번호 체계 사용
    # RPi.GPIO가 없는 환경(PC 등)에서는 자동으로 가상 시뮬레이션 모드로 전환됩니다.
    USE_SIMULATION = False  # False일 경우 실제 GPIO 접근 시도 후 실패 시 자동 Fallback

    # 좌측 모터 (Motor Left) - L298N 모터 드라이버 기준
    LEFT_MOTOR_IN1 = 17   # 정회전 핀
    LEFT_MOTOR_IN2 = 27   # 역회전 핀
    LEFT_MOTOR_ENA = 22   # 속도 제어(PWM) 핀

    # 우측 모터 (Motor Right) - L298N 모터 드라이버 기준
    RIGHT_MOTOR_IN3 = 23  # 정회전 핀
    RIGHT_MOTOR_IN4 = 24  # 역회전 핀
    RIGHT_MOTOR_ENB = 25  # 속도 제어(PWM) 핀

    # PWM 주파수 (Hz)
    PWM_FREQUENCY = 1000

    # 모터 속도 설정 (0 ~ 100 %)
    DEFAULT_CRUISE_SPEED = 70   # 기본 순항 주행 속도 (%)
    MIN_RUNNING_SPEED = 20      # 모터 최소 기동 속도 (%)
    DECELERATION_STEP = 10      # 감속 단계별 감소 속도 (%)
    DECELERATION_INTERVAL = 0.3 # 감속 단계 간격 (초 단위)

    # 비상등 LED (Hazard Warning LED)
    HAZARD_LED_PIN = 18         # 비상등 LED GPIO 핀
    HAZARD_BLINK_INTERVAL = 0.4 # 비상등 점멸 주기 (초 단위: 0.4초 켜짐 / 0.4초 꺼짐)


# ==============================================================================
# 2. 비전 및 AI 설정 (Camera & YOLOv8 Drowsiness Model)
# ==============================================================================
class VisionConfig:
    # 카메라 입력 설정
    CAMERA_INDEX = 0            # 기본 카메라 인덱스 (라즈베리파이 카메라 또는 USB 웹캠 0번)
    FRAME_WIDTH = 640           # 영상 가로 해상도
    FRAME_HEIGHT = 480          # 영상 세로 해상도
    TARGET_FPS = 30             # 목표 프레임 레이트

    # YOLOv8 모델 설정
    MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
    MODEL_PATH = os.path.join(MODEL_DIR, "best.pt") # 학습된 best.pt 가중치 파일 경로
    CONFIDENCE_THRESHOLD = 0.5  # 객체 인식 신뢰도 임계값 (0.0 ~ 1.0)
    
    # 졸음 판정 클래스 이름 (사용자가 best.pt 학습 시 지정한 클래스 이름과 매칭)
    # 판정 로직은 부분 문자열 매칭(dc in cls_name)이므로 "eyes_closed" 하나만 있어도
    # eyes_closed_head_left / eyes_closed_head_right 등 파생 클래스가 전부 자동 매칭됩니다.
    # 현재 배치된 models/best.pt의 실제 클래스: eyes_closed, eyes_closed_head_left,
    # eyes_closed_head_right, focused, head_down, head_up, seeing_left, seeing_right, yarning
    DROWSY_CLASS_NAMES = ["eyes_closed", "closed_eye", "closed_eyes", "drowsy", "sleep", "sleeping"]

    # 졸음 판정 지속 시간 (순간적인 눈 깜빡임 오작동 방지용 필터링)
    DROWSY_DURATION_THRESHOLD_SEC = 1.2  # 연속으로 눈을 감고 있는 시간 (초)
    DROWSY_RECOVERY_TIME_SEC = 1.0       # 눈을 뜬 상태가 지속될 때 정상 복귀 임계시간 (초)

    # 이벤트 비디오 자동 녹화 설정
    RECORDINGS_DIR = os.path.join(os.path.dirname(__file__), "recordings")
    VIDEO_FOURCC = "mp4v"       # 비디오 코덱 (mp4v, XVID 등)
    VIDEO_EXT = ".mp4"          # 저장 비디오 확장자


# ==============================================================================
# 3. 웹 서버 및 통신 설정 (Flask Web Server)
# ==============================================================================
class WebConfig:
    HOST = "0.0.0.0"            # 모든 네트워크 인터페이스에서 접속 허용
    PORT = 5000                 # 웹 서버 포트
    DEBUG = False               # 디버그 모드 (GUI 스레드 충돌 방지를 위해 False 권장)
    SECRET_KEY = "embedded_robot_secret_key"


# ==============================================================================
# 4. 차선 인식 및 갓길 주차 확장용 설정 (Future Extension)
# ==============================================================================
class AutonomousPullOverConfig:
    ENABLE_PULL_OVER_EXTENSION = True # 갓길 주차 모듈 활성화 여부
    LANE_CAMERA_INDEX = 1             # 전방 차선 인식용 보조 USB 카메라 인덱스
    PULL_OVER_STEER_RATIO = 0.3       # 갓길 우측 이동 시 좌/우 모터 차등 조향 비율
