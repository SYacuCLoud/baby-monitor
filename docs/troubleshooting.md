# 문제 해결

| 증상 | 원인과 조치 |
| --- | --- |
| `rtsp.env needs RTSP_USER RTSP_PASS RTSP_HOST` | `rtsp.env` 또는 환경 변수에 세 값이 모두 있어야 합니다 |
| `RTSP_HOST must be host or host:port only` | `rtsp://`나 `/` 없이 `192.168.0.x` 또는 `192.168.0.x:554`로 적으세요 |
| `stream6 is human PTZ, not the alert loop` | `RTSP_PATH`를 `stream1`로 |
| `action: error`, `reason: grab failed` | ffmpeg 캡처 실패, 또는 받은 프레임이 깨졌음(잘림·빈 파일·읽을 수 없음·한 변이 32px 미만). 깨진 프레임은 판정하지 않고 캡처 오류로 셉니다. 카메라 전원·네트워크·계정, ffmpeg 설치와 PATH 확인 |
| `reason: FileNotFoundError` | `ffmpeg`가 PATH에 없음 |
| `reason: ConnectionRefusedError` | 모델 서버가 꺼져 있음 (Jev 8090 / Qwen 8080) |
| `qwen HTTP N`, `jev HTTP N refused` | 서버가 200이 아닌 응답. 서버 로그 확인 (GUI는 `frames/gui-*.log`) |
| `jev server did not read the frame; verdict dropped` | Jev 서버가 이미지를 못 읽음 (텍스트 전용 서버). 이미지를 받는 imajev playground 서버인지 확인 |
| `jev url must be http loopback...` | `CRIB_JEV_URL`이 `http://127.0.0.1:포트/v1/systemone` 형태인지 확인 (로컬 기본 경로의 오류). 원격 서버면 `remote jev settings invalid`로 나오며, `localhost`·사설 IP가 아닌 주소는 `https://`와 `CRIB_JEV_TOKEN`이 필요함 |
| `RemoteJevError: remote jev connection failed / timeout / tls error / HTTP 401` | 원격 Jev 문제. 서버가 꺼졌는지, 주소·토큰이 맞는지 확인. 실시간 감시에서는 이 오류가 반복되면 `판정 불가` 푸시가 감 |
| `CRIB_MODEL must be qwen or jev` | `CRIB_MODEL` 값 수정 또는 삭제 (삭제하면 `jev`) |
| `ValueError: invalid literal for int()` | `WATCH_HEARTBEAT_SEC`가 정수가 아님 |
| `CRIB_JEV_ALERT_AT_... must be in (0, 1]` | 기준 값을 0 초과 1 이하 숫자로 (비우면 기본 0.60) |
| 점수 기록 파일이 안 생김 | `CRIB_SCORE_LOG=off`인지, `python watch.py <인자>` 직접 실행 문제(위)가 아닌지 확인. 기록 실패 시 stderr에 `[score_log]` 경고가 한 번 나옴 |
| 푸시가 안 옴 | `--send`(GUI는 푸시 체크)를 켰는지, 폰 ntfy 앱이 `ntfy.env`의 토픽을 구독 중인지, `action`이 `send_fail`인지 확인. `python ntfy_alert.py --ping`으로 테스트 |
| `rtsp.env` 값이 안 먹음 | 값에 따옴표를 넣지 않았는지 확인 (코드가 벗기지 않음) |
| GUI: `실행 파일이 없습니다: ...` | `IMAJEV_PY` 또는 `LLAMA_SERVER`, `QWEN_DIR` 환경 변수를 실제 경로로 설정 |
| GUI: `다른 모델 서버가 켜져 있습니다` | 8GB 가정으로 한쪽만 켜게 막혀 있음. 다른 서버를 먼저 중지 |
| GUI: 트레이가 없음 | `pywin32`가 필요합니다 (`pip install pywin32`, `requirements.txt`에는 없음) |
| GUI: `서버가 꺼져 있어서 감시를 시작하지 않습니다` | 선택한 모델의 포트(jev 8090 / qwen 8080)가 열려 있어야 함 |
