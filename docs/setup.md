# 설치

Windows에서 쓰는 프로그램입니다. 트레이와 서버 중지는 Windows API를 씁니다.

## 1. Python 패키지

```text
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt`에는 Pillow와 numpy만 있습니다. Jev(torch)와 llama-server는 여기 없습니다.

## 2. 비밀 파일

`rtsp.env.example`을 `rtsp.env`로 복사하고 카메라 계정, 비밀번호, 호스트, 경로(`stream1`)를 채웁니다. 값에 따옴표를 넣지 마세요.

`ntfy.env.example`을 `ntfy.env`로 복사하고 `NTFY_TOPIC`을 길고 알아보기 어렵게 만듭니다. 파일이 없으면 푸시를 보내는 시점에 `crib-`로 시작하는 값을 만들어 두기도 합니다. 폰의 ntfy 앱에서 그 토픽을 구독하세요.

두 파일은 `.gitignore`에 있습니다. 커밋하지 마세요.

## 3. 판정 서버

GUI의 서버 시작이 아래를 띄웁니다. 직접 켜도 됩니다.

Jev (기본), 포트 8090:

```text
runtime\imajev\.venv\Scripts\python.exe imajev_serve.py
```

가중치와 imajev 소스는 `runtime/imajev`, `runtime/models`입니다. 다른 위치면 `IMAJEV_DIR`, `IMAJEV_MODELS`, `IMAJEV_PY`를 바꿉니다.

Qwen, 포트 8080:

```text
runtime\llama-server\llama-server.exe -m runtime\models\Qwen3-VL-4B\Qwen3-VL-4B-Instruct-Q4_K_M.gguf --mmproj runtime\models\Qwen3-VL-4B\mmproj-F16.gguf -ngl 99 --mmproj-offload --image-min-tokens 1024 --host 127.0.0.1 --port 8080 -c 4096
```

경로가 다르면 `LLAMA_SERVER`, `QWEN_DIR`을 씁니다. 자세한 목록은 [환경 변수](settings.md)와 `RUNTIME.txt`입니다.

한 번에 서버 하나만 켜세요.

## 4. 창 또는 명령줄

```text
python crib_gui.py
python watch.py --rtsp --send
python watch.py --image photo.jpg
```

인자 없이 각 스크립트를 실행하면 자체 검사가 돌아갑니다. `python crib_gui.py --check`는 창을 띄우지 않고 검사만 합니다.
