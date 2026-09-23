/**
 * [웹 프론트엔드 실시간 관제 스크립트 - app.js]
 * 라즈베리파이 REST API와 통신하여 UI를 실시간 동기화하고 제어 명령을 즉시 전송합니다.
 */

// 주기적 상태 폴링 (300ms 간격)
setInterval(updateStatus, 300);

function updateStatus() {
    fetch('/api/status')
        .then(response => response.json())
        .then(res => {
            if (!res.success) return;
            const data = res.data;
            const motor = data.motor;
            const led = data.led;
            const rec = data.recorder;
            const drowsy = data.drowsiness;

            // 1. 상단 글로벌 관제 배너 업데이트
            const banner = document.getElementById('system-banner');
            const bannerText = document.getElementById('system-status-text');

            if (drowsy.is_drowsy) {
                banner.className = 'system-banner danger';
                bannerText.innerText = `🚨 [위험 경보] 운전자 졸음 감지! 비상 감속 및 이벤트 영상 자동 녹화 중 (${drowsy.duration}초)`;
            } else if (motor.state === 'DECELERATING') {
                banner.className = 'system-banner warning';
                bannerText.innerText = '⚠️ [안전 제어] 차량 속도를 점진적으로 안전 감속하는 중...';
            } else {
                banner.className = 'system-banner';
                bannerText.innerText = '● 관제 시스템 상태: 정상 주행 상태 모니터링 중';
            }

            // 2. AI 모델 상태 배지
            const aiBadge = document.getElementById('ai-model-badge');
            if (drowsy.is_yolo) {
                aiBadge.innerText = 'AI 엔진: YOLOv8 (best.pt)';
                aiBadge.style.color = '#3fb950';
            } else {
                aiBadge.innerText = 'AI 엔진: 안면 및 시선 실시간 분석';
                aiBadge.style.color = '#58a6ff';
            }

            // 3. 모터 상태 및 게이지
            document.getElementById('motor-state').innerText = motor.state;
            document.getElementById('motor-speed-val').innerText = `${motor.speed} %`;
            const gauge = document.getElementById('motor-gauge');
            gauge.style.width = `${motor.speed}%`;

            if (motor.state === 'DECELERATING') {
                gauge.style.backgroundColor = '#d29922';
            } else if (motor.state === 'FORWARD') {
                gauge.style.backgroundColor = '#3fb950';
            } else {
                gauge.style.backgroundColor = '#da3633';
            }

            // 주행 토글 버튼 텍스트 동기화
            const driveBtn = document.getElementById('btn-drive');
            if (driveBtn) {
                driveBtn.innerText = motor.is_moving ? '⏸️ 차량 주행 일시 정지' : '🚗 차량 주행 시작';
            }

            // 4. 비상 경고등 상태
            const hazardVisual = document.getElementById('hazard-visual');
            const hazardState = document.getElementById('hazard-state');
            const hazardText = document.getElementById('hazard-text');

            if (led.is_blinking) {
                hazardVisual.className = 'hazard-visual active';
                hazardState.innerText = '비상 점멸 중 (경고 발령)';
                hazardState.className = 'text-danger';
                hazardText.innerText = '비상 경고등 점멸 작동 중!';
            } else {
                hazardVisual.className = 'hazard-visual';
                hazardState.innerText = '소등 (정상)';
                hazardState.className = 'text-muted';
                hazardText.innerText = '비상 경고등 정상 (소등)';
            }

            // 5. 비디오 녹화기 상태
            const recIndicator = document.getElementById('rec-indicator');
            const recPathDisplay = document.getElementById('rec-path-display');
            if (rec.is_recording) {
                recIndicator.className = 'rec-indicator active';
                recIndicator.innerText = `🔴 REC 자동 녹화 중 (${rec.duration}초)`;
                recPathDisplay.innerText = `저장 위치: ${rec.filepath}`;
            } else {
                recIndicator.className = 'rec-indicator';
                recIndicator.innerText = '● 대기 중';
                if (rec.filepath) {
                    recPathDisplay.innerText = `최근 저장: ${rec.filepath}`;
                }
            }

            // 6. 모의 시험 버튼 상태
            const simBtn = document.getElementById('btn-sim');
            if (simBtn) {
                if (drowsy.is_simulated) {
                    simBtn.innerText = '🧪 졸음 모의 시험 [켜짐 - 눈 감김]';
                    simBtn.className = 'btn btn-warning active';
                } else {
                    simBtn.innerText = '🧪 졸음 감지 모의 시험 [꺼짐]';
                    simBtn.className = 'btn btn-warning';
                }
            }

            // 7. 관제 기록 메시지
            document.getElementById('event-log-text').innerText = data.last_event_message || '정상 주행 중';
        })
        .catch(err => {
            console.error("상태 데이터 수신 오류:", err);
        });
}

// [핵심 기능] 운전자 정상 상태 확인 후 정상 주행 복귀 명령
function recoverSystem() {
    const btn = document.getElementById('btn-recover');
    if (btn) btn.style.opacity = '0.7';

    fetch('/api/recover', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            console.log("복구 완료:", data);
            updateStatus();
        })
        .catch(err => alert("복구 요청 실패: " + err))
        .finally(() => {
            if (btn) btn.style.opacity = '1.0';
        });
}

// 비상 정지 (긴급 제동)
function stopSystem() {
    fetch('/api/stop', { method: 'POST' })
        .then(() => updateStatus())
        .catch(err => console.error("정지 요청 오류:", err));
}

// 졸음 감지 모의 시험 토글
function toggleSimDrowsy() {
    fetch('/api/trigger_drowsy', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            console.log("모의 시험 토글:", data);
            updateStatus();
        })
        .catch(err => console.error("모의 시험 요청 오류:", err));
}

// 차량 주행 시작 / 정지 토글
function toggleDrive() {
    fetch('/api/toggle_drive', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            console.log("주행 토글:", data);
            updateStatus();
        })
        .catch(err => console.error("주행 토글 오류:", err));
}
