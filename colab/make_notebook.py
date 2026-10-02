"""Generate colab/imajev_colab.ipynb (nbformat 4). Run: python colab/make_notebook.py [--check]

--check regenerates in memory and fails if the committed notebook differs (keeps the .ipynb in sync).
The notebook only calls colab/start_colab.py; it holds no token and no tunnel URL.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "imajev_colab.ipynb"

TITLE = """\
# Jev 서버를 Colab에서 돌리기 (아기 감시 PC용)

**먼저 읽으세요**
- 이 노트북은 imajev(Jev) 서버를 Colab GPU에서 켜고, **토큰이 있어야만** 쓸 수 있는 공개 터널(Cloudflare quick tunnel) 주소를 만듭니다.
- 아기 사진이 집 밖(공개 터널 → Google Colab)으로 나갑니다. 토큰과 주소는 **공유하지 마세요**. 셀 출력에 토큰이 찍히므로 노트북을 공유하기 전에 출력을 지우세요.
- 무료 Colab 런타임은 놀고 있으면 끊깁니다. **테스트·점수 측정용**으로 쓰고, 알림의 유일한 경로로 쓰지 마세요. 로컬 서버를 기본으로 두세요.
- 아래 셀을 **위에서 아래로 차례로** 실행합니다. 런타임 유형은 **T4 GPU**.
- 이 노트북 전체를 Colab에서 실행해 본 적은 **없습니다 (검증 안 됨)**. 각 단계는 손으로 따로 확인된 것입니다.
"""

PARAM = """\
# 1) 설정. 4B 권장(업스트림 기본). 2B는 VRAM 약 4.6GB, 4B는 약 9.9GB.
MODEL = '4b'  # '2b' 또는 '4b'
REPO = 'https://github.com/SYacuCLoud/baby-monitor.git'
BRANCH = 'main'  # PR 머지 전에 시험하려면 'colab-remote-jev'
"""

CLONE = """\
# 2) 이 저장소에서 colab/ 스크립트만 받아 옵니다.
import os, subprocess, sys
if not os.path.isdir('baby-monitor'):
    subprocess.run(['git', 'clone', '--depth', '1', '-b', BRANCH, REPO, 'baby-monitor'], check=True)
sys.path.insert(0, os.path.abspath('baby-monitor/colab'))
import start_colab as sc
"""

INSTALL = """\
# 3) imajev 설치와 모델 내려받기 (처음에는 10분 안팎, 다시 실행해도 안전)
sc.install(MODEL)
"""

LAUNCH = """\
# 4) 서버 + 게이트웨이(토큰 검사) + 터널을 켜고, PC에 넣을 두 줄을 출력합니다.
state = sc.launch(MODEL)
"""

HEALTH = """\
# 5) 상태 확인 (30초마다 한 줄). 터널이 죽으면 줄 끝에 <<< 표시가 붙습니다. 멈추려면 셀을 중단하세요.
sc.health_loop(state)
"""

STOP = """\
# 6) 다 쓰면 끄세요. (런타임 > 세션 연결 해제도 됩니다)
sc.stop(state)
"""

CELLS = [
    ("markdown", TITLE), ("code", PARAM), ("code", CLONE), ("code", INSTALL),
    ("code", LAUNCH), ("code", HEALTH), ("code", STOP),
]


def _lines(text: str) -> list[str]:
    parts = text.splitlines(keepends=True)
    if parts and parts[-1].endswith("\n"):
        parts[-1] = parts[-1][:-1]
    return parts


def build() -> dict:
    cells = []
    for i, (kind, text) in enumerate(CELLS):
        cell = {"cell_type": kind, "id": hashlib.sha1(f"{i}{text}".encode()).hexdigest()[:8],
                "metadata": {}, "source": _lines(text)}
        if kind == "code":
            cell["execution_count"] = None
            cell["outputs"] = []
        cells.append(cell)
    return {
        "cells": cells,
        "metadata": {
            "accelerator": "GPU",
            "colab": {"gpuType": "T4", "provenance": []},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=1) + "\n"


def check_valid(nb: dict) -> None:
    """Minimal nbformat v4 structure check (no nbformat dependency)."""
    assert nb["nbformat"] == 4 and isinstance(nb["cells"], list) and nb["cells"]
    ids = set()
    for c in nb["cells"]:
        assert c["cell_type"] in ("markdown", "code") and isinstance(c["source"], list)
        assert isinstance(c["metadata"], dict) and c["id"] not in ids
        ids.add(c["id"])
        if c["cell_type"] == "code":
            assert c["outputs"] == [] and c["execution_count"] is None
            compile("".join(c["source"]), "<cell>", "exec")  # syntax check
        assert len(c["source"]) <= 12  # keep cells small
    text = json.dumps(nb)
    assert "trycloudflare.com" not in text and "CRIB_JEV_TOKEN" not in text and "Bearer" not in text


if __name__ == "__main__":
    nb = build()
    check_valid(nb)
    if "--check" in sys.argv:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != render():
            raise SystemExit("imajev_colab.ipynb is out of date: run python colab/make_notebook.py")
        print("ok notebook")
    else:
        OUT.write_text(render(), encoding="utf-8")
        print(f"wrote {OUT}")
