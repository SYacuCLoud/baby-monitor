# Local crib monitor

로컬 PC에서 침대 사진 한 장씩을 판정 모델(Jev 또는 Qwen)에 물어보고, 규칙에 걸리면 **텍스트 푸시(ntfy)** 를 보내는 개인용 감시 도구입니다.

> **의료기기도, 안전장치도 아닙니다. 성인 감독을 대체하지 않습니다. 신생아를 혼자 두지 마세요.**
> 알림이 오지 않는다고 안전하다는 뜻이 아닙니다. 모델 정확도는 이 저장소의 테스트로 검증되지 않았습니다.

하지 않는 일: 울음·호흡·체온 감지, 사진 전송, 클라우드 API 호출, 모델 학습, 사이렌.

## 문서

주제별로 나눠 두었습니다. 기능을 없애면 해당 파일만 지웁니다.

- [하는 일](docs/what.md)
- [동작 방식](docs/how-it-works.md)
- [검증 상태](docs/verification.md)
- [필요한 것](docs/requirements.md)
- [설치와 실행](docs/setup.md)
- [설정](docs/settings.md)
- [판정 규칙](docs/rules.md)
- [알림 동작](docs/alerts.md)
- [점수 기록](docs/score-log.md)
- [사진 폴더 점수 측정](docs/score-folder.md)
- [원격 Jev](docs/remote-jev.md)
- [파일 구조](docs/files.md)
- [개인정보와 보안](docs/privacy.md)
- [한계와 알려진 문제](docs/limits.md)
- [문제 해결](docs/troubleshooting.md)
- [실행 파일](RUNTIME.txt) · [SECURITY.md](SECURITY.md)

## 바로 실행 (Windows)

```text
pip install -r requirements.txt
python crib_gui.py
```

실행 파일은 `runtime/` 아래 (`RUNTIME.txt`). Jev 8090, Qwen 8080. 8GB라 동시에 켜지 마세요.
창을 닫으면 트레이, 종료는 트레이 메뉴. 푸시에 사진 없음. 비밀은 `rtsp.env` / `ntfy.env`.

MIT.
