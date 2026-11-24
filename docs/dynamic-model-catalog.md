# Dynamic CLI model discovery

Duo discovers installed, supported CLIs and queries their current model choices.
The researched model catalog now supplies role policy. It no longer establishes live availability for real role runs.

Run `duo models` to refresh and show the current list. The command does not start a task.
The existing Check again action also refreshes discovery.

## Refresh and routing

- Startup queries installed CLIs.
- A new real role run forces a fresh query for its project.
- Later assignments refresh expired snapshots. The default cache lifetime is 60 seconds.
- Executable changes, removal, and changes to authentication-file metadata invalidate cached snapshots.
- Model-not-found and expired-login failures invalidate the affected project snapshot.
- An explicit refresh bypasses the cache.

Absent CLIs contribute no models. Failed discovery contributes no models and reports an error.
Duo does not label an old list as current after a failed refresh.
Running assignments keep their selected model. Updated choices apply to later assignments.
Existing run selections still limit which CLIs can receive work; discovery does not attach extra workers to a running task.

Every reported model appears in the live inventory. Existing policy supplies known model tiers.
Unknown models have status `needs_tier` and cannot receive automatic assignments until a tier is assigned.
Duo does not estimate model size from a name or a reasoning-effort label.
Exact, documented Antigravity effort aliases reuse their explicit family policy.

Add a policy without replacing the existing catalog:

```json
{
  "model_tiers": {
    "local-agent": {
      "planner-v1": "large",
      "critic-v1": "medium",
      "coder-v1": "small"
    }
  }
}
```

Save this configuration in `~/Duo/config.json`, then restart Duo to reload it.
An explicit `available: false` in `model_catalog` remains disabled.

## Provider discovery methods

| CLI | Method | What it establishes |
|---|---|---|
| Codex | Initialize a private stdio app-server, then call `model/list` with pagination | CLI-reported catalog; it can contain cached or bundled choices |
| Claude | Send the SDK `initialize` control request and read its model metadata | Model choices reported by the installed CLI |
| Antigravity | Run `agy models` | Account-specific identifiers reported by Antigravity |
| Configured CLI | Run its explicit model-list argument vector | Reported choices in the configured JSON or text format |

Codex's catalog is not an entitlement check. See [OpenAI's model-list documentation](https://learn.chatgpt.com/docs/app-server#list-models-modellist) and [account-access limitations](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server).
The Claude control handshake follows the [official SDK implementation](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/_internal/query.py).
Google documents `agy models` in its [headless CLI guide](https://antigravity.google/docs/cli/headless/).

Discovery sends no inference prompt and starts no task or Codex thread.
Claude metadata discovery uses safe mode with tools and MCP servers disabled. Project plugins do not execute during this query.
Consequently, project-specific plugin model changes are not an account-access guarantee.
Duo reads authentication-file timestamps and sizes for invalidation, without reading or returning credential values.
Only model fields from the control response enter the inventory; account details are discarded.

Listed models can still fail because of quota, billing limits, login state, or provider restrictions.
Actual assignment results remain the access check. Discovery never promises remaining quota.
Some clients can return cached catalogs even when Duo makes a fresh request.

## Custom discovery configuration

Add these fields to the CLI's existing adapter definition:

```json
{
  "model_list_args": ["models", "--json"],
  "model_list_format": "json"
}
```

JSON must contain an array of IDs or model objects, optionally inside `models` or `data`.
Objects need an `id`, `model`, `slug`, or supported resolved-model field.
Hidden and explicitly unavailable entries are excluded.
Text format expects an identifier, at least two spaces, and a display name on each model line.
Listing commands use argument vectors, without shell interpolation.
An adapter without a supported listing method reports `unsupported`; it does not silently use configured models as live results.

## API and settings

`GET /api/models/catalog` returns:

- `models`: choices reported by installed CLIs, with tier status and check time.
- `configured_models`: policy entries, separate from current discovery.
- `discovery`: CLI installation and query status, `checked_at`, and `expires_at`.

Use `GET /api/models/catalog?refresh=1` for a forced refresh.
`POST /api/rescan` refreshes CLI detection and model discovery.
`model_discovery_ttl_seconds` defaults to 60. `model_discovery_timeout_seconds` defaults to 15 per CLI.
Snapshots stay in memory and are scoped by project. Restarting the engine discards them.

## Validation

[Discovery tests](../tests/test_model_discovery.py) cover refresh, removal, errors, aliases, tier policy, pagination, and control requests.
Executable fixtures reject non-metadata requests. API tests verify that static policy entries are separate from live choices.
Live metadata checks on this Mac found Codex and Claude. Antigravity was absent.
No model inference was needed for those checks.
