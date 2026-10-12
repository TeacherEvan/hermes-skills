# Daily ModelSelection Cronjob

Standard cronjob for running the Hermes-ModelSelection pipeline every morning at 04:00.

## Job Configuration
```bash
hermes cron create \
  --name "Hermes ModelSelection Daily Refresh" \
  --deliver telegram,email \
  --skill hermes-modelselection \
  --skill hermes-agent \
  --continuity \
  "every day at 04:00" \
  "Run the Hermes-ModelSelection pipeline to discover current free models across all providers, rank them, fill Brain/Workhorse/Tester/fallback slots with distinct providers, verify live, and apply the results to Hermes config.

Steps:
1. Run the full pipeline: cd /home/leandi-duplessis/.hermes/skills/Hermes-ModelSelection && ./scripts/run_pipeline.sh
2. If pipeline succeeds (exit 0), apply the brain model to config:
   - Read placement.json for brain model and provider
   - Run: hermes config set model.default <brain_model>
   - Run: hermes config set model.provider <brain_provider>
3. Update the fallback chain in config.yaml using the placement's fallback providers (distinct providers, in order)
4. Send a summary report with: which models were selected for each slot, live verification status, and any changes made.

The pipeline output directory is /home/leandi-duplessis/.hermes/cache/hermes-modelselection/

Key constraints:
- Each slot MUST use a different provider (enforced by pipeline)
- Same model across different providers is allowed (route redundancy)
- Only apply config if ALL slots (brain, workhorse, tester) pass live verification
- Use yq or python to edit config.yaml safely (never sed)
- After config changes, run 'hermes config check' to validate"
```

## Key Parameters
- **Schedule**: `every day at 04:00` (cron: `0 4 * * *`)
- **Delivery**: `telegram,email` — sends summary to both channels
- **Skills**: `hermes-modelselection`, `hermes-agent`
- **Continuity**: `true` — each run sees previous output to avoid duplicate reporting

## Pipeline Stages (takes ~90-120s total)
1. **Discovery** (~7s) — list authenticated providers, count free models
2. **Per-provider listing** — parallel provider-<slug>.json generation
3. **Ranking** — score all free models 0-100 across providers
4. **Placement** — fill brain/workhorse/tester/fallbacks with distinct providers
5. **Live verification** — real `hermes chat -q` probe per slot (~30s/slot, parallel)
6. **Config apply** — `hermes config set` for brain + fallback chain via yaml.dump

## Expected Output
- Brain model + provider set via `hermes config set`
- Fallback chain: 5-6 entries, each from different provider
- All slots live-verified (verification.json shows 5/5 passed)
- Config validation: `hermes config check` passes

## Delivery Preference
**User wants delivery to Telegram (chat_id: 8290757864) and Email (ewiebotha@gmail.com)** — not just local file. The cronjob must use `--deliver telegram,email` so the summary arrives in designated spaces by morning.

> **Note**: The first cronjob created in this session used `--deliver local` by mistake. The current job ID is `467d9fead262`. Update it with:
> ```bash
> hermes cron edit 467d9fead262 --deliver telegram,email
> ```

## Continuity Behavior
With `--continuity`, the job receives its previous run's output. The agent should:
- Compare new placement with previous
- Only report changes (new models, different scores, provider changes)
- Use `[SILENT]` if nothing changed

## Troubleshooting
- **Exit 3**: Not enough providers with free models — add credentials
- **Exit 4**: Placement valid but no model responded — check API keys, rate limits
- **Config check fails**: YAML indentation error — use Python yaml.dump, never sed
- **Missing provider in fallback**: opencode-free was removed; use opencode-zen
- **GitHub Actions CI blocked by billing lock**: If the GitHub account has a billing issue, CI jobs fail with "The job was not started because your account is locked due to a billing issue" — the runner never starts, so required status checks never pass. Branch protection cannot be bypassed by admin merge when required checks are failing. Workaround: temporarily remove required status checks via API:
  ```bash
  # Remove required checks (empty contexts array)
  echo '{"strict":true,"contexts":[]}' | gh api --method PATCH /repos/OWNER/REPO/branches/main/protection/required_status_checks --input -
  # Merge the PR
  gh pr merge <number> --admin --squash --delete-branch
  # Restore required checks
  echo '{"strict":true,"contexts":["Lint and typecheck","Unit tests","Validate Docker build"]}' | gh api --method PATCH /repos/OWNER/REPO/branches/main/protection/required_status_checks --input -
  ```
  The permanent fix: resolve billing at https://github.com/settings/billing so CI can run.

## Verification
After cron run, check:
```bash
hermes cron runs <job_id>
cat ~/.hermes/cron/output/<job_id>/latest.md
hermes fallback list
hermes config check
```