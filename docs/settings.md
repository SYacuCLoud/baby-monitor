# 설정

### 환경 변수

코드(`os.environ`)가 실제로 읽는 것 전부입니다.

| 이름 | 기본값 | 읽는 곳 | 의미 |
| --- | --- | --- | --- |
| `CRIB_MODEL` | `jev` | `judge.py` | 판정 모델. `jev` 또는 `qwen` (대소문자 무시, 빈 값이면 `jev`). 그 외는 종료 |
| `CRIB_JEV_URL` | `http://127.0.0.1:8090/v1/systemone` | `jev_protocol.py` | Jev 서버 주소. 기본: `http`, loopback, 인증 정보 없음, 경로 `/v1/systemone`. **원격(선택)**: 그 밖의 주소는 원격 서버로 인식합니다 ([원격 Jev](remote-jev.md)). 경로는 자유(없으면 `/v1/systemone`). `localhost`·사설 IP는 `http`와 토큰 없음 가능, 그 밖은 `https`와 `CRIB_JEV_TOKEN` 필수 |
| `CRIB_JEV_TOKEN` | (없음) | `jev_protocol.py` | 원격 Jev 서버용 토큰. 공개 주소(그 밖의 주소)에는 필수, `localhost`·사설 IP에서는 선택. 있으면 `Authorization: Bearer` 헤더로만 보냄(주소에 넣지 않음). 로그·오류에 찍지 않음 |
| `CRIB_JEV_TIMEOUT` | 호출하는 쪽 기본(180초) | `jev_protocol.py` | 원격 요청 한 건의 제한 시간(초, 0 초과 3600 이하). 로컬에는 적용 안 함 |
| `CRIB_JEV_REMOTE_LIVE` | `0` | `jev_protocol.py`, `watch.py` | `1`이면 **실시간 감시(watch.py)도** 원격 Jev를 씀. 기본은 꺼짐: 원격이 설정돼 있어도 실시간 감시는 로컬 서버만 씀 |
| `CRIB_JEV_BACKEND` | `local` | `remote_settings.py` | `remote`일 때만 `crib_remote.env` 파일 값을 읽음. 환경 변수로 `local`을 주면 파일을 무시 |
| `CRIB_REMOTE_ENV` | `crib_remote.env` | `remote_settings.py` | 설정 파일 경로 바꾸기 (시험용) |
| `CRIB_JEV_MODEL` | `jev-latest` | `jev_protocol.py` | 요청 본문의 `model` 값 |
| `CRIB_JEV_KEY` | (없음) | `jev_protocol.py` | 설정하면 `Authorization: Bearer ...` 헤더를 붙임 |
| `CRIB_JEV_ALERT_AT_FACE_COVER`, `CRIB_JEV_ALERT_AT_CLIMBING` | `0.60` | `jev_protocol.py` | 규칙별 알림 점수 기준 (Jev만). 0 초과 1 이하의 숫자, 비우면 0.60. 숫자가 아니거나 범위 밖이면 `SystemExit`로 종료. `empty`(0.30 미만 기준)와 표시용 0.50은 환경 변수로 못 바꿈 |
| `CRIB_JEV_PORT` | `8090` | `imajev_serve.py` | imajev 서버가 열 포트. 클라이언트(`CRIB_JEV_URL`)와 GUI(8090 고정)는 따라 바뀌지 않으므로 바꾸면 `CRIB_JEV_URL`도 맞춰야 하고 GUI는 인식하지 못함 |
| `IMAJEV_DIR` | `runtime/imajev` | `imajev_serve.py` | imajev 저장소 위치 |
| `IMAJEV_MODELS` | `runtime/models` | `imajev_serve.py` | 모델 폴더. `imajev-2b/` 어댑터를 이 아래에서 찾음 |
| `IMAJEV_CHECK_FRAME` | `C:\_AX\baby-monitor\baby-monitor-private\frames-live\latest.jpg` | `imajev_serve.py --check` | 속도·점수 비교 검사에 쓸 프레임 파일 |
| `HF_HUB_OFFLINE` | `1` (미설정일 때만 설정) | `imajev_serve.py` | Hugging Face 오프라인 모드 |
| `IMAJEV_PY` | `runtime/imajev/.venv/Scripts/python.exe` | `crib_gui.py` | GUI가 Jev 서버를 띄울 Python |
| `LLAMA_SERVER` | `runtime/llama-server/llama-server.exe` | `crib_gui.py` | GUI가 Qwen 서버로 띄울 실행 파일 |
| `QWEN_DIR` | `runtime/models/Qwen3-VL-4B` | `crib_gui.py` | GUI가 찾는 `Qwen3-VL-4B-Instruct-Q4_K_M.gguf`, `mmproj-F16.gguf`의 폴더 |
| `RTSP_USER` | (필수) | `watch.py` | 카메라 계정. `rtsp.env`로도 지정 (환경 변수 우선) |
| `RTSP_PASS` | (필수) | `watch.py` | 카메라 비밀번호. 위와 같음 |
| `RTSP_HOST` | (필수) | `watch.py` | `host` 또는 `host:port`만 허용 (`rtsp://`나 `/` 불가). 포트 없으면 `:554` |
| `RTSP_PATH` | `stream1` | `watch.py` | 스트림 경로. `stream6`이 들어 있으면 종료 |
| `WATCH_HEARTBEAT_SEC` | `21600` (6시간) | `watch.py` | "감시 중" 푸시 간격(초). `0`이면 끔. 정수가 아니면 시작할 때 `ValueError` |
| `CRIB_SCORE_LOG` | `logs/scores.jsonl` (저장소 폴더 기준) | `score_log.py` | 점수 기록 파일 경로. `off`(또는 `0`, `false`, `no`, `none`, `disabled`)면 기록 안 함. 저장 사진 폴더는 이 파일 옆의 `frames/` |
| `CRIB_LOG_SAVE_FRAMES` | 꺼짐 | `score_log.py` | `1`/`true`/`yes`/`on`이면 판정한 사진을 **전부** `logs/frames/`에 JPEG로 저장 (아기 사진) |
| `CRIB_LOG_SAVE_AMBIGUOUS` | 꺼짐 | `score_log.py` | `0.3-0.7` 형식. 규칙 점수(`face_cover`, `climbing`) 중 하나가 이 범위(양 끝 포함)에 들면 그 사진만 저장. 형식이 틀리면 경고 한 번 후 꺼진 것으로 처리. Qwen은 점수가 없어 이 옵션으로는 저장되지 않음 |
| `CRIB_LOG_MAX_FRAMES` | `200` | `score_log.py` | 저장 사진 최대 개수 (1 이상 정수). 넘으면 오래된 것부터 삭제. 잘못된 값이면 경고 한 번 후 200 |
| `NTFY_TOPIC` | (없음) | `ntfy_alert.py` | ntfy 토픽. 없으면 `ntfy.env`, 그것도 없으면 무작위로 생성해 `ntfy.env`에 저장 |
| `NTFY_SERVER` | `https://ntfy.sh` | `ntfy_alert.py` | ntfy 서버 주소. 불러올 때 한 번 읽음 |

