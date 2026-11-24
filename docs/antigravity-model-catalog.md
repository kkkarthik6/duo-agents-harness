# Antigravity models in Duo

Checked against Google's official documentation on 2026-11-22.
The catalog now has 16 entries: six existing entries and ten Antigravity routes.
This is the researched policy catalog. The [dynamic inventory](dynamic-model-catalog.md) shows what installed CLIs currently report.
Antigravity is not installed on this Mac. Account access remains unverified.

| Antigravity model | CLI identifier | Duo role tier |
|---|---|---|
| Gemini 4 Neon | `gemini-4-neon` | large |
| Gemini 4 Carbon | `gemini-4-carbon` | large |
| Gemini 4 Argon | `gemini-4-argon` | large |
| Gemini 4 Hydrogen | `gemini-4-hydrogen` | small |
| Gemini 3.8 Flash | `gemini-3.8-flash` | small |
| Gemini 3.7 Flash | `gemini-3.7-flash` | small |
| Gemini 3.6 Flash | `gemini-3.6-flash` | small |
| Gemini 3.1 Pro | `gemini-3.1-pro-preview` | large |
| Claude Sonnet 5.5 (Thinking) | `claude-sonnet-5-5` | medium |
| Claude Opus 5.5 (Thinking) | `claude-opus-5-5` | large |

Identifiers come from [Google's identifier table](https://antigravity.google/docs/enterprise/).
The Claude entries use Google's Antigravity account, separately from the native Claude CLI account.

Tiers are Duo routing policy, not Google's model-size labels or parameter counts.
Using the larger planning families and faster execution families follows the responsibilities described in [Google's model guide](https://antigravity.google/docs/models).
This is a routing inference. A reasoning level such as Medium or High does not change the Duo tier.

Access depends on the plan. Carbon requires Ultra or Enterprise. Flash 3.7, Flash 3.6, and Pro 3.1 are Enterprise-only.
Flash 3.8 includes free access. Some other models require paid or eligible Pro access.
See the [current plan matrix](https://antigravity.google/docs/models) for complete conditions.

Orchestrator is excluded from independent role routing because it combines planning and execution within one conversation.
The same guide lists Sonnet 4.6, Opus 4.6, and GPT-OSS 120B for removal on November 30, 2026.
Those retiring families are documented here without adding unverified legacy identifiers to default routing.

## Worker support

Duo includes a native `agy` adapter. It sends one JSON prompt through standard input and closes the pipe after submission.
Each assignment starts a new process without conversation-resume arguments. The adapter reads terminal results, usage, and failures.
This follows [Google's headless protocol](https://antigravity.google/docs/cli/headless/).
Native permissions remain active; no permission bypass flag is added.

`available: true` means enabled in configuration. It does not establish local installation, subscription access, or account quota.
After installing and signing in to Antigravity, run `agy models` to confirm account-specific identifiers.
Duo checks CLI flags during discovery and actual model access during invocation.
Existing model preferences remain first in the catalog. The new routes are additional choices.

## Files and validation

- [Catalog entries](../engine/antigravity_models.py)
- [Native adapter](../engine/cli_adapters.py)
- [Protocol and catalog tests](../tests/test_antigravity.py)
- [Focused verification](../Validation%20Logs/Generic%20Agents/antigravity-verification.log)
- [Core checks](../Validation%20Logs/Generic%20Agents/antigravity-core-checks.log)

Tests use local executable fixtures. Live Antigravity access cannot be checked without the CLI and account.

Latest edit: 2026-11-22 20:18 EST (-0500).

Verification: 77 focused tests, seven core checks, and one local catalog API check passed.
The six Antigravity tests include a real local executable fixture, without a model service call.

Duo core 1.17.1 installed and its sealed files verified 2026-11-22 20:19 EST (-0500). Restart Duo to load it.
