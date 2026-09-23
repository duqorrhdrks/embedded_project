# 프로젝트 현재 상황 정리 (2026-09-23 기준)

## 1. 이 프로젝트는 무엇인가

라즈베리파이 기반 **AI 운전자 졸음 감지 & 자율 안전 감속 로봇 관제 시스템**입니다.
ROS를 쓰지 않는 순수 Python 프로젝트이며 `main.py` 한 프로세스 안에서
`SystemManager`(하드웨어+비전 오케스트레이션) + `Flask 웹 서버` + `PyQt5 GUI`가 함께 돕니다.

핵심 시나리오: 카메라로 운전자 눈을 보다가 **눈 감김이 1.2초 이상 지속되면** →
① 이벤트 영상 자동 녹화 시작 ② 비상등 LED 점멸 ③ (주행 중이면) 모터를 점진적으로 감속.
웹/GUI의 복구 버튼을 누르면 정상 상태로 복귀.

## 2. 사용자가 원하는 목표 구조와 실제 구현 상태

요청하신 구조:
> PC에서 best.pt로 인식 → 그 인식 값만 "주소(웹)"로 전송 → 웹이 받은 값을 기반으로 움직인다

이 구조는 **이미 코드로 구현되어 있습니다** (`DEPLOY_MODEL.md` 6절 "노트북에서 추론하고
결과만 라즈베리파이로 전달하기"에 설계 문서까지 존재).

```
[라즈베리파이]                                    [PC/노트북]
 카메라 캡처                                       best.pt로 YOLOv8 추론 (GPU/빠른 CPU)
   |                                                    |
   +-- GET /video_feed_raw (원본 스트림) ------------->+
   |                                                    |
   +<-- POST /api/external_judgment {"eyes_closed":bool} --+
   |
 system_manager.apply_external_judgment()
   -> drowsiness_detector.report_external_judgment()
   -> 기존과 동일하게 1.2초 지속시간 필터링 후
      모터 감속 / LED 점멸 / 녹화 그대로 수행
```

관련 파일:
- `laptop_client/infer_from_pi_stream.py` — **PC에서 실행하는 스크립트**. 라즈베리파이의
  `/video_feed_raw`를 열어서 `models/best.pt`로 프레임마다 YOLO 추론 → 결과를
  `{"eyes_closed": true/false}` JSON으로 `/api/external_judgment`에 POST.
- `web/server.py` — 라즈베리파이 쪽 Flask 서버. `/video_feed_raw`(원본 스트림 송출)와
  `/api/external_judgment`(외부 판정 수신) 라우트 존재.
- `system_manager.py:apply_external_judgment()` → `vision/drowsiness_detector.py:report_external_judgment()`
  — 외부 판정을 받으면 `external_judgment_mode=True`로 전환되고, 이후 로컬 YOLO/Haar보다
  **이 외부 판정이 항상 우선 적용**됨. 이후 로직(지속시간 필터링, 콜백 실행)은 로컬 판정과 동일.

즉 "PC 인식값만 보내고, 받은 쪽(라즈베리파이)이 그 값 기반으로 움직인다"는 파이프라인은
설계+구현이 끝나 있는 상태입니다.

## 3. 완료된 것 (코드 레벨)

- [x] 모터 PWM 듀얼 제어 + 점진적 감속/복구 (`hardware/motor_controller.py`)
- [x] 비상등 LED 점멸 제어 (`hardware/led_controller.py`)
- [x] YOLOv8(`best.pt`) 로컬 추론 + Haar Cascade 폴백 + 모의시험 모드 (`vision/drowsiness_detector.py`)
- [x] 눈 감김 지속시간(1.2초) 기반 졸음 확정 필터링 + 자동 복귀(1.0초)
- [x] 이벤트 영상 자동 녹화 (`vision/video_recorder.py`) — `recordings/`에 2026-09-04 녹화본 3개 존재 → **실제로 한 번은 end-to-end 테스트가 된 이력이 있음**
- [x] PyQt5 GUI 대시보드 (`gui/dashboard_gui.py`, 451줄)
- [x] Flask 웹 대시보드 + REST API (`/api/status`, `/api/recover`, `/api/trigger_drowsy`, `/api/stop`, `/api/start`, `/api/toggle_drive`, `/api/inference`)
- [x] **PC 원격 추론 → 결과만 전송하는 구조** (`laptop_client/infer_from_pi_stream.py` + `/video_feed_raw` + `/api/external_judgment`)
- [x] `best.pt` 실제 파일 존재 (`models/best.pt`, 22MB), 클래스: `eyes_closed`, `eyes_closed_head_left`, `eyes_closed_head_right`, `focused`, `head_down`, `head_up`, `seeing_left`, `seeing_right`, `yarning` — 학습 완료된 모델임
- [x] 라즈베리파이 배포 가이드(`DEPLOY_MODEL.md`), 부서별 설계 문서(`MODIFICATIONS.md`), 모델 배치 가이드(`models/README.md`) 문서화 충실
- [x] 향후 확장용 차선 인식/갓길 정차 훅 (`vision/lane_assistant.py`) — 뼈대만 존재

## 4. 아직 안 된 것 / 확인이 필요한 것

- [ ] **Git 커밋이 하나도 없음** — `git status` 기준 모든 파일이 `??`(untracked) 상태. 현재까지의
  작업을 잃어버리지 않으려면 초기 커밋부터 필요.
- [ ] **이 PC(`ros30308-750XDA`, x86_64)의 `venv`가 비어 있음** — `venv/bin/pip list` 결과 아무 패키지도
  없음. `ultralytics`, `torch`, `opencv-python` 등이 설치되지 않아 `laptop_client/infer_from_pi_stream.py`를
  아직 이 PC에서 실행해본 적이 없는 것으로 보임 (`pip install -r laptop_client/requirements.txt` 필요).
- [ ] **실제 라즈베리파이 대상 종단 간(E2E) 검증 미확인** — `recordings/`의 녹화본은 2026-09-04자로,
  로컬(라즈베리파이 자체 YOLO/모의시험) 경로로 만들어졌을 가능성이 높고, "PC 추론 → 라즈베리파이 전송"
  경로가 실제로 동작하는지는 로그/기록상 확인되지 않음.
- [ ] 라즈베리파이의 실제 IP 주소가 `DEPLOY_MODEL.md`에 `172.30.11.206`으로 하드코딩되어 있음 — 현재도
  유효한 IP인지 재확인 필요 (네트워크가 바뀌었으면 `--host` 인자로 새 IP 지정).
- [ ] GPIO/모터 실제 하드웨어 동작 검증 여부 불명 (`config.py`의 `USE_SIMULATION=False`이므로 실제 GPIO
  핀 접근을 시도하며, 이 PC(x86_64 노트북)에는 `RPi.GPIO`가 없어 자동으로 시뮬레이션 폴백됨 — 실기 테스트는
  라즈베리파이에서만 가능).
- [ ] 차선 인식/갓길 정차(`lane_assistant.py`)는 미완성 확장 기능 (설계 문서상 "향후 계획"으로 명시됨).
- [ ] 웹 대시보드 AI 배지가 "외부(PC) 판정 사용 중"임을 명확히 표시하지 않음 — `laptop_client/README.md`에도
  "아직 이 배지는 로컬 YOLO 여부만 표시"라고 명시된 알려진 제약사항.

## 5. 다음에 하면 좋은 일 (제안)

1. 현재 상태 그대로 **git 초기 커밋** (작업 유실 방지).
2. 이 PC를 "PC 추론 클라이언트"로 쓸 계획이면 `pip install -r laptop_client/requirements.txt`로
   venv 채우기.
3. 라즈베리파이에서 `./venv/bin/python3 main.py --no-gui`로 서버를 띄운 뒤, 이 PC에서
   `python laptop_client/infer_from_pi_stream.py --host <라즈베리파이 IP> --show`로 실제 1회 E2E 테스트.
4. 정상 동작 확인되면 `/api/status`의 `is_external` 필드 또는 웹 배지 표시를 개선해
   "지금 PC 판정을 쓰고 있는지"를 시각적으로 명확히 하기.
