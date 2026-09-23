"""
[메인 통합 실행 파일 - main.py]
임베디드 로봇의 중앙 관제 시스템 구동 엔트리포인트
1. SystemManager 초기화 (모터, LED, 졸음감지 AI, 녹화기)
2. Flask 웹 서버 백그라운드 실행 (포트 5000)
3. PyQt5 GUI 대시보드 실행 (디스플레이 감지 시) 또는 Web-Only 모드 자동 지원
"""

import sys
import os
import argparse
import logging
import signal

# 로깅 포맷 설정
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("MainApplication")

# 패키지 경로 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import WebConfig
from system_manager import SystemManager
from web.server import FlaskWebEngine


def main():
    parser = argparse.ArgumentParser(description="AI 운전자 안면 모니터링 및 자율 안전 로봇 관제 시스템")
    parser.add_argument("--no-gui", action="store_true", help="GUI 창을 띄우지 않고 웹 서버 및 백그라운드 모드로만 구동")
    parser.add_argument("--port", type=int, default=WebConfig.PORT, help="Flask 웹 서버 포트 (기본: 5000)")
    args = parser.parse_args()

    logger.info("==========================================================")
    logger.info("  AI 운전자 안면 모니터링 및 자율 안전 로봇 관제 시스템 시작  ")
    logger.info("==========================================================")

    # 1. 시스템 매니저 초기화 (모든 하드웨어 및 비전 객체 연결)
    system_mgr = SystemManager()

    # 2. Flask 웹 서버 백그라운드 스레드 시작
    web_server = FlaskWebEngine(
        system_manager=system_mgr,
        host=WebConfig.HOST,
        port=args.port,
        secret_key=WebConfig.SECRET_KEY
    )
    web_server.start()
    logger.info(f"🌐 [웹 대시보드 접속 가능] http://localhost:{args.port} 또는 http://<라즈베리파이-IP>:{args.port}")

    # 3. 주행 및 비전 분석 백그라운드 루프 개시
    system_mgr.start_system()

    # 4. GUI 실행 여부 판단 (디스플레이 환경 확인)
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    should_run_gui = (not args.no_gui) and has_display

    # 종료 시그널 핸들러 (Ctrl+C 안전 종료)
    def handle_signal(sig, frame):
        logger.info("\n종료 시그널 수신 -> 시스템을 안전하게 정지합니다...")
        system_mgr.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)

    if should_run_gui:
        try:
            logger.info("🖥️ [GUI 모드] PyQt5 그래픽 대시보드를 실행합니다.")
            from PyQt5.QtWidgets import QApplication
            from gui.dashboard_gui import DashboardMainWindow

            app = QApplication(sys.argv)
            window = DashboardMainWindow(system_mgr)
            window.show()
            sys.exit(app.exec_())
        except Exception as e:
            logger.warning(f"⚠️ GUI 실행 실패 ({e}). 웹 관제 모드로 계속 실행합니다.")
            should_run_gui = False

    if not should_run_gui:
        logger.info("📡 [웹/헤드리스 모드] 디스플레이가 없거나 --no-gui 옵션이 지정되어 웹 관제 모드로 상시 구동합니다.")
        logger.info("종료하려면 터미널에서 Ctrl+C를 누르세요.")
        try:
            while True:
                signal.pause()
        except (KeyboardInterrupt, AttributeError):
            import time
            while True:
                time.sleep(1)


if __name__ == "__main__":
    main()
