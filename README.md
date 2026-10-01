# Local crib monitor

로컬 PC에서 침대 사진 한 장씩을 판정 모델(Jev 또는 Qwen)에 물어보고, 규칙에 걸리면 **텍스트 푸시(ntfy)** 를 보내는 개인용 감시 도구입니다.

> **의료기기도, 안전장치도 아닙니다. 성인 감독을 대체하지 않습니다. 신생아를 혼자 두지 마세요.**
> 알림이 오지 않는다고 안전하다는 뜻이 아닙니다. 모델 정확도는 이 저장소의 테스트로 검증되지 않았습니다. 특히 엎드림(`face_down`)은 불안정합니다 ([검증 상태](#검증-상태), [한계](#한계와-알려진-문제) 참고).

## 목차

- [하는 일](#하는-일) · [동작 방식](#동작-방식) · [검증 상태](#검증-상태)
- [필요한 것](#필요한-것) · [설치와 실행](#설치와-실행)
- [설정](#설정) · [판정 규칙](#판정-규칙) · [알림 동작](#알림-동작) · [점수 기록](#점수-기록)
- [파일 구조](#파일-구조) · [개인정보와 보안](#개인정보와-보안)
- [한계와 알려진 문제](#한계와-알려진-문제) · [문제 해결](#문제-해결) · [License](#license)

## 하는 일

- LAN 카메라의 RTSP `stream1`에서 ffmpeg로 한 장을 캡처하거나, 이미지 파일 한 장(`--image`)을 입력으로 받습니다.
- 픽셀 차이(numpy)로 "움직임이 있나"만 보고, 변화가 없으면 모델 호출을 건너뜁니다.
- 로컬 모델이 네 가지 규칙(`face_cover`, `face_down`, `climbing`, `empty`)에 해당하는지 판정합니다. 정확도는 **검증 안 됨**입니다.
- 규칙에 걸리면 ntfy로 텍스트 푸시를 보냅니다. 감시가 멈추거나 모델이 계속 실패해도 푸시합니다.
- 푸시는 `--send`(GUI에서는 "푸시" 체크)를 켠 경우에만 나갑니다. 기본은 꺼짐입니다.
- 모델을 부른 판정마다 점수 한 줄을 `logs/scores.jsonl`에 기록합니다 (기본 켜짐. 사진은 기본으로 저장하지 않음). [점수 기록](#점수-기록) 참고.
- 선택 사항으로 Windows용 작은 창(`crib_gui.py`)이 있습니다.

하지 않는 일: 울음·호흡·체온 감지, 사진 전송(ntfy에 사진 없음), 클라우드 API 호출, 모델 학습, 사이렌.

## 동작 방식

```text
카메라 RTSP stream1 ──ffmpeg 1장──▶ frames/latest.jpg
                                     │
                           움직임 게이트 (motion.py)
                  변화 없음 ─▶ 모델 호출 생략 (단, 60초마다 강제 판정)
                                     │
                           judge.py  (CRIB_MODEL)
                     ┌───────────────┴───────────────┐
              jev (기본)                              qwen
   127.0.0.1:8090/v1/systemone              127.0.0.1:8080/v1/chat/completions
   imajev_serve.py, 질문 8개 점수             llama-server, prompt.txt, JSON
                     └───────────────┬───────────────┘
                                     ├─▶ 점수 한 줄 기록 (score_log.py → logs/scores.jsonl)
                       normalize (qwen_client.py) 가드
                                     │
                  규칙별 10분 쿨다운 ─▶ ntfy 텍스트 푸시 (--send일 때만)
```

1. **캡처** (`watch.py`): `ffmpeg -rtsp_transport tcp`로 한 프레임을 `frames/latest.jpg`에 저장합니다. 루프 간격은 15초입니다.
2. **움직임 게이트** (`motion.py`): 그레이스케일 픽셀 차이가 25를 넘는 픽셀이 전체의 2% 이상이면 "움직임 있음"입니다. 직전 프레임과, 마지막으로 판정한 프레임을 둘 다 비교합니다 (천천히 변하는 장면을 놓치지 않기 위해). 첫 프레임이거나 마지막 판정 후 60초가 지났으면 변화가 없어도 판정합니다.
3. **판정** (`judge.py`): `CRIB_MODEL`이 `jev`(기본) 또는 `qwen`입니다. 그 외 값이면 `CRIB_MODEL must be qwen or jev`로 종료합니다. 프레임은 최대 긴 변 1280px, JPEG 품질 85로 줄여서 보냅니다.
   - **Jev**: 질문 8개의 점수(0~1)를 받아 코드가 알림 여부를 정합니다. 엎드림(`face_down`)은 단서 질문 3개(볼/등/배)를 따로 묻고, 그중 **두 번째로 높은 값**을 `face_down` 점수로 씁니다 (단서 2개 이상이 함께 높아야 올라감). 규칙 점수가 기준(기본 0.60) 이상이면 알림입니다 ([판정 규칙](#판정-규칙)). Jev는 문장을 쓰지 않으므로 `reason`은 비어 있습니다.
   - **Qwen**: `prompt.txt`와 사진을 한 메시지로 보내고 JSON 한 개를 받습니다. 모델이 `should_alert`를 정하고, 코드가 `normalize`로 보정합니다. 점수는 없습니다.
4. **기록** (`score_log.py`): 모델을 부른 판정(감시 루프, `--image` 한 장, GUI의 파일·붙여넣기 테스트)마다 점수 한 줄을 `logs/scores.jsonl`에 추가합니다. 움직임이 없어 건너뛴 틱과 모델 오류는 기록하지 않습니다.
5. **알림** (`ntfy_alert.py`): `should_alert`이고 같은 규칙의 쿨다운(10분)이 지났으면 ntfy에 텍스트를 보냅니다.

## 검증 상태

이 README의 내용은 코드를 읽고, 아래 자체 검사를 실행해서 확인한 것입니다.

| 항목 | 상태 |
| --- | --- |
| `motion.py`, `qwen_client.py`, `jev_protocol.py`, `judge.py`, `score_log.py`, `watch.py`(인자 없이) 자체 검사 | 통과 (합성 데이터, 가짜 로컬 서버) |
| `crib_gui.py --check` | 통과. tkinter가 있어야 실행됨 (확인할 때는 tkinter를 가짜 모듈로 대체) |
| ntfy 전송 형식 (JSON, 제목 `아기`, 우선순위 5) | 로컬 가짜 서버로 확인. 실제 ntfy.sh 전달은 **검증 안 됨** |
| 실제 모델(Jev·Qwen)의 규칙별 판정 정확도 | **검증 안 됨** (저장소에 실제 사진 테스트 없음) |
| `face_down` 판정 | **불안정** ([한계](#한계와-알려진-문제) 참고). 단서 3개(두 번째로 높은 값) 방식이 실제 엎드린 아기 사진에서 나아졌는지는 **검증 안 됨** |
| 점수 기록 (`logs/scores.jsonl`, 환경 변수, `--tail/--csv/--label`, 사진 저장 옵션) | 가짜 Jev 서버(고정 점수)와 `watch.main()`으로 동작 확인. 실제 모델 출력으로는 **검증 안 됨** |
| 규칙별 기준 `CRIB_JEV_ALERT_AT_<RULE>` | 가짜 Jev 서버로 동작과 잘못된 값(`2`)의 종료 확인. 실제 모델 점수에 맞는 값은 **검증 안 됨** |
| 실제 카메라 RTSP 루프 (`--rtsp`) | **검증 안 됨** (ffmpeg·카메라 없이는 못 돌림) |
| imajev 서버 (`imajev_serve.py`, `--check`), llama-server 실행 | **검증 안 됨** (GPU·모델 필요) |
| Windows GUI·트레이·서버 시작/중지 | **검증 안 됨** (Windows 전용 코드, Linux에서는 실행 불가) |
| 속도·VRAM 수치 | 이 README에서 주장하지 않음 (질문이 8개가 된 뒤의 속도도 **검증 안 됨**) |

## 필요한 것

- Python 3 (`from __future__ import annotations` 사용). 자체 검사는 Python 3.13에서 돌려 봤습니다. 지원 하한 버전은 **검증 안 됨**.
- `pip install -r requirements.txt` → `Pillow>=10,<12`, `numpy>=1.26,<3`
- [ffmpeg](https://ffmpeg.org/download.html): `--rtsp` 캡처에 필요합니다. `ffmpeg`가 PATH에 있어야 합니다.
- 판정 서버 중 하나 (둘 다 로컬, 동시에 필요하지 않음):
  - **Jev (기본)**: [imajev](https://github.com/mohit67890/imajev) 저장소와 `imajev-2b` 어댑터(`calibration.json` 포함). 이 저장소의 `imajev_serve.py`가 imajev의 playground 서버를 불러 씁니다. imajev의 의존성(torch 등)은 imajev 쪽 venv에 설치해야 하며 `requirements.txt`에는 없습니다. 설치 방법은 imajev 문서를 따르세요.
  - **Qwen**: [llama.cpp](https://github.com/ggml-org/llama.cpp)의 `llama-server`, Qwen3-VL-4B-Instruct `Q4_K_M` GGUF와 `mmproj-F16.gguf`. 가중치는 저장소에 없습니다. 직접 받으세요.
- ntfy 앱 (푸시를 받을 폰). 기본 서버는 `https://ntfy.sh`입니다.
- **GUI (`crib_gui.py`)**: Windows 전용입니다 (`netstat`, `powershell`, `taskkill`, `win32gui` 사용). tkinter가 필요합니다. 트레이 아이콘은 `pywin32`가 있어야 만들어지는데 `requirements.txt`에는 없습니다. 없으면 창은 뜨지만 트레이는 없습니다.
- 작성자 환경은 Windows 11 + 8GB GPU로 보입니다 (GUI가 "8GB라 같이 못 올립니다"라고 두 서버를 동시에 켜지 못하게 함). CLI 쪽은 OS 종속 코드가 없지만 Windows 밖에서의 전체 동작은 **검증 안 됨**.

## 설치와 실행

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
D:\Dev\imajev\.venv\Scripts\python.exe imajev_serve.py
```

경로 기본값은 [설정](#설정)의 `IMAJEV_DIR`, `IMAJEV_MODELS`입니다. 서버는 `HF_HUB_OFFLINE=1`(미설정 시)로 모델을 오프라인에서 읽고, uvicorn을 `asyncio:SelectorEventLoop`로 띄웁니다 (파일 머리말 주석: Windows 기본 Proactor 루프에서 loopback 응답이 끊겼음). 질문(8개)이 공유하는 이미지 prefill을 한 번만 계산하도록 imajev의 `TorchBackend.score`를 교체합니다. 원본 경로와의 점수 차이는 `imajev_serve.py --check`가 확인합니다 (최대 차이 0.05 미만이어야 통과, **실행해 보지 않음**). `--check`의 기본 프레임 경로는 작성자 PC의 비공개 경로(`C:\_AX\baby-monitor\baby-monitor-private\frames-live\latest.jpg`)이므로, 다른 PC에서는 환경 변수 `IMAJEV_CHECK_FRAME`으로 프레임 파일을 지정하세요.

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

판정 결과는 한 줄 JSON으로 출력됩니다 (아래 [알림 동작](#알림-동작)의 `action` 참고).

### 5. GUI (crib_gui.py, Windows)

```text
python crib_gui.py
```

- 위쪽: `jev` / `qwen` 선택, 서버 시작·중지. 서버 포트(jev 8090, qwen 8080)는 코드에 고정입니다.
- 감시 시작·중지: `watch.py --rtsp`(+ 푸시 체크 시 `--send`)를 자식 프로세스로 띄웁니다. 선택한 모델 서버가 꺼져 있으면 시작하지 않습니다. 푸시 체크는 기본 꺼짐입니다.
- 위쪽 카메라 표시: 이 창이 감시를 켜 둔 동안 최근 틱의 `camera` 값에 따라 `카메라 연결됨`/`카메라 끊김`/`카메라 확인 중`, 감시가 꺼져 있으면 `카메라 대기`. 별도로 카메라를 조사하거나 푸시하지 않습니다 (표시 로직은 `--check`로 확인, 실제 창은 **검증 안 됨**).
- 큰 글씨로 최근 판정, 아래에 `frames/latest.jpg` 미리보기와 최근 50줄의 글자 기록(`ticks.jsonl`)을 보여줍니다.
- `파일 테스트` / `붙여넣기`(Ctrl+V): 선택한 모델로 사진 한 장만 판정합니다. 폰으로 보내지 않고, 창의 "기록" 목록(`ticks.jsonl`)에도 넣지 않습니다. 다만 판정이 성공하면 점수 기록(`logs/scores.jsonl`, `source: gui-test`)에는 남습니다 (`image` 칸에 파일 테스트는 파일 이름, 붙여넣기는 `clipboard`). 실패한 테스트는 기록하지 않습니다.
- Jev일 때 점수 6개(엎드림·입코·난간·아기·어른·얼굴)와 "알림 기준 0.60"이 표시되고, 엎드림 줄에는 원값이 `(볼 0.20 / 등 0.72 / 배 0.35)` 형태로 붙습니다. "알림 기준" 숫자는 코드의 기본값 `ALERT_AT`이므로 `CRIB_JEV_ALERT_AT_<RULE>`로 기준을 바꿔도 창의 숫자는 바뀌지 않습니다. Qwen 결과에는 점수가 없어 이 줄들이 나오지 않습니다.
- 감시 루프의 결과는 창이 `status.json`에서 읽는데 거기에는 점수가 없어서 창에 점수가 나오지 않습니다. 감시 결과의 점수는 `python score_log.py --tail 20`으로 봅니다.
- 창을 닫으면 트레이로 갑니다 (트레이가 없으면 안 닫힘). "종료"는 이 창이 켠 감시만 끄고 모델 서버는 그대로 둡니다.
- "서버 중지"는 이 창이 켠 서버, 또는 해당 포트에서 `imajev_serve.py`/`llama-server`로 확인되는 프로세스만 종료합니다.
- 서버·감시의 출력은 `frames/gui-jev.log`, `frames/gui-qwen.log`, `frames/gui-watch.log`에 쌓입니다.

## 설정

### 환경 변수

코드(`os.environ`)가 실제로 읽는 것 전부입니다.

| 이름 | 기본값 | 읽는 곳 | 의미 |
| --- | --- | --- | --- |
| `CRIB_MODEL` | `jev` | `judge.py` | 판정 모델. `jev` 또는 `qwen` (대소문자 무시, 빈 값이면 `jev`). 그 외는 종료 |
| `CRIB_JEV_URL` | `http://127.0.0.1:8090/v1/systemone` | `jev_protocol.py` | Jev 서버 주소. `http`, loopback(`127.0.0.1`/`localhost`/`::1`), 인증 정보 없음, 경로 `/v1/systemone`만 허용. 아니면 종료 |
| `CRIB_JEV_MODEL` | `jev-latest` | `jev_protocol.py` | 요청 본문의 `model` 값 |
| `CRIB_JEV_KEY` | (없음) | `jev_protocol.py` | 설정하면 `Authorization: Bearer ...` 헤더를 붙임 |
| `CRIB_JEV_ALERT_AT_FACE_COVER`, `CRIB_JEV_ALERT_AT_FACE_DOWN`, `CRIB_JEV_ALERT_AT_CLIMBING` | `0.60` | `jev_protocol.py` | 규칙별 알림 점수 기준 (Jev만). 0 초과 1 이하의 숫자, 비우면 0.60. 숫자가 아니거나 범위 밖이면 `SystemExit`로 종료. `empty`(0.30 미만 기준)와 표시용 0.50은 환경 변수로 못 바꿈 |
| `CRIB_JEV_PORT` | `8090` | `imajev_serve.py` | imajev 서버가 열 포트. 클라이언트(`CRIB_JEV_URL`)와 GUI(8090 고정)는 따라 바뀌지 않으므로 바꾸면 `CRIB_JEV_URL`도 맞춰야 하고 GUI는 인식하지 못함 |
| `IMAJEV_DIR` | `D:\Dev\imajev` | `imajev_serve.py` | imajev 저장소 위치 |
| `IMAJEV_MODELS` | `D:\Dev\_Models` | `imajev_serve.py` | 모델 폴더. `imajev-2b/` 어댑터를 이 아래에서 찾음 |
| `IMAJEV_CHECK_FRAME` | `C:\_AX\baby-monitor\baby-monitor-private\frames-live\latest.jpg` | `imajev_serve.py --check` | 속도·점수 비교 검사에 쓸 프레임 파일 |
| `HF_HUB_OFFLINE` | `1` (미설정일 때만 설정) | `imajev_serve.py` | Hugging Face 오프라인 모드 |
| `IMAJEV_PY` | `D:\Dev\imajev\.venv\Scripts\python.exe` | `crib_gui.py` | GUI가 Jev 서버를 띄울 Python |
| `LLAMA_SERVER` | `D:\Dev\llama.cpp\llama-server.exe` | `crib_gui.py` | GUI가 Qwen 서버로 띄울 실행 파일 |
| `QWEN_DIR` | `D:\Dev\_Models\Qwen3-VL-4B` | `crib_gui.py` | GUI가 찾는 `Qwen3-VL-4B-Instruct-Q4_K_M.gguf`, `mmproj-F16.gguf`의 폴더 |
| `RTSP_USER` | (필수) | `watch.py` | 카메라 계정. `rtsp.env`로도 지정 (환경 변수 우선) |
| `RTSP_PASS` | (필수) | `watch.py` | 카메라 비밀번호. 위와 같음 |
| `RTSP_HOST` | (필수) | `watch.py` | `host` 또는 `host:port`만 허용 (`rtsp://`나 `/` 불가). 포트 없으면 `:554` |
| `RTSP_PATH` | `stream1` | `watch.py` | 스트림 경로. `stream6`이 들어 있으면 종료 |
| `WATCH_HEARTBEAT_SEC` | `21600` (6시간) | `watch.py` | "감시 중" 푸시 간격(초). `0`이면 끔. 정수가 아니면 시작할 때 `ValueError` |
| `CRIB_SCORE_LOG` | `logs/scores.jsonl` (저장소 폴더 기준) | `score_log.py` | 점수 기록 파일 경로. `off`(또는 `0`, `false`, `no`, `none`, `disabled`)면 기록 안 함. 저장 사진 폴더는 이 파일 옆의 `frames/` |
| `CRIB_LOG_SAVE_FRAMES` | 꺼짐 | `score_log.py` | `1`/`true`/`yes`/`on`이면 판정한 사진을 **전부** `logs/frames/`에 JPEG로 저장 (아기 사진) |
| `CRIB_LOG_SAVE_AMBIGUOUS` | 꺼짐 | `score_log.py` | `0.3-0.7` 형식. 규칙 점수(`face_cover`, `face_down`, `climbing`) 중 하나가 이 범위(양 끝 포함)에 들면 그 사진만 저장. 형식이 틀리면 경고 한 번 후 꺼진 것으로 처리. Qwen은 점수가 없어 이 옵션으로는 저장되지 않음 |
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
| `ALERT_AT` / `ABSENT_AT` / `PRESENT_AT` | 0.60 / 0.30 / 0.50 | `jev_protocol.py` | Jev 점수 기준 ([판정 규칙](#판정-규칙)). `ALERT_AT`은 규칙별 환경 변수로 덮어쓸 수 있는 기본값 |
| `MAX_BYTES` / `KEEP` | 5MB / 3 | `score_log.py` | 점수 기록 파일 회전 크기와 보관 개수 (현재 파일 + `.1` + `.2`) |
| `MAX_EDGE` | 1280 | `qwen_client.py` | 모델로 보내기 전 긴 변 최대 픽셀 (Jev도 같은 함수 사용) |

## 판정 규칙

규칙은 네 가지뿐이고 이것만 알림이 됩니다. 정의는 Jev 질문과 Qwen 프롬프트가 서로 다릅니다. 수면, 이불이 몸만 덮은 경우, 장난감 등은 알림이 아닙니다. 모르면 알리지 않는 쪽입니다.

| rule | 푸시 문구 (`ntfy_alert.py`) | 의미 |
| --- | --- | --- |
| `face_cover` | 입코 가림 | 천이 입과 코를 가림. 눈이나 볼이 보여도 취소되지 않음 |
| `face_down` | 얼굴 파묻힘 | 엎드림. Jev는 단서 3개를 묻고 두 번째로 높은 값을 씀 (아래). Qwen은 `prompt.txt`의 "배를 대고 엎드린 상태" 정의. **불안정**, [한계](#한계와-알려진-문제) 참고 |
| `climbing` | 난간 기어오름 | 아기가 난간 위에 있음. 아기가 보여야 알림 |
| `empty` | 아기 없음 | 아기도 어른도 없음. 아기가 보이면 알림 취소 |

### Jev: 질문과 임계값 (`jev_protocol.py`)

질문 8개를 각각 `noul` 점수(0~1)로 받습니다. `face_down`이라는 질문은 없고 아래 단서 3개가 그 자리를 대신합니다. 아래는 코드의 질문 원문입니다.

| 키 | 질문 (instructions) |
| --- | --- |
| `baby_present` | Is a baby visible in this crib frame? |
| `adult_present` | Is an adult visible in this frame? An adult is not a baby. |
| `face_visible` | Is any skin of an eye, cheek, mouth, or nose visible? |
| `face_cover` | Does fabric cover the mouth AND the nose? A visible eye or cheek does not cancel this. Fabric on the body or shoulders only is not a cover. |
| `face_down_cheek` | Is the baby's cheek or face pressed against the mattress or surface? |
| `face_down_back` | Do the baby's back, bottom, or the back of the head face up toward the camera while the baby lies down? |
| `face_down_belly` | Is the baby's belly or chest pressed down onto the surface and not visible? |
| `climbing` | Is the baby on the crib rail? |

**엎드림 점수**: `face_down` 점수는 `face_down_cheek`, `face_down_back`, `face_down_belly` 세 값 중 **두 번째로 높은 값**입니다. 즉 단서 두 개 이상이 함께 높아야 점수가 올라갑니다 (코드 주석: 질문 하나만 쓸 때는 엎드린 사진에서 0.2~0.45 정도가 나왔다고 함). 세 원값은 결과의 `face_down_parts`에 그대로 남고 GUI와 점수 기록에 나옵니다. 세 값 중 하나라도 응답에 없으면 0으로 치지 않고 오류입니다.

코드가 `should_alert`를 정합니다.

| 조건 | 결과 |
| --- | --- |
| `face_cover`, `face_down`, `climbing` 중 점수가 그 규칙의 기준 **이상**인 것이 있음 (기준은 기본 0.60, `CRIB_JEV_ALERT_AT_<RULE>`로 규칙별 변경) | 알림. 점수가 가장 높은 규칙 (동점이면 `face_cover` → `face_down` → `climbing` 순) |
| 위 세 규칙 점수가 모두 < 0.30 이고 `baby_present` < 0.30, `adult_present` < 0.30 | 알림, `empty` |
| 그 외 (기준 미만의 애매한 구간 포함) | 알리지 않음 |
| `baby_present`, `face_visible` 표시 값 | 점수 ≥ 0.50 이면 true |

코드 주석에 따르면 0.60은 보정된 Jev 임계값이 아닙니다. 기본 기준에서 점수 0.59는 알림이 되지 않고 0.60은 알림이 됩니다 (확인함). 규칙별 기준을 낮추면 놓침은 줄 수 있어도 오탐이 늘 수 있고, 어떤 값이 적절한지는 **검증 안 됨**입니다.

보정(`normalize`) 이후: `climbing`은 `baby_present`가 false면 취소, `empty`는 `baby_present`가 true면 취소, `face_cover`/`face_down`은 아기가 안 보여도 유지하고 본문에 "아기가 안 보임"을 붙입니다 (이불에 완전히 덮인 경우를 놓치지 않기 위해). 서버 응답의 `usage.images`가 정확히 1개가 아니면 서버가 사진을 못 본 것으로 보고 판정을 버립니다.

### Qwen: `prompt.txt`

Qwen 경로에는 점수 임계값이 없고, `prompt.txt`의 규칙을 모델이 판정합니다. 출력 JSON 스키마는 다음과 같고 `rule`은 허용 값(네 규칙 또는 null)으로 제한됩니다. `reason`은 최대 120자입니다.

```json
{"should_alert":false,"baby_present":false,"face_visible":false,"rule":null,"reason":""}
```

- `face_visible`: 눈·볼·입·코 피부 중 하나라도 보이면 true
- `reason`: 사진에 보이는 한국어 한 문장 (프롬프트를 베끼지 않기). 알림이 아닌 판정에서 프롬프트 문구를 베낀 `reason`은 코드가 지움
- `face_down` (프롬프트): 아기가 배를 대고 엎드려 있으면 매트리스·소파·바닥·어른 위든 해당. 얼굴이 옆으로 돌아가 있거나 가려져도 셈. 등을 대고 눕거나 옆으로 눕거나 앉거나 세워 안은 경우는 아님
- 알림이 아니면 `rule`은 null, 알림이면 `rule`은 네 가지 중 하나
- 머리가 프레임 밖이라는 이유만으로 `face_cover`로 하지 않기

위의 `normalize` 보정은 Qwen 경로에도 똑같이 적용됩니다. Qwen 판정의 정확도는 **검증 안 됨**입니다.

## 알림 동작

모든 푸시는 텍스트뿐이며 `--send`일 때만 나갑니다. `--send` 없이 돌리면 `action: dry`로 표시만 합니다.

| 종류 | 제목 | 본문 | 우선순위 |
| --- | --- | --- | --- |
| 규칙 알림 | `아기` | `{규칙 문구}. {reason}. Tapo 확인` (`reason`이 비면 `{규칙 문구}. Tapo 확인`) | 5 |
| 오류 알림 | `감시` | 카메라: `캠 화면을 못 받음. 전원, 와이파이, Tapo 앱을 확인하세요` (캡처 실패), `감시가 끊김. Tapo 앱과 PC를 확인하세요` (그 외). 모델: `판정 모델 오류. llama-server나 Jev 서버, PC를 확인하세요` | 5 |
| 복구 알림 | `감시` | `감시 복구됨 (캠)` 또는 `감시 복구됨 (모델)` | 5 |
| 하트비트 | `감시` | `감시 중. 이 알림이 끊기면 PC와 캠을 확인하세요` | 3 |

- **쿨다운**: 같은 규칙은 10분(600초)에 한 번만 알립니다. 규칙별로 따로 셉니다. 메모리에만 있어서 프로세스를 다시 켜면 초기화됩니다. 전송에 실패하면 쿨다운을 시작하지 않습니다.
- **오류 알림**: 캡처(카메라)와 모델 실패를 따로 셉니다. 연속 3번 실패하면 푸시하고, 계속 실패하면 30분마다 다시 보냅니다. 성공하면 복구 알림을 한 번 보내고 카운터를 0으로 되돌립니다. `--image` 한 장 모드에는 오류 알림이 없습니다.
- **하트비트**: `WATCH_HEARTBEAT_SEC`(기본 6시간)마다 "감시 중"을 보냅니다. 카메라와 모델이 모두 정상이고 마지막 판정이 180초 이내일 때만 보냅니다. `0`이면 하트비트만 끕니다.
- **전송 실패**: 규칙 알림 전송이 실패하면 `action: send_fail`. 오류·하트비트 푸시 실패는 stderr에 예외 이름만 출력합니다.

틱마다 한 줄 JSON을 출력하고 `frames/`에 기록합니다.

```json
{"n":1,"ts":"00:46:57","action":"sent","should_alert":true,"baby_present":true,"face_visible":false,"rule":"face_cover","reason":"","motion":true,"skipped":false}
```

`action` 값: `skip`(움직임 없어 모델 생략), `error`, `quiet`(알림 아님), `cooldown`, `sent`, `send_fail`, `dry`(`--send` 없음). 판정 오류일 때 `reason`에는 예외 이름이 들어갑니다.

`--rtsp` 루프의 틱에는 `camera` 칸이 추가됩니다: 캡처 성공 판정은 `"ok"`, 캡처 실패 오류 틱은 `"down"` (`--image` 한 장에는 없음). GUI의 카메라 표시가 이 값을 읽습니다.

이 틱 기록(`status.json`, `status.html`, `ticks.jsonl`)에는 `scores`와 `face_down_parts`가 없습니다 (코드가 위 키만 씁니다). 점수는 따로 `logs/scores.jsonl`에 남습니다 ([점수 기록](#점수-기록)).

## 점수 기록

모델을 부른 판정마다 한 줄(JSON)을 `logs/scores.jsonl`에 추가합니다 (`score_log.py`). 점수를 모아 기준을 정하려는 용도입니다. 기본으로 켜져 있고 `CRIB_SCORE_LOG=off`로 끕니다.

- **누가 쓰나**: 감시 루프(`watch.py`, `source: watch`. `--image` 한 장도 포함)와 GUI의 파일·붙여넣기 테스트(`source: gui-test`). 움직임이 없어 건너뛴 틱과 모델 오류는 기록하지 않습니다.
- **언제 쓰나**: 판정 직후에 씁니다. 쿨다운이나 `--send` 여부와 상관없습니다. `alert` 칸은 `should_alert` 값이지 푸시가 실제로 나갔다는 뜻이 아닙니다.
- **한 줄의 내용**: `type`, 짧은 `id`(6자리 16진수), `time`(+09:00 같은 오프셋 포함), `source`, `model`, `scores`, `face_down_parts`(볼/등/배 원값), `thresholds`(Jev일 때 규칙별 기준), `alert`, `rule`, `reason`(300자까지), `image`(GUI 테스트의 파일 이름 또는 `clipboard`), `frame`(저장된 사진 파일 이름 또는 null). Qwen은 점수가 없어 `scores`가 비고 `thresholds`는 null입니다.
- **회전**: 5MB를 넘으면 `scores.jsonl.1`, `.2`로 넘기고 최대 3개만 유지합니다.
- **실패해도 판정은 계속**: 기록 실패는 판정을 막지 않고, 경고를 stderr에 한 번만 냅니다.
- GUI의 "기록" 목록(`frames/ticks.jsonl`)과는 별개입니다.

### 사진 저장 (기본 꺼짐)

기본으로는 점수와 글자만 기록하고 **사진은 저장하지 않습니다.** 아래 환경 변수를 켠 경우에만 판정한 프레임을 `logs/frames/<시각>_<id>.jpg`(JPEG 품질 90)로 저장합니다.

- `CRIB_LOG_SAVE_FRAMES=1`: 판정한 사진 전부
- `CRIB_LOG_SAVE_AMBIGUOUS=0.3-0.7`: 규칙 점수가 그 범위에 든 사진만 (애매한 사진만 모으려는 용도)
- `CRIB_LOG_MAX_FRAMES` (기본 200): 넘으면 오래된 것부터 삭제

> **저장된 사진은 아기 사진이 이 PC에 그대로 남는 것입니다.** `logs/`는 `.gitignore`에 있어 커밋되지 않지만, 폴더를 통째로 공유·백업·업로드하지 않도록 조심하고 필요 없어지면 지우세요.

### 보기와 라벨

```text
python score_log.py --tail 20                 # 최근 20줄 표 (옵션이 없으면 최근 20줄)
python score_log.py --csv                     # CSV 전체. --tail N과 같이 쓰면 최근 N줄
python score_log.py --label <id> real prone   # 그 줄에 메모(라벨) 추가
```

표 열: 시각, id, 모델, 엎드림(`face_down`), 볼, 등, 배, 입코(`face_cover`), 알림(`ALERT`), 사진 유무(`y`), 라벨. 라벨은 기록 파일을 고치지 않고 `type: label` 줄을 덧붙이는 방식이고, 같은 id에 여러 번 달면 마지막 것이 보입니다. 없는 id면 오류로 끝나고, 로그가 아직 없으면 `no log yet`을 출력하고 종료 코드 1입니다. 라벨 내용은 사람이 붙이는 메모일 뿐 판정에 쓰이지 않습니다. `python score_log.py`를 인자 없이 실행하면 자체 검사입니다.

## 파일 구조

| 파일 | 역할 |
| --- | --- |
| `watch.py` | CLI. 캡처 루프(ffmpeg), 움직임 게이트 호출, 쿨다운, 오류·하트비트 알림, `frames/` 기록 |
| `motion.py` | 픽셀 차이 움직임 게이트 (`PIXEL_DELTA=25`, `MIN_CHANGED=0.02`) |
| `judge.py` | `CRIB_MODEL`로 Jev/Qwen 선택 |
| `jev_protocol.py` | Jev 클라이언트. 질문 8개, 엎드림 단서 3개 → 두 번째로 높은 값, 점수 → 규칙 변환, 규칙별 기준, loopback 강제 |
| `imajev_serve.py` | imajev playground 서버 실행기 (SelectorEventLoop, 이미지 prefill 공유) |
| `score_log.py` | 판정 점수 기록(`logs/scores.jsonl`), 사진 저장 옵션, `--tail/--csv/--label` CLI |
| `qwen_client.py` | llama-server 클라이언트, JSON 파싱, `normalize` 보정, 이미지 축소 |
| `prompt.txt` | Qwen용 프롬프트 (Jev 경로는 쓰지 않음) |
| `ntfy_alert.py` | ntfy 텍스트 푸시. 빈 메시지와 `\x00` 포함 메시지는 거부 |
| `crib_gui.py` | Windows 창 (서버·감시 시작/중지, 미리보기, 기록, 트레이, 한 장 테스트, 점수와 엎드림 원값 표시) |
| `ntfy.env.example`, `rtsp.env.example` | 설정 예시. 실제 `ntfy.env`, `rtsp.env`는 커밋 금지 |
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

## 개인정보와 보안

- **프레임은 이 PC의 loopback(`127.0.0.1`) 서버로만 보냅니다.** Jev 클라이언트는 http loopback 외의 주소를 거부하고, 리다이렉트를 따라가지 않습니다 (`http.client` 사용). Qwen 주소는 `127.0.0.1:8080`으로 고정입니다. `api.typesafe.ai` 등 클라우드로 보내지 않습니다.
- **ntfy에는 사진이 없고 텍스트만 갑니다.** 다만 기본 서버가 `https://ntfy.sh`(외부 서비스)이므로, 규칙 문구와 Qwen이 쓴 `reason`(사진에 보이는 내용을 설명한 문장)은 그 서버를 거칩니다. 자체 서버를 쓰려면 `NTFY_SERVER`를 바꾸세요.
- **ntfy 토픽은 사실상 비밀번호입니다.** 토픽만 알면 누구나 구독할 수 있고 인증 기능은 코드에 없습니다. 길고 무작위한 값을 쓰고 공유하지 마세요. 자동 생성 값은 `crib-` + 무작위 32자입니다. 의심되면 새 토픽으로 바꾸세요.
- **이미지가 디스크에 남지 않는 것은 아닙니다.**
  - `--rtsp` 모드는 마지막 프레임을 `frames/latest.jpg`로 저장하고 매번 덮어씁니다 (GUI 미리보기가 이 파일을 읽음). 이 파일 하나 말고 과거 프레임은 기본으로 보관하지 않습니다.
  - GUI의 `파일 테스트`/`붙여넣기` 사진은 기본으로 저장하지 않습니다.
  - `CRIB_LOG_SAVE_FRAMES` 또는 `CRIB_LOG_SAVE_AMBIGUOUS`를 켜면 판정한 사진이 `logs/frames/`에 **쌓입니다** (기본 꺼짐, 최대 `CRIB_LOG_MAX_FRAMES`장, 기본 200). 아기 사진이 이 PC에 그대로 남으니 공유하거나 올리지 마세요. 사진은 ntfy로는 가지 않습니다.
  - 글자 기록은 사진이 없어도 남습니다: `frames/ticks.jsonl`(`reason` 텍스트), `logs/scores.jsonl`(점수, `reason`, GUI 테스트에서 고른 파일 이름). 파일 이름에 개인 정보가 있을 수 있습니다. 둘 다 기본으로 켜져 있고, 둘 다 5MB 넘으면 로테이션해 최대 3개 파일만 유지합니다. GUI의 "기록" 목록은 현재 `ticks.jsonl`만 읽으므로 로테이션 직후에는 줄 수가 적을 수 있습니다.
- **RTSP 자격증명은 저장소에 없습니다.** `rtsp.env`는 `.gitignore`에 있고, 예시 파일만 커밋됩니다. 다만 ffmpeg를 실행할 때 자격증명이 들어간 URL을 명령줄 인자로 넘기므로, 같은 PC의 다른 사용자가 프로세스 목록에서 볼 수 있습니다.
- 카메라 `stream1`(고정 화면)만 쓰도록 되어 있고 `stream6`(PTZ)은 코드가 거부합니다. RTSP, ffmpeg, 감시 루프, 모델 서버를 외부 인터넷에 열지 마세요.
- 공개 저장소·이슈에 아기 사진(`logs/frames/` 포함), RTSP URL, `ntfy.env`, 토픽, 알림 기록, 점수 기록(`logs/scores.jsonl`)을 올리지 마세요. 자세한 내용은 [SECURITY.md](SECURITY.md).

## 한계와 알려진 문제

- **`face_down`(엎드림) 판정은 현재 불안정합니다.** 사용자가 엎드린 아기 사진으로 직접 테스트했을 때 Jev 점수가 낮게 나왔고, 알림 임계값 0.60 미만인 경우가 많았습니다. 이 결과는 사용자 실측 보고이며 저장소에는 해당 테스트 데이터가 없습니다. 기준 미만 구간은 알림이 되지 않으므로(코드로 확인) 이런 사진은 알림 없이 지나갈 수 있습니다. **보조 수단으로만 쓰고 안전 보장으로 믿지 마세요.**
  - 현재 코드는 엎드림을 단서 3개의 두 번째로 높은 값으로 계산합니다. 이 방식이 점수를 올렸는지, 반대로 단서가 하나만 높은 실제 엎드림을 놓치는지는 이 저장소로는 **검증 안 됨**입니다 (자체 검사는 합성 점수로 계산식만 확인).
  - `CRIB_JEV_ALERT_AT_FACE_DOWN`으로 기준을 낮출 수 있지만 오탐도 늘 수 있습니다. 어떤 값이 맞는지는 **검증 안 됨**이며, 점수 기록과 라벨로 직접 모아 봐야 합니다.
- **이 도구는 의료기기·안전장치가 아닙니다.** 어떤 규칙이든 놓칠 수 있고 오탐도 있을 수 있습니다. 실제 모델 정확도는 **검증 안 됨**입니다. 성인 감독을 대체하지 않습니다.
- Jev 임계값(기본 0.60/0.30/0.50)은 보정된 값이 아닙니다 (코드 주석). Jev는 문장을 만들지 않으므로 `reason`이 비고, 푸시에는 규칙 문구만 갑니다 (`face_cover`/`face_down`인데 아기가 안 보이면 "아기가 안 보임"이 붙음).
- 움직임이 없으면 모델을 부르지 않습니다. 변화가 임계값(2%) 미만이어도 60초마다는 판정하므로, 그 사이의 변화는 최대 60초 늦게 알 수 있습니다. 판정 자체의 시간은 이 README에서 주장하지 않습니다.
- 알림 쿨다운은 메모리에만 있어 재시작하면 초기화됩니다. 알림이 계속될 때는 같은 규칙으로 10분마다 다시 갑니다.
- 캡처·이미지 읽기·모델 호출 중 일반 예외는 오류 틱으로 기록하고 루프를 계속 돌지만, 설정 오류(`CRIB_MODEL`이 잘못됨, `CRIB_JEV_URL`이 loopback이 아님, `CRIB_JEV_ALERT_AT_<RULE>`가 범위 밖)는 `SystemExit`라서 감시 프로세스가 종료됩니다.
- 기본 경로(`D:\Dev\...`, `C:\_AX\...`)가 작성자 PC 기준이라 다른 PC에서는 환경 변수(`IMAJEV_DIR`, `IMAJEV_MODELS`, `IMAJEV_PY`, `LLAMA_SERVER`, `QWEN_DIR`, `IMAJEV_CHECK_FRAME`)로 바꿔야 합니다. 서버 포트(8090/8080)는 GUI에 고정입니다. GUI는 Windows 전용이고, 한 번에 한 모델 서버만 켜도록 막혀 있습니다.
- ntfy 인증, 사진 첨부, 사진 보관 기능은 없습니다.
- 캡처한 프레임은 판정 전에 Pillow로 끝까지 읽어 확인합니다. 깨졌거나 너무 작으면(한 변 32px 미만) 모델을 부르지 않고 캡처 오류(`grab failed`, `camera: down`)로 처리해 연속 3번이면 푸시합니다. 정상 프레임의 동작은 그대로입니다. 가짜 ffmpeg 파일로만 시험했고 실제 카메라는 **검증 안 됨**.
- `ffmpeg`가 없으면 캡처 오류가 `FileNotFoundError`로 표시되고 카메라 오류 일반 문구가 갑니다 (코드상 `GrabError`로 바뀌지 않음).

## 문제 해결

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
| `jev url must be http loopback...` | `CRIB_JEV_URL`이 `http://127.0.0.1:포트/v1/systemone` 형태인지 확인 |
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

## License

MIT. [LICENSE](LICENSE) 참고.
