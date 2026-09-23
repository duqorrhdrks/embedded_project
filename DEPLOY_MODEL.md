# 노트북에서 학습한 모델을 라즈베리파이에서 추론하는 방법

## 0. 이 프로젝트는 ROS가 아닙니다

이 프로젝트는 **ROS(ROS1/ROS2)를 사용하지 않습니다.** 순수 Python 애플리케이션이며,
`main.py`가 `SystemManager`(하드웨어/비전 오케스트레이션) + `Flask 웹 서버` + `PyQt5 GUI`를
한 프로세스 안에서 직접 구동합니다. 노드/토픽/퍼블리셔-서브스크라이버 같은 ROS 개념은
쓰이지 않고, 카메라 프레임을 함수 호출로 바로 주고받는 구조입니다.

그래서 "노트북에서 학습 → 라즈베리파이에서 판단"은 ROS 노드를 따로 만들 필요 없이,
**학습된 가중치 파일(`best.pt`) 하나만 라즈베리파이로 옮기면** 끝입니다. 아래는 그 절차입니다.

---

## 1. 전체 흐름 요약

```
[노트북 / PC (GPU)]                         [라즈베리파이 (추론 전용)]
 1) 데이터셋 라벨링                          4) best.pt를 models/ 폴더에 복사
 2) YOLOv8로 학습 (yolo train ...)     --->  5) config.py 클래스 이름 매칭 확인
 3) runs/detect/train/weights/best.pt        6) python3 main.py 실행 → 자동 로드
```

라즈베리파이는 **학습(Training)은 하지 않고 추론(Inference)만** 합니다.
학습은 노트북/PC(가능하면 GPU)에서, 실제 판단(추론)은 라즈베리파이에서 실행하는
"Edge Inference" 구조이며, 이 프로젝트의 `vision/drowsiness_detector.py`가
이미 이 구조에 맞춰 설계되어 있습니다 (`ultralytics.YOLO(model_path)`로 로드 후
`model.predict(frame)`으로 프레임마다 추론).

---

## 2. [노트북/PC] YOLOv8 모델 학습

```bash
# 1) 가상환경 준비 (예시)
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install ultralytics

# 2) 눈 감김/졸음 데이터셋 라벨링 (Roboflow, LabelImg, CVAT 등 사용)
#    data.yaml 예시:
#    train: dataset/images/train
#    val:   dataset/images/val
#    names: ['drowsy', 'closed_eye', 'normal']   # 이 이름을 3번에서 그대로 사용

# 3) 학습 실행 (GPU가 있으면 자동으로 사용됨)
yolo detect train data=dataset/data.yaml model=yolov8n.pt epochs=100 imgsz=640

# 4) 학습 완료 후 결과 확인
#    runs/detect/train/weights/best.pt  <- 이 파일을 라즈베리파이로 옮기면 됩니다
```

**라즈베리파이용 모델 선택 팁**
- 반드시 `yolov8n.pt`(nano) 또는 `yolov8s.pt`(small) 기반으로 학습하세요.
  `m/l/x` 모델은 라즈베리파이 CPU에서 프레임당 추론이 너무 느려집니다(수 초 단위 지연).
- 클래스 이름은 나중에 `config.py`의 `DROWSY_CLASS_NAMES`와 매칭되니,
  `drowsy`, `closed_eye`, `sleeping` 등 알아보기 쉬운 이름을 쓰는 걸 권장합니다.

---

## 3. [노트북 → 라즈베리파이] 가중치 파일 전송

라즈베리파이의 IP는 현재 `172.30.11.206` 입니다 (같은 네트워크에 있어야 함).

```bash
# 노트북 터미널에서 실행 (scp)
scp runs/detect/train/weights/best.pt pi30308@172.30.11.206:/home/pi30308/embedded_project/models/best.pt
```

SSH/scp 환경이 아니라면 USB 드라이브로 복사해도 됩니다. 파일명은 반드시 `best.pt`,
위치는 반드시 `embedded_project/models/best.pt` 여야 합니다 (`config.py`의
`VisionConfig.MODEL_PATH` 기본값이 이 경로를 가리킵니다).

