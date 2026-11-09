#!/usr/bin/env python3
"""Short real llama.cpp/Metal JSON-format smoke test for Qwen3.5 GGUF."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

MODEL_REVISION = "dc40af5d4666acd6ce1c1dd71ec61cee2f1c966e"
MODEL_FILENAME = "qwen3.5-9b-Q4_K_M.gguf"

def find_model() -> Path:
    snapshots = (Path.home() / ".cache" / "huggingface" / "hub"
                 / "models--gguf--Qwen3.5-9B-GGUF" / "snapshots")
    preferred = snapshots / MODEL_REVISION / MODEL_FILENAME
    if preferred.is_file():
        return preferred
    matches = sorted(snapshots.glob("*/" + MODEL_FILENAME)) if snapshots.is_dir() else []
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError("Qwen3.5 GGUF отсутствует в локальном Hugging Face cache: " + str(snapshots))
    raise RuntimeError(
        "Найдено несколько Qwen3.5 GGUF; ожидаемый pinned revision отсутствует: "
        + ", ".join(str(path) for path in matches)
    )

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args()
    if not 64 <= args.max_tokens <= 512:
        parser.error("--max-tokens должен быть в диапазоне 64..512")
    model_path = find_model()
    print("SMOKE_MODEL=" + str(model_path), flush=True)
    print("SMOKE_MAX_TOKENS=" + str(args.max_tokens), flush=True)
    from llama_cpp import Llama
    llm = Llama(model_path=str(model_path), n_gpu_layers=-1, n_ctx=4096, verbose=False)
    try:
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": "Ты — генератор строгого JSON. Верни один полный JSON-объект. Не пиши пояснений, рассуждений или Markdown."},
                {"role": "user", "content": 'Верни ровно объект {"ok":true,"backend":"llama.cpp"}. Не добавляй другие поля.'},
            ],
            max_tokens=args.max_tokens,
            temperature=0.0,
            stream=False,
        )
        choices = response.get("choices") or []
        choice = choices[0] if choices else {}
        raw = str((choice.get("message") or {}).get("content") or "")
        print("SMOKE_FINISH_REASON=" + str(choice.get("finish_reason")), flush=True)
        print("SMOKE_RAW_LENGTH=" + str(len(raw)), flush=True)
        print("SMOKE_FIRST_BRACE_OFFSET=" + str(raw.find("{")), flush=True)
        print("SMOKE_RAW_HEAD=" + raw[:1200], flush=True)
        print("SMOKE_RAW_TAIL=" + raw[-800:], flush=True)
        try:
            parsed = json.loads(raw[raw.find("{"):])
        except Exception as exc:
            print("SMOKE_JSON=FAILED: " + repr(exc), file=sys.stderr, flush=True)
            return 3
        if parsed != {"ok": True, "backend": "llama.cpp"}:
            print("SMOKE_JSON=FAILED: unexpected payload " + repr(parsed), file=sys.stderr, flush=True)
            return 4
        print("SMOKE_JSON=PASSED", flush=True)
        print("SMOKE_USAGE=" + json.dumps(response.get("usage"), ensure_ascii=False), flush=True)
        return 0
    finally:
        llm.close()

if __name__ == "__main__":
    raise SystemExit(main())
