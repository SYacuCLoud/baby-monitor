"""Start imajev's playground server with asyncio SelectorEventLoop and a shared image prefill.

1. uvicorn picks ProactorEventLoop on Windows. On this PC that reset ~30% of loopback
   replies (WinError 10054, server log WinError 10022). Selector: 0.
2. imajev's torch backend runs one full forward per question, so the image tokens are
   computed once per question. score_shared() runs the image and the common prompt
   prefix once, then each question continues from a copy of that cache with only its own
   tail tokens. Same weights, same decision position; see check_shared().

  <imajev venv python> imajev_serve.py          serve on 8090
  <imajev venv python> imajev_serve.py --check  shared vs original logits (frame: IMAJEV_CHECK_FRAME)
  (the author's PC uses D:/Dev/imajev/.venv/Scripts/python.exe; paths are env-overridable)
Not a medical device.
"""

from __future__ import annotations

import os
import sys

IMAJEV = os.environ.get("IMAJEV_DIR", r"D:\Dev\imajev")
MODELS = os.environ.get("IMAJEV_MODELS", r"D:\Dev\_Models")
PORT = os.environ.get("CRIB_JEV_PORT", "8090")
CHECK_FRAME = os.environ.get("IMAJEV_CHECK_FRAME", r"C:\_AX\baby-monitor\baby-monitor-private\frames-live\latest.jpg")
ADAPTER = os.path.join(MODELS, "imajev-2b")

sys.path[:0] = [os.path.join(IMAJEV, "src"), os.path.join(IMAJEV, "scripts"), os.path.join(IMAJEV, "scripts", "playground")]
os.environ.setdefault("HF_HUB_OFFLINE", "1")
_original_score = None


def score_shared(self, images, request, thinking=None):
    """TorchBackend.score with one prefill for the image and the prompt prefix every question shares."""
    from time import perf_counter
    from transformers import DynamicCache
    from vision_decision.scoring import compile_question, result_from_logits

    if (thinking is not None and thinking.active) or self.rotations != 1 or not images:
        return _original_score(self, images, request, thinking)
    torch, engine = self.torch, self.engine
    start = perf_counter()
    prepared = []
    for field in request.fields:
        header, choices, texts = compile_question(field, request.state, self.prompt_layout)
        labels = engine.labels(len(choices), len(images))
        prompt = header + "\n".join(f"{label}: {text}" for label, text in zip(labels, texts))
        _, inputs, token_ids = engine.prepare(images, prompt, labels)
        prepared.append((choices, inputs, token_ids))
    rows = [p[1]["input_ids"][0].tolist() for p in prepared]
    shared = min(len(r) for r in rows) - 1  # every question keeps at least its decision position
    for i in range(shared):
        if len({r[i] for r in rows}) != 1:
            shared = i
            break
    base = engine._base()
    m = base.model
    visual = {base.config.image_token_id, getattr(base.config, "video_token_id", -1)}
    if shared < 1 or any(t in visual for r in rows for t in r[shared:]):
        return _original_score(self, images, request, thinking)  # image not inside the shared prefix
    lm = m.language_model
    with torch.inference_mode():
        first = {k: v.to(engine.device) for k, v in prepared[0][1].items()}
        embeds, positions = engine._embeds_positions(first)  # vision tower runs once here
        prefix = DynamicCache(config=lm.config)
        lm(inputs_embeds=embeds[:, :shared], position_ids=positions[:, :, :shared], past_key_values=prefix, use_cache=True)
        # All question tails in one batch, right-padded: every layer is causal (full attention, gated DeltaNet and its
        # short conv), so tokens after a row's decision position cannot change it. Read each row at its own last index.
        tails, tail_pos = [], []
        for _, inputs, _ in prepared:
            ids = inputs["input_ids"].to(engine.device)
            pos, _ = m.get_rope_index(ids, image_grid_thw=inputs["image_grid_thw"].to(engine.device),
                                      mm_token_type_ids=inputs["mm_token_type_ids"].to(engine.device))
            tails.append(ids[0, shared:])
            tail_pos.append(pos[:, 0, shared:])
        width = max(len(t) for t in tails)
        pad = engine.processor.tokenizer.pad_token_id or 0
        batch_ids = torch.full((len(tails), width), pad, dtype=tails[0].dtype, device=engine.device)
        batch_pos = torch.empty((3, len(tails), width), dtype=tail_pos[0].dtype, device=engine.device)
        for row, (t, p) in enumerate(zip(tails, tail_pos)):
            n = len(t)
            batch_ids[row, :n] = t
            batch_pos[:, row, :n] = p
            batch_pos[:, row, n:] = p[:, -1:] + torch.arange(1, width - n + 1, device=engine.device)
        prefix.reorder_cache(torch.zeros(len(tails), dtype=torch.long, device=engine.device))  # batch 1 -> N copies
        states = lm(inputs_embeds=m.get_input_embeddings()(batch_ids), position_ids=batch_pos,
                    past_key_values=prefix, use_cache=True).last_hidden_state
        results, tokens = [], shared + width
        for row, ((choices, _, token_ids), t) in enumerate(zip(prepared, tails)):
            hidden = states[row, len(t) - 1]
            idx = engine._readout_indices(token_ids)
            scored = (engine.readout(hidden.float())[idx] if engine.readout is not None and idx is not None
                      else hidden.float() @ base.lm_head.weight[torch.tensor(token_ids, device=engine.device)].float().T)
            results.append(result_from_logits(choices, [float(x) for x in scored.cpu().tolist()], token_ids=token_ids))
        del prefix, states
    usage = {"prefill_ms": 0.0, "questions_ms": round((perf_counter() - start) * 1000, 1), "input_tokens": tokens,
             "shared_prefix_tokens": shared, "rotations": self.rotations}
    return results, usage


