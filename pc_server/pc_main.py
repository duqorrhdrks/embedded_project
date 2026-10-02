"""
[PC 중앙 서버 - pc_main.py]
PC(노트북)가 "두뇌" 역할을 하고, 라즈베리파이는 카메라 송출 + 명령 실행만 담당하는 구조입니다.

  [라즈베리파이 pi_agent.py]                         [PC pc_main.py (이 파일)]
   카메라 캡처 ── POST /api/frame (JPEG) ──────────>  best(1).pt 로 졸음 분류 추론
                                                      1.2초 지속 시 졸음 확정 -> 명령 변경
   GPIO 실행  <── GET  /api/command (JSON) ─────────  {"motor":"off","led":"blink",...}
                                                      웹 대시보드(/)에서 사람이 직접 버튼으로도 명령 변경

사용법 (PC에서):
    ./venv/bin/python pc_server/pc_main.py
    -> 브라우저에서 http://localhost:8000 접속
"""

import os
import sys
import time
import argparse
import logging
import threading

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, render_template

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PCServerConfig, HardwareConfig  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("PCServer")
logging.getLogger("werkzeug").setLevel(logging.WARNING)  # 초당 수십 번 오는 프레임/폴링 요청 로그 숨김


class CommandBoard:
    """
    라즈베리파이가 가져갈 '명령 게시판'.
    명령이 바뀔 때마다 seq가 1씩 증가하므로, 라즈베리파이는 seq가 바뀌었을 때만 GPIO를 다시 실행합니다.
    """

    VALID_MOTOR = ("on", "off")
    VALID_LED = ("on", "off", "blink")

    def __init__(self):
        self._lock = threading.Lock()
        self.motor = "off"
        self.speed = HardwareConfig.DEFAULT_CRUISE_SPEED
        self.led = "off"
        self.seq = 0
        # 서버가 재시작되면 seq가 0부터 다시 시작하므로, 라즈베리파이가 (boot, seq) 쌍으로 새 명령을 구분하게 함
        self.boot = int(time.time())
        self.source = "init"
        self.pi_last_seen = 0.0
        self.pi_ack_seq = -1

    def set(self, motor=None, led=None, speed=None, source="web"):
        with self._lock:
            if motor is not None:
                if motor not in self.VALID_MOTOR:
                    raise ValueError(f"motor는 {self.VALID_MOTOR} 중 하나여야 합니다.")
                self.motor = motor
            if led is not None:
                if led not in self.VALID_LED:
                    raise ValueError(f"led는 {self.VALID_LED} 중 하나여야 합니다.")
                self.led = led
            if speed is not None:
                self.speed = max(0, min(100, int(speed)))
            self.seq += 1
            self.source = source
            logger.info(f"📮 [명령 변경 #{self.seq}] motor={self.motor} speed={self.speed} led={self.led} (by {source})")
            return self._snapshot()

    def mark_pi_seen(self, ack_seq):
        with self._lock:
            self.pi_last_seen = time.time()
            if ack_seq is not None:
                self.pi_ack_seq = ack_seq

    def _snapshot(self):
        return {"motor": self.motor, "speed": self.speed, "led": self.led, "seq": self.seq,
                "boot": self.boot, "source": self.source}

    def snapshot(self):
        with self._lock:
            return self._snapshot()


