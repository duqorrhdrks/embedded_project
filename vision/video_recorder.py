"""
[컴퓨터 비전 모듈 - video_recorder.py]
운전자 졸음 감지 시 이벤트 영상을 파일로 자동 저장하는 비디오 레코더
- 감지 발생 즉시 recordings/ 폴더에 타임스탬프 기반 파일 생성 (MP4/AVI)
- 다중 스레드 동시 접근 보호 (Thread-Safe Video Writing)
- 녹화 시작/종료 이벤트 로깅 및 GUI/웹 연동 상태 제공
"""

import os
import time
import threading
import logging
from datetime import datetime
import cv2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("VideoRecorder")


class EventVideoRecorder:
    """
    졸음 감지 시점부터의 영상을 자동 캡처하여 저장하는 레코더 클래스
    """

    def __init__(self, output_dir: str = "recordings", fourcc_str: str = "mp4v", ext: str = ".mp4"):
        """
        :param output_dir: 비디오 저장 디렉토리 경로
        :param fourcc_str: 비디오 코덱 (mp4v, XVID, avc1 등)
        :param ext: 확장자 (.mp4, .avi)
        """
        self.output_dir = output_dir
        self.fourcc_str = fourcc_str
        self.ext = ext

        self.writer = None
        self._is_recording = False
        self.current_filepath = None
        self.start_time = 0.0
        self.frames_recorded = 0
        self._lock = threading.Lock()

        # 저장 폴더가 없으면 자동 생성
        os.makedirs(self.output_dir, exist_ok=True)

    def start_recording(self, frame_width: int, frame_height: int, fps: float = 20.0) -> str:
        """
        새로운 녹화 세션 시작 (스레드 안전)
        :return: 생성된 파일 경로
        """
        with self._lock:
            if self._is_recording:
                logger.info("ℹ️ [녹화기] 이미 녹화가 진행 중입니다.")
                return self.current_filepath

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"drowsy_event_{timestamp}{self.ext}"
            self.current_filepath = os.path.join(self.output_dir, filename)

            try:
                fourcc = cv2.VideoWriter_fourcc(*self.fourcc_str)
                self.writer = cv2.VideoWriter(
                    self.current_filepath,
                    fourcc,
                    fps,
                    (frame_width, frame_height)
                )

                if not self.writer.isOpened():
                    # mp4v 실패 시 XVID 및 .avi로 자동 폴백 시도
                    fallback_path = os.path.join(self.output_dir, f"drowsy_event_{timestamp}.avi")
                    logger.warning(f"⚠️ {self.fourcc_str} 코덱 열기 실패. XVID/AVI 로 폴백 시도합니다.")
                    fourcc = cv2.VideoWriter_fourcc(*"XVID")
                    self.writer = cv2.VideoWriter(fallback_path, fourcc, fps, (frame_width, frame_height))
                    self.current_filepath = fallback_path

                self._is_recording = True
                self.start_time = time.time()
                self.frames_recorded = 0
                logger.warning(f"🔴 [녹화기] 이벤트 영상 자동 녹화 시작 -> {self.current_filepath}")
                return self.current_filepath

            except Exception as e:
                logger.error(f"❌ [녹화기] 녹화 시작 실패: {e}")
                self._is_recording = False
                self.writer = None
                return ""

    def write_frame(self, frame):
        """녹화 중인 경우 프레임 기록 (스레드 안전)"""
        with self._lock:
            if self._is_recording and self.writer is not None:
                try:
                    self.writer.write(frame)
                    self.frames_recorded += 1
                except Exception as e:
                    logger.error(f"프레임 쓰기 오류: {e}")

    def stop_recording(self) -> str:
        """녹화 종료 및 파일 저장 완료 (스레드 안전)"""
        with self._lock:
            if not self._is_recording:
                return ""

            saved_path = self.current_filepath
            duration = time.time() - self.start_time

            try:
                if self.writer is not None:
                    self.writer.release()
                    self.writer = None
                logger.info(f"💾 [녹화기] 녹화 저장 완료: {saved_path} (총 {self.frames_recorded}프레임 / {duration:.1f}초)")
            except Exception as e:
                logger.error(f"녹화 종료 중 오류: {e}")
            finally:
                self._is_recording = False
                self.current_filepath = None
                self.frames_recorded = 0

            return saved_path

    def is_recording(self) -> bool:
        """현재 녹화 진행 여부"""
        with self._lock:
            return self._is_recording

    def get_status(self) -> dict:
        """GUI 및 Web 전송용 녹화 상태"""
        with self._lock:
            duration = (time.time() - self.start_time) if self._is_recording else 0.0
            return {
                "is_recording": self._is_recording,
                "filepath": self.current_filepath or "",
                "duration": round(duration, 1),
                "frames": self.frames_recorded
            }
