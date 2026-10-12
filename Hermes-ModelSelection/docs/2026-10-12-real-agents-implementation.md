# Implementation Plan: Real AGENT:Investigation and AGENT:Placement for Hermes-ModelSelection

## Objective
Replace the deterministic Python scripts `rank_models.py` (AGENT:Investigation) and `place_models.py` (AGENT:Placement) with real AI subagents that perform qualitative investigation, read model cards/benchmarks/community discussions, weigh trade-offs, and produce reasoned recommendations with citations.

## Scope
- **Target skill:** `Hermes-ModelSelection` (existing skill at `~/.hermes/skills/Hermes-ModelSelection/`)
- **New agents:** `AGENT:Investigation` and `AGENT:Placement` as real subagent missions
- **Orchestration:** Use `surgical-orchestration` Worker+Verifier pattern
- **Integration:** Keep existing pipeline stages (discovery, provider listing, live testing) — only replace Investigation and Placement

## Requirements

### AGENT:Investigation (replaces `rank_models.py`)
**Mission:** Given all `provider-*.json` files (free models per provider), produce a ranked leaderboard with qualitative analysis.

**Inputs:**
- All `provider-*.json` files from discovery stage
- `models_dev_cache.json` for metadata
- Access to web search for model cards, benchmarks, community discussions

**Outputs:**
- `ranking.json` (same schema as current for compatibility)
- `qualitative_report.md` — human-readable analysis with citations

**Capabilities needed:**
- Web search (model cards, benchmarks, Reddit/HN/Discord)
- Read local JSON artifacts
- Reasoning over conflicting signals
- Source credibility weighting (Tier 1-4)

**Deliverable format (ranking.json):**
```json
{
  "generated_at": "...",
  "as_of": "2026-10-12",
  "providers_scanned": 5,
  "models_scanned": 49,
  "scoring": { ... },
  "per_provider": { ... },
  "global_leaderboard": [
    {
      "id": "...",
      "name": "...",
      "provider": "...",
      "provider_name": "...",
      "score": 91.7,
      "breakdown": { "capability": 35, "context": 25, "recency": 20, "openness": 10, "size_hint": 10, "qualitative_bonus": 5 },
      "qualitative_notes": "Strong coding benchmarks (HumanEval 85%), community praise for reasoning, but rate-limited on OpenRouter free tier",
      "citations": [
        { "source": "OpenRouter model card", "url": "...", "tier": 1, "claim": "Reasoning + tool calling" },
        { "source": "Reddit r/LocalLLAMA", "url": "...", "tier": 4, "claim": "Best free model for coding" }
      ],
      "context": 976000,
      "reasoning": true,
      "tool_call": true,
      "structured_output": true,
      "open_weights": false,
      "release_date": "2024-06-15"
    }
  ]
}
```

### AGENT:Placement (replaces `place_models.py`)
**Mission:** Given `ranking.json` + `qualitative_report.md` + use-case profile, assign models to slots with reasoned rationale.

**Inputs:**
- `ranking.json` (with qualitative notes + citations)
- `qualitative_report.md`
- Use-case profile (from user or defaults: coding, reasoning, writing, low-latency interactive)

**Outputs:**
- `placement.json` (same schema as current for compatibility)
- `rationale.md` — human-readable explanation of each slot choice

**Capabilities needed:**
- Read local JSON/MD artifacts
- Reason about trade-offs (redundancy vs diversity, reasoning vs speed, context vs latency)
- Consider use-case fit
- Explain "why not X" for skipped models

**Deliverable format (placement.json):**
```json
{
  "generated_at": "...",
  "as_of": "2026-10-12",
  "source_ranking": "...",
  "providers_available": 5,
  "distinct_free_model_ids": 4,
  "slots": {
    "brain": {
      "slot": "brain",
      "provider": "kilocode",
      "provider_name": "Kilo Code",
      "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
      "model_name": "Nemotron 3 Ultra",
      "score": 91.7,
      "brain_view_score": 91.7,
      "context": 976000,
      "reasoning": true,
      "tool_call": true,
      "structured_output": true,
      "open_weights": false,
      "release_date": "2024-06-15",
      "description": "...",
      "why": "Top reasoning-weighted score; 976k context handles large codebases; reasoning + tool calling + structured output for planning/debugging; community-validated for coding tasks",
      "qualitative_rationale": "Chosen over OpenRouter same-model route because Kilo Code shows better uptime on free tier. Not chosen: Qwen2.5-Coder (lower reasoning score, smaller context). Not chosen: DeepSeek-V2 (not free on credentialed providers)."
    }
  },
  "fallback_chain": [...],
  "chain": [...],
  "slots_filled": ["brain", "workhorse", "tester"],
  "slots_unfilled": [],
  "constraint": { ... },
  "unplaced_providers": [...],
  "coverage_notes": [...]
}
```

## Architecture

```
discover_providers.py (unchanged)
    → discovery.json
    → list_provider_models.py × N (unchanged, parallel subagents)
        → provider-*.json
    → AGENT:Investigation (NEW - real AI subagent)
        → ranking.json + qualitative_report.md
    → AGENT:Placement (NEW - real AI subagent)
        → placement.json + rationale.md
    → test_models_live.py (unchanged)
        → verification.json
    → hermes config set (unchanged)
```

