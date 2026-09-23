"""
[컴퓨터 비전 & 미래 확장 모듈 - lane_assistant.py]
차선 인식 및 비상 갓길 주차(Emergency Roadside Pull-Over) 보조 모듈
- 향후 추가 USB 전방 카메라를 연동하여 도로 차선(Lane)을 실시간 검출
- 운전자 졸음 지속 시 차량을 안전하게 우측 갓길로 조향 및 정차시키는 알고리즘 인터페이스
"""

import logging
import cv2
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LanePullOver")


class LaneDeparturePullOver:
    """
    전방 도로 차선 인식 및 갓길 자동 유도 제어 클래스
    """

    def __init__(self, camera_index: int = 1, pull_over_steer_ratio: float = 0.3):
        """
        :param camera_index: 전방 차선 인식용 보조 USB 카메라 인덱스
        :param pull_over_steer_ratio: 갓길 진입 시 좌/우 모터 차등 제어 비율
        """
        self.camera_index = camera_index
        self.steer_ratio = pull_over_steer_ratio
        self.is_active = False
        self.cap = None

        logger.info("🚗 [미래 확장 모듈] LaneDeparturePullOver (차선 인식 및 갓길 주차 보조기) 로드됨")

    def initialize_lane_camera(self) -> bool:
        """2번째 USB 카메라 연결 시도"""
        try:
            self.cap = cv2.VideoCapture(self.camera_index)
            if self.cap.isOpened():
                self.is_active = True
                logger.info(f"✅ [차선 카메라] 보조 카메라({self.camera_index}번) 연결 성공")
                return True
            else:
                logger.info(f"ℹ️ [차선 카메라] 보조 카메라({self.camera_index}번) 미연결 상태 (필요 시 연결 후 활성화 가능)")
                self.is_active = False
                return False
        except Exception as e:
            logger.warning(f"차선 카메라 초기화 실패: {e}")
            self.is_active = False
            return False

    def detect_lane_lines(self, frame):
        """
        OpenCV 기반 기본 차선 인식 파이프라인
        1. ROI(관심 영역) 설정
        2. Canny Edge 검출
        3. Hough Transform 직선 검출
        """
        if frame is None:
            return None, 0.0

        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 50, 150)

        # 하단 삼각 ROI
        mask = np.zeros_like(edges)
        roi_pts = np.array([[(0, h), (w // 2 - 40, h // 2 + 50), (w // 2 + 40, h // 2 + 50), (w, h)]], dtype=np.int32)
        cv2.fillPoly(mask, roi_pts, 255)
        masked_edges = cv2.bitwise_and(edges, mask)

        # 허프 변환으로 차선 추출
        lines = cv2.HoughLinesP(masked_edges, 1, np.pi / 180, 40, minLineLength=50, maxLineGap=150)

        lane_offset = 0.0  # 중앙 이탈 오프셋
        if lines is not None:
            for line in lines:
                x1, y1, x2, y2 = line[0]
                cv2.line(frame, (x1, y1), (x2, y2), (0, 255, 255), 3)

        return frame, lane_offset

    def calculate_pull_over_speeds(self, base_speed: int) -> tuple:
        """
        갓길(우측 도로변)로 이동하기 위한 좌/우 모터 차등 속도 계산
        :return: (left_motor_speed, right_motor_speed)
        """
        # 우측으로 조향하려면 좌측 모터가 우측 모터보다 빨라야 함
        left_speed = int(base_speed)
        right_speed = int(base_speed * (1.0 - self.steer_ratio))
        return left_speed, right_speed

    def cleanup(self):
        """카메라 자원 정리"""
        if self.cap and self.cap.isOpened():
            self.cap.release()
