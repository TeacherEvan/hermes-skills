#!/usr/bin/env python3
"""
discover_providers.py — Provider Discovery Agent (AGENT:Discovery)

Discovers every LLM provider the user has authenticated credentials for, then
for each provider extracts its FREE models using Hermes's own discovery
machinery (list_authenticated_providers) plus the models.dev cache.

Output: writes <out_dir>/discovery.json

Usage:
  discover_providers.py [--out-dir DIR]

The JSON shape:
{
  "generated_at": "<ISO8601>",
  "hermes_home": "/home/.../.hermes",
  "providers": [
    {
      "slug": "openrouter",
      "name": "OpenRouter",
      "source": "built-in|hermes|canonical|user-config|model-config",
      "total_models": 59,
      "free_model_count": 7,
      "models": [ ...free model dicts with id/name/context/reasoning/tool_call... ],
      "assign_agent": "AGENT:OpenRouter"
    }
  ],
  "provider_count": N,
  "total_free_models": M
}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERMES_AGENT_DIR = Path(os.environ.get("HERMES_AGENT_DIR", str(Path.home() / ".hermes" / "hermes-agent")))
HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_models_dev(hermes_home: Path | None = None) -> dict:
    """Load the models.dev cache (provider -> {models: {id: meta}})."""
    path = (hermes_home or HERMES_HOME) / "models_dev_cache.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] failed to read models_dev_cache.json: {e}", file=sys.stderr)
        return {}


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


def _dev_provider_index(dev: dict) -> dict[str, dict]:
    """Build a global model-id -> metadata index across ALL models.dev providers.

    Aggregators (nous, kilocode, ai-gateway) serve the same upstream models as
    OpenRouter but have no local models.dev entry, so a per-provider lookup
    scores the identical model wildly differently on each provider. Indexing
    every provider's models once gives every model one authoritative metadata
    record regardless of which route serves it."""
    index: dict[str, dict] = {}
    for prov, pdata in dev.items():
        if not isinstance(pdata, dict):
            continue
        for mid, meta in (pdata.get("models") or {}).items():
            if isinstance(meta, dict) and mid not in index:
                index[mid] = meta
    return index


def _enrich_from_models_dev(slug: str, model_ids: list[str], dev: dict) -> list[dict]:
    """Join Hermes model ids against models.dev metadata; synthesize minimal
    entries for ids absent from the cache so nothing is silently dropped.

    Resolution order: this provider's own entry -> any provider's entry
    (cross-route index) -> canonical_model_id match -> minimal stub."""
    dev_provider = _model_slug_to_dev_provider(slug)
    dev_models = {}
    if dev_provider and dev_provider in dev:
        dev_models = dev.get(dev_provider, {}).get("models", {}) or {}
    global_index = _dev_provider_index(dev)

    out = []
    for mid in model_ids:
        meta = dev_models.get(mid) or global_index.get(mid)
        if meta is None:
            # try canonical id (some entries carry canonical_model_id)
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
                "modalities": meta.get("modalities"),
                "dev_meta": True,
                "models_dev_free": is_models_dev_free,
            })
        else:
            out.append({
                "id": mid,
                "name": mid,
                "description": "",
                "reasoning": False,
                "tool_call": False,
                "structured_output": False,
                "open_weights": False,
                "context": None,
                "output_limit": None,
                "release_date": None,
                "last_updated": None,
                "dev_meta": False,
            })
    return out


def _is_free(model: dict) -> bool:
    """Free = id contains ':free' OR '-free' (opencode-zen) OR models.dev cost is zero for a provider with its own models.dev entry.

    Deliberately conservative: ':free' suffix is the canonical Hermes/OpenRouter
    free-tier marker. '-free' is used by opencode-zen. A zero-cost models.dev
    row alone is not enough (many aggregators list 0 placeholders for paid tiers).

    But for providers with their own models.dev entry (not aggregators mirroring
    OpenRouter), we can trust zero-cost entries from models.dev.
    """
    mid = model.get("id", "")
    if ":free" in mid or "-free" in mid:
        return True
    # Check if models.dev metadata says this model is free (cost 0) AND
    # the provider has its own models.dev entry (dev_meta=True means we found it)
    if model.get("dev_meta") and model.get("models_dev_free"):
        return True
    return False


def discover(out_dir: Path, agent_dir: Path | None = None, home: Path | None = None) -> dict:
    agent_dir = agent_dir or HERMES_AGENT_DIR
    home = home or HERMES_HOME
    out_dir.mkdir(parents=True, exist_ok=True)

    # Import Hermes's own provider discovery — this is the single source of truth
    # for "what does this user have credentials for".
    sys.path.insert(0, str(agent_dir))
    try:
        from hermes_cli.model_switch_providers import list_authenticated_providers
    except Exception as e:
        print(f"[fatal] could not import Hermes provider discovery: {e}", file=sys.stderr)
        print(f"        HERMES_AGENT_DIR={HERMES_AGENT_DIR} (override with --hermes-agent-dir)",
              file=sys.stderr)
        sys.exit(2)

    started = time.time()
    providers = list_authenticated_providers()
    elapsed = time.time() - started

    dev = _load_models_dev(home)

    result = {
        "generated_at": _now(),
        "hermes_home": str(home),
        "hermes_agent_dir": str(agent_dir),
        "discovery_seconds": round(elapsed, 2),
        "providers": [],
    }

    total_free = 0
    for p in providers:
        slug = p.get("slug", "")
        name = p.get("name", slug)
        raw_models = p.get("models", []) or []

        # models entries from list_authenticated_providers are id strings
        # (or dicts in some code paths) — normalise to strings.
        ids = []
        for m in raw_models:
            if isinstance(m, str):
                ids.append(m)
            elif isinstance(m, dict) and "id" in m:
                ids.append(m["id"])

        enriched = _enrich_from_models_dev(slug, ids, dev)
        free_models = [m for m in enriched if _is_free(m)]
        free_models.sort(key=lambda m: (m.get("context") or 0, m.get("name") or ""), reverse=True)

        total_free += len(free_models)

        result["providers"].append({
            "slug": slug,
            "name": name,
            "source": p.get("source", "built-in"),
            "is_current": p.get("is_current", False),
            "total_models": p.get("total_models", len(ids)),
            "free_model_count": len(free_models),
            "models": free_models,
            "assign_agent": f"AGENT:{name}",
            "assign_agent_slug": f"AGENT:{slug}",
        })

    # Providers WITH free models are the ones that can carry a Brain/Workhorse/
    # Tester slot — sort so the richest providers come first.
    result["providers"].sort(key=lambda x: x["free_model_count"], reverse=True)
    result["provider_count"] = len(result["providers"])
    result["providers_with_free_models"] = sum(1 for x in result["providers"] if x["free_model_count"] > 0)
    result["total_free_models"] = total_free

    out_file = out_dir / "discovery.json"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2)

    # Human-readable summary to stdout
    print(f"[discovery] {result['provider_count']} providers, "
          f"{result['providers_with_free_models']} with free models, "
          f"{total_free} total free models ({elapsed:.1f}s)")
    for x in result["providers"]:
        if x["free_model_count"] > 0:
            print(f"  {x['assign_agent']:<28} {x['free_model_count']:>3} free  (of {x['total_models']} total)")
    print(f"[discovery] wrote {out_file}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Discover Hermes providers + free models")
    ap.add_argument("--out-dir",
                    default=str(HERMES_HOME / "cache" / "hermes-modelselection"),
                    help="Output directory for discovery.json")
    ap.add_argument("--hermes-agent-dir", default=str(HERMES_AGENT_DIR),
                    help="Path to the hermes-agent install")
    ap.add_argument("--hermes-home", default=str(HERMES_HOME),
                    help="Hermes home (default ~/.hermes)")
    args = ap.parse_args()

    # Local rebind (not a module global) so --hermes-home overrides work cleanly.
    agent_dir = Path(args.hermes_agent_dir)
    home = Path(args.hermes_home)
    discover(Path(args.out_dir), agent_dir=agent_dir, home=home)
    return 0


if __name__ == "__main__":
    sys.exit(main())
