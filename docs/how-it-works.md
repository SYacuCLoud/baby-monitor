# 동작 방식

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
   imajev_serve.py, 짧은 noul 질문            llama-server, prompt.txt, JSON
                     └───────────────┬───────────────┘
                                     ├─▶ 점수 한 줄 기록 (score_log.py → logs/scores.jsonl)
                       normalize (qwen_client.py) 가드
                                     │
                  규칙별 10분 쿨다운 ─▶ ntfy 텍스트 푸시 (--send일 때만)
```

1. **캡처** (`watch.py`): `ffmpeg -rtsp_transport tcp`로 한 프레임을 `frames/latest.jpg`에 저장합니다. 루프 간격은 15초입니다.
2. **움직임 게이트** (`motion.py`): 그레이스케일 픽셀 차이가 25를 넘는 픽셀이 전체의 2% 이상이면 "움직임 있음"입니다. 직전 프레임과, 마지막으로 판정한 프레임을 둘 다 비교합니다 (천천히 변하는 장면을 놓치지 않기 위해). 첫 프레임이거나 마지막 판정 후 60초가 지났으면 변화가 없어도 판정합니다.
3. **판정** (`judge.py`): `CRIB_MODEL`이 `jev`(기본) 또는 `qwen`입니다. 그 외 값이면 `CRIB_MODEL must be qwen or jev`로 종료합니다. 프레임은 최대 긴 변 1280px, JPEG 품질 85로 줄여서 보냅니다.
   - **Jev**: 짧은 noul 질문의 점수(0~1)를 받아 코드가 알림 여부를 정합니다. `face_cover`는 `face_in_bed`와 `mouth_nose_blocked`의 최댓값입니다. 규칙 점수가 기준(기본 0.60) 이상이면 알림입니다 ([판정 규칙](rules.md)). Jev는 문장을 쓰지 않으므로 `reason`은 비어 있습니다.
   - **Qwen**: `prompt.txt`와 사진을 한 메시지로 보내고 JSON 한 개를 받습니다. 모델이 `should_alert`를 정하고, 코드가 `normalize`로 보정합니다. 점수는 없습니다.
4. **기록** (`score_log.py`): 모델을 부른 판정(감시 루프, `--image` 한 장, GUI의 파일·붙여넣기 테스트)마다 점수 한 줄을 `logs/scores.jsonl`에 추가합니다. 움직임이 없어 건너뛴 틱과 모델 오류는 기록하지 않습니다.
5. **알림** (`ntfy_alert.py`): `should_alert`이고 같은 규칙의 쿨다운(10분)이 지났으면 ntfy에 텍스트를 보냅니다.
