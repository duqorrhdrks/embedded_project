"""
[GUI 소프트웨어 모듈 - dashboard_gui.py]
PyQt5 기반 운전자 모니터링 및 자율 안전 로봇 관제 대시보드
- 실시간 카메라 영상 피드 (환경 화면 및 AI 안전 관제 OSD)
- 구동 모터 속도 및 비상 감속 상태 시각화
- 비상 경고등(LED) 상태 및 점멸 인디케이터
- 이벤트 영상 자동 녹화 장치 연동
- [경보 해제 및 정상 주행 복귀] 원터치 복구 버튼
- 비상 정지 및 졸음 감지 모의 시험(테스트) 기능
"""

import sys
import cv2
import logging
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QFrame
)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QImage, QPixmap

logger = logging.getLogger("DashboardGUI")


class DashboardMainWindow(QMainWindow):
    """
    임베디드 로봇 통합 관제 GUI 메인 윈도우
    """

    def __init__(self, system_manager):
        super().__init__()
        self.system_manager = system_manager

        self.setWindowTitle("AI 운전자 안면 모니터링 및 자율 안전 로봇 관제 시스템")
        self.resize(1100, 700)
        self.setMinimumSize(980, 620)

        # 상태 캐시 (불필요한 스타일시트 재파싱 방지로 UI 반응속도 및 버튼 클릭 보장)
        self._last_banner_mode = None
        self._last_motor_mode = None
        self._last_hazard_state = None
        self._last_rec_state = None
        self.blink_tick = 0

        # 기본 전역 스타일시트
        self.setStyleSheet("""
            QMainWindow {
                background-color: #101419;
            }
            QFrame.card {
                background-color: #171c23;
                border: 1px solid #28313e;
                border-radius: 12px;
                padding: 12px;
            }
            QLabel {
                color: #e2e8f0;
                font-family: 'Segoe UI', 'Malgun Gothic', 'Noto Sans CJK KR', sans-serif;
            }
            QPushButton {
                font-family: 'Segoe UI', 'Malgun Gothic', 'Noto Sans CJK KR', sans-serif;
                font-weight: bold;
                border-radius: 8px;
                padding: 12px 18px;
                font-size: 14px;
            }
            QPushButton#btn-recover {
                background-color: #1f883d;
                color: #ffffff;
                border: 1px solid #2ea043;
                font-size: 15px;
            }
            QPushButton#btn-recover:hover {
                background-color: #2ea043;
            }
            QPushButton#btn-recover:pressed {
                background-color: #176326;
            }
            QPushButton#btn-stop {
                background-color: #cf222e;
                color: #ffffff;
                border: 1px solid #da3633;
            }
            QPushButton#btn-stop:hover {
                background-color: #da3633;
            }
            QPushButton#btn-stop:pressed {
                background-color: #82071e;
            }
            QPushButton#btn-drive {
                background-color: #1f6feb;
                color: #ffffff;
                border: 1px solid #388bfd;
            }
            QPushButton#btn-drive:hover {
                background-color: #388bfd;
            }
            QPushButton#btn-drive:pressed {
                background-color: #1158c7;
            }
            QPushButton#btn-sim {
                background-color: #2d333b;
                color: #adbac7;
                border: 1px solid #444c56;
            }
            QPushButton#btn-sim:hover {
                background-color: #373e47;
            }
            QProgressBar {
                border: 1px solid #2d333b;
                border-radius: 6px;
                background-color: #0d1117;
                text-align: center;
                color: #ffffff;
                font-weight: bold;
                height: 24px;
            }
            QProgressBar::chunk {
                background-color: #238636;
                border-radius: 5px;
            }
        """)

        self._init_ui()

        # UI 업데이트용 주기적 타이머 (40ms = 25 FPS, CPU 과점유 방지)
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._refresh_dashboard)
        self.update_timer.start(40)

    def _init_ui(self):
        """UI 위젯 레이아웃 구성"""
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(14)

        # ======================================================================
        # 1. 상단 글로벌 관제 상태 배너
        # ======================================================================
        self.banner_frame = QFrame()
        self.banner_frame.setFixedHeight(54)
        self.banner_frame.setStyleSheet("""
            background-color: #122419;
            border-radius: 8px;
            border: 1px solid #238636;
            border-left: 6px solid #3fb950;
        """)
        banner_layout = QHBoxLayout(self.banner_frame)
        banner_layout.setContentsMargins(18, 0, 18, 0)

        self.lbl_system_status = QLabel("● 관제 시스템 상태: 정상 주행 상태 모니터링 중")
        self.lbl_system_status.setStyleSheet("font-size: 15px; font-weight: bold; color: #3fb950;")
        banner_layout.addWidget(self.lbl_system_status)

        self.lbl_web_info = QLabel("🌐 원격 웹 관제: http://<라즈베리파이-IP>:5000")
        self.lbl_web_info.setStyleSheet("font-size: 13px; color: #8b949e;")
        banner_layout.addWidget(self.lbl_web_info, alignment=Qt.AlignRight)

        main_layout.addWidget(self.banner_frame)

        # ======================================================================
        # 2. 중앙 컨텐츠 영역 (좌측: 실시간 카메라 피드, 우측: 상태 패널)
        # ======================================================================
        content_layout = QHBoxLayout()
        content_layout.setSpacing(14)

        # 2-1. 좌측 비디오 피드 카드
        video_card = QFrame()
        video_card.setProperty("class", "card")
        video_card.setStyleSheet("background-color: #171c23; border: 1px solid #28313e; border-radius: 12px; padding: 10px;")
        video_vbox = QVBoxLayout(video_card)

        lbl_video_title = QLabel("🎥 실시간 운전자 영상 모니터링 (실시간 카메라 환경)")
        lbl_video_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #58a6ff;")
        video_vbox.addWidget(lbl_video_title)

        self.lbl_video_feed = QLabel("카메라 영상을 불러오는 중입니다...")
        self.lbl_video_feed.setAlignment(Qt.AlignCenter)
        self.lbl_video_feed.setMinimumSize(640, 440)
        self.lbl_video_feed.setStyleSheet("background-color: #0b0e12; border-radius: 8px; border: 1px solid #1f2733;")
        video_vbox.addWidget(self.lbl_video_feed, stretch=1)

        content_layout.addWidget(video_card, stretch=6)

        # 2-2. 우측 제어 및 상태 모니터링 패널
        right_panel = QVBoxLayout()
        right_panel.setSpacing(12)

        # [구동 모터 상태 카드]
        motor_card = QFrame()
        motor_card.setStyleSheet("background-color: #171c23; border: 1px solid #28313e; border-radius: 12px; padding: 12px;")
        motor_vbox = QVBoxLayout(motor_card)

        lbl_motor_head = QLabel("⚙️ 구동 모터 주행 상태")
        lbl_motor_head.setStyleSheet("font-size: 14px; font-weight: bold; color: #58a6ff;")
        motor_vbox.addWidget(lbl_motor_head)

        self.lbl_motor_state = QLabel("모터 상태: 정지 (STOPPED)")
        self.lbl_motor_state.setStyleSheet("font-size: 13px; font-weight: bold; color: #c9d1d9;")
        motor_vbox.addWidget(self.lbl_motor_state)

        motor_vbox.addWidget(QLabel("모터 출력 (PWM 속도):"))
        self.progress_speed = QProgressBar()
        self.progress_speed.setRange(0, 100)
        self.progress_speed.setValue(0)
        self.progress_speed.setFormat("%v %")
        motor_vbox.addWidget(self.progress_speed)

        right_panel.addWidget(motor_card)

        # [비상 경고등 상태 카드]
        led_card = QFrame()
        led_card.setStyleSheet("background-color: #171c23; border: 1px solid #28313e; border-radius: 12px; padding: 12px;")
        led_vbox = QVBoxLayout(led_card)

        lbl_led_head = QLabel("🚨 비상 경고등 (Hazard Warning LED)")
        lbl_led_head.setStyleSheet("font-size: 14px; font-weight: bold; color: #f0883e;")
        led_vbox.addWidget(lbl_led_head)

        self.lbl_led_indicator = QLabel("● 비상 경고등: 정상 (소등)")
        self.lbl_led_indicator.setStyleSheet("font-size: 14px; font-weight: bold; color: #8b949e;")
        led_vbox.addWidget(self.lbl_led_indicator)

        self.lbl_led_pin_info = QLabel("제어 핀: GPIO 18번")
        self.lbl_led_pin_info.setStyleSheet("font-size: 12px; color: #6e7681;")
        led_vbox.addWidget(self.lbl_led_pin_info)

        right_panel.addWidget(led_card)

        # [이벤트 영상 자동 녹화 카드]
        rec_card = QFrame()
        rec_card.setStyleSheet("background-color: #171c23; border: 1px solid #28313e; border-radius: 12px; padding: 12px;")
        rec_vbox = QVBoxLayout(rec_card)

        lbl_rec_head = QLabel("📹 이벤트 영상 자동 녹화 장치")
        lbl_rec_head.setStyleSheet("font-size: 14px; font-weight: bold; color: #f85149;")
        rec_vbox.addWidget(lbl_rec_head)

        self.lbl_rec_status = QLabel("대기 중 (졸음 감지 시 즉시 자동 녹화)")
        self.lbl_rec_status.setStyleSheet("font-size: 13px; color: #8b949e;")
        rec_vbox.addWidget(self.lbl_rec_status)

        self.lbl_rec_path = QLabel("저장 위치: recordings/")
        self.lbl_rec_path.setStyleSheet("font-size: 11px; color: #6e7681;")
        rec_vbox.addWidget(self.lbl_rec_path)

        right_panel.addWidget(rec_card)

        # [시스템 실시간 관제 기록 카드]
        msg_card = QFrame()
        msg_card.setStyleSheet("background-color: #11151a; border: 1px dashed #2d3644; border-radius: 8px; padding: 10px;")
        msg_vbox = QVBoxLayout(msg_card)

        lbl_log_head = QLabel("📋 시스템 실시간 관제 기록")
        lbl_log_head.setStyleSheet("font-size: 12px; font-weight: bold; color: #79c0ff;")
        msg_vbox.addWidget(lbl_log_head)

        self.lbl_event_log = QLabel("시스템 준비 완료: 정상 주행 대기 중")
        self.lbl_event_log.setWordWrap(True)
        self.lbl_event_log.setStyleSheet("font-size: 12px; color: #8b949e;")
        msg_vbox.addWidget(self.lbl_event_log)
        right_panel.addWidget(msg_card)

        content_layout.addLayout(right_panel, stretch=4)
        main_layout.addLayout(content_layout, stretch=1)

        # ======================================================================
        # 3. 하단 컨트롤 버튼 바 (원활한 조작 및 명확한 시각 피드백)
        # ======================================================================
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        # 3-1. 핵심 복구 버튼
        self.btn_recover = QPushButton("🟢 경보 해제 및 정상 주행 복귀")
        self.btn_recover.setObjectName("btn-recover")
        self.btn_recover.setCursor(Qt.PointingHandCursor)
        self.btn_recover.clicked.connect(self._on_recover_clicked)
        btn_layout.addWidget(self.btn_recover, stretch=4)

        # 3-2. 긴급 비상 제동 버튼
        self.btn_stop = QPushButton("🛑 비상 정지 (긴급 제동)")
        self.btn_stop.setObjectName("btn-stop")
        self.btn_stop.setCursor(Qt.PointingHandCursor)
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        btn_layout.addWidget(self.btn_stop, stretch=2)

        # 3-3. 모의 졸음 유발 토글 버튼
        self.btn_sim = QPushButton("🧪 졸음 감지 모의 시험 [꺼짐]")
        self.btn_sim.setObjectName("btn-sim")
        self.btn_sim.setCursor(Qt.PointingHandCursor)
        self.btn_sim.clicked.connect(self._on_toggle_sim_clicked)
        btn_layout.addWidget(self.btn_sim, stretch=2)

        # 3-4. 차량 주행 시작 / 일시 정지 토글 버튼
        self.btn_drive = QPushButton("🚗 차량 주행 시작")
        self.btn_drive.setObjectName("btn-drive")
        self.btn_drive.setCursor(Qt.PointingHandCursor)
        self.btn_drive.clicked.connect(self._on_drive_toggle_clicked)
        btn_layout.addWidget(self.btn_drive, stretch=2)

        main_layout.addLayout(btn_layout)

    def _refresh_dashboard(self):
        """주기적으로 시스템 상태 및 비디오 프레임 갱신 (성능 최적화 적용)"""
        self.blink_tick += 1

        # 1. 비디오 프레임 렌더링
        frame = self.system_manager.get_latest_frame()
        if frame is not None:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_frame.shape
            bytes_per_line = ch * w
            qt_img = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
            # FastTransformation을 사용하여 ARM 라즈베리파이에서 CPU 점유율을 대폭 낮춤
            scaled_pixmap = QPixmap.fromImage(qt_img).scaled(
                self.lbl_video_feed.size(), Qt.KeepAspectRatio, Qt.FastTransformation
            )
            self.lbl_video_feed.setPixmap(scaled_pixmap)

        # 2. 시스템 상태 데이터 취득
        status = self.system_manager.get_full_system_status()
        motor = status["motor"]
        led = status["led"]
        rec = status["recorder"]
        drowsy = status["drowsiness"]

        # 모터 상태 갱신
        self.lbl_motor_state.setText(f"모터 상태: {motor['state']}")
        self.progress_speed.setValue(motor["speed"])

        # 주행 버튼 텍스트 동기화
        if motor["is_moving"]:
            self.btn_drive.setText("⏸️ 차량 주행 일시 정지")
        else:
            self.btn_drive.setText("🚗 차량 주행 시작")

        # 모터 게이지 스타일 (상태 변경 시에만 업데이트)
        if self._last_motor_mode != motor["state"]:
            self._last_motor_mode = motor["state"]
            if motor["state"] == "DECELERATING":
                self.progress_speed.setStyleSheet("QProgressBar::chunk { background-color: #d29922; border-radius: 5px; }")
            elif motor["state"] == "FORWARD":
                self.progress_speed.setStyleSheet("QProgressBar::chunk { background-color: #238636; border-radius: 5px; }")
            else:
                self.progress_speed.setStyleSheet("QProgressBar::chunk { background-color: #da3633; border-radius: 5px; }")

        # 비상등 상태 갱신
        if led["is_blinking"]:
            if (self.blink_tick // 8) % 2 == 0:
                self.lbl_led_indicator.setText("🚨 비상 경고등: 비상 점멸 작동 중! [점등]")
                self.lbl_led_indicator.setStyleSheet("font-size: 14px; font-weight: bold; color: #ff7b72;")
            else:
                self.lbl_led_indicator.setText("⚠️ 비상 경고등: 비상 점멸 작동 중! [소등]")
                self.lbl_led_indicator.setStyleSheet("font-size: 14px; font-weight: bold; color: #d29922;")
        else:
            if self._last_hazard_state != "OFF":
                self._last_hazard_state = "OFF"
                self.lbl_led_indicator.setText("● 비상 경고등: 정상 (소등)")
                self.lbl_led_indicator.setStyleSheet("font-size: 14px; font-weight: bold; color: #8b949e;")

        # 이벤트 영상 자동 녹화 상태 갱신
        if rec["is_recording"]:
            rec_icon = "🔴 REC" if (self.blink_tick // 10) % 2 == 0 else "⚪ REC"
            self.lbl_rec_status.setText(f"{rec_icon} 이벤트 자동 녹화 중... ({rec['duration']}초)")
            self.lbl_rec_status.setStyleSheet("font-size: 13px; font-weight: bold; color: #f85149;")
            self.lbl_rec_path.setText(f"파일: {rec['filepath']}")
        else:
            if self._last_rec_state != "IDLE":
                self._last_rec_state = "IDLE"
                self.lbl_rec_status.setText("대기 중 (졸음 감지 시 즉시 자동 녹화)")
                self.lbl_rec_status.setStyleSheet("font-size: 13px; color: #8b949e;")

        # 상단 글로벌 배너 상태 갱신 (상태 전이 시에만 스타일시트 재적용)
        banner_mode = "NORMAL"
        if drowsy["is_drowsy"]:
            banner_mode = "DROWSY"
        elif motor["state"] == "DECELERATING":
            banner_mode = "DECEL"

        if self._last_banner_mode != banner_mode:
            self._last_banner_mode = banner_mode
            if banner_mode == "DROWSY":
                self.banner_frame.setStyleSheet("""
                    background-color: #3b1219;
                    border-radius: 8px;
                    border: 1px solid #da3633;
                    border-left: 6px solid #f85149;
                """)
                self.lbl_system_status.setText("🚨 [위험 경보] 운전자 졸음 감지! 비상 감속 및 이벤트 영상 자동 녹화 중")
                self.lbl_system_status.setStyleSheet("font-size: 15px; font-weight: bold; color: #ff7b72;")
            elif banner_mode == "DECEL":
                self.banner_frame.setStyleSheet("""
                    background-color: #2b2210;
                    border-radius: 8px;
                    border: 1px solid #d29922;
                    border-left: 6px solid #e3b341;
                """)
                self.lbl_system_status.setText("⚠️ [안전 제어] 차량 속도를 점진적으로 안전 감속하는 중...")
                self.lbl_system_status.setStyleSheet("font-size: 15px; font-weight: bold; color: #e3b341;")
            else:
                self.banner_frame.setStyleSheet("""
                    background-color: #122419;
                    border-radius: 8px;
                    border: 1px solid #238636;
                    border-left: 6px solid #3fb950;
                """)
                self.lbl_system_status.setText("● 관제 시스템 상태: 정상 주행 상태 모니터링 중")
                self.lbl_system_status.setStyleSheet("font-size: 15px; font-weight: bold; color: #3fb950;")

        # 모의 시험 버튼 상태 표시 동기화
        sim_state = drowsy.get("is_simulated", False)
        if sim_state:
            self.btn_sim.setText("🧪 졸음 모의 시험 [켜짐 - 눈 감김]")
            self.btn_sim.setStyleSheet("background-color: #9e6a03; color: #ffffff; border: 1px solid #d29922;")
        else:
            self.btn_sim.setText("🧪 졸음 감지 모의 시험 [꺼짐]")
            self.btn_sim.setStyleSheet("background-color: #2d333b; color: #adbac7; border: 1px solid #444c56;")

        # 로그 메시지 갱신
        self.lbl_event_log.setText(f"관제 메시지: {status['last_event_message']}")

    def _on_recover_clicked(self):
        """[핵심 버튼] 운전자 정상 상태 확인 후 정상 주행 복구"""
        self.system_manager.recover_system()
        logger.info("GUI: [경보 해제 및 정상 주행 복귀] 버튼 클릭 완료")

    def _on_stop_clicked(self):
        """비상 정지 클릭"""
        self.system_manager.manual_stop()
        logger.warning("GUI: [비상 정지] 버튼 클릭 완료")

    def _on_toggle_sim_clicked(self):
        """테스트용 모의 졸음 상태 토글"""
        detector = self.system_manager.drowsiness_detector
        new_sim_state = detector.toggle_simulation_drowsy()
        msg = "모의 눈 감김 활성화" if new_sim_state else "모의 눈 감김 해제"
        logger.info(f"GUI: {msg}")

    def _on_drive_toggle_clicked(self):
        """주행 시작 / 정지 토글 클릭"""
        is_driving = self.system_manager.toggle_drive()
        logger.info(f"GUI: 주행 토글 -> {'주행 시작' if is_driving else '주행 정지'}")

    def closeEvent(self, event):
        """창 닫기 이벤트 시 안전 종료"""
        self.update_timer.stop()
        self.system_manager.shutdown()
        event.accept()

