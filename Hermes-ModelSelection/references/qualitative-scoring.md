# Qualitative Scoring Dimensions (v2.1)

Deterministic additions to base score, baked into `rank_models.py`. No AI agent required.

## Dimensions

| Dimension | Max Points | Rule |
|-----------|------------|------|
| **Multimodal** | +8 | +2 per non-text input modality (image, video, pdf, audio). Max 4 non-text modalities = +8. Muse Spark 1.3 has 5 modalities (text+image+video+pdf+audio) → +8.
| **Structured Output** | +5 | Reliable JSON schema enforcement for coding/agentic workflows. Binary: +5 or +0.
| **Output Limit** | +3 | +3 for >100k tokens (full-file generation), +2 for >50k, +1 for >32k, +0 for ≤32k or unspecified.
| **Open Weights** | +2 | Fully open weights (enables fine-tuning, local deployment, auditability). Binary: +2 or +0.

## Implementation

In `score_model()` in `rank_models.py`:
```python
QUAL_MAX_MULTIMODAL = 8
QUAL_MAX_STRUCTURED = 5
QUAL_MAX_OUTPUT_LIMIT = 3
QUAL_MAX_OPEN_WEIGHTS = 2

MULTIMODAL_CAPS = {"text": 0, "image": 2, "video": 2, "pdf": 2, "audio": 2}

def _output_limit_bonus(limit: int | None) -> float:
    if not limit: return 0.0
    if limit >= 100_000: return 3.0
    if limit >= 50_000: return 2.0
    if limit >= 32_000: return 1.0
    return 0.0
```

## Input Format

`modalities` field in provider JSON can be:
- Dict with `input` list: `{"input": ["text", "image", "video"]}`
- Flat list: `["text", "image", "video"]`

Both handled by extracting `input` if dict, else using list directly.

## Example Scores

| Model | Base | Multimodal | Structured | Output Limit | Open Weights | Final |
|-------|------|------------|------------|--------------|--------------|-------|
| DeepSeek V4 Flash | 88 | +2 (image) | +5 | +3 (131k) | +2 | **100** |
| Muse Spark 1.3 | 83 | +8 (5 mods) | +5 | +3 (131k) | +0 | **99** |
| MiniMax M3 | 83 | +4 (img+vid) | +5 | +3 (131k) | +0 | **95** |
| Nemotron 3 Super | 86.4 | +0 | +5 | +3 (236k) | +2 | **96.4** |
| Nemotron 3.5 Lightning | 83 | +0 | +5 | +3 (262k) | +2 | **93** |

## Pitfall

**Do not double-count open weights.** Base scoring already gives +10 for `openness` (open_weights). The qualitative +2 is for *transparency/auditability value beyond parameter count* — it's a separate dimension. Keep both; they measure different things (base: openness as capability enabler; qualitative: openness as trust/fine-tuning enabler).

**Do not score output limit if unknown.** `None` or missing → +0. Do not guess from context window.

**Multimodal caps at +8.** Even if a model claims 6+ modalities, max is +8. The +2 per modality rule naturally caps at 4 non-text modalities.

**Provider JSON must include `modalities`, `output_limit`, `open_weights`, `structured_output`.** If missing, qualitative adjustment = 0 for that dimension. Enrich Nous Portal zero-metadata models BEFORE ranking.