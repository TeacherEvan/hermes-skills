#!/usr/bin/env python3
"""Enumerate free models across all Hermes providers with live credentials.

Usage:
    python3 scripts/scan-free-models.py [--json] [--provider NAME]

Reads ~/.hermes/.env and ~/.hermes/auth.json for credentials.
Outputs a ranked table or JSON of free models per provider.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone


HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def load_env():
    """Load .env file into a dict."""
    env = {}
    env_file = HERMES_HOME / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def load_auth():
    """Load auth.json."""
    auth_file = HERMES_HOME / "auth.json"
    if auth_file.exists():
        return json.loads(auth_file.read_text())
    return {}


def fetch_models(base_url, api_key):
    """Fetch /v1/models from a provider."""
    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Hermes-ModelSelection/1.0")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
            return data.get("data", data.get("models", []))
    except urllib.error.HTTPError as e:
        # Some providers reject urllib's default UA — retry with curl
        import subprocess
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "20", url,
                 "-H", f"Authorization: Bearer {api_key}",
                 "-H", "Content-Type: application/json"],
                capture_output=True, text=True, timeout=25
            )
            data = json.loads(result.stdout)
            return data.get("data", data.get("models", []))
        except (json.JSONDecodeError, subprocess.TimeoutExpired) as e2:
            print(f"  ERROR fetching {url}: {e} (curl fallback also failed: {e2})", file=sys.stderr)
            return []
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        print(f"  ERROR fetching {url}: {e}", file=sys.stderr)
        return []


def is_free_openrouter(model):
    """OpenRouter/KiloCode free detection."""
    pricing = model.get("pricing", {})
    if pricing.get("prompt") == "0" and pricing.get("completion") == "0":
        return True
    if ":free" in model.get("id", ""):
        return True
    return False


def is_free_opencode(model):
    """OpenCode Zen free detection (hyphen suffix)."""
    return "-free" in model.get("id", "")


def get_nous_free_models():
    """Read Nous free models from cache."""
    cache = HERMES_HOME / "cache" / "nous_recommended_cache.json"
    if not cache.exists():
        return []
    try:
        data = json.loads(cache.read_text())
        for portal_url, portal_data in data.items():
            free = portal_data.get("data", {}).get("freeRecommendedModels", [])
            return [
                {
                    "id": m.get("modelName", ""),
                    "name": m.get("displayName", m.get("modelName", "")),
                    "context_length": m.get("contextLength") or 0,
                }
                for m in free
                if isinstance(m, dict)
            ]
    except (json.JSONDecodeError, KeyError):
        return []
    return []


def scan_openrouter(env):
    key = env.get("OPENROUTER_API_KEY")
    if not key:
        return None
    models = fetch_models("https://openrouter.ai/api/v1", key)
    free = [
        {
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "context_length": m.get("context_length", 0),
            "max_output": m.get("top_provider", {}).get("max_completion_tokens", 0),
        }
        for m in models
        if is_free_openrouter(m) and m.get("id") != "openrouter/free"
    ]
    free.sort(key=lambda x: x["context_length"], reverse=True)
    return {"provider": "openrouter", "free_count": len(free), "models": free}


def scan_kilocode(env):
    key = env.get("KILOCODE_API_KEY")
    if not key:
        return None
    models = fetch_models("https://api.kilo.ai/api/gateway", key)
    free = [
        {
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "context_length": m.get("top_provider", {}).get("context_length", 0) or 0,
            "max_output": m.get("top_provider", {}).get("max_completion_tokens", 0) or 0,
        }
        for m in models
        if is_free_openrouter(m) and m.get("id") != "kilo-auto/free"
    ]
    free.sort(key=lambda x: x["context_length"], reverse=True)
    return {"provider": "kilocode", "free_count": len(free), "models": free}


def scan_opencode(env):
    key = env.get("OPENCODE_ZEN_API_KEY")
    if not key:
        return None
    models = fetch_models("https://opencode.ai/zen/v1", key)
    free = [
        {
            "id": m["id"],
            "name": m.get("id", ""),
            "context_length": m.get("context_length", 0) or 0,
            "max_output": 0,
        }
        for m in models
        if is_free_opencode(m)
    ]
    free.sort(key=lambda x: x["context_length"], reverse=True)
    return {"provider": "opencode-free", "free_count": len(free), "models": free}


def scan_nous():
    models = get_nous_free_models()
    if not models:
        return None
    models.sort(key=lambda x: x.get("context_length", 0), reverse=True)
    return {"provider": "nous", "free_count": len(models), "models": models}


def scan_all():
    env = load_env()
    results = []
    for scanner in (scan_openrouter, scan_kilocode, scan_opencode):
        r = scanner(env)
        if r:
            results.append(r)
    nous = scan_nous()
    if nous:
        results.append(nous)
    return results


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Scan free models across Hermes providers")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--provider", help="Scan only this provider")
    args = parser.parse_args()

    results = scan_all()

    if args.provider:
        results = [r for r in results if r["provider"] == args.provider]

    if args.json:
        print(json.dumps(results, indent=2))
        return

    # Human-readable table
    print(f"\n{'='*70}")
    print(f"  FREE MODEL SCAN — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}\n")

    for r in results:
        print(f"  {r['provider'].upper()} ({r['free_count']} free models)")
        print(f"  {'-'*60}")
        for m in r["models"][:10]:
            ctx = f"{m['context_length']//1024}K" if m["context_length"] else "?"
            print(f"    {m['id']:<55} ctx={ctx}")
        print()


if __name__ == "__main__":
    main()
