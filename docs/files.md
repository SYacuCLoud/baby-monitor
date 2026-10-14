# 파일 구조

| 파일 | 역할 |
| --- | --- |
| `watch.py` | CLI. 캡처 루프(ffmpeg), 움직임 게이트 호출, 쿨다운, 오류·하트비트 알림, `frames/` 기록 |
| `motion.py` | 픽셀 차이 움직임 게이트 (`PIXEL_DELTA=25`, `MIN_CHANGED=0.02`) |
| `judge.py` | `CRIB_MODEL`로 Jev/Qwen 선택 |
| `jev_protocol.py` | Jev 클라이언트. 짧은 noul 질문, face_cover=max(face_in_bed, mouth_nose_blocked), loopback 강제 |
| `imajev_serve.py` | imajev playground 서버 실행기 (SelectorEventLoop, 이미지 prefill 공유) |
| `score_log.py` | 판정 점수 기록(`logs/scores.jsonl`), 사진 저장 옵션, `--tail/--csv/--label` CLI |
| `score_folder.py` | 사진 폴더 점수 측정 CLI (읽기 전용: 푸시·기록·상태 파일 쓰기 없음) |
| `qwen_client.py` | llama-server 클라이언트, JSON 파싱, `normalize` 보정, 이미지 축소 |
| `prompt.txt` | Qwen용 프롬프트 (Jev 경로는 쓰지 않음) |
| `ntfy_alert.py` | ntfy 텍스트 푸시. 빈 메시지와 `\x00` 포함 메시지는 거부 |
| `crib_gui.py` | Windows 창 (서버·감시 시작/중지, 미리보기, 기록, 트레이, 한 장 테스트, 점수 표시) |
| `remote_settings.py` | 원격 Jev 설정 읽기·저장·검증·마스킹·연결 테스트 (tkinter 없음, 자체 검사 포함) |
| `test_remote.py`, `test_remote_live.py` | 원격 모드 자체 검사 (가짜 서버) |
| `proc_stop.py`, `test_stop.py` | 서버 중지(프로세스 트리 종료, Windows는 `taskkill /T /F`)와 그 자체 검사 |
| `ntfy.env.example`, `rtsp.env.example`, `crib_remote.env.example` | 설정 예시. 실제 `ntfy.env`, `rtsp.env`, `crib_remote.env`는 커밋 금지 |
| `requirements.txt` | Python 의존성 (`Pillow`, `numpy`) |
| `docs/architecture.html` | 파이프라인 도식. 일부 설명이 현재 코드와 다름 (움직임 비교가 "이전 프레임 대비"만으로 적혀 있고, `normalize`가 "얼굴 노출 시 알림 취소"를 한다고 적혀 있으나 코드에는 없음, 모델을 Qwen으로 표기) |
| `SECURITY.md` | 비밀 정보 취급, 취약점 보고 |
| `LICENSE` | MIT |

실행 중 만들어지는 파일 (모두 `.gitignore`의 `frames/`, `logs/`, `*.env`):

| 파일 | 내용 |
| --- | --- |
| `frames/latest.jpg` | 마지막으로 캡처한 카메라 프레임 (매번 덮어씀) |
| `frames/status.json`, `frames/status.html` | 마지막 판정 한 줄 |
| `frames/ticks.jsonl` | 판정 기록. 5MB를 넘으면 `.1`, `.2`로 넘기고 최대 3개만 유지 (`watch.py`, `score_log.py`와 같은 방식). 로테이션이 실패해도 감시는 멈추지 않음 |
| `frames/gui-*.log` | GUI가 띄운 서버·감시의 출력 |
| `logs/scores.jsonl` (+ `.1`, `.2`) | 점수 기록 |
| `logs/frames/*.jpg` | 사진 저장 옵션을 켠 경우에만 생기는 판정 사진 (아기 사진) |
| `ntfy.env` | 로컬 ntfy 토픽 |
| `crib_remote.env` | GUI의 'Jev 서버' 저장 파일. **원격 토큰이 들어 있음**. 커밋 금지(`.gitignore`), 권한 0600 (가능한 환경에서) |