class DrowsinessBrain:
    """best(1).pt (YOLOv8 분류 모델: Drowsy / Non_Drowsy) 추론 + 지속시간 기반 졸음 확정"""

    def __init__(self, model_path, board: CommandBoard):
        self.board = board
        self.drowsy_names = [n.lower() for n in PCServerConfig.DROWSY_CLASS_NAMES]
        self.prob_threshold = PCServerConfig.DROWSY_PROB_THRESHOLD
        self.duration_threshold = PCServerConfig.DROWSY_DURATION_THRESHOLD_SEC
        self.auto_safety = PCServerConfig.AUTO_SAFETY_ACTION

        logger.info(f"🔄 [YOLO 모델] 로드: {model_path}")
        from ultralytics import YOLO
        self.model = YOLO(model_path)
        if self.model.task != "classify":
            raise RuntimeError(f"이 서버는 분류(classify) 모델용입니다. 로드된 모델 task={self.model.task}")
        self.drowsy_ids = [i for i, n in self.model.names.items() if n.lower() in self.drowsy_names]
        logger.info(f"✅ [YOLO 모델] 로드 완료. 클래스: {self.model.names}, 졸음 클래스 id: {self.drowsy_ids}")

        self._frame_lock = threading.Lock()
        self._new_frame = threading.Event()
        self._latest_jpeg_in = None
        self.latest_annotated = None
        self.last_frame_time = 0.0
        self.infer_fps = 0.0

        self.top_class = "-"
        self.drowsy_prob = 0.0
        self.eyes_closed = False
        self.closed_since = None
        self.drowsy_confirmed = False

        threading.Thread(target=self._loop, daemon=True).start()

    def submit_jpeg(self, jpeg_bytes: bytes):
        with self._frame_lock:
            self._latest_jpeg_in = jpeg_bytes
            self.last_frame_time = time.time()
        self._new_frame.set()

    def reset(self):
        self.closed_since = None
        self.drowsy_confirmed = False

    def _loop(self):
        while True:
            self._new_frame.wait()
            self._new_frame.clear()
            with self._frame_lock:
                jpeg = self._latest_jpeg_in
            frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue

            t0 = time.time()
            try:
                self._infer(frame)
            except Exception as e:
                logger.error(f"추론 오류: {e}")
                continue
            self._update_drowsy_state()
            self._annotate(frame)
            dt = time.time() - t0
            self.infer_fps = 0.8 * self.infer_fps + 0.2 * (1.0 / max(dt, 1e-3))

            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if ok:
                self.latest_annotated = buf.tobytes()

    def _infer(self, frame):
        result = self.model.predict(frame, verbose=False)[0]
        probs = result.probs.data.cpu().numpy()
        self.top_class = self.model.names[int(probs.argmax())]
        self.drowsy_prob = float(sum(probs[i] for i in self.drowsy_ids))
        self.eyes_closed = self.drowsy_prob >= self.prob_threshold

    def _update_drowsy_state(self):
        now = time.time()
        if self.eyes_closed:
            if self.closed_since is None:
                self.closed_since = now
            if not self.drowsy_confirmed and now - self.closed_since >= self.duration_threshold:
                self.drowsy_confirmed = True
                logger.warning(f"🚨 [졸음 확정] Drowsy가 {self.duration_threshold}초 이상 지속됨")
                if self.auto_safety:
                    self.board.set(motor="off", led="blink", source="auto:drowsy")
        else:
            self.closed_since = None

    @property
    def closed_duration(self):
        return 0.0 if self.closed_since is None else time.time() - self.closed_since

    def _annotate(self, frame):
        color = (0, 0, 255) if self.eyes_closed else (0, 200, 0)
        cv2.putText(frame, f"{self.top_class}  drowsy={self.drowsy_prob:.2f}", (15, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        if self.drowsy_confirmed:
            cv2.putText(frame, "DROWSY CONFIRMED", (15, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        elif self.closed_since is not None:
            cv2.putText(frame, f"closed {self.closed_duration:.1f}s", (15, 65),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2)


def create_app(brain: DrowsinessBrain, board: CommandBoard):
    app = Flask(__name__, template_folder="templates")

    @app.route("/")
    def index():
        return render_template("dashboard.html")

    # ---------------- 라즈베리파이가 호출하는 API ----------------
    @app.route("/api/frame", methods=["POST"])
    def api_frame():
        """라즈베리파이가 카메라 프레임(JPEG 바이트)을 body로 올려보냄"""
        data = request.get_data()
        if not data:
            return jsonify({"success": False, "message": "JPEG body가 비어 있습니다."}), 400
        brain.submit_jpeg(data)
        return jsonify({"success": True})

    @app.route("/api/command", methods=["GET"])
    def api_command_get():
        """라즈베리파이가 주기적으로 들어와 현재 명령을 확인 (?ack=<마지막으로 실행한 seq>)"""
        board.mark_pi_seen(request.args.get("ack", type=int))
        return jsonify(board.snapshot())

    # ---------------- 웹 대시보드 / 외부에서 호출하는 API ----------------
    @app.route("/api/command", methods=["POST"])
    def api_command_post():
        """명령 변경. 예: {"motor":"on"} / {"led":"blink"} / {"motor":"on","speed":60}"""
        data = request.get_json(silent=True) or {}
        try:
            snap = board.set(motor=data.get("motor"), led=data.get("led"), speed=data.get("speed"), source="web")
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400
        return jsonify({"success": True, "command": snap})

    @app.route("/api/recover", methods=["POST"])
    def api_recover():
        """졸음 경보 해제: 상태 리셋 + LED 끄고 모터 다시 켜기"""
        brain.reset()
        snap = board.set(motor="on", led="off", source="web:recover")
        return jsonify({"success": True, "command": snap})

    @app.route("/api/auto_safety", methods=["POST"])
    def api_auto_safety():
        data = request.get_json(silent=True) or {}
        brain.auto_safety = bool(data.get("enabled", not brain.auto_safety))
        return jsonify({"success": True, "auto_safety": brain.auto_safety})

    @app.route("/api/status")
    def api_status():
        now = time.time()
        return jsonify({
            "command": board.snapshot(),
            "pi": {
                "online": now - board.pi_last_seen < 2.0,
                "last_seen_sec": round(now - board.pi_last_seen, 1) if board.pi_last_seen else None,
                "ack_seq": board.pi_ack_seq,
            },
            "vision": {
                "receiving": now - brain.last_frame_time < 2.0,
                "top_class": brain.top_class,
                "drowsy_prob": round(brain.drowsy_prob, 3),
                "eyes_closed": brain.eyes_closed,
                "closed_duration": round(brain.closed_duration, 1),
                "drowsy_confirmed": brain.drowsy_confirmed,
                "infer_fps": round(brain.infer_fps, 1),
            },
            "auto_safety": brain.auto_safety,
        })

    @app.route("/video_feed")
    def video_feed():
        """추론 결과가 그려진 영상을 브라우저로 MJPEG 스트리밍"""
        def gen():
            last = None
            while True:
                jpeg = brain.latest_annotated
                if jpeg is None or jpeg is last:
                    time.sleep(0.03)
                    continue
                last = jpeg
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
        return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

    return app


def main():
    parser = argparse.ArgumentParser(description="PC 중앙 서버: 라즈베리파이 카메라 수신 + best(1).pt 추론 + 명령 게시")
    parser.add_argument("--port", type=int, default=PCServerConfig.PORT)
    parser.add_argument("--model", default=PCServerConfig.MODEL_PATH)
    args = parser.parse_args()

    board = CommandBoard()
    brain = DrowsinessBrain(args.model, board)
    app = create_app(brain, board)
    logger.info(f"🌐 [PC 서버] http://localhost:{args.port} (라즈베리파이는 http://<이 PC의 IP>:{args.port} 로 접속)")
    app.run(host=PCServerConfig.HOST, port=args.port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
