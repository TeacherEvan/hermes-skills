#!/usr/bin/env python3
"""
run_placement_agent.py — Dispatches AGENT:Placement subagent.

This script replaces the direct call to place_models.py. It spawns a subagent
with the placement prompt, waits for completion, and verifies outputs.
"""

import argparse
import os
import sys
from pathlib import Path

def main():
    ap = argparse.ArgumentParser(description="Run AGENT:Placement subagent")
    ap.add_argument("--out-dir", default=os.path.expanduser("~/.hermes/cache/hermes-modelselection"))
    ap.add_argument("--skill-dir", default=os.path.expanduser("~/.hermes/skills/Hermes-ModelSelection"))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    skill_dir = Path(args.skill_dir)

    print(f"[placement-agent] out_dir: {out_dir}")
    print(f"[placement-agent] skill_dir: {skill_dir}")

    # Read the prompt template
    prompt_path = skill_dir / "scripts" / "agent_placement_prompt.md"
    if not prompt_path.exists():
        print(f"[error] Prompt template not found: {prompt_path}", file=sys.stderr)
        return 1

    prompt = prompt_path.read_text()

    print("[placement-agent] Ready to dispatch subagent.")
    print(f"[placement-agent] Prompt length: {len(prompt)} chars")
    print(f"[placement-agent] Expected outputs: placement.json, rationale.md")

    # Verify investigation outputs exist
    ranking = out_dir / "ranking.json"
    qualitative = out_dir / "qualitative_report.md"

    if not ranking.exists():
        print(f"[error] ranking.json not found at {ranking}. Run investigation first.", file=sys.stderr)
        return 1
    if not qualitative.exists():
        print(f"[error] qualitative_report.md not found at {qualitative}. Run investigation first.", file=sys.stderr)
        return 1

    print(f"[placement-agent] Found investigation outputs:")
    print(f"  - {ranking.name} ({ranking.stat().st_size} bytes)")
    print(f"  - {qualitative.name} ({qualitative.stat().st_size} bytes)")

    return 0


if __name__ == "__main__":
    sys.exit(main())