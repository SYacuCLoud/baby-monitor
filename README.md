# Local crib monitor

로컬 PC에서 침대 카메라(또는 사진 한 장)를 Jev 또는 Qwen에 물어보고, 규칙에 걸리면 **ntfy 텍스트만** 보냅니다. 개인용입니다.

> **의료기기도 안전장치도 아닙니다.** 성인 감독을 대체하지 않습니다. 알림이 없다고 안전한 것이 아닙니다. 모델 정확도는 검증되지 않았습니다.

하지 않는 일: 울음·호흡·체온 감지, 사진 푸시, 클라우드 API, 학습, 사이렌.

| 규칙 | 의미 |
| --- | --- |
| `face_cover` | 입코 가림. 천, 또는 엎드리거나 옆으로 누워 얼굴이 매트/베개에 파묻힘 |
| `climbing` | 난간 |
| `empty` | 아기·어른 없음 |

엎드림은 별도 규칙이 아닙니다. 알림 기준 기본값 0.60.

## Quick start (Windows)

```text
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python crib_gui.py
```

창에서 Jev 또는 Qwen 서버를 켠 뒤 감시를 시작합니다. 실행 파일은 `runtime/` 아래입니다 (`RUNTIME.txt`). Jev `127.0.0.1:8090`, Qwen `127.0.0.1:8080`. 8GB GPU라 둘 다 켜지 마세요.

창을 닫으면 트레이입니다. 종료는 트레이 메뉴. 푸시에는 사진이 없습니다. 비밀은 `rtsp.env`, `ntfy.env`만 쓰고 커밋하지 마세요.

CLI:

```text
python watch.py --image photo.jpg
python watch.py --rtsp --send
```

푸시는 `--send`(GUI의 푸시 체크)일 때만 나갑니다.

## 문서

자세한 내용은 주제별 파일입니다. 기능을 없애면 그 파일만 지웁니다.

- [하는 일](docs/what.md) · [동작 방식](docs/how-it-works.md) · [판정 규칙](docs/rules.md)
- [설치와 실행](docs/setup.md) · [필요한 것](docs/requirements.md) · [설정](docs/settings.md)
- [알림](docs/alerts.md) · [점수 기록](docs/score-log.md) · [사진 폴더](docs/score-folder.md)
- [원격 Jev](docs/remote-jev.md) · [파일](docs/files.md) · [보안](docs/privacy.md)
- [한계](docs/limits.md) · [문제 해결](docs/troubleshooting.md) · [검증](docs/verification.md)
- [SECURITY.md](SECURITY.md)

## License

[MIT](LICENSE).
