# herdr-usage

A [herdr](https://herdr.dev) plugin that shows how much of your coding-agent
subscription you have used. It opens a popup with one progress bar per
rate-limit window (session, weekly, per-model) and the time each one resets.

Supported services:

- **Claude Code** — reads the same numbers the `/usage` command in Claude Code
  shows, via the account's OAuth usage endpoint.

Everything is Python 3.9+ standard library. No build step, no dependencies.

## Install

Link a local checkout while developing:

```sh
herdr plugin link /path/to/herdr-usage
```

Or install from GitHub:

```sh
herdr plugin install kalbhor/herdr-usage
```

Open the popup:

```sh
herdr plugin pane open --plugin kalbhor.usage --entrypoint usage
```

Bind a key in `~/.config/herdr/config.toml`, then `herdr server reload-config`:

```toml
[[keys.command]]
key = "prefix+u"
type = "plugin_action"
command = "kalbhor.usage.open"
description = "subscription usage"
```

Inside the popup: `r` refreshes now, `q` or `Esc` closes it. It refreshes on
its own every 5 minutes while open. Results under a minute old are reused when
the popup is reopened, because the Claude usage endpoint rate limits after a
handful of calls in a few minutes.

Print once from any shell (useful for scripts and debugging):

```sh
python3 herdr_usage.py once
```

## Configuration

Optional `config.json` in the plugin config directory
(`herdr plugin config-dir kalbhor.usage`):

```json
{
  "refresh_seconds": 300,
  "providers": ["claude"]
}
```

`refresh_seconds` must be at least 30. `providers` defaults to every registered provider. The last successful result
is cached in the plugin state directory so a failed refresh still shows the
previous numbers, marked stale.

## How Claude Code usage is read

The plugin reads the OAuth token Claude Code already stores locally
(`~/.claude/.credentials.json`, or `CLAUDE_CONFIG_DIR`, or the macOS keychain
entry `Claude Code-credentials`) and calls
`https://api.anthropic.com/api/oauth/usage`. The token is only sent to that
endpoint and is never written anywhere by this plugin. If the token has
expired, open Claude Code once; it refreshes the token itself.

This differs from tools like `ccusage`, which sum token counts out of local
transcript files and estimate cost at API prices. Those cannot know the real
subscription limit, so their "percent used" is an approximation. The usage
endpoint returns the actual utilization and reset time per window.

## Adding a provider

1. Create `providers/<service>.py` with a `Provider` subclass. Set `id` and
   `title`, and implement `fetch()` returning a `UsageReport` whose `windows`
   are `UsageWindow(label, percent, resets_at)`. Raise `ProviderError` with a
   user-facing message when credentials are missing or the request fails.
2. Register the class in `PROVIDERS` in `providers/__init__.py`.

The renderer and cache are provider-agnostic.
