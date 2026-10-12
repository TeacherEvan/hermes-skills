#!/usr/bin/env python3
"""
rank_models.py — Investigation Agent (AGENT:Investigation)

Reads every provider-<slug>.json produced by the AGENT:<Provider> swarm and
ranks all free models across providers using transparent, deterministic
heuristics. Emits a per-provider leaderboard plus a global ranking.

Scoring (0-100, all components documented so the ranking is auditable):

  capability   (0-35)  tool_call +15, reasoning +12, structured_output +8
  context      (0-25)  log-scaled: 25 * min(1, log2(ctx/32k) / log2(1M/32k))
  recency      (0-20)  full score at release within 6mo, linear decay to 0 at 24mo
  openness     (0-10)  open_weights +10 (verifiable weights = no silent roasts)
  size_hint    (0-10)  parameter hints parsed from the id/name (e.g. "120b", "31b")

Output: <out_dir>/ranking.json + a console leaderboard.

The Placement Agent consumes ranking.json to fill Brain/Workhorse/Tester slots
with the DIFFERENT-PROVIDER constraint enforced.

Usage:
  rank_models.py [--out-dir DIR] [--as-of YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERMES_HOME = Path.home() / ".hermes"
DEFAULT_OUT = HERMES_HOME / "cache" / "hermes-modelselection"

# Reference points for the log-scaled context score.
CTX_FLOOR = 32 * 1024      # 32k = minimum viable
CTX_CEIL = 1024 * 1024     # 1M = full score

# Qualitative scoring adjustments (added to base score, capped at 100)
QUAL_MAX_MULTIMODAL = 8    # +2 per non-text modality (image/video/pdf/audio)
QUAL_MAX_STRUCTURED = 5    # reliable JSON schema for coding/agentic
QUAL_MAX_OUTPUT_LIMIT = 3  # +3 for >100k, +2 for >50k, +1 for >32k
QUAL_MAX_OPEN_WEIGHTS = 2  # +2 for fully open weights (fine-tunable, auditable)

MULTIMODAL_CAPS = {
    "text": 0, "image": 2, "video": 2, "pdf": 2, "audio": 2
}

def _output_limit_bonus(limit: int | None) -> float:
    if not limit:
        return 0.0
    if limit >= 100_000:
        return 3.0
    if limit >= 50_000:
        return 2.0
    if limit >= 32_000:
        return 1.0
    return 0.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _months_since(release: str | None, as_of: date) -> float | None:
    if not release:
        return None
    try:
        d = datetime.strptime(release[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    return (as_of - d).days / 30.44


def _parse_size_hint(mid: str, name: str) -> float | None:
    """Extract a parameter-count hint like '120b', '31b', '2.6b', '30b-a3b'."""
    text = f"{mid} {name}".lower()
    # match '120b', '2.6b', '550b-a55b' -> take the first/leading number
    m = re.search(r"(\d+(?:\.\d+)?)\s*b(?:-|\b|$)", text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def score_model(m: dict, as_of: date) -> tuple[float, dict]:
    """Return (score, breakdown). Every component is additive and capped."""
    b: dict[str, float] = {}
    qual_details = []

    # --- capability (0-35) ---
    cap = 0.0
    if m.get("tool_call"):
        cap += 15
    if m.get("reasoning"):
        cap += 12
    if m.get("structured_output"):
        cap += 8
    b["capability"] = min(cap, 35)

    # --- context (0-25), log-scaled ---
    ctx = m.get("context") or 0
    if ctx <= 0:
        b["context"] = 0.0
    else:
        ratio = math.log2(max(ctx, CTX_FLOOR) / CTX_FLOOR) / math.log2(CTX_CEIL / CTX_FLOOR)
        b["context"] = round(25 * min(1.0, max(0.0, ratio)), 2)

    # --- recency (0-20): full at <=6mo, linear decay to 0 at 24mo ---
    age = _months_since(m.get("release_date") or m.get("last_updated"), as_of)
    if age is None:
        b["recency"] = 8.0          # unknown age = neutral mid-low score
    elif age <= 6:
        b["recency"] = 20.0
    elif age >= 24:
        b["recency"] = 0.0
    else:
        b["recency"] = round(20 * (24 - age) / 18, 2)

    # --- openness (0-10) ---
    b["openness"] = 10.0 if m.get("open_weights") else 0.0

    # --- size hint (0-10): log-scaled from 1b..550b ---
    size = _parse_size_hint(m.get("id", ""), m.get("name", ""))
    if size is None:
        b["size_hint"] = 3.0        # unknown = low-neutral
    else:
        ratio = math.log2(max(size, 1.0)) / math.log2(550.0)
        b["size_hint"] = round(10 * min(1.0, max(0.0, ratio)), 2)

    # --- qualitative adjustments (additive, cap total at 100) ---
    qual_adj = 0.0

    # Multimodal: +2 per non-text modality (max +8)
    modalities_obj = m.get("modalities")
    input_modalities = []
    if isinstance(modalities_obj, dict):
        input_modalities = modalities_obj.get("input", [])
    elif isinstance(modalities_obj, list):
        input_modalities = modalities_obj
    
    if input_modalities:
        multimodal_bonus = sum(MULTIMODAL_CAPS.get(mod, 0) for mod in input_modalities if mod != "text")
        if multimodal_bonus > 0:
            qual_adj += multimodal_bonus
            qual_details.append(f"multimodal({'+'.join(input_modalities)}):+{multimodal_bonus}")

    # Structured output: +5 for coding/agentic workflows
    if m.get("structured_output"):
        qual_adj += QUAL_MAX_STRUCTURED
        qual_details.append(f"structured_output:+{QUAL_MAX_STRUCTURED}")

    # Output limit: bonus based on adequacy for agent workflows
    output_limit = m.get("output_limit")
    if output_limit:
        out_bonus = _output_limit_bonus(output_limit)
        if out_bonus > 0:
            qual_adj += out_bonus
            qual_details.append(f"output_limit({output_limit}):+{out_bonus}")

    # Open weights: +2 for fully open (fine-tunable, auditable)
    if m.get("open_weights"):
        qual_adj += QUAL_MAX_OPEN_WEIGHTS
        qual_details.append(f"open_weights:+{QUAL_MAX_OPEN_WEIGHTS}")

    b["qualitative_adjustment"] = round(qual_adj, 2)
    b["qualitative_details"] = "; ".join(qual_details) if qual_details else ""

    base_total = round(sum(v for k, v in b.items() if k not in ("qualitative_adjustment", "qualitative_details")), 2)
    total = min(100.0, round(base_total + qual_adj, 2))
    return total, b


def load_provider_files(out_dir: Path) -> list[dict]:
    """Load every provider-*.json, skipping discovery.json."""
    files = sorted(out_dir.glob("provider-*.json"))
    results = []
    for f in files:
        try:
            data = json.load(open(f))
        except Exception as e:
            print(f"[warn] skipping unreadable {f.name}: {e}", file=sys.stderr)
            continue
        if data.get("error"):
            print(f"[warn] {f.name} carries an error: {data['error']}", file=sys.stderr)
        results.append(data)
    return results


def rank(out_dir: Path, as_of: date) -> dict:
    providers = load_provider_files(out_dir)
    if not providers:
        return {"error": "no provider-*.json files found", "out_dir": str(out_dir),
                "hint": "run discover_providers.py then list_provider_models.py first"}

    global_rank: list[dict] = []
    per_provider: dict[str, dict] = {}

    for pdata in providers:
        pinfo = pdata.get("provider", {})
        slug = pinfo.get("slug", "unknown")
        models = pdata.get("models", []) or []

        scored = []
        for m in models:
            score, breakdown = score_model(m, as_of)
            scored.append({
                        "id": m.get("id"),
                        "name": m.get("name"),
                        "provider": slug,
                        "provider_name": pinfo.get("name", slug),
                        "score": score,
                        "breakdown": breakdown,
                        "context": m.get("context"),
                        "reasoning": m.get("reasoning", False),
                        "tool_call": m.get("tool_call", False),
                        "structured_output": m.get("structured_output", False),
                        "open_weights": m.get("open_weights", False),
                        "release_date": m.get("release_date") or m.get("last_updated"),
                        "description": m.get("description", ""),
                        "modalities": m.get("modalities", []),
                        "output_limit": m.get("output_limit"),
                        "qualitative_adjustment": breakdown.get("qualitative_adjustment", 0),
                        "qualitative_details": breakdown.get("qualitative_details", ""),
                    })
        scored.sort(key=lambda x: x["score"], reverse=True)

        per_provider[slug] = {
            "provider_name": pinfo.get("name", slug),
            "total_models_seen": pdata.get("total_models_seen"),
            "free_model_count": pdata.get("free_model_count", len(models)),
            "leaderboard": scored,
            "best": scored[0] if scored else None,
        }
        global_rank.extend(scored)

    global_rank.sort(key=lambda x: x["score"], reverse=True)

    result = {
        "generated_at": _now(),
        "as_of": as_of.isoformat(),
        "out_dir": str(out_dir),
        "providers_scanned": len(providers),
        "models_scanned": len(global_rank),
        "scoring": {
            "capability": "0-35 (tool_call +15, reasoning +12, structured_output +8)",
            "context": "0-25 log-scaled 32k..1M",
            "recency": "0-20 full at <=6mo, linear decay to 0 at 24mo",
            "openness": "0-10 open_weights",
            "size_hint": "0-10 log-scaled 1b..550b from id/name",
            "qualitative": "multimodal +0..8, structured_output +5, output_limit +0..3, open_weights +2",
            "max": 100,
        },
        "per_provider": per_provider,
        "global_leaderboard": global_rank,
    }

    out_file = out_dir / "ranking.json"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2)

    # Console leaderboard
    print(f"[rank] {len(providers)} providers, {len(global_rank)} free models, "
          f"as-of {as_of}")
    print()
    print("GLOBAL TOP 12")
    print("-" * 78)
    for i, m in enumerate(global_rank[:12], 1):
        ctx = m.get("context")
        ctxs = f"{ctx//1024}k" if ctx else "?"
        qual = m.get("qualitative_adjustment", 0)
        qual_str = f"+{qual:.1f}" if qual > 0 else ""
        print(f"{i:>2}. {m['score']:>5.1f}  {m['id']:<48} {m['provider_name']:<14} {ctxs:>7} {qual_str:>5}")
    print()
    print("PER-PROVIDER LEADERS")
    print("-" * 78)
    for slug, p in per_provider.items():
        best = p.get("best")
        if best:
            print(f"  {p['provider_name']:<18} {best['id']:<44} score={best['score']:.1f}")
        else:
            print(f"  {p['provider_name']:<18} (no free models)")
    print()
    print(f"[rank] wrote {out_file}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Rank free models across providers")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--as-of", default=None,
                    help="Reference date YYYY-MM-DD for recency scoring (default: today)")
    args = ap.parse_args()

    if args.as_of:
        try:
            as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
        except Exception:
            print(f"[error] bad --as-of {args.as_of!r}, expected YYYY-MM-DD", file=sys.stderr)
            return 2
    else:
        as_of = date.today()

    result = rank(Path(args.out_dir), as_of)
    if result.get("error"):
        print(f"[error] {result['error']}: {result.get('hint', '')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
