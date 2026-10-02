# PC 중앙 서버 모드 (best(1).pt)

PC가 인식/판단/웹을 모두 담당하고, 라즈베리파이는 카메라 송출 + 명령 실행만 합니다.

```
[라즈베리파이 pi_agent/pi_agent.py]            [PC pc_server/pc_main.py]
 카메라 ── POST /api/frame (JPEG, 15fps) ──>   models/best_1.pt (Drowsy/Non_Drowsy 분류)
 GPIO  <── GET  /api/command (0.1초마다) ───   {"motor":"on|off","speed":70,"led":"on|off|blink","seq":N}
                                               웹 대시보드 http://<PC IP>:8000
```

## 실행

PC:
```bash
./venv/bin/pip install flask requests ultralytics opencv-python
./venv/bin/python pc_server/pc_main.py
```

라즈베리파이 (torch/ultralytics 불필요):
```bash
./venv/bin/pip install requests      # 또는 sudo apt install python3-requests
./venv/bin/python3 pi_agent/pi_agent.py --server http://<PC IP>:8000
```

카메라 없이 명령 실행만 시험: `--no-camera`

## 명령 API

| 요청 | 설명 |
|---|---|
| `POST /api/command` `{"motor":"on"}` | 모터 ON (`"speed": 0~100` 같이 지정 가능) |
| `POST /api/command` `{"motor":"off"}` | 모터 OFF |
| `POST /api/command` `{"led":"on" / "off" / "blink"}` | LED 제어 |
| `POST /api/recover` | 졸음 경보 해제 + 모터 ON + LED OFF |
| `GET /api/status` | 인식 결과, 명령, 라즈베리파이 연결 상태 |

## 동작 규칙

- Drowsy 확률 ≥ 0.6 이 1.2초 이상 지속되면 자동으로 `motor=off, led=blink` (대시보드 체크박스로 끌 수 있음)
- 라즈베리파이가 PC와 2초 이상 통신이 끊기면 스스로 모터 정지 + LED 점멸 (페일세이프).
  연결이 돌아와도 자동 재가동하지 않고, 웹에서 새 명령을 내려야 움직입니다.
- 설정값은 `config.py`의 `PCServerConfig`.
