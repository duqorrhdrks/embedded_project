"""
[노트북 전용 - 원격 추론 클라이언트 - infer_from_pi_stream.py]

라즈베리파이는 카메라 캡처와 모터/LED/녹화 제어만 담당하고,
무거운 YOLOv8 추론은 이 스크립트를 실행하는 노트북(PC)에서 수행합니다.

흐름:
  1) 라즈베리파이의 /video_feed_raw (OSD 오버레이 없는 원본 MJPEG 스트림)를 받아옴
  2) 노트북에 있는 best.pt로 YOLOv8 추론 수행 (GPU/빠른 CPU 활용)
  3) 눈 감김(졸음 관련 클래스) 판정 결과를 라즈베리파이의
     POST /api/external_judgment 로 전송
  4) 라즈베리파이는 이 판정을 받아 기존과 동일한 지속시간 필터링(1.2초) 후
     모터 감속 / 비상등 점멸 / 이벤트 녹화를 그대로 수행

사용법:
    python infer_from_pi_stream.py --host 172.30.11.206 --model models/best.pt --show

이 스크립트는 프로젝트 저장소를 그대로 clone한 노트북에서 실행하는 것을 전제로
루트의 config.py(DROWSY_CLASS_NAMES, CONFIDENCE_THRESHOLD)를 그대로 재사용합니다.
"""

import os
import sys
import time
import argparse
import logging

import cv2
import requests

# 프로젝트 루트(embedded_project/)를 import 경로에 추가하여 config.py 재사용
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import VisionConfig  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("LaptopInferenceClient")


def parse_args():
    parser = argparse.ArgumentParser(description="라즈베리파이 카메라 스트림을 받아 노트북에서 YOLOv8 추론 후 결과 전송")
    parser.add_argument("--host", required=True, help="라즈베리파이 IP 주소 (예: 172.30.11.206)")
    parser.add_argument("--port", type=int, default=5000, help="라즈베리파이 Flask 서버 포트 (기본 5000)")
    parser.add_argument("--model", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "best.pt"),
                         help="노트북에 있는 YOLOv8 가중치 경로 (기본: 저장소 루트의 models/best.pt)")
    parser.add_argument("--conf", type=float, default=VisionConfig.CONFIDENCE_THRESHOLD, help="신뢰도 임계값")
    parser.add_argument("--show", action="store_true", help="추론 결과를 로컬 미리보기 창으로 표시")
    return parser.parse_args()


def is_drowsy_class(cls_name: str, drowsy_classes: list) -> bool:
    cls_name = cls_name.lower()
    return any(dc in cls_name for dc in drowsy_classes)


def main():
    args = parse_args()
    drowsy_classes = [c.lower() for c in VisionConfig.DROWSY_CLASS_NAMES]

    stream_url = f"http://{args.host}:{args.port}/video_feed_raw"
    judgment_url = f"http://{args.host}:{args.port}/api/external_judgment"

    logger.info(f"🔄 [YOLO 모델] 로드 시도: {args.model}")
    from ultralytics import YOLO
    model = YOLO(args.model)
    logger.info(f"✅ [YOLO 모델] 로드 완료. 클래스: {model.names}")

    last_sent_state = None

    while True:
        logger.info(f"🔄 [스트림 연결] {stream_url} 연결 시도...")
        cap = cv2.VideoCapture(stream_url)
        if not cap.isOpened():
            logger.warning("⚠️ 스트림 연결 실패. 3초 후 재시도합니다.")
            time.sleep(3)
            continue

        logger.info("✅ [스트림 연결] 라즈베리파이 원본 영상 수신 시작")

        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                logger.warning("⚠️ 프레임 수신 실패. 스트림을 다시 연결합니다.")
                break

            results = model.predict(frame, conf=args.conf, verbose=False)
            eyes_closed = False
            if results and len(results) > 0:
                for box in results[0].boxes:
                    cls_id = int(box.cls[0].item())
                    cls_name = model.names.get(cls_id, str(cls_id))
                    if is_drowsy_class(cls_name, drowsy_classes):
                        eyes_closed = True

                    if args.show:
                        xyxy = box.xyxy[0].cpu().numpy().astype(int)
                        color = (0, 0, 255) if is_drowsy_class(cls_name, drowsy_classes) else (0, 255, 0)
                        cv2.rectangle(frame, (xyxy[0], xyxy[1]), (xyxy[2], xyxy[3]), color, 2)
                        cv2.putText(frame, cls_name, (xyxy[0], max(20, xyxy[1] - 8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            # 판정 결과를 라즈베리파이로 전송 (상태가 바뀔 때는 즉시, 그 외엔 하트비트 목적으로 계속 전송)
            try:
                requests.post(judgment_url, json={"eyes_closed": eyes_closed}, timeout=1.0)
                if eyes_closed != last_sent_state:
                    logger.info(f"📡 [판정 전송] eyes_closed={eyes_closed} -> {judgment_url}")
                    last_sent_state = eyes_closed
            except requests.exceptions.RequestException as e:
                logger.error(f"❌ [판정 전송 실패] {e}")

            if args.show:
                cv2.imshow("Laptop Inference (Pi Stream)", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    cap.release()
                    cv2.destroyAllWindows()
                    return

        cap.release()
        time.sleep(1)


if __name__ == "__main__":
    main()
