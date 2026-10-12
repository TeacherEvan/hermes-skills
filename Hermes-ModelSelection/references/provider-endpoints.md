# Provider Endpoints & Free Model Detection

## openrouter
- Base URL: `https://openrouter.ai/api/v1`
- Models endpoint: `GET /v1/models`
- Auth: `Authorization: Bearer $OPENROUTER_API_KEY`
- Free detection: `pricing.prompt == "0" AND pricing.completion == "0"` OR `id` ends with `:free`
- Free suffix: `:free` (colon)
- Exclude: `openrouter/free` (router meta-model)

## kilocode
- Base URL: `https://api.kilo.ai/api/gateway`
- Models endpoint: `GET /v1/models`
- Auth: `Authorization: Bearer $KILOCODE_API_KEY`
- Free detection: `pricing.prompt == "0"` OR `id` ends with `:free`
- Free suffix: `:free` (colon)
- Exclude: `kilo-auto/free` (routing alias)

## opencode-zen (opencode-free in config)
- Base URL: `https://opencode.ai/zen/v1`
- Models endpoint: `GET /v1/models`
- Auth: `Authorization: Bearer $OPENCODE_ZEN_API_KEY`
- Free detection: `id` ends with `-free` (hyphen, no colon) OR models.dev cost is zero
- Free suffix: `-free`
- Config slug in Hermes: `opencode-free` (deprecated/removed, now uses `opencode-zen` directly)

## nous (Nous Portal OAuth)
- Base URL: `https://inference-api.nousresearch.com/v1`
- Models endpoint: `GET /v1/models` (requires OAuth token)
- Auth: `Authorization: Bearer <access_token>` from `~/.hermes/auth.json` → `providers.nous.access_token`
- Free models list: `~/.hermes/cache/nous_recommended_cache.json` → `[portal_url].data.freeRecommendedModels`
- Free detection: listed in `freeRecommendedModels` array
- Context length: from `contextLength` field in cache

## huggingface
- Base URL: `https://router.huggingface.co/v1`
- Models endpoint: `GET /v1/models`
- Auth: `Authorization: Bearer $HF_TOKEN`
- Free detection: models.dev cost is zero
- Free suffix: varies (no consistent suffix)
- Current free models: `zai-org/GLM-4.7-Flash`

## Config Slug Reference
| Env var | config.yaml provider slug |
|---------|--------------------------|
| OPENROUTER_API_KEY | openrouter |
| KILOCODE_API_KEY | kilocode |
| OPENCODE_ZEN_API_KEY | opencode-zen |
| NOUS OAuth | nous |
| HF_TOKEN | huggingface |
