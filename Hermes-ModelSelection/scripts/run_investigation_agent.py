#!/usr/bin/env python3
"""
run_investigation_agent.py — Dispatches AGENT:Investigation subagent.

This script replaces the direct call to rank_models.py. It spawns a subagent
with the investigation prompt, waits for completion, and verifies outputs.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "hermes-agent"))

# We'll use the delegate_task tool via the hermes runtime
# For now, this is a placeholder that will be called from the shell
# The actual subagent dispatch happens via the Hermes delegate_task tool

def main():
    ap = argparse.ArgumentParser(description="Run AGENT:Investigation subagent")
    ap.add_argument("--out-dir", default=os.path.expanduser("~/.hermes/cache/hermes-modelselection"))
    ap.add_argument("--skill-dir", default=os.path.expanduser("~/.hermes/skills/Hermes-ModelSelection"))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    skill_dir = Path(args.skill_dir)

    print(f"[investigation-agent] out_dir: {out_dir}")
    print(f"[investigation-agent] skill_dir: {skill_dir}")

    # Read the prompt template
    prompt_path = skill_dir / "scripts" / "agent_investigation_prompt.md"
    if not prompt_path.exists():
        print(f"[error] Prompt template not found: {prompt_path}", file=sys.stderr)
        return 1

    prompt = prompt_path.read_text()

    # The actual subagent dispatch is done via the Hermes delegate_task tool
    # This script just prepares the context and prints the prompt for the orchestrator
    # In practice, the run_pipeline.sh will call delegate_task directly

    print("[investigation-agent] Ready to dispatch subagent.")
    print(f"[investigation-agent] Prompt length: {len(prompt)} chars")
    print(f"[investigation-agent] Expected outputs: ranking.json, qualitative_report.md")

    # Verify provider files exist
    provider_files = list(out_dir.glob("provider-*.json"))
    print(f"[investigation-agent] Found {len(provider_files)} provider files:")
    for f in provider_files:
        print(f"  - {f.name}")

    if not provider_files:
        print("[error] No provider-*.json files found. Run discovery + list_provider_models first.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())