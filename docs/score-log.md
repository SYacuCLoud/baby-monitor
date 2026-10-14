# 점수 기록

모델을 부른 판정마다 한 줄(JSON)을 `logs/scores.jsonl`에 추가합니다 (`score_log.py`). 점수를 모아 기준을 정하려는 용도입니다. 기본으로 켜져 있고 `CRIB_SCORE_LOG=off`로 끕니다.

- **누가 쓰나**: 감시 루프(`watch.py`, `source: watch`. `--image` 한 장도 포함)와 GUI의 파일·붙여넣기 테스트(`source: gui-test`). 움직임이 없어 건너뛴 번과 모델 오류는 기록하지 않습니다.
- **언제 쓰나**: 판정 직후에 씁니다. 쿨다운이나 `--send` 여부와 상관없습니다. `alert` 칸은 `should_alert` 값이지 푸시가 실제로 나갔다는 뜻이 아닙니다.
- **한 줄의 내용**: `type`, 짧은 `id`(6자리 16진수), `time`(+09:00 같은 오프셋 포함), `source`, `model`, `scores`, `thresholds`(Jev일 때 규칙별 기준), `alert`, `rule`, `reason`(300자까지), `image`(GUI 테스트의 파일 이름 또는 `clipboard`), `frame`(저장된 사진 파일 이름 또는 null). 원격 Jev로 판정한 행에만 `backend: "remote"`가 붙습니다 (`frames/ticks.jsonl`의 기록도 마찬가지). 주소와 토큰은 기록하지 않습니다. Qwen은 점수가 없어 `scores`가 비고 `thresholds`는 null입니다.
- **회전**: 5MB를 넘으면 `scores.jsonl.1`, `.2`로 넘기고 최대 3개만 유지합니다.
- **실패해도 판정은 계속**: 기록 실패는 판정을 막지 않고, 경고를 stderr에 한 번만 냅니다.
- GUI의 "기록" 목록(`frames/ticks.jsonl`)과는 별개입니다.

### 사진 저장 (기본 꺼짐)

기본으로는 점수와 글자만 기록하고 **사진은 저장하지 않습니다.** 아래 환경 변수를 켠 경우에만 판정한 프레임을 `logs/frames/<시각>_<id>.jpg`(JPEG 품질 90)로 저장합니다.

- `CRIB_LOG_SAVE_FRAMES=1`: 판정한 사진 전부
- `CRIB_LOG_SAVE_AMBIGUOUS=0.3-0.7`: 규칙 점수가 그 범위에 든 사진만 (애매한 사진만 모으려는 용도)
- `CRIB_LOG_MAX_FRAMES` (기본 200): 넘으면 오래된 것부터 삭제

> **저장된 사진은 아기 사진이 이 PC에 그대로 남는 것입니다.** `logs/`는 `.gitignore`에 있어 커밋되지 않지만, 폴더를 통째로 공유·백업·업로드하지 않도록 조심하고 필요 없어지면 지우세요.

권한: POSIX(Linux·macOS)에서는 기록 폴더와 `frames/`를 `0700`, 기록 파일(회전된 `.1`, `.2` 포함)과 저장 사진을 `0600`으로 만들며(chmod 실패는 무시, 기록은 멈추지 않음), 이미 있던 폴더의 권한은 바꾸지 않고, Windows에서는 chmod가 거의 효과가 없어 폴더 ACL에 의존합니다.

### 조회와 메모

```text
python score_log.py --tail 20                 # 최근 20줄 표 (옵션이 없으면 최근 20줄)
python score_log.py --csv                     # CSV 전체. --tail N과 같이 쓰면 최근 N줄
python score_log.py --label <id> real prone   # 그 줄에 메모(라벨) 추가
```

표 열: 시각, id, 모델, 입코(`face_cover`), 난간(`climbing`), 알림(`ALERT`), 사진 유무(`y`), 라벨. 라벨은 기록 파일을 고치지 않고 `type: label` 줄을 덧붙이는 방식이고, 같은 id에 여러 번 달면 마지막 것이 보입니다. 없는 id면 오류로 끝나고, 로그가 아직 없으면 `no log yet`을 출력하고 종료 코드 1입니다. 라벨 내용은 사람이 붙이는 메모일 뿐 판정에 쓰이지 않습니다. `python score_log.py`를 인자 없이 실행하면 자체 검사입니다.