---

## 4. [라즈베리파이] 클래스 이름 매칭 확인

`config.py`의 `VisionConfig.DROWSY_CLASS_NAMES` 목록에 학습 시 사용한
"졸음/눈 감김" 클래스 이름이 포함되어 있는지 확인하세요.

```python
# config.py
class VisionConfig:
    DROWSY_CLASS_NAMES = ["drowsy", "closed_eye", "closed_eyes", "sleep", "sleeping"]
```

학습할 때 다른 이름(예: `eye_close`)을 썼다면 이 리스트에 추가해야
`vision/drowsiness_detector.py`가 해당 클래스를 졸음으로 인식합니다.

> ⚠️ **실제로 겪었던 함정**: 판정 로직은 `dc in cls_name` 방식의 **부분 문자열 매칭**입니다.
> 즉 리스트에 `"closed_eye"`를 넣어도 학습된 클래스 이름이 `"eyes_closed"`(단어 순서가 반대)라면
> 절대 매칭되지 않습니다! `"eyes_closed" in "eyes_closed"` 는 True지만
> `"closed_eye" in "eyes_closed"` 는 False입니다. **반드시 `yolo predict` 결과나
> 로그에 찍히는 실제 클래스 이름을 그대로 복사해서 리스트에 넣으세요.** (아래처럼 확인 가능)
> ```bash
> venv/bin/python3 -c "from ultralytics import YOLO; print(YOLO('models/best.pt').names)"
> ```

---

## 5. [라즈베리파이] 가상환경 준비 및 실행

이 라즈베리파이(Debian Bookworm)는 시스템 파이썬에 `pip install`이 막혀있어서
(PEP 668, `externally-managed-environment`), 반드시 가상환경을 만들어서 설치해야 합니다.
이때 `picamera2`/`RPi.GPIO`/`PyQt5`/시스템 `cv2`는 apt로 이미 설치돼 있으므로
`--system-site-packages` 옵션으로 그대로 물려받고, `ultralytics`/`torch`만 venv 안에 새로 설치합니다.

```bash
cd /home/pi30308/embedded_project

# 1) venv 생성 (최초 1회) — 시스템 apt 패키지(cv2, RPi.GPIO, picamera2, PyQt5) 상속
python3 -m venv --system-site-packages venv

# 2) 의존성 설치 (torch/torchvision/numpy 버전이 requirements.txt에 고정되어 있음)
./venv/bin/pip install -r requirements.txt

# 3) pip이 opencv-python을 함께 설치했다면 반드시 제거 (시스템 cv2와 충돌 + numpy 2.x 요구)
./venv/bin/pip uninstall -y opencv-python

# 4) 실행 — 반드시 venv의 python으로 실행해야 ultralytics/torch를 찾습니다!
./venv/bin/python3 main.py --no-gui
```

> ⚠️ **실제로 겪었던 함정 (라즈베리파이 4 전용)**: `pip install ultralytics`가 기본으로 받아오는
> 최신 torch(2.4+)는 ARM 최적화 컨볼루션 커널이 dot-product 명령어(`asimddp`)를 사용하는데,
> Pi4의 Cortex-A72 CPU는 이 명령어를 지원하지 않아서 `model.predict()` 호출 시
> **"Illegal instruction" 로 프로세스가 그냥 죽습니다** (에러 메시지도 거의 없이 죽어서
> 원인 파악이 어렵습니다). `requirements.txt`에 이미 `torch==2.2.2` / `torchvision==0.17.2` /
> `numpy<2`로 고정해뒀으니 그대로 설치하면 재발하지 않습니다. (Pi 5는 Cortex-A76이라
> 이 문제가 없을 가능성이 높지만 검증된 값은 아닙니다.)
>
> 또한 **`python3 main.py`처럼 시스템 파이썬으로 실행하면 venv에 설치한 ultralytics를
> 못 찾아서** 조용히 Haar Cascade 폴백으로 넘어갑니다 (에러 없이 그냥 YOLO가 꺼진 채로
> 동작). 항상 `./venv/bin/python3 main.py` 로 실행하세요.

