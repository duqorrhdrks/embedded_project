# YOLOv8 모델 (best.pt) 가이드

이 폴더(`models/`)는 YOLOv8로 직접 학습시킨 운전자 졸음/눈 감김 감지 모델 가중치(`best.pt`)를 배치하는 위치입니다.

---

## 1. 모델 배치 방법
1. 학습이 완료된 가중치 파일의 이름을 `best.pt`로 지정합니다.
2. 이 디렉토리에 복사합니다:
   ```bash
   cp /경로/to/your_trained_model.pt /home/pi30308/embedded_project/models/best.pt
   ```

---

## 2. 권장 패키지 설치 (라즈베리파이 가상환경 또는 시스템)
YOLOv8 추론을 위해 `ultralytics` 라이브러리를 설치합니다:
```bash
pip install ultralytics
# 또는 라즈베리파이 전용 경량 실행을 위해:
# pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

---

## 3. 학습 클래스 이름 매핑 (`config.py`)
학습 시 사용한 클래스 이름이 다음과 같다면 자동으로 졸음으로 인식됩니다:
- `drowsy`
- `closed_eye`
- `closed_eyes`
- `sleep`
- `sleeping`

만약 다른 클래스 이름(예: `eye_close`, `drowsiness`)을 사용하셨다면, `config.py`의 `VisionConfig.DROWSY_CLASS_NAMES` 리스트에 해당 이름을 추가하시면 됩니다:
```python
# config.py 내
class VisionConfig:
    DROWSY_CLASS_NAMES = ["drowsy", "closed_eye", "sleeping", "your_class_name"]
```

---

## 4. `best.pt` 파일이 아직 없을 때의 동작
- `best.pt` 파일이 없거나 `ultralytics`가 설치되지 않은 경우에도 프로그램이 에러로 종료되지 않습니다.
- 자동으로 **모의 시뮬레이션 및 Haar Cascade Fallback 모드**로 작동하여 GUI 및 웹의 테스트 버튼을 통해 모터 감속, 비상등 점멸, 자동 녹화 기능을 테스트하실 수 있습니다.
