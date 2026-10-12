#!/usr/bin/env python3
"""
test_place_models.py — unit tests for the placement constraint logic.

Uses SYNTHETIC ranking fixtures (no network, no Hermes import) so the
different-provider constraint is proven across provider counts that this
machine cannot reach. Run:

    python3 test_place_models.py
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent

spec = importlib.util.spec_from_file_location("place_models", HERE / "place_models.py")
if spec is None or spec.loader is None:  # pragma: no cover
    sys.exit("[fatal] could not load place_models.py")
pm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pm)


def _cand(slug, model, score, **kw):
    return {"provider_slug": slug, "provider_name": slug.title(), "model_id": model,
            "model_name": model, "score": score, "brain_view_score": score,
            "context": kw.get("context", 128 * 1024), "reasoning": kw.get("reasoning", True),
            "tool_call": True, "structured_output": False, "open_weights": True,
            "release_date": "2026-01-01", "description": ""}


def _ranking(cands, as_of="2026-10-10"):
    """Build a ranking.json-shaped dict with one 'best' per provider."""
    per = {}
    for c in cands:
        if c["provider_slug"] in per:
            continue
        per[c["provider_slug"]] = {
            "provider_name": c["provider_name"],
            "total_models_seen": 100,
            "free_model_count": 5,
            "best": {"id": c["model_id"], "name": c["model_name"], "score": c["score"],
                     "context": c["context"], "reasoning": c["reasoning"],
                     "tool_call": True, "structured_output": False,
                     "open_weights": True, "release_date": "2026-01-01", "description": ""},
        }
    return {"as_of": as_of, "out_dir": "/tmp", "per_provider": per}


failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


# ---------------------------------------------------------------- test cases

print("test: single provider cannot satisfy the constraint")
one = pm.place(_ranking([_cand("openrouter", "a:free", 90)]))
check("only brain filled", one["slots_filled"] == ["brain"], str(one["slots_filled"]))
check("workhorse/tester unfilled", set(one["slots_unfilled"]) == {"workhorse", "tester"})
check("constraint satisfied (trivially)", one["constraint"]["satisfied"] is True)

print("test: two providers -> brain + workhorse differ, tester unfilled")
two = pm.place(_ranking([_cand("kilocode", "a:free", 95),
                         _cand("openrouter", "a:free", 90)]))
check("brain+workhorse filled", set(two["slots_filled"]) == {"brain", "workhorse"})
check("tester unfilled", two["slots_unfilled"] == ["tester"])
check("providers differ",
      two["slots"]["brain"]["provider"] != two["slots"]["workhorse"]["provider"])
check("same model across routes ALLOWED",
      two["slots"]["brain"]["model"] == two["slots"]["workhorse"]["model"])
check("constraint satisfied", two["constraint"]["satisfied"] is True)
check("shared model reported",
      two["constraint"]["models_shared_across_routes"] == ["a:free"])

print("test: four providers -> all slots + a fallback")
four = pm.place(_ranking([_cand("kilocode", "a:free", 95),
                          _cand("openrouter", "b:free", 90),
                          _cand("nous", "c:free", 80),
                          _cand("gemini", "d:free", 70)]))
check("all three slots filled", set(four["slots_filled"]) == {"brain", "workhorse", "tester"})
check("one fallback queued", len(four["fallback_chain"]) == 1)
chain_providers = [c["provider"] for c in four["chain"]]
check("all providers in chain distinct",
      len(chain_providers) == len(set(chain_providers)), str(chain_providers))
check("constraint satisfied", four["constraint"]["satisfied"] is True)

print("test: brain prefers reasoning over raw score")
# Lower generic score but reasoning + huge context should win the brain slot.
r = pm.place(_ranking([
    _cand("openrouter", "big-nonreasoning:free", 92, reasoning=False, context=1024 * 1024),
    _cand("nous", "reasoner:free", 78, reasoning=True, context=512 * 1024),
]))
check("brain is the reasoning model", r["slots"]["brain"]["provider"] == "nous",
      str(r["slots"]["brain"]))

print("test: no cross-provider duplicate ever appears")
multi = pm.place(_ranking([_cand(f"p{i}", "m:free", 90 - i) for i in range(6)]))
chain_p = [c["provider"] for c in multi["chain"]]
check("six distinct providers, no repeats",
      len(chain_p) == len(set(chain_p)) == 6, str(chain_p))
check("constraint satisfied", multi["constraint"]["satisfied"] is True)

print("test: emit_yaml includes slot reference")
y = pm.emit_yaml(four)
check("yaml has model.default", "default: " in y)
check("yaml lists slots", "#   brain" in y)

print()
if failures:
    print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("RESULT: all placement tests passed")
sys.exit(0)