터미널 로그에 아래 줄이 뜨면 노트북에서 학습한 모델이 정상적으로 로드되어
추론에 사용되고 있는 것입니다.

```
✅ [AI 모델] YOLOv8 best.pt 모델 로드 완료 (실시간 딥러닝 추론 준비)
```

반대로 `best.pt`가 없거나 로드에 실패하면 **에러로 죽지 않고** 자동으로
OpenCV Haar Cascade(안면/눈 인식) 또는 모의시험 모드로 전환되므로, 모델을
아직 준비 못 한 상태에서도 나머지 시스템(모터 감속, LED, 녹화)은 그대로 테스트할 수 있습니다.

웹 대시보드(`http://172.30.11.206:5000`)의 상단 배지에서도
`AI 엔진: YOLOv8 (best.pt)` 로 표시되면 정상 반영된 것입니다.

---

## 6. 노트북에서 추론하고 결과만 라즈베리파이로 전달하기

라즈베리파이 4(CPU 전용)는 YOLOv8 추론이 느리고, 5절의 SIGILL 문제처럼 ARM 환경
특유의 호환성 문제도 있습니다. **노트북(GPU 또는 빠른 CPU)이 카메라 영상을 받아
직접 추론하고, 판단 결과만 라즈베리파이로 넘겨서 모터/LED/녹화를 제어**하는 구조를
쓰면 이 문제를 전부 피할 수 있습니다.

```
[라즈베리파이]                              [노트북]
 카메라 캡처 --> /video_feed_raw (원본 스트림) --> YOLOv8 추론 (GPU/빠른 CPU)
                                                        |
 모터/LED/녹화 제어 <-- POST /api/external_judgment <--┘
 (기존과 동일한 1.2초 지속시간 필터링 그대로 적용)
```

- 라즈베리파이 쪽은 이미 준비되어 있습니다: `web/server.py`의
  `GET /video_feed_raw`(OSD 없는 원본 MJPEG 스트림)와
  `POST /api/external_judgment` (`{"eyes_closed": true/false}`)를 받으면
  `vision/drowsiness_detector.py`가 로컬 YOLO/Haar보다 이 값을 우선 사용합니다.
- 노트북 쪽 실행 스크립트는 `laptop_client/infer_from_pi_stream.py` 입니다.
  자세한 사용법은 `laptop_client/README.md` 참고:
  ```bash
  # 노트북에서
  pip install -r laptop_client/requirements.txt
  python laptop_client/infer_from_pi_stream.py --host <라즈베리파이 IP> --show
  ```
- 이 구조에서는 라즈베리파이에 `ultralytics`/`torch`를 아예 설치하지 않아도 됩니다
  (5절의 venv/torch 고정 작업이 필요 없어짐). 다만 이미 설치해뒀다면 그대로 둬도
  상관없습니다 — 노트북 판정이 도착하는 즉시 우선순위가 자동으로 넘어갑니다.

---

## 7. (참고) 라즈베리파이에서 속도가 느릴 때

- `config.py`의 `VisionConfig.FRAME_WIDTH/HEIGHT`를 낮춰보세요 (예: 640x480 → 416x320).
- YOLOv8 모델을 NCNN/ONNX로 내보내면 라즈베리파이 ARM CPU에서 더 빠릅니다:
  ```bash
  yolo export model=best.pt format=ncnn
  ```
  (내보낸 포맷을 쓰려면 `drowsiness_detector.py`의 모델 로드 부분을 해당 포맷에
  맞게 수정해야 합니다 — 필요하면 별도로 작업 요청해주세요.)
- `CONFIDENCE_THRESHOLD`를 너무 낮게 두면 오탐이 늘어나 불필요한 감속/녹화가
  자주 발생하니 0.5 근처에서 시작해 조정하세요.
