# TODO

## Per-pane usage

Show what the agent session in the focused pane has consumed, above the
account-wide totals.

- Read the focused pane id from `HERDR_PLUGIN_CONTEXT_JSON`, then
  `herdr agent get <pane>` for the agent kind and session id.
- Claude Code: the transcript is
  `~/.claude/projects/<cwd with / replaced by ->/<session id>.jsonl`. Each
  `assistant` entry carries `message.model` and `message.usage` with input,
  output, cache-write and cache-read token counts. Dedupe on
  `(message.id, requestId)` since a response is logged once per content block.
- Render a "This pane" block: model, responses, tokens by type, session
  duration. Omit the block when the pane has no recognised agent session.
- Token counts cannot be turned into a percentage of the subscription window,
  because the limits are not published in tokens. Show raw tokens. An
  API-price-equivalent cost would need a per-model price table; decide later.
- The same block is needed for every provider, not only Claude. Codex sessions
  live under `~/.codex/sessions/` as JSONL with their own usage shape.

## More providers

- Codex (OpenAI): read the OAuth token from `~/.codex/auth.json`, fetch the
  account rate-limit windows (primary 5-hour and secondary weekly), and map
  them onto `UsageWindow`. Add it as `providers/codex.py` and register it in
  `PROVIDERS`.
- Any provider added here also needs the per-pane block above.

## Distribution

- Add the `herdr-plugin` GitHub topic so the herdr marketplace indexes the repo.
- Pick a license.