### 코드 상수 (환경 변수 아님, 바꾸려면 코드 수정)

| 상수 | 값 | 파일 | 의미 |
| --- | --- | --- | --- |
| `INTERVAL_SEC` | 15 | `watch.py` | 루프 간격(초) |
| `FORCE_SEC` | 60 | `watch.py` | 변화 없어도 이 시간마다 판정 |
| `COOLDOWN_SEC` | 600 | `watch.py` | 같은 규칙 재알림 간격 |
| `GRAB_TIMEOUT_SEC` | 25 | `watch.py` | ffmpeg 캡처 전체 제한. ffmpeg 소켓 timeout은 8초 |
| `ERROR_ALERT_AFTER` | 3 | `watch.py` | 연속 실패 몇 번째에 오류 푸시 |
| `ERROR_REALERT_SEC` | 1800 | `watch.py` | 계속 실패할 때 오류 푸시 반복 간격 |
| `PIXEL_DELTA` | 25 | `motion.py` | 픽셀이 "변했다"고 보는 밝기 차 |
| `MIN_CHANGED` | 0.02 | `motion.py` | 변한 픽셀 비율이 이 이상이면 "움직임" |
| `ALERT_AT` / `ABSENT_AT` / `PRESENT_AT` | 0.60 / 0.30 / 0.50 | `jev_protocol.py` | Jev 점수 기준 ([판정 규칙](rules.md)). `ALERT_AT`은 규칙별 환경 변수로 덮어쓸 수 있는 기본값 |
| `MAX_BYTES` / `KEEP` | 5MB / 3 | `score_log.py` | 점수 기록 파일 회전 크기와 보관 개수 (현재 파일 + `.1` + `.2`) |
| `MAX_EDGE` | 1280 | `qwen_client.py` | 모델로 보내기 전 긴 변 최대 픽셀 (Jev도 같은 함수 사용) |
