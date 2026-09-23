"""
[웹 백엔드 모듈 - server.py]
Flask 기반 실시간 비디오 스트리밍 및 원격 관제 REST API 서버
- /video_feed : 카메라 MJPEG 실시간 스트리밍
- /api/status : 차량 및 비전, 녹화기 상태 실시간 반환
- /api/recover : [경보 해제 및 정상 주행 복귀] 웹 원격 명령
- /api/trigger_drowsy : 졸음 감지 시뮬레이션 테스트
- /api/inference : 외부 프레임 업로드 시 YOLOv8 졸음 판정 API
"""

import time
import threading
import logging
import cv2
import numpy as np
from flask import Flask, render_template, Response, jsonify, request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FlaskWebServer")


class FlaskWebEngine:
    """
    라즈베리파이 웹 관제 서버 엔진
    GUI와 함께 백그라운드 스레드로 실행됩니다.
    """

    def __init__(self, system_manager, host="0.0.0.0", port=5000, secret_key="secret"):
        self.system_manager = system_manager
        self.host = host
        self.port = port
        self.server_thread = None

        self.app = Flask(
            __name__,
            template_folder="templates",
            static_folder="static"
        )
        self.app.secret_key = secret_key

        self._register_routes()

    def _register_routes(self):
        """Flask 라우트 핸들러 등록"""

        @self.app.route("/")
        def index():
            """웹 메인 대시보드 페이지"""
            return render_template("index.html")

        @self.app.route("/video_feed")
        def video_feed():
            """MJPEG 실시간 비디오 스트리밍 엔드포인트 (OSD 오버레이 포함, 사람이 보는 대시보드용)"""
            return Response(
                self._generate_frames(annotated=True),
                mimetype="multipart/x-mixed-replace; boundary=frame"
            )

        @self.app.route("/video_feed_raw")
        def video_feed_raw():
            """
            [노트북 등 외부 AI 추론용] OSD 오버레이가 없는 원본 카메라 MJPEG 스트림.
            노트북에서 이 주소를 cv2.VideoCapture로 열어 YOLO 추론에 사용하세요.
            """
            return Response(
                self._generate_frames(annotated=False),
                mimetype="multipart/x-mixed-replace; boundary=frame"
            )

        @self.app.route("/api/external_judgment", methods=["POST"])
        def api_external_judgment():
            """
            [노트북 등 외부 AI 판단 수신 API]
            /video_feed_raw 로 전달받은 영상을 노트북에서 YOLO로 판단한 결과를
            {"eyes_closed": true/false} JSON으로 전달받아 그대로 반영합니다.
            한 번이라도 호출되면 이후 라즈베리파이 로컬 YOLO/Haar 판정보다 이 값이 우선됩니다.
            """
            data = request.get_json(silent=True) or {}
            if "eyes_closed" not in data:
                return jsonify({
                    "success": False,
                    "message": "'eyes_closed'(boolean) 필드가 필요합니다. 예: {\"eyes_closed\": true}"
                }), 400

            eyes_closed = bool(data["eyes_closed"])
            self.system_manager.apply_external_judgment(eyes_closed)
            return jsonify({"success": True, "eyes_closed": eyes_closed})

        @self.app.route("/api/status")
        def api_status():
            """전체 시스템 상태 JSON API"""
            status = self.system_manager.get_full_system_status()
            return jsonify({
                "success": True,
                "data": status
            })

        @self.app.route("/api/recover", methods=["POST", "GET"])
        def api_recover():
            """[원격 복구 API] 운전자 정상 확인 후 비상등 해제 및 정상 주행 복귀"""
            self.system_manager.recover_system()
            logger.info("🌐 [Web API] 운전자 정상 상태 확인 및 주행 복귀 명령이 실행되었습니다.")
            return jsonify({
                "success": True,
                "message": "운전자 상태 정상 확인: 경보 해제 및 정상 주행 복귀 완료"
            })

        @self.app.route("/api/trigger_drowsy", methods=["POST", "GET"])
        def api_trigger_drowsy():
            """[테스트 API] 모의 졸음(눈 감김) 상태 토글"""
            detector = self.system_manager.drowsiness_detector
            state = detector.toggle_simulation_drowsy()
            return jsonify({
                "success": True,
                "simulated_eye_closed": state,
                "message": f"모의 눈 감김 상태: {'눈 감김 [켜짐]' if state else '눈 뜸 [꺼짐]'}"
            })

        @self.app.route("/api/stop", methods=["POST", "GET"])
        def api_stop():
            """[원격 긴급 제동 API]"""
            self.system_manager.manual_stop()
            return jsonify({
                "success": True,
                "message": "원격 비상 정지(긴급 제동) 명령이 실행되었습니다."
            })

        @self.app.route("/api/start", methods=["POST", "GET"])
        def api_start():
            """[원격 주행 시작 API]"""
            self.system_manager.motor_controller.start_forward()
            return jsonify({
                "success": True,
                "message": "원격 주행 시작 명령이 실행되었습니다."
            })

        @self.app.route("/api/toggle_drive", methods=["POST", "GET"])
        def api_toggle_drive():
            """[원격 주행 시작 / 정지 토글 API]"""
            is_driving = self.system_manager.toggle_drive()
            return jsonify({
                "success": True,
                "is_driving": is_driving,
                "message": "차량 주행 시작" if is_driving else "차량 주행 정지"
            })

        @self.app.route("/api/inference", methods=["POST"])
        def api_inference():
            """
            [외부 프레임 업로드 추론 API]
            multipart/form-data 로 key='frame' 이미지 파일을 받아 현재 로드된
            YOLOv8(best.pt) 또는 Haar Cascade 엔진으로 즉시 졸음(눈 감김) 판정만 수행합니다.
            (차량의 실시간 졸음 지속시간 판정 상태에는 영향을 주지 않는 단발성 조회용 API)
            """
            if "frame" not in request.files:
                return jsonify({
                    "success": False,
                    "message": "이미지 파일이 필요합니다 (multipart/form-data, key='frame')"
                }), 400

            file_bytes = np.frombuffer(request.files["frame"].read(), dtype=np.uint8)
            frame = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
            if frame is None:
                return jsonify({
                    "success": False,
                    "message": "업로드된 이미지를 디코딩할 수 없습니다."
                }), 400

            detector = self.system_manager.drowsiness_detector
            result = detector.infer_single_image(frame)
            return jsonify({
                "success": True,
                "is_drowsy": result["eyes_closed"],
                "detail": result
            })

    def _generate_frames(self, annotated: bool = True):
        """MJPEG 프레임 제너레이터
        :param annotated: True면 OSD 오버레이가 그려진 프레임(/video_feed),
                           False면 원본 프레임(/video_feed_raw, 외부 AI 추론용)
        """
        while True:
            frame = self.system_manager.get_latest_frame() if annotated else self.system_manager.get_latest_raw_frame()
            if frame is None:
                time.sleep(0.05)
                continue

            # JPEG 인코딩
            ret, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if not ret:
                time.sleep(0.05)
                continue

            frame_bytes = buffer.tobytes()
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")

            time.sleep(0.04)  # 약 25 FPS 스트리밍

    def start(self):
        """별도 데몬 스레드에서 Flask 서버 시작"""
        self.server_thread = threading.Thread(
            target=lambda: self.app.run(
                host=self.host,
                port=self.port,
                debug=False,
                use_reloader=False,
                threaded=True
            ),
            daemon=True
        )
        self.server_thread.start()
        logger.info(f"🌐 [Flask 웹 서버] 구동 완료! 접속 URL: http://{self.host}:{self.port}")
