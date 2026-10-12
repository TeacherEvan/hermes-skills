#!/usr/bin/env python3
"""
list_provider_models.py — Provider Agent runner (AGENT:<Provider>)

Runs inside ONE subagent. Takes a single provider slug (the subagent's assigned
provider) and produces a canonical free-model list for that provider ONLY.

This is the script the orchestrator hands to each AGENT:<Provider> subagent.
It is provider-scoped by design: the subagent gets its slug as an argument, so
it can only see its own provider's models.

Usage:
  list_provider_models.py --provider openrouter --out-dir DIR [--hermes-home DIR]

Output: <out_dir>/provider-<slug>.json

{
  "provider": {"slug": "...", "name": "..."},
  "generated_at": "...",
  "free_model_count": N,
  "models": [
    {"id": "...", "name": "...", "context": 262144, "reasoning": true,
     "tool_call": true, "family": "...", "release_date": "...", ...}
  ]
}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME = Path.home() / ".hermes"
HERMES_AGENT_DIR = HERMES_HOME / "hermes-agent"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _from_discovery(slug: str, out_dir: Path) -> dict | None:
    """Fast path: reuse discovery.json if it already has this provider's models.

    The orchestrator runs discovery ONCE; every AGENT:<Provider> reuses that
    one snapshot instead of re-importing Hermes N times. Falls back to live
    discovery if the snapshot is missing or lacks the provider."""
    disc = out_dir / "discovery.json"
    if not disc.exists():
        return None
    try:
        data = json.load(open(disc))
    except Exception:
        return None
    for p in data.get("providers", []):
        if p.get("slug") == slug:
            return p
    return None


def _live_discovery(slug: str, hermes_home: Path, hermes_agent_dir: Path) -> dict | None:
    """Slow path: import Hermes discovery directly for one provider."""
    sys.path.insert(0, str(hermes_agent_dir))
    try:
        from hermes_cli.model_switch_providers import list_authenticated_providers
    except Exception as e:
        print(f"[fatal] import failed: {e}", file=sys.stderr)
        return None

    providers = list_authenticated_providers()
    for p in providers:
        if p.get("slug") == slug:
            return p
    return None


def _dev_provider_index(dev: dict) -> dict[str, dict]:
    """Global model-id -> metadata index across ALL models.dev providers.

    Aggregators (nous, kilocode, ai-gateway) serve the same upstream models as
    OpenRouter but carry no local models.dev entry, so a per-provider-only
    lookup scores the identical model differently per route. One global index
    gives every model a single authoritative metadata record."""
    index: dict[str, dict] = {}
    for prov, pdata in dev.items():
        if not isinstance(pdata, dict):
            continue
        for mid, meta in (pdata.get("models") or {}).items():
            if isinstance(meta, dict) and mid not in index:
                index[mid] = meta
    return index


def _model_slug_to_dev_provider(slug: str) -> str | None:
    """Map a Hermes picker slug back to a models.dev provider key."""
    # Hermes slugs mostly equal models.dev keys; a few differ.
    aliases = {
        "openai-api": "openai",
        "ai-gateway": None,          # aggregator, no models.dev entry
        "copilot-acp": "copilot",    # ACP variant shares copilot catalog
        "xai-oauth": "xai",
        "opencode-zen": "opencode",  # models.dev uses "opencode" for both Zen and Go
        "opencode-go": "opencode-go",
    }
    if slug in aliases:
        return aliases[slug]
    return slug


def _enrich_from_models_dev(slug: str, model_ids: list[str], hermes_home: Path) -> list[dict]:
    dev_path = hermes_home / "models_dev_cache.json"
    dev = {}
    if dev_path.exists():
        try:
            dev = json.load(open(dev_path))
        except Exception:
            pass
    dev_provider = _model_slug_to_dev_provider(slug)
    dev_models = dev.get(dev_provider, {}).get("models", {}) or {}
    global_index = _dev_provider_index(dev)

    out = []
    for mid in model_ids:
        meta = dev_models.get(mid) or global_index.get(mid)
        if meta is None:
            for m in dev_models.values():
                if isinstance(m, dict) and m.get("canonical_model_id") == mid:
                    meta = m
                    break
        if meta is None:
            for m in global_index.values():
                if m.get("canonical_model_id") == mid:
                    meta = m
                    break
        if meta:
            limit = meta.get("limit") or {}
            cost = meta.get("cost")
            is_models_dev_free = False
            if cost and isinstance(cost, dict):
                inp = cost.get("input", 0)
                outp = cost.get("output", 0)
                if inp == 0 and outp == 0:
                    is_models_dev_free = True
            out.append({
                "id": mid,
                "name": meta.get("name", mid),
                "description": meta.get("description", ""),
                "family": meta.get("family"),
                "reasoning": bool(meta.get("reasoning", False)),
                "tool_call": bool(meta.get("tool_call", False)),
                "structured_output": bool(meta.get("structured_output", False)),
                "open_weights": bool(meta.get("open_weights", False)),
                "context": limit.get("context"),
                "output_limit": limit.get("output"),
                "release_date": meta.get("release_date"),
                "last_updated": meta.get("last_updated"),
                "dev_meta": True,
                "models_dev_free": is_models_dev_free,
            })
        else:
            out.append({
                "id": mid, "name": mid, "description": "",
                "reasoning": False, "tool_call": False, "structured_output": False,
                "open_weights": False, "context": None, "output_limit": None,
                "release_date": None, "last_updated": None, "dev_meta": False,
            })
    return out


def _is_free(model: dict) -> bool:
    """Free = id contains ':free' OR '-free' (opencode-zen) OR models.dev cost is zero for a provider with its own models.dev entry."""
    mid = model.get("id", "")
    if ":free" in mid or "-free" in mid:
        return True
    # Check if models.dev metadata says this model is free (cost 0) AND
    # the provider has its own models.dev entry (dev_meta=True means we found it)
    if model.get("dev_meta") and model.get("models_dev_free"):
        return True
    return False


def list_models(slug: str, out_dir: Path, hermes_home: Path, hermes_agent_dir: Path) -> dict:
    started = time.time()

    entry = _from_discovery(slug, out_dir)
    if entry is None:
        entry = _live_discovery(slug, hermes_home, hermes_agent_dir)
    if entry is None:
        return {
            "provider": {"slug": slug, "name": slug},
            "generated_at": _now(),
            "error": f"provider '{slug}' not found in authenticated providers",
            "free_model_count": 0,
            "models": [],
        }

    raw = entry.get("models", []) or []
    ids = []
    for m in raw:
        if isinstance(m, str):
            ids.append(m)
        elif isinstance(m, dict) and "id" in m:
            ids.append(m["id"])

    # If discovery.json already carries enriched dicts (from discover_providers),
    # reuse them directly — they are already free-filtered.
    enriched = None
    if raw and isinstance(raw[0], dict) and "dev_meta" in raw[0]:
        enriched = [m for m in raw if isinstance(m, dict)]
    else:
        enriched = _enrich_from_models_dev(slug, ids, hermes_home)

    free_models = [m for m in enriched if _is_free(m)]
    free_models.sort(key=lambda m: (m.get("context") or 0, m.get("name") or ""), reverse=True)

    result = {
        "provider": {"slug": slug, "name": entry.get("name", slug)},
        "generated_at": _now(),
        "elapsed_seconds": round(time.time() - started, 2),
        "total_models_seen": entry.get("total_models", len(ids)),
        "free_model_count": len(free_models),
        "models": free_models,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"provider-{slug}.json"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2)

    print(f"[{slug}] {len(free_models)} free models of {result['total_models_seen']} "
          f"({result['elapsed_seconds']}s) -> {out_file}")
    for m in free_models[:10]:
        ctx = m.get("context")
        ctxs = f"{ctx//1024}k" if ctx else "?"
        flags = []
        if m.get("reasoning"):
            flags.append("reason")
        if m.get("tool_call"):
            flags.append("tools")
        if m.get("open_weights"):
            flags.append("open")
        print(f"    {m['id']:<55} {ctxs:>7} {' '.join(flags)}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="List free models for ONE provider")
    ap.add_argument("--provider", required=True, help="Provider slug (e.g. openrouter)")
    ap.add_argument("--out-dir", default=str(HERMES_HOME / "cache" / "hermes-modelselection"),
                    help="Output directory")
    ap.add_argument("--hermes-home", default=str(HERMES_HOME))
    ap.add_argument("--hermes-agent-dir", default=str(HERMES_AGENT_DIR))
    args = ap.parse_args()

    result = list_models(args.provider, Path(args.out_dir),
                         Path(args.hermes_home), Path(args.hermes_agent_dir))
    if result.get("error"):
        print(f"[error] {result['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
