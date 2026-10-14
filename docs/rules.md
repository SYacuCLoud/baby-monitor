# 판정 규칙

규칙은 세 가지뿐입니다. 수면, 이불이 몸만 덮은 경우, 장난감은 알림이 아닙니다. 모르면 알리지 않습니다.
엎드림은 별도 규칙이 아닙니다. 얼굴이 매트/베개에 파묻히면 `face_cover`입니다.

| rule | 푸시 문구 | 의미 |
| --- | --- | --- |
| `face_cover` | 입코 가림 | 천이 입과 코를 가리거나, 엎드리거나 옆으로 누워 얼굴이 매트/베개에 파묻힘 |
| `climbing` | 난간 기어오름 | 아기가 난간 위에 있음. 아기가 보여야 알림 |
| `empty` | 아기 없음 | 아기도 어른도 없음. 아기가 보이면 알림 취소 |

## Jev (`jev_protocol.py`)

질문은 짧습니다. 긴 문장은 2B가 0.5 근처로 뭉갭니다. `face_cover` 점수는 `face_in_bed`와 `mouth_nose_blocked`의 최댓값입니다.

| 키 | 질문 |
| --- | --- |
| `baby_present` | Is a baby visible in this crib frame? |
| `adult_present` | Is an adult visible in this frame? An adult is not a baby. |
| `face_visible` | Is any skin of an eye, cheek, mouth, or nose visible? |
| `face_in_bed` | Is the baby lying face-down or on their side, with the face in the mattress or pillow? |
| `mouth_nose_blocked` | Is fabric covering the baby's mouth and nose? |
| `climbing` | Is the baby on the crib rail? |

코드가 `should_alert`를 정합니다. 기준 기본값 0.60 (`CRIB_JEV_ALERT_AT_<RULE>`로 규칙별 변경).

| 조건 | 결과 |
| --- | --- |
| `face_cover` 또는 `climbing` 점수가 기준 이상 | 알림. 점수가 더 높은 규칙 |
| 두 규칙 모두 < 0.30 이고 아기·어른도 < 0.30 | 알림, `empty` |
| 그 외 | 알리지 않음 |

`climbing`은 아기가 안 보이면 취소. `face_cover`는 아기가 안 보여도 유지하고 본문에 "아기가 안 보임"을 붙입니다. 응답의 `usage.images`가 1개가 아니면 판정을 버립니다.

## Qwen (`prompt.txt`)

점수가 없습니다. 모델이 JSON으로 `should_alert`와 `rule`을 줍니다. 허용 규칙은 위 세 가지와 null입니다. 같은 `normalize`가 적용됩니다.
