#!/usr/bin/env python3
"""
place_models.py — Placement Agent (AGENT:Placement)

Consumes ranking.json (from AGENT:Investigation) and populates the four Hermes
job slots with the highest-scoring FREE model from a DIFFERENT provider each.

HARD CONSTRAINT (from the mission spec, corrected mid-build):
  Every slot (main + each fallback) must come from a DIFFERENT PROVIDER.
  The SAME model served by two routes IS allowed — that buys route redundancy,
  not a constraint violation. User ruling: "models can be similar,
  PROVIDERS cant be similar."

Slot assignment strategy
------------------------
Slots are filled in priority order. Each slot claims a provider, and once a
provider is claimed it is excluded from every later slot — guaranteeing the
different-provider rule holds by construction rather than by post-hoc checking.

  brain      — highest-scoring model overall (reasoning-weighted view)
  workhorse  — best from the highest-scoring provider NOT used by brain
  tester     — best from a provider NOT used by brain/workhorse
  fallbacks  — every remaining provider's leader, ranked, chained after
               the slot that outranks it (brain -> workhorse -> tester -> rest)

Output: <out_dir>/placement.json  (+ --emit-yaml for a config snippet)

Usage:
  place_models.py [--out-dir DIR] [--emit-yaml]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME = Path.home() / ".hermes"
DEFAULT_OUT = HERMES_HOME / "cache" / "hermes-modelselection"

SLOT_ORDER = ["brain", "workhorse", "tester"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _brain_view_score(m: dict) -> float:
    """Brain is the thinking/planning/investigating/reviewing slot.

    Capability is ALREADY priced into the investigation score, so this view must
    NOT re-add context/size weight — a 1M-context bonus can outweigh reasoning
    and hand the thinking slot to a non-reasoning model (caught by
    test_place_models.py). Only penalise a model that is not reasoning-capable,
    and slightly prefer structured output for planning/verification work."""
    base = m.get("score", 0)
    if not m.get("reasoning"):
        base -= 15          # a thinking slot that cannot reason is a misfit
    if m.get("structured_output"):
        base += 4
    return base


def load_ranking(out_dir: Path) -> dict:
    path = out_dir / "ranking.json"
    if not path.exists():
        raise SystemExit(
            f"[fatal] ranking.json not found at {path}\n"
            f"        run discover_providers.py -> list_provider_models.py (per provider) "
            f"-> rank_models.py first"
        )
    with open(path) as f:
        return json.load(f)


def place(ranking: dict) -> dict:
    per_provider = ranking.get("per_provider", {}) or {}

    # Candidate pool: one best entry per provider (the provider's leader).
    candidates = []
    for slug, p in per_provider.items():
        best = p.get("best")
        if not best:
            continue
        candidates.append({
            "provider_slug": slug,
            "provider_name": p.get("provider_name", slug),
            "free_model_count": p.get("free_model_count", 0),
            "model_id": best["id"],
            "model_name": best.get("name"),
            "score": best["score"],
            "brain_view_score": _brain_view_score(best),
            "context": best.get("context"),
            "reasoning": best.get("reasoning", False),
            "tool_call": best.get("tool_call", False),
            "structured_output": best.get("structured_output", False),
            "open_weights": best.get("open_weights", False),
            "release_date": best.get("release_date"),
            "description": best.get("description", ""),
        })

    if not candidates:
        raise SystemExit("[fatal] ranking.json has no per-provider leaders to place")

    # ---- BRAIN: best by reasoning-weighted view ----
    brain_pool = sorted(candidates, key=lambda c: c["brain_view_score"], reverse=True)
    brain = brain_pool[0]
    used_providers = {brain["provider_slug"]}

    # ---- WORKHORSE / TESTER: best from an unused provider each ----
    # HARD CONSTRAINT: slots must come from DIFFERENT PROVIDERS. The same model
    # id served by two routes IS allowed (spec: "models can be similar,
    # providers cant be similar") — it buys route redundancy.
    remaining = [c for c in brain_pool if c["provider_slug"] not in used_providers]
    workhorse = remaining[0] if remaining else None
    if workhorse:
        used_providers.add(workhorse["provider_slug"])

    remaining2 = [c for c in remaining if c["provider_slug"] not in used_providers]
    tester = remaining2[0] if remaining2 else None
    if tester:
        used_providers.add(tester["provider_slug"])

    # ---- FALLBACKS: every remaining provider leader, chained ----
    leftovers = [c for c in brain_pool if c["provider_slug"] not in used_providers]

    def slot_entry(c: dict, slot: str) -> dict:
        return {
            "slot": slot,
            "provider": c["provider_slug"],
            "provider_name": c["provider_name"],
            "model": c["model_id"],
            "model_name": c["model_name"],
            "score": c["score"],
            "brain_view_score": c["brain_view_score"],
            "context": c["context"],
            "reasoning": c["reasoning"],
            "tool_call": c["tool_call"],
            "structured_output": c["structured_output"],
            "open_weights": c["open_weights"],
            "release_date": c["release_date"],
            "description": c["description"],
            "why": _why(slot, c),
        }

    slots = {"brain": slot_entry(brain, "brain")}
    if workhorse:
        slots["workhorse"] = slot_entry(workhorse, "workhorse")
    if tester:
        slots["tester"] = slot_entry(tester, "tester")

    fallback_chain = [slot_entry(c, f"fallback{i+1}") for i, c in enumerate(leftovers)]

    # ---- Self-verification of the hard constraint ----
    chain = [slots[s] for s in SLOT_ORDER if s in slots] + fallback_chain
    providers_in_chain = [c["provider"] for c in chain]
    models_in_chain = [c["model"] for c in chain]
    dup_providers = sorted({p for p in providers_in_chain if providers_in_chain.count(p) > 1})
    constraint_ok = len(dup_providers) == 0

    filled_slots = [s for s in SLOT_ORDER if s in slots]
    missing_slots = [s for s in SLOT_ORDER if s not in slots]

    # Depth is bounded by DISTINCT PROVIDERS, not distinct models: the same
    # upstream model reached through two routes is two valid, independent slots.
    distinct_model_ids = len({c["model_id"] for c in candidates})
    shared_models = [m for m in {c["model_id"] for c in candidates}
                     if sum(1 for c in candidates if c["model_id"] == m) > 1]

    result = {
        "generated_at": _now(),
        "as_of": ranking.get("as_of"),
        "source_ranking": str(Path(ranking.get("out_dir", "")) / "ranking.json"),
        "providers_available": len(candidates),
        "distinct_free_model_ids": distinct_model_ids,
        "slots": slots,
        "fallback_chain": fallback_chain,
        "chain": chain,
        "slots_filled": filled_slots,
        "slots_unfilled": missing_slots,
        "constraint": {
            "rule": "every slot (main + each fallback) must come from a DIFFERENT "
                    "provider; the same model across routes is allowed",
            "satisfied": constraint_ok,
            "violating_providers": dup_providers,
            "distinct_providers_in_chain": len(set(providers_in_chain)),
            "models_shared_across_routes": sorted(shared_models),
        },
        "unplaced_providers": [c["provider_slug"] for c in leftovers],
        "coverage_notes": _coverage_notes(len(candidates), distinct_model_ids,
                                          len(filled_slots), missing_slots,
                                          sorted(shared_models)),
    }
    return result


def _why(slot: str, c: dict) -> str:
    bits = []
    if slot == "brain":
        if c["reasoning"]:
            bits.append("reasoning-capable")
        if c["structured_output"]:
            bits.append("structured output")
        ctx = c["context"] or 0
        if ctx:
            bits.append(f"{ctx // 1024}k context")
        bits.append(f"top reasoning-weighted score ({c['brain_view_score']:.1f})")
    elif slot == "workhorse":
        bits.append(f"best free model from a different provider (score {c['score']:.1f})")
        if c["tool_call"]:
            bits.append("tool-calling for execution")
        ctx = c["context"] or 0
        if ctx:
            bits.append(f"{ctx // 1024}k context")
    elif slot == "tester":
        bits.append(f"third distinct provider (score {c['score']:.1f})")
        bits.append("independent route for verification")
    else:
        bits.append(f"next distinct provider (score {c['score']:.1f})")
    return "; ".join(bits)


def _coverage_notes(n_providers: int, distinct_models: int,
                    filled: int, missing: list[str],
                    shared_models: list[str] | None = None) -> list[str]:
    """Report what the placement can and cannot cover, honestly.

    Depth is bounded by DISTINCT PROVIDERS — the constraint. The same upstream
    model reached through two routes is two valid independent slots."""
    shared_models = shared_models or []
    notes = []
    if n_providers < 2:
        notes.append("Only ONE provider has free models — the different-provider "
                     "constraint CANNOT be satisfied. Every slot would share that "
                     "provider. Add a second provider credential.")
    else:
        notes.append(f"{n_providers} providers with free models "
                     f"({distinct_models} distinct model ids); "
                     f"{filled}/{len(SLOT_ORDER)} slots filled by distinct providers.")
        if missing:
            notes.append(f"Unfilled slots: {', '.join(missing)} — add another provider "
                         f"credential to fill them.")
        else:
            notes.append("brain/workhorse/tester all use different providers.")
    if shared_models:
        notes.append(f"{len(shared_models)} model id(s) are reachable through more than "
                     f"one provider — that is allowed and buys route redundancy: "
                     f"{', '.join(shared_models[:3])}"
                     f"{'…' if len(shared_models) > 3 else ''}.")
    return notes


def emit_yaml(placement: dict) -> str:
    """Emit a config.yaml-shaped snippet for the placed models.

    Hermes config uses model.default + a provider map. This is advisory output:
    the agent applies it via `hermes config set` rather than overwriting the
    user's config.yaml wholesale."""
    lines = ["# Hermes model placement (generated by AGENT:Placement)",
             "# Apply with: hermes config set model.default <model>",
             "#              hermes config set model.provider <provider>",
             "model:"]
    b = placement["slots"].get("brain")
    if b:
        lines.append(f"  default: {b['model']}")
        lines.append(f"  provider: {b['provider']}")
    lines.append("")
    lines.append("# Slot reference (brain/workhorse/tester + fallback chain)")
    for c in placement["chain"]:
        lines.append(f"#   {c['slot']:<10} {c['provider']:<12} {c['model']}")
    if not placement["constraint"]["satisfied"]:
        lines.append("")
        lines.append("# WARNING: different-provider constraint NOT satisfied — "
                     f"{placement['constraint']['violating_providers']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Place free models into Hermes job slots")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--emit-yaml", action="store_true",
                    help="also print a config.yaml snippet")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    ranking = load_ranking(out_dir)
    placement = place(ranking)

    with open(out_dir / "placement.json", "w") as f:
        json.dump(placement, f, indent=2)

    print(f"[placement] {placement['providers_available']} providers with free models")
    print()
    print("SLOT ASSIGNMENT")
    print("-" * 78)
    for c in placement["chain"]:
        ctx = c.get("context")
        ctxs = f"{ctx // 1024}k" if ctx else "?"
        print(f"  {c['slot']:<10} {c['provider_name']:<16} {c['model']:<44} {ctxs:>7}")
    print()
    ok = placement["constraint"]["satisfied"]
    print(f"[placement] different-provider constraint: "
          f"{'SATISFIED' if ok else 'VIOLATED'}"
          f" ({placement['constraint']['distinct_providers_in_chain']} distinct providers)")
    if placement["constraint"]["violating_providers"]:
        print(f"            duplicate providers: "
              f"{placement['constraint']['violating_providers']}")
    for n in placement["coverage_notes"]:
        print(f"[placement] note: {n}")
    print()
    print(f"[placement] wrote {out_dir / 'placement.json'}")

    if args.emit_yaml:
        print()
        print(emit_yaml(placement))

    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
