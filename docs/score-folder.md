# 사진 폴더 점수 측정

`score_folder.py`는 폴더 안의 사진을 지금 켜져 있는 판정 서버(Jev 또는 Qwen)에 한 장씩 보내 점수를 표로 보여주는 **읽기 전용**입니다. 알림 기준을 잡을 때 참고할 숫자를 모으는 용도입니다. GUI의 "사진 한 장 테스트"와 같은 경로(`Image.open(...).convert("RGB")` → `judge.ask`)를 그대로 쓰고, HTTP 코드를 따로 갖고 있지 않습니다.

**파일 이름 규칙**: 이름이 `<접두어>_`로 시작하면 그 접두어가 정답 태그입니다 (대소문자 무시). 예: `엎드림_01.jpg`, `prone_02.png`.

| 접두어 | 태그 | 접두어 | 태그 |
| --- | --- | --- | --- |
| `엎드림_` / `prone_` | prone | `안김_` / `held_` | held |
| `정상_` / `normal_` | normal | `빈침대_` / `empty_` | empty |
| `옆으로_` / `side_` | side | `앉음_` / `sitting_` | sitting |

접두어가 없거나 모르는 이름은 태그 `?`로 표시하고 요약의 임계값 계산에서 뺍니다. 확장자는 jpg, jpeg, png, bmp, webp만 읽습니다.

```text
python score_folder.py <폴더> [--model jev|qwen] [--csv out.csv]
python score_folder.py --selftest
```

- 판정 서버를 먼저 켜 두어야 합니다 (`--model` 기본값은 `jev`).
- 표: 파일 이름순으로 `face_cover`, `climbing`, baby/adult/face, 알림 yes/no, 정답 태그. 파일 이름 접두어 `엎드림_`/`prone_`은 입코 가림이 높아야 하는 그룹입니다.
- 사진 한 장이 실패하면 그 줄에 `ERR`과 이유만 표시하고 나머지는 계속합니다. 전부 실패하면 종료 코드 1입니다.
- 요약: 태그별 장수와 `face_cover`의 최소/평균/최대. 힌트 범위는 자동 적용하지 않습니다.
- 사진은 이 PC에만 있고 어디에도 올리거나 푸시하지 않습니다. 푸시 알림, `frames/`(ticks, status, latest.jpg), `logs/scores.jsonl` 모두 건드리지 않습니다. 파일을 쓰는 것은 `--csv`를 준 경우의 그 CSV 하나뿐입니다 (엑셀용 UTF-8 BOM).
- 자체 검사는 가짜 판정 함수와 임시 폴더로 표, 요약, CSV, 5장 규칙을 확인한 것입니다. **실제 모델(Jev·Qwen)로 돌린 결과는 여기서 확인하지 않았습니다.**
