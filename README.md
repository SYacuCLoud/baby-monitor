# Local crib monitor

로컬 PC에서 침대 사진을 보고, 네 가지 규칙만 JSON으로 판정한 뒤 **텍스트 푸시**를 보냅니다.

**의료기기가 아닙니다. 성인 감독을 대체하지 않습니다. 신생아를 혼자 두지 마세요.**

학습하지 않습니다. 아기 영상을 클라우드 API로 보내지 않습니다. ntfy에는 사진이 없습니다.

## 하는 일

1. `stream1` TCP 한 장, 또는 파일 (`--image`)
2. numpy 픽셀 차이로 “움직임 있나”만 봄 (`motion.py`)
3. 로컬 모델이 JSON 한 줄. 기본은 Qwen. `CRIB_MODEL=jev`이면 루프백 Jev
4. `should_alert`이면 ntfy 텍스트. 같은 규칙은 10분 쿨다운

알림 제목은 항상 `아기`. 본문 예: `입코 가림. {보이는 것}. Tapo 확인`

흐름 요약: [docs/architecture.html](docs/architecture.html)

## 하지 않는 일

- 울음·호흡·체온 감지
- 아기 영상 학습 / 데이터셋
- ngrok, WAN 포트포워드, RTSP 공개
- ntfy에 사진
- 아기방 사이렌
- Abliterated 가중치, 8B BF16, Ollama로 같은 GGUF 재다운로드
- MiniCPM-V를 규칙 모델로 쓰기 (캡션은 빠르고 JSON 규칙은 실패함)

## 규칙

`prompt.txt`가 전부입니다. 이것만 알림입니다.

| rule | 의미 |
| --- | --- |
| `face_cover` | 이불/베개/옷이 입이나 코를 가림 |
| `face_down` | 얼굴이 매트리스에 파묻힘 |
| `climbing` | 난간을 타고 기어오름 |
| `empty` | 아기 없고 어른도 없음 |

그 외(수면, 이불이 몸만 덮음, 장난감)는 알림 없음. 모르면 `should_alert`는 false.

출력:

```json
{"should_alert":false,"baby_present":false,"face_visible":false,"rule":null,"reason":""}
```

- `face_visible`: 눈·볼·입·코 피부 중 하나라도 보이면 true
- `reason`: 예시 문구 복사가 아니라, 사진에 보이는 한국어 한 문장

## 상태 (2026-08)

- Qwen health + 파일 한 장 dry-run: 동작
- ntfy 한국어 텍스트: 동작
- 캠 RTSP 루프: `watch.py --rtsp` (15초, 움직임 없으면 VLM 생략)

## 필요

### Python

- Windows, Python 3.11+
- `pip install -r requirements.txt` (`Pillow`, `numpy`)

### 시스템

