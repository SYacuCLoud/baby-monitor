# 설치와 실행

### 1. 준비

```text
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`rtsp.env.example` → `rtsp.env` 로 복사하고 채웁니다 (`RTSP_USER`, `RTSP_PASS`, `RTSP_HOST`, `RTSP_PATH`). 환경 변수가 있으면 파일보다 우선합니다. 값에 따옴표를 쓰지 마세요 (코드가 따옴표를 벗기지 않습니다).

`ntfy.env.example` → `ntfy.env` 로 복사해 `NTFY_TOPIC`을 길고 무작위한 값으로 바꿉니다. 파일이 없고 `NTFY_TOPIC` 환경 변수도 없으면, 푸시를 보내는 시점에 `ntfy_alert.py`가 `crib-` + 무작위 문자열로 `ntfy.env`를 자동 생성합니다. `python ntfy_alert.py`(자체 검사)도 같은 파일을 만듭니다. 폰의 ntfy 앱에서 이 토픽을 구독하세요.

두 `.env` 파일은 `.gitignore`에 들어 있습니다. 커밋하지 마세요.

### 2. 판정 서버 켜기

**Jev (기본).** imajev venv의 Python으로 실행합니다. `127.0.0.1:8090`에서 뜹니다.

```text
runtime\imajev\.venv\Scripts\python.exe imajev_serve.py
```

경로 기본값은 [설정](settings.md)의 `IMAJEV_DIR`, `IMAJEV_MODELS`입니다. 서버는 `HF_HUB_OFFLINE=1`(미설정 시)로 모델을 오프라인에서 읽고, uvicorn을 `asyncio:SelectorEventLoop`로 띄웁니다 (파일 머리말 주석: Windows 기본 Proactor 루프에서 loopback 응답이 끊겼음). 질문(이미지 prefill 공유)을 한 번만 계산하도록 imajev의 `TorchBackend.score`를 교체합니다. 원본 경로와의 점수 차이는 `imajev_serve.py --check`가 확인합니다 (최대 차이 0.05 미만이어야 통과, **실행해 보지 않음**). `--check`의 기본 프레임 경로는 작성자 PC의 비공개 경로(`C:\_AX\baby-monitor\baby-monitor-private\frames-live\latest.jpg`)이므로, 다른 PC에서는 환경 변수 `IMAJEV_CHECK_FRAME`으로 프레임 파일을 지정하세요.

**Qwen.** `CRIB_MODEL=qwen`일 때 `127.0.0.1:8080`이 필요합니다 (주소는 코드에 고정, 환경 변수 없음).

```text
llama-server.exe -m Qwen3-VL-4B-Instruct-Q4_K_M.gguf --mmproj mmproj-F16.gguf -ngl 99 --mmproj-offload --image-min-tokens 1024 --host 127.0.0.1 --port 8080 -c 4096
```

(GUI의 "서버 시작"이 쓰는 인자와 같습니다.) `qwen_client.py`는 요청마다 `temperature: 0`, `max_tokens: 256`, `cache_prompt: false`, JSON 스키마(`response_format`)를 보냅니다. 코드 주석에 따르면 prompt 캐시를 켜면 같은 사진에서 temperature 0 답이 바뀌었다고 합니다. 로컬 연결이 끊기면 한 번 재시도합니다.

### 3. 자체 검사 (선택)

```text
python motion.py
python qwen_client.py
python jev_protocol.py
python judge.py
python score_log.py             # 임시 폴더에서만 돌고 실제 logs/는 건드리지 않음
python score_folder.py --selftest   # 가짜 판정 함수와 임시 폴더만 씀
python remote_settings.py
python test_remote.py           # 원격 모드 규칙, 가짜 https 서버 (openssl 필요)
python test_webapi.py           # 웹 API 주소 규칙: 경로, 토큰 선택, 헤더로만 전송, 리다이렉트 거부, 예전 remote env 호환
python test_remote_live.py      # 실시간 감시 경로 (푸시는 가짜 함수로 대체, 실제 ntfy 안 씀)
python test_stop.py             # GUI '서버 중지': 프로세스 트리 종료 (가짜 프로세스, tkinter 없이)
python watch.py
python crib_gui.py --check
python ntfy_alert.py            # ntfy.env가 없으면 만듦. 푸시는 안 보냄
python ntfy_alert.py --ping     # 실제로 테스트 푸시를 1건 보냄
```

각각 `ok ...` 한 줄을 출력하면 통과입니다. `python watch.py`는 인자 없이(또는 `--selftest`로) 실행하면 자체 검사만 하고, 이때 `frames/ticks.jsonl` 등을 만듭니다.

### 4. CLI (watch.py)

옵션 (`python watch.py --help`):

| 옵션 | 의미 |
| --- | --- |
| `--image 파일` | 이미지 한 장만 판정. 카메라·움직임 게이트 없음 |
| `--rtsp` | `rtsp.env`의 `stream1`에서 캡처하는 루프. `--rtsp`가 없어도 `rtsp.env`가 있으면 루프로 감 |
| `--once` | 루프를 한 번만 |
| `--ticks N` | N프레임 후 종료 (이때 대기 1초) |
| `--send` | 실제로 ntfy 푸시. **없으면 폰으로 안 감** (기본) |

의도된 사용법:

```text
python watch.py --image photo.jpg
python watch.py --rtsp --once
python watch.py --rtsp
python watch.py --rtsp --send
```

`python watch.py <인자>`(`--image`, `--rtsp` 등)는 자체 검사를 돌리지 않고 곧바로 `main()`을 실행합니다. 자체 검사는 인자가 없거나 `--selftest`일 때만 돕니다(`watch.py` 맨 아래 `__main__` 분기). `--rtsp` 루프는 카메라로는 **검증 안 됨**.

판정 결과는 한 줄 JSON으로 출력됩니다 (아래 [알림](alerts.md)의 `action` 참고).

### 5. GUI (crib_gui.py, Windows)

```text
python crib_gui.py
```

- 위쪽: `jev` / `qwen` 선택, 서버 시작·중지. 서버 포트(jev 8090, qwen 8080)는 코드에 고정입니다.
- 감시 시작·중지: `watch.py --rtsp`(+ 푸시 체크 시 `--send`)를 자식 프로세스로 띄웁니다. 선택한 모델 서버가 꺼져 있으면 시작하지 않습니다. 푸시 체크는 기본 꺼짐입니다.
- 위쪽 카메라 표시: 이 창이 감시를 켜 둔 동안 최근 틱의 `camera` 값에 따라 `카메라 연결됨`/`카메라 끊김`/`카메라 확인 중`, 감시가 꺼져 있으면 `카메라 대기`. 별도로 카메라를 조사하거나 푸시하지 않습니다 (표시 로직은 `--check`로 확인, 실제 창은 **검증 안 됨**).
- 큰 글씨로 최근 판정, 아래에 `frames/latest.jpg` 미리보기와 최근 50줄의 글자 기록(`ticks.jsonl`)을 보여줍니다.
- `파일 테스트` / `붙여넣기`(Ctrl+V): 선택한 모델로 사진 한 장만 판정합니다. 폰으로 보내지 않고, 창의 "기록" 목록(`ticks.jsonl`)에도 넣지 않습니다. 다만 판정이 성공하면 점수 기록(`logs/scores.jsonl`, `source: gui-test`)에는 남습니다 (`image` 칸에 파일 테스트는 파일 이름, 붙여넣기는 `clipboard`). 실패한 테스트는 기록하지 않습니다.
- Jev일 때 입코·난간·아기·어른·얼굴 점수와 알림 기준 0.60이 표시됩니다. Qwen 결과에는 점수가 없어 이 줄들이 나오지 않습니다.
- 감시 루프의 결과는 창이 `status.json`에서 읽는데 거기에는 점수가 없어서 창에 점수가 나오지 않습니다. 감시 결과의 점수는 `python score_log.py --tail 20`으로 봅니다.
- 창을 닫으면 트레이로 갑니다 (트레이가 없으면 안 닫힘). "종료"는 이 창이 켠 감시만 끄고 모델 서버는 그대로 둡니다.
- "서버 중지"는 이 창이 켠 서버, 또는 해당 포트에서 `imajev_serve.py`/`llama-server`로 확인되는 프로세스만 종료합니다.
- 서버·감시의 출력은 `frames/gui-jev.log`, `frames/gui-qwen.log`, `frames/gui-watch.log`에 쌓입니다.
