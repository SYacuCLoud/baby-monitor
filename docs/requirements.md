# 필요한 것

- Python 3 (`from __future__ import annotations` 사용). 자체 검사는 Python 3.13에서 돌려 봤습니다. 지원하는 가장 낮은 버전은 **확인하지 않았습니다**.
- `pip install -r requirements.txt` → `Pillow>=10,<12`, `numpy>=1.26,<3`
- [ffmpeg](https://ffmpeg.org/download.html): `--rtsp` 캡처에 필요합니다. `ffmpeg`가 PATH에 있어야 합니다.
- 판정 서버 중 하나 (둘 다 로컬, 동시에 필요하지 않음):
  - **Jev (기본)**: [imajev](https://github.com/mohit67890/imajev) 저장소와 `imajev-2b` 어댑터(`calibration.json` 포함). 이 저장소의 `imajev_serve.py`가 imajev의 playground 서버를 불러 씁니다. imajev의 의존성(torch 등)은 imajev 쪽 venv에 설치해야 하며 `requirements.txt`에는 없습니다. 설치 방법은 imajev 문서를 따르세요.
  - **Qwen**: [llama.cpp](https://github.com/ggml-org/llama.cpp)의 `llama-server`, Qwen3-VL-4B-Instruct `Q4_K_M` GGUF와 `mmproj-F16.gguf`. 가중치는 저장소에 없습니다. 직접 받으세요.
- ntfy 앱 (푸시를 받을 폰). 기본 서버는 `https://ntfy.sh`입니다.
- **GUI (`crib_gui.py`)**: Windows 전용입니다 (`netstat`, `powershell`, `taskkill`, `win32gui` 사용). tkinter가 필요합니다. 트레이 아이콘은 `pywin32`가 있어야 만들어지는데 `requirements.txt`에는 없습니다. 없으면 창은 뜨지만 트레이는 없습니다.
- 작성자 환경은 Windows 11 + 8GB GPU로 보입니다 (GUI가 "8GB라 같이 못 올립니다"라고 두 서버를 동시에 켜지 못하게 함). CLI 쪽은 OS 종속 코드가 없지만 Windows 밖에서의 전체 동작은 **확인하지 않았습니다**.
