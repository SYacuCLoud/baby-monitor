# Local crib monitor

로컬 PC에서 침대 사진을 Jev 또는 Qwen에 물어보고, 규칙에 걸리면 ntfy 텍스트만 보냅니다.

**의료기기가 아닙니다.** 알림이 없다고 안전한 것이 아닙니다.

## 알림

- `face_cover` 입코 가림 (천, 또는 엎드리거나 옆으로 누워 얼굴이 매트/베개에 파묻힘)
- `climbing` 난간
- `empty` 아기·어른 없음

엎드림은 별도 규칙이 아닙니다. 기준 기본값 0.60.

## 실행 (Windows)

```text
pip install -r requirements.txt
python crib_gui.py
```

실행 파일은 `RUNTIME.txt`. 전부 `runtime/` 아래. Jev 8090, Qwen 8080. 8GB라 동시에 켜지 마세요.
창을 닫으면 트레이, 종료는 트레이 메뉴. 푸시에 사진 없음.

비밀은 `rtsp.env`, `ntfy.env`만. 커밋 금지. `SECURITY.md`.

MIT.
