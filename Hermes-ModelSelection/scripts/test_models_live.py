#!/usr/bin/env python3
"""
test_models_live.py — Tester Agent (AGENT:Tester)

Proves the placed models ACTUALLY WORK by sending a real inference request to
each one, in parallel. A ranking is a guess until a model answers; this is the
only stage that separates "listed" from "working".

Method: shells out to `hermes chat -q` per model. That is the authoritative
path — it uses the user's real credential pool, provider routing and
base_url handling instead of re-implementing auth (which would need raw API
keys the agent must never touch).

Usage:
  test_models_live.py [--out-dir DIR] [--timeout SECONDS] [--prompt TEXT]

Output: <out_dir>/verification.json
Exit 0 if the brain model responds; exit 4 if it does not (placement is
theoretical until the primary slot is proven live).

Parallelism: one subprocess per model, capped at --concurrency (default 4).
Each probe is bounded by --timeout so a hung provider cannot stall the run.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
DEFAULT_OUT = HERMES_HOME / "cache" / "hermes-modelselection"

DEFAULT_PROMPT = "Reply with exactly: PONG"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_placement(out_dir: Path) -> dict:
    path = out_dir / "placement.json"
    if not path.exists():
        raise SystemExit(f"[fatal] placement.json not found at {path} — run place_models.py first")
    with open(path) as f:
        return json.load(f)


def probe_one(slot: str, model: str, provider: str, prompt: str, timeout: int) -> dict:
    """Send one live inference request. Never raises — a failure is a result."""
    started = time.time()
    hermes = shutil.which("hermes")
    if not hermes:
        return {"slot": slot, "model": model, "provider": provider,
                "status": "error", "ok": False,
                "error": "hermes binary not on PATH", "latency_s": 0.0,
                "response": None}

    cmd = [hermes, "chat", "-q", prompt, "-m", model, "--provider", provider, "-Q"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            # Keep the probe from inheriting an interactive TTY expectation.
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return {"slot": slot, "model": model, "provider": provider,
                "status": "timeout", "ok": False,
                "error": f"no response within {timeout}s", "latency_s": timeout,
                "response": None}
    except Exception as e:  # noqa: BLE001 — a probe failure is data, not a crash
        return {"slot": slot, "model": model, "provider": provider,
                "status": "error", "ok": False, "error": str(e),
                "latency_s": round(time.time() - started, 2), "response": None}

    latency = round(time.time() - started, 2)
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    # hermes chat -q prints the reply plus a trailing session_id line and may
    # emit non-fatal warnings; strip those so we judge the model's actual words.
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    noise = ("session_id:", "Warning:", "warning:")
    reply_lines = [ln for ln in lines if not any(ln.strip().startswith(n) for n in noise)]
    reply = "\n".join(reply_lines).strip()

    ok = proc.returncode == 0 and bool(reply)
    status = "ok" if ok else ("empty_response" if proc.returncode == 0 else "nonzero_exit")

    return {"slot": slot, "model": model, "provider": provider,
            "status": status, "ok": ok,
            "error": None if ok else (stderr[:400] or "empty response"),
            "latency_s": latency, "response": reply[:400],
            "exit_code": proc.returncode}


def verify(out_dir: Path, prompt: str, timeout: int, concurrency: int) -> dict:
    placement = _load_placement(out_dir)
    chain = placement.get("chain", []) or []
    if not chain:
        raise SystemExit("[fatal] placement.json has an empty chain — nothing to test")

    targets = [(c["slot"], c["model"], c["provider"]) for c in chain]

    results: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(probe_one, s, m, p, prompt, timeout): (s, m, p)
                   for (s, m, p) in targets}
        for fut in cf.as_completed(futures):
            results.append(fut.result())

    order = {s: i for i, (s, _m, _p) in enumerate(targets)}
    results.sort(key=lambda r: order.get(r["slot"], 999))

    passed = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    brain = next((r for r in results if r["slot"] == "brain"), None)
    brain_ok = bool(brain and brain["ok"])

    out = {
        "generated_at": _now(),
        "prompt": prompt,
        "timeout_s": timeout,
        "concurrency": concurrency,
        "tested": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "brain_alive": brain_ok,
        "results": results,
        "verdict": ("ALL SLOTS LIVE" if not failed else
                    "PARTIAL — see failing slots" if passed else
                    "NO MODEL RESPONDED"),
        "recommended_action": _recommend(brain_ok, failed, results),
    }

    with open(out_dir / "verification.json", "w") as f:
        json.dump(out, f, indent=2)
    return out


def _recommend(brain_ok: bool, failed: list[dict], results: list[dict]) -> str:
    if brain_ok and not failed:
        return ("Placement is proven live. Apply with "
                "`hermes config set model.default <brain model>` and "
                "`hermes config set model.provider <brain provider>`.")
    if brain_ok:
        dead = ", ".join(f"{f['slot']}({f['provider']})" for f in failed)
        return (f"Brain is live, but these slots did not answer: {dead}. The chain still "
                f"works because fallbacks cover it — but do not rely on the dead slots "
                f"until their providers recover.")
    dead_all = ", ".join(f"{r['slot']}({r['provider']}): {r['status']}" for r in results)
    return (f"NO placed model responded — {dead_all}. Do NOT apply this placement. "
            f"Check credentials (`hermes auth list`) and provider status before retrying.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Live-test the placed models (AGENT:Tester)")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--timeout", type=int, default=90, help="Per-model timeout (s)")
    ap.add_argument("--concurrency", type=int, default=4, help="Parallel probes")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out = verify(out_dir, args.prompt, args.timeout, args.concurrency)

    print(f"[tester] probed {out['tested']} slots, prompt={args.prompt!r}, "
          f"timeout={args.timeout}s")
    print()
    print("LIVE VERIFICATION")
    print("-" * 78)
    for r in out["results"]:
        mark = "OK  " if r["ok"] else "FAIL"
        print(f"  [{mark}] {r['slot']:<10} {r['provider']:<12} "
              f"{r['latency_s']:>6.1f}s  {r['model']}")
        if not r["ok"]:
            err = (r.get("error") or "").replace("\n", " ")[:70]
            print(f"           ↳ {err}")
    print()
    print(f"[tester] verdict: {out['verdict']}")
    print(f"[tester] {out['recommended_action']}")
    print()
    print(f"[tester] wrote {out_dir / 'verification.json'}")

    return 0 if out["brain_alive"] else 4


if __name__ == "__main__":
    sys.exit(main())