- [ffmpeg](https://ffmpeg.org/download.html) — `ffmpeg`가 PATH에 있어야 `watch.py --rtsp`가 한 장을 캡처합니다. Windows면 빌드 zip을 풀고 `bin`을 PATH에 추가하세요.
- [llama.cpp](https://github.com/ggml-org/llama.cpp) CUDA 빌드 (`llama-server.exe`)

### 모델 (로컬 GGUF)

Qwen3-VL-4B Instruct **Q4_K_M** + mmproj. 예:

- 모델: Hugging Face에서 `Qwen3-VL-4B-Instruct` GGUF (Q4_K_M) 검색 후 다운로드  
  (커뮤니티 GGUF 미러가 여러 개 있으니, 파일명에 `Q4_K_M`과 `mmproj`가 맞는지 확인)
- mmproj: 같은 배포의 `mmproj-F16.gguf` (또는 안내된 vision projector)

가중치·mmproj는 이 저장소에 포함하지 않습니다. 용량이 크니 직접 받아 `llama-server` `-m` / `--mmproj` 경로에 넣으세요.

### Jev로 바꾸기

기본값은 그대로 Qwen입니다. 되돌릴 때는 `CRIB_MODEL`을 지우면 됩니다.

- `CRIB_MODEL=jev`
- `CRIB_JEV_URL` 기본값 `http://127.0.0.1:8090/v1/systemone` (8080은 llama-server)
- 서버: [imajev](https://github.com/mohit67890/imajev)를 `D:\Dev\imajev`에, 모델을 `D:\Dev\_Models\Qwen3.5-2B`·`imajev-2b`에 둡니다. 실행: `D:\Dev\imajev\.venv\Scripts\python.exe imajev_serve.py`
- `imajev_serve.py`는 uvicorn을 SelectorEventLoop으로 띄웁니다. Windows 기본 Proactor에서는 응답 약 30%가 WinError 10054로 끊겼습니다.
- 속도: 사진과 공통 프롬프트를 한 번만 계산하고(prefix 공유), 질문 6개 꼬리를 한 배치로 돌립니다. 판정 1회 3.4초 → 약 1초(HTTP 포함)입니다. 원본 경로와의 확률 차이는 0.02 이하이며 `imajev_serve.py --check`로 확인합니다.
- venv에 `triton-windows<3.7`, `flash-linear-attention`을 설치했습니다. `--fast`(CUDA 그래프)는 이 PC에서 오히려 느려서 쓰지 않습니다.
- 프레임은 최상위 `images`의 data URL로 보냅니다. 질문 6개는 noul입니다.
- 응답 `usage.images`가 1장이 아니면 서버가 사진을 안 본 것이라 판정을 버립니다 (텍스트 전용 Jev 차단).

`api.typesafe.ai`로는 보내지 않습니다. Jev는 문장을 쓰지 않아서 `reason`은 비고, 알림은 규칙 이름만 갑니다. 애매하면 알리지 않습니다 (`ALERT_AT=0.70`).

테스트한 PC: Win11, RTX 3070 Laptop 8GB, CUDA UMD 13.3. 이 GPU에서 7B vLLM은 건너뜀.

캠 조건: 고정 렌즈, 난간+매트리스가 한 프레임. 침대 난간에 달지 않음(선). PTZ는 사람이 볼 때만. 루프는 wide/fixed `stream1`만.

## 실행

1. `ntfy.env.example` → `ntfy.env`, `rtsp.env.example` → `rtsp.env`. 둘 다 커밋하지 않기. ntfy 토픽은 **길고 무작위**로 (짧은 토픽은 `ntfy.sh`에서 추측·구독 위험이 큼).

2. 의존성:

```text
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

3. llama-server (경로는 본인 GGUF로):

```text
llama-server.exe -m Qwen3-VL-4B-Instruct-Q4_K_M.gguf --mmproj mmproj-F16.gguf -ngl 99 --mmproj-offload --image-min-tokens 1024 --host 127.0.0.1 --port 8080 -c 4096
```

4. `curl http://127.0.0.1:8080/health` → `{"status":"ok"}`

5. 자체 검사:

```text
python motion.py
python qwen_client.py
python jev_protocol.py
python judge.py
python watch.py
python ntfy_alert.py --ping
```

6. 캠 한 장 / 루프 (푸시 없음이 기본. `--send`여야 폰):

```text
python watch.py --rtsp --once
python watch.py --rtsp
python watch.py --rtsp --send
python watch.py --image photo.jpg --once
```

통과 기준: 정면으로 누운 사진에서 `should_alert` false, `reason`이 얼굴이 보인다고 **실제로** 씀. 실패: `reason`이 `한 줄`, 시계, 플래그와 이유 모순.

`--send` 없이 돌리면 폰에 안 갑니다.

## 파일

| 파일 | 역할 |
| --- | --- |
| `prompt.txt` | VLM 규칙. 시스템 칸 비움. 사진+이 텍스트 한 메시지 |
| `qwen_client.py` | `127.0.0.1:8080` OpenAI 호환 chat. 기본 모델 |
| `judge.py` | `CRIB_MODEL=qwen`(기본) 또는 `jev` |
| `jev_protocol.py` | 로컬 `POST /v1/systemone`. 클라우드 거부 |
| `imajev_serve.py` | imajev-2b 서버 8090. SelectorEventLoop, prefix 공유 + 배치 |
| `motion.py` | absdiff. `PIXEL_DELTA=25`, `MIN_CHANGED=0.02` |
| `ntfy_alert.py` | 텍스트만. 빈 메시지·바이너리 거부 |
| `watch.py` | 파일 또는 `stream1` TCP 루프. 같은 규칙 10분 쿨다운 |
| `ntfy.env` | 로컬 토픽. git에 넣지 않음 |
| `rtsp.env` | LAN `stream1`만. git에 넣지 않음 |
| `requirements.txt` | Python 의존성 |
| `docs/architecture.html` | 파이프라인 요약 |
| `SECURITY.md` | 시크릿·취약점 보고 |

## 개인정보

프레임은 이 PC에서만 추론합니다. ntfy 본문에 사진·주소·토픽 설명 넣지 마세요. 공개 저장소에 아기 사진, RTSP URL, `ntfy.env`를 올리지 마세요. 자세한 내용은 [SECURITY.md](SECURITY.md).

## License

MIT. `LICENSE` 참고.
