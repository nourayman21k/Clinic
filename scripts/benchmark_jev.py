"""Benchmark the WhatsApp agent's System 1 router (app/whatsapp_agent/router.py)
on the 100 labelled patient messages in benchmarks/jev/questions.py.

  .venv\\Scripts\\python.exe scripts/benchmark_jev.py --quant 8bit     # router only: free, local
  .venv\\Scripts\\python.exe scripts/benchmark_jev.py --quant bf16     # same, full precision, for comparison
  .venv\\Scripts\\python.exe scripts/benchmark_jev.py --quant 8bit --live 10

Router metrics: label accuracy, confident-pick precision, fallback rate, coverage
(is the tool the message needs actually bound?), tool-schema tokens bound per
message vs all 10 (o200k tokenizer, close to gpt-oss's), latency, peak VRAM.

--live N: a bounded A/B through Groq on the agent's FIRST call for N messages
(one per label): all tools vs the routed scope, real prompt/completion tokens
and latency, and which tool each picked. No tool is executed, nothing is
written. Kept small on purpose: the free tier's daily token cap is shared
with the live WhatsApp bot.
"""
import argparse
import importlib.util
import json
import os
import statistics
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "benchmarks", "jev", "results")


def load_questions():
    spec = importlib.util.spec_from_file_location("questions", os.path.join(ROOT, "benchmarks", "jev", "questions.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.QUESTIONS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quant", default="8bit", choices=["8bit", "4bit", "bf16"])
    parser.add_argument("--live", type=int, default=0)
    args = parser.parse_args()

    os.environ["JEV_QUANTIZATION"] = args.quant
    os.environ["WHATSAPP_AGENT_USE_JEV"] = "1"
    sys.path.insert(0, ROOT)
    import tiktoken
    import torch
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from app.whatsapp_agent import router
    from app.whatsapp_agent.tools import tools

    enc = tiktoken.get_encoding("o200k_base")
    schema_tokens = {t.name: len(enc.encode(json.dumps(convert_to_openai_tool(t), ensure_ascii=False))) for t in tools}
    all_tokens = sum(schema_tokens.values())

    torch.cuda.reset_peak_memory_stats()
    started = time.time()
    if router.get_decider() is None:
        sys.exit(f"Router failed to load: {router._load_error}")
    load_seconds = time.time() - started

    rows = []
    for expected, text, previous in load_questions():
        t = time.time()
        d = router.route(text, previous, None)
        latency = time.time() - t
        bound = router.ALL_TOOLS if (not d["selected_tools"] and not d["general_chat"]) else d["selected_tools"]
        rows.append({
            "expected": expected, "text": text, "has_context": previous is not None,
            "label": d["jev_label"], "confidence": round(d["jev_confidence"], 4),
            "correct": d["jev_label"] == expected,
            "confident": d["jev_confidence"] >= (router.GENERAL_CHAT_THRESHOLD if d["jev_label"] == "general_chat"
                                                 else router.CONFIDENCE_THRESHOLD),
            "fallback_all_tools": not d["selected_tools"] and not d["general_chat"],
            "covered": expected == "general_chat" or expected in bound,
            "tools_bound": len(bound), "tool_tokens": sum(schema_tokens[n] for n in bound),
            "latency": round(latency, 4),
        })
        print(f"{'OK ' if rows[-1]['correct'] else 'BAD'} {d['jev_confidence']:.2f} {expected:>26} <- {d['jev_label']:<26} {text}")

    confident = [r for r in rows if r["confident"]]
    latencies = sorted(r["latency"] for r in rows)
    per_label = {}
    for r in rows:
        p = per_label.setdefault(r["expected"], [0, 0])
        p[0] += r["correct"]
        p[1] += 1
    summary = {
        "model": router.JEV_MODEL, "quantization": args.quant, "threshold": router.CONFIDENCE_THRESHOLD,
        "questions": len(rows),
        "accuracy_pct": round(100 * sum(r["correct"] for r in rows) / len(rows), 1),
        "accuracy_with_context_pct": round(100 * sum(r["correct"] for r in rows if r["has_context"]) /
                                           max(1, sum(r["has_context"] for r in rows)), 1),
        "confident_picks": len(confident),
        "confident_precision_pct": round(100 * sum(r["correct"] for r in confident) / max(1, len(confident)), 1),
        "fallback_all_tools_pct": round(100 * sum(r["fallback_all_tools"] for r in rows) / len(rows), 1),
        "coverage_pct": round(100 * sum(r["covered"] for r in rows) / len(rows), 1),
        "tool_tokens_all": all_tokens,
        "tool_tokens_mean_routed": round(statistics.mean(r["tool_tokens"] for r in rows), 1),
        "tool_tokens_saved_pct": round(100 * (1 - statistics.mean(r["tool_tokens"] for r in rows) / all_tokens), 1),
        "latency_p50_ms": round(1000 * statistics.median(latencies), 1),
        "latency_p95_ms": round(1000 * latencies[int(0.95 * (len(latencies) - 1))], 1),
        "load_seconds": round(load_seconds, 1),
        "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1024 ** 3, 2),
        "accuracy_by_label_pct": {k: round(100 * c / n, 1) for k, (c, n) in per_label.items()},
    }
    if args.live:
        summary["live"] = live_ab(args.live, router)

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"router_{args.quant}.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def live_ab(n: int, router) -> dict:
    """First agent call, all tools vs routed scope, on n messages (one per label)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.whatsapp_agent import graph

    seen, picks = set(), []
    for expected, text, previous in load_questions():
        if previous is None and expected not in seen:
            seen.add(expected)
            picks.append((expected, text))
    picks = picks[:n]

    results = []
    for expected, text in picks:
        decision = router.route(text, None, None)
        messages = [SystemMessage(content=graph._dated_system_prompt()), HumanMessage(content=text)]
        entry = {"expected": expected, "text": text, "jev_label": decision["jev_label"]}
        for name, model in (("all_tools", graph.llm_full_with_tools), ("routed", graph._model_for(decision))):
            t = time.time()
            reply = model.invoke(messages)
            usage = reply.usage_metadata or {}
            calls = [c["name"] for c in (reply.tool_calls or [])]
            entry[name] = {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                           "latency": round(time.time() - t, 2), "tool_called": calls[0] if calls else "none"}
        results.append(entry)
        print(f"live {expected}: all={entry['all_tools']['input_tokens']} tok/{entry['all_tools']['tool_called']} "
              f"routed={entry['routed']['input_tokens']} tok/{entry['routed']['tool_called']}")

    def total(key, field):
        return sum(r[key][field] or 0 for r in results)

    return {
        "messages": len(results),
        "input_tokens_all_tools": total("all_tools", "input_tokens"),
        "input_tokens_routed": total("routed", "input_tokens"),
        "input_tokens_saved_pct": round(100 * (1 - total("routed", "input_tokens") / max(1, total("all_tools", "input_tokens"))), 1),
        "mean_latency_all_tools_s": round(total("all_tools", "latency") / len(results), 2),
        "mean_latency_routed_s": round(total("routed", "latency") / len(results), 2),
        "same_tool_choice": sum(r["all_tools"]["tool_called"] == r["routed"]["tool_called"] for r in results),
        "rows": results,
    }


if __name__ == "__main__":
    main()
