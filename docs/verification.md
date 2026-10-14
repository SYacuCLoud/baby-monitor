# 검사

코드에 들어 있는 자체 검사는 가짜 서버와 합성 그림으로 돌립니다. 통과해도 실제 아기 사진에서 맞는다는 뜻이 **아닙니다**.

```text
python motion.py
python jev_protocol.py
python qwen_client.py
python crib_gui.py --check
python score_log.py
python score_folder.py --selftest
```

카메라 감시, GPU 위 모델, ntfy.sh 전달, 창을 띄운 채 클릭은 이 저장소에서 자동으로 확인하지 않습니다.