def _install():
    import server
    global _original_score
    if _original_score is None:
        _original_score = server.TorchBackend.score
        server.TorchBackend.score = score_shared
    return server


def check_shared(frame=None):
    """Shared-prefix logits must match imajev's own per-question forward on a real frame."""
    import base64
    frame = frame or CHECK_FRAME
    from time import perf_counter
    from PIL import Image
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import jev_protocol as J
    from qwen_client import _jpeg_b64
    from vision_decision.images import load_image_bytes

    server = _install()
    os.chdir(IMAJEV)
    backend = server.build_backend("torch", ADAPTER)
    b64 = _jpeg_b64(Image.open(frame))
    images = [load_image_bytes(base64.b64decode(b64))[0]]
    payload = {k: v for k, v in J.build_request(b64, "imajev-2b").items() if k not in ("images", "model")}
    request, _ = server.compile_payload(payload, backend.max_options)
    timed = {}
    for name, fn in (("original", _original_score), ("shared", score_shared)):
        fn(backend, images, request)  # warm-up
        t = perf_counter()
        results, usage = fn(backend, images, request)
        timed[name] = (perf_counter() - t, results, usage)
    worst = 0.0  # bf16: a different kernel order moves probabilities slightly, so compare them, not the argmax of near ties
    for field, a, b in zip(request.fields, timed["original"][1], timed["shared"][1]):
        assert list(a.scores) == list(b.scores)
        diff = max(abs(a.scores[k] - b.scores[k]) for k in a.scores)
        print(f"  {field.id:14s} " + " ".join(f"{k}={a.scores[k]:.3f}/{b.scores[k]:.3f}" for k in a.scores) + f"  diff {diff:.3f}")
        worst = max(worst, diff)
    print(f"original {timed['original'][0]:.2f}s  shared {timed['shared'][0]:.2f}s  "
          f"shared_prefix_tokens {timed['shared'][2]['shared_prefix_tokens']}  max_prob_diff {worst:.3f}")
    assert worst < 0.05, worst
    print("ok imajev-shared-prefix")


if __name__ == "__main__":
    if "--check" in sys.argv:
        check_shared()
        raise SystemExit
    import uvicorn

    _run = uvicorn.run

    def _selector_run(app, **kw):
        kw["loop"] = "asyncio:SelectorEventLoop"
        return _run(app, **kw)

    uvicorn.run = _selector_run
    server = _install()
    os.chdir(IMAJEV)
    server.main(["--backend", "torch", "--adapter", ADAPTER,
                 "--calibration", os.path.join(ADAPTER, "calibration.json"),
                 "--model-name", "imajev-2b", "--host", "127.0.0.1", "--port", PORT])