## Subagent Specifications

### AGENT:Investigation Subagent
```yaml
Goal: "You are AGENT:Investigation. Read all provider-*.json files, enrich with web research (model cards, benchmarks, community), and produce ranking.json + qualitative_report.md with citations."
Context: |
  Skill dir: /home/leandi-duplessis/.hermes/skills/Hermes-ModelSelection
  Out dir: /home/leandi-duplessis/.hermes/cache/hermes-modelselection
  Provider files: provider-kilocode.json, provider-openrouter.json, provider-opencode-zen.json, provider-nous.json, provider-huggingface.json
  Models dev cache: /home/leandi-duplessis/.hermes/models_dev_cache.json
  Use web_search for: model cards (OpenRouter, HF), benchmarks (HumanEval, MMLU, MT-Bench), community (Reddit r/LocalLLAMA, r/OpenRouter, HN)
  Source tiers: 1=official docs, 2=GitHub issues/StackOverflow, 3=tech blogs, 4=Reddit/HN/Discord
  Output ranking.json schema MUST match current (see investigation) + qualitative_notes + citations fields
  Output qualitative_report.md with sections: Executive Summary, Per-Provider Analysis, Global Recommendations, Caveats
Tools: read_file, web_search, write_file
```

### AGENT:Placement Subagent
```yaml
Goal: "You are AGENT:Placement. Read ranking.json + qualitative_report.md, apply use-case profile (coding, reasoning, writing, low-latency), and produce placement.json + rationale.md with reasoned slot assignments."
Context: |
  Skill dir: /home/leandi-duplessis/.hermes/skills/Hermes-ModelSelection
  Out dir: /home/leandi-duplessis/.hermes/cache/hermes-modelselection
  Inputs: ranking.json, qualitative_report.md
  Use-case: Primary = coding/debugging/architecture; Secondary = writing/analysis/research; Constraints = low latency for interactive, high context for large codebases, reliable fallback
  Distinct-provider constraint: HARD (every slot different provider; same model across routes allowed = route redundancy)
  Output placement.json schema MUST match current + qualitative_rationale field
  Output rationale.md with sections: Brain Choice, Workhorse Choice, Tester Choice, Fallback Chain, Trade-offs Considered, Why Not Others
Tools: read_file, write_file
```

## Verification Gates

| Gate | Check |
|------|-------|
| **Investigation output valid** | `ranking.json` validates against schema; `qualitative_report.md` exists with ≥3 citations |
| **Placement output valid** | `placement.json` validates; constraint.satisfied = true; `rationale.md` exists |
| **Pipeline integration** | `run_pipeline.sh` modified to call subagents instead of scripts; exits 0 on success |
| **Backward compatibility** | `placement.json` consumed by `test_models_live.py` without changes |
| **Live probe passes** | All 3 main slots (brain/workhorse/tester) answer real request |

## Implementation Steps

### Phase 1: Create Subagent Mission Files
1. Create `scripts/agent_investigation_prompt.md` — Investigation subagent prompt template
2. Create `scripts/agent_placement_prompt.md` — Placement subagent prompt template
3. Create `scripts/run_investigation_agent.py` — Dispatches Investigation subagent
4. Create `scripts/run_placement_agent.py` — Dispatches Placement subagent

### Phase 2: Update Pipeline
5. Modify `run_pipeline.sh` to call agent runners instead of `rank_models.py` / `place_models.py`
6. Ensure artifact paths match (ranking.json, placement.json in same location)

### Phase 3: Test & Verify
7. Run full pipeline
8. Verify live probes pass
9. Verify config applies correctly

## Success Criteria

- [ ] `run_pipeline.sh` completes with exit 0
- [ ] `ranking.json` contains `qualitative_notes` and `citations` fields for top models
- [ ] `qualitative_report.md` exists with ≥10 citations across Tier 1-4 sources
- [ ] `placement.json` constraint.satisfied = true (5 distinct providers)
- [ ] `rationale.md` explains each slot choice with "why not" reasoning
- [ ] All 3 main slots respond to live probe
- [ ] `hermes config check` passes after apply

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Subagent hallucinates citations | Require URL in citation; verify Tier 1-2 sources exist |
| Subagent exceeds context | Limit web_search to 5 queries; summarize findings |
| Subagent ignores distinct-provider constraint | Hard-code constraint in prompt; verify in placement.json |
| Pipeline breaks on subagent failure | Keep deterministic scripts as fallback (`--fallback-deterministic` flag) |
| Web search rate limits | Cache search results per model; reuse across runs |

## Timeline

| Phase | Est. Time |
|-------|-----------|
| Phase 1: Subagent missions | 30 min |
| Phase 2: Pipeline integration | 20 min |
| Phase 3: Test & verify | 10 min (parallel probes) |
| **Total** | **~60 min** |

## Notes

- The existing `rank_models.py` and `place_models.py` are KEPT as fallback (`--fallback-deterministic`)
- The skill's architecture diagram in SKILL.md will be updated to reflect real agents
- All output schemas remain compatible with `test_models_live.py` and `hermes config set`
- Use `delegate_task` for subagent dispatch (surgical-orchestration pattern)