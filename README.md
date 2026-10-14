# 아기 침대 감시

아기 침대에 단 카메라를 이 컴퓨터에서 감시합니다. 입이나 코가 가려졌거나 아기가 난간에 올라가 있으면 휴대전화로 알림 문구만 옵니다. 사진 한 장을 넣어서 시험해 볼 수도 있습니다.

이건 의료기기가 아닙니다. 아기를 혼자 두는 용도로 쓰지 마세요. 알림이 안 왔다고 괜찮은 상황이 아니고, 모델이 얼마나 정확한지도 여기서 확인한 적이 없습니다. 울음이나 숨, 체온은 재지 않고, 알림에 사진도 넣지 않습니다.

## 어떤 때 알림이 오나

| 이름 | 뜻 |
| --- | --- |
| `face_cover` | 입코 가림. 이불이 입과 코를 덮었거나, 엎드리거나 옆으로 누워 얼굴이 매트나 베개에 파묻힌 때 |
| `climbing` | 난간에 올라감 |
| `empty` | 아기와 어른이 모두 없음 |

얼굴을 매트에 묻고 자는 것만 따로 알리지는 않습니다. 그때는 입코 가림으로 봅니다. 점수가 0.60을 넘으면 알립니다.

## 쓰는 법

윈도우에서 돌아갑니다. 파이썬 3이 필요하고, 카메라를 쓰려면 ffmpeg도 있어야 합니다. 판정은 Jev 아니면 Qwen입니다.

```text
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python crib_gui.py
```

창이 뜨면 서버를 켠 뒤 감시를 시작하면 됩니다. 서버 프로그램은 `runtime` 폴더에 있고, 파일 이름은 `RUNTIME.txt`에 적어 두었습니다.

Jev는 `127.0.0.1:8090`, Qwen은 `127.0.0.1:8080`을 씁니다. 그래픽카드 메모리가 8GB라서 둘을 한꺼번에 올리면 안 됩니다.

창을 닫으면 시계 옆 알림 영역으로 내려갑니다. 끄려면 거기서 종료를 고르면 됩니다.

카메라 비밀번호와 ntfy 토픽은 `rtsp.env`, `ntfy.env`에만 적습니다. 저장소에 올리지 마세요. 빈 양식은 `rtsp.env.example`, `ntfy.env.example`입니다.

창 없이 명령만 쓰려면 이렇게 합니다.

```text
python watch.py --image photo.jpg
python watch.py --rtsp --send
```

`--send`를 빼면 휴대전화로 가지 않습니다. 창의 푸시 칸과 같은 뜻입니다.

## 자세한 설명

- [설치](docs/setup.md)
- [어떻게 돌아가나](docs/how-it-works.md)
- [판정 규칙](docs/rules.md)
- [환경 변수](docs/settings.md)
- [알림이 나가는 방식](docs/alerts.md)
- [점수 기록](docs/score-log.md)
- [사진 폴더 채점](docs/score-folder.md)
- [원격 Jev](docs/remote-jev.md)
- [한계](docs/limits.md)
- [안 될 때](docs/troubleshooting.md)
- [비밀을 공개 이슈에 적지 말 것](SECURITY.md)

라이선스는 [MIT](LICENSE)입니다.
