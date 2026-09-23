# 노트북 원격 추론 클라이언트

라즈베리파이는 카메라 캡처 + 모터/LED/녹화 제어만 담당하고, **무거운 YOLOv8 추론은
이 폴더의 스크립트를 실행하는 노트북에서** 수행하는 구조입니다. 자세한 배경은
`../DEPLOY_MODEL.md`의 "7. 노트북에서 추론하고 결과만 라즈베리파이로 전달하기" 참고.

## 준비

```bash
git clone <이 저장소 URL>
cd embedded_project

python -m venv venv           # 노트북용 가상환경 (Pi와 별개, 그냥 일반 venv면 됩니다)
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r laptop_client/requirements.txt

# 노트북에서 학습한 가중치를 models/best.pt 로 배치
cp /path/to/your_trained/best.pt models/best.pt
```

## 실행

라즈베리파이 IP(예: `172.30.11.206`)와 이미 실행 중인 `main.py`가 필요합니다
(라즈베리파이는 평소처럼 `./venv/bin/python3 main.py --no-gui`로 켜두세요 — 이 상태에서
파이 쪽 로컬 YOLO가 있든 없든 상관없이, 노트북 판정이 도착하는 순간부터는 노트북 판정이
우선 적용됩니다).

```bash
python laptop_client/infer_from_pi_stream.py --host 172.30.11.206 --show
```

- `--host` : 라즈베리파이 IP (필수)
- `--port` : 라즈베리파이 Flask 포트 (기본 5000)
- `--model` : 노트북에 있는 `best.pt` 경로 (기본: 저장소 루트의 `models/best.pt`)
- `--conf` : 신뢰도 임계값 (기본: `config.py`의 `CONFIDENCE_THRESHOLD`)
- `--show` : 추론 박스가 그려진 미리보기 창을 노트북 화면에 띄움 (디버깅용, `q`로 종료)

## 동작 확인

- 노트북 콘솔에 `📡 [판정 전송] eyes_closed=True -> ...` 로그가 뜨면 정상 전송 중입니다.
- 라즈베리파이 웹 대시보드(`http://172.30.11.206:5000`)의 상단 AI 배지가
  `AI 엔진: 안면 및 시선 실시간 분석`으로 보여도 정상입니다 (이 배지는 아직 "로컬 YOLO
  사용 여부"만 표시하도록 되어 있음 — `is_external` 필드는 `/api/status` JSON으로 확인 가능).
- 노트북에서 5초 이상 `eyes_closed: true`를 계속 보내면(`DROWSY_DURATION_THRESHOLD_SEC=1.2초`
  이상), 라즈베리파이 쪽에서 모터 감속/비상등/녹화가 실제로 시작됩니다.

## 주의

- 라즈베리파이와 노트북이 **같은 네트워크(같은 와이파이/공유기)**에 있어야 합니다.
- `/video_feed_raw`는 OSD 오버레이가 없는 원본 프레임입니다. 사람이 보기용인
  `/video_feed`와는 다른 주소이니 혼동하지 마세요.
- 네트워크가 끊기면 스크립트가 자동으로 스트림 재연결을 시도합니다.
