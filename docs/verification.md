# 검증 상태

이 README의 내용은 코드를 읽고, 아래 자체 검사를 실행해서 확인한 것입니다.

| 항목 | 상태 |
| --- | --- |
| `motion.py`, `qwen_client.py`, `jev_protocol.py`, `judge.py`, `score_log.py`, `watch.py`(인자 없이) 자체 검사 | 통과 (합성 데이터, 가짜 로컬 서버) |
| GUI 화면(tkinter) | **검증 안 됨**. 이 PC에는 tkinter가 없어 화면을 띄워 보지 못함. 로직(`remote_settings.py`)은 시험했고 `crib_gui.py`는 `py_compile`과 가짜 tkinter 모듈로만 확인 |
| `crib_gui.py --check` | 통과. tkinter가 있어야 실행됨 (확인할 때는 tkinter를 가짜 모듈로 대체) |
| ntfy 전송 형식 (JSON, 제목 `아기`, 우선순위 5) | 로컬 가짜 서버로 확인. 실제 ntfy.sh 전달은 **검증 안 됨** |
| 실제 모델(Jev·Qwen)의 규칙별 판정 정확도 | **검증 안 됨** (저장소에 실제 사진 테스트 없음) |
| 점수 기록 (`logs/scores.jsonl`, 환경 변수, `--tail/--csv/--label`, 사진 저장 옵션) | 가짜 Jev 서버(고정 점수)와 `watch.main()`으로 동작 확인. 실제 모델 출력으로는 **검증 안 됨** |
| 규칙별 기준 `CRIB_JEV_ALERT_AT_<RULE>` | 가짜 Jev 서버로 동작과 잘못된 값(`2`)의 종료 확인. 실제 모델 점수에 맞는 값은 **검증 안 됨** |
| 실제 카메라 RTSP 루프 (`--rtsp`) | **검증 안 됨** (ffmpeg·카메라 없이는 못 돌림) |
| imajev 서버 (`imajev_serve.py`, `--check`), llama-server 실행 | **검증 안 됨** (GPU·모델 필요) |
| Windows GUI·트레이·서버 시작/중지 | **검증 안 됨** (Windows 전용 코드, Linux에서는 실행 불가) |
| 속도·VRAM 수치 | 이 README에서 주장하지 않음 (질문이 8개가 된 뒤의 속도도 **검증 안 됨**) |
