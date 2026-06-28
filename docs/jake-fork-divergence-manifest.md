# Jake Hermes fork divergence manifest

Last audited: 2026-06-25

This document is the durable orientation map for Jake's Hermes fork. Its job is to save future upgrade sessions from rediscovering the fork's local feature set from raw `git log` every time.

It is intentionally a feature manifest, not a perfect design doc. Use it to answer: "what does this fork intentionally carry that upstream Hermes may not?" and "what tests/symbols should an upgrade preserve?"

## Scope and anchors

Current branch audited:

- Branch: `jake/integrate-v2026.6.19-20260624-234414`
- Audited feature/code HEAD: `438431705` (`fix(dashboard): default sessions to recent activity`). This manifest may live in a later docs-only commit.
- Current live branch at audit time: `jake/live-stable-v2026.6.5-refresh` at `f7e43b8c5d4b8f5b0ca8c0c38abee19febaf548a`
- Upstream release integrated: `v2026.6.19`
  - tag object: `681cd638d`
  - peeled commit: `2bd1977d8fad185c9b4be47884f7e87f1add0ce3`
- Integration merge commit: `6f7057a93` (`merge: integrate Hermes v2026.6.19`)
  - parent 1: `f7e43b8c5` (Jake live fork before v0.17.0 integration)
  - parent 2: `2bd1977d8` (upstream release commit)

Command used for the main commit set:

```bash
git cherry -v v2026.6.19 HEAD
```

At audit time, every commit in the cherry list was still fork-local relative to both `v2026.6.19` and the fetched `upstream/main` (`d6269da7f`). That can change later if upstream accepts equivalent work, so future agents should re-run `git cherry -v upstream/main HEAD` before carrying a delta forward.

## How to use this during the next upgrade

1. Pick the upstream release tag and create an integration branch from Jake's current live branch.
2. Open this file before resolving conflicts.
3. For each feature area below, verify the user-facing behavior, not just file presence. Upstream often moves handlers, so stale code can remain in old files while runtime dispatch bypasses it.
4. Run the preservation tests listed under each area, plus the broader fork gate from the upgrade playbook.
5. If a behavior is no longer needed or has landed upstream, mark it here with the upstream commit/PR instead of silently dropping it.
6. Do not rewrite a pushed/live branch just to make history pretty. If a cleaned history is desired, build a separate candidate branch and use this manifest as the grouping guide.

## Feature areas to preserve

### 1. File-backed durable memory search

Purpose: Jake's Markdown/Git memory cabinet is the source of truth; SQLite and vector data are rebuildable indexes. This fork gives Hermes a `memory_search` tool over ChatWorkspace, Hermes memories, LocalOps, and imported OpenClaw history.

Core behavior:

- Search local durable memory without browser/web access.
- Support source filters, path filters, chunk and observation granularity, and compact TOON rendering.
- Prefer `memory_search` as the first recall layer for durable project/user context, preferences, setup facts, and prior decisions; use `session_search` for raw past-chat transcripts and `search_files`/`read_file` for live disk/source-code state.
- Keep the coding/ACP toolsets exposing `memory_search` alongside file/search tools so project-context grounding remains available in code workspaces.
- Keep Markdown files canonical; indexes are caches.
- Preserve source/file/line provenance so results can be audited.
- Preserve OpenClaw legacy session import paths.

Key files:

- `tools/memory_search_tool.py`
- `tools/session_search_tool.py`
- `tools/file_tools.py`
- `tools/code_execution_tool.py`
- `tests/tools/test_memory_search_tool.py`
- `tests/tools/test_session_search.py`
- `toolsets.py`
- `agent/prompt_builder.py`
- `tests/agent/test_prompt_builder.py`
- `tests/agent/test_coding_context.py`
- `tools/toon_renderer.py`
- `tests/tools/test_toon_renderer.py`
- `scripts/spikes/toon_memory_render_benchmark.py`

Commits:

- `c5b1f45f8` - initial local memory search tool and prompt/toolset wiring.
- `a099adfd4` - OpenClaw legacy sessions import support.
- `a3afaa7bc` - opt-in TOON rendering for compact results.
- `78bdc3986` - observation-granularity search with typed observation lines and provenance.

Preservation checks:

```bash
python -m pytest tests/tools/test_memory_search_tool.py tests/tools/test_toon_renderer.py -o 'addopts=' -q
python -m pytest tests/agent/test_prompt_builder.py tests/agent/test_coding_context.py tests/tools/test_session_search.py tests/tools/test_memory_search_tool.py -o 'addopts=' -q
```

Live smoke for Jake's profile:

```text
memory_search query: Noah one stamp no tracking beta shipping
expected: hybrid mode, Gemini backend, sqlite-vec index, complete observation vector coverage, MamiyaPan / Noah / one-stamp / no-tracking hits
```

### 2. Semantic memory search with Gemini, persisted vectors, and sqlite-vec

Purpose: make memory search fast and useful at Jake's corpus size by matching OpenClaw-style persisted embeddings rather than doing slow foreground rebuilds.

Core behavior:

- Default retrieval is hybrid keyword + semantic.
- Gemini embeddings are the default semantic backend for Jake's setup.
- Normal search embeds only the query; document/chunk vectors are persisted.
- Cold rebuilds are bounded and explicit/background preindexing is preferred.
- Vectors are stored as float32 blobs and queried via sqlite-vec when coverage is complete.

Key files:

- `tools/memory_search_tool.py`
- `tests/tools/test_memory_search_tool.py`
- `pyproject.toml`
- `uv.lock`

Commits:

- `99cfed0e7` - hybrid semantic search.
- `299ccf8f0` - default to hybrid and support Gemini embeddings.
- `706879ded` - make Gemini embeddings the default semantic backend.
- `c78b39c3b` - bound cold Gemini semantic rebuilds so searches do not hang.
- `796d971d1` - persist Gemini embeddings.
- `f7e43b8c5` - use sqlite-vec for KNN retrieval.

Preservation checks:

```bash
python -m pytest tests/tools/test_memory_search_tool.py -o 'addopts=' -q
```

Config/runtime facts to verify in Jake's live profile:

- `mode: hybrid`
- `strategy: semantic+keyword_rrf`
- backend `gemini:gemini-embedding-2`
- `vector_index: sqlite-vec`
- missing vector count should be `0` for the preindexed corpus

### 3. Memory tool durability and background review routing

Purpose: keep Hermes's durable memory writes compact, atomic, and safe when the store is near capacity or when background review writes notes.

Core behavior:

- Background review notes route through durable storage with proper provenance.
- Full-store memory add errors return compact structured context instead of appending the entire memory store into the model loop.
- Memory writes support batch remove/replace/add patterns so cleanup and additions can happen atomically.

Key files:

- `tools/memory_tool.py`
- `agent/background_review.py`
- `tests/tools/test_memory_tool.py`
- `tests/run_agent/test_background_review_summary.py`

Commits:

- `e125df23f` - route background review notes to durable storage.
- `affcc80b6` - compact full-store add errors.
- `61b186361` - stable-refresh conflict finish that touched `tools/memory_tool.py`; no separate product feature, but keep it in the audit trail.

Preservation checks:

```bash
python -m pytest tests/tools/test_memory_tool.py tests/run_agent/test_background_review_summary.py -o 'addopts=' -q
```

### 4. Discord guild message search

Purpose: allow Hermes to search Discord guild/channel history through Jake's configured Discord tool surface, separate from passive gateway context.

Core behavior:

- Discord tool exposes guild message search with attachment/timestamp context.
- Useful because the gateway itself cannot retroactively see arbitrary channel history in this chat context.

Key files:

- `tools/discord_tool.py`
- `tests/tools/test_discord_tool.py`

Commits:

- `a21110f43` - Discord guild message search.

Preservation checks:

```bash
python -m pytest tests/tools/test_discord_tool.py -o 'addopts=' -q
```

### 5. Gateway display and progress UX

Purpose: make long-running gateway/tool work legible without noisy or misleading status messages.

Core behavior:

- Better gateway tool progress labels.
- Terminal progress previews hide low-signal shell safety prologues like `set -euo pipefail` and show the first meaningful command line instead.
- Terminal heredoc previews summarize script bodies (for example `python - <<'PY'`) rather than showing only the wrapper line.
- Avoid chat chunk pagination markers leaking into assistant-visible or user-facing content.
- Retry/memory-full/provider-status noise reduced so the user sees signal rather than internal churn.

Key files:

- `agent/display.py`
- `agent/tool_executor.py`
- `gateway/display_config.py`
- `gateway/platforms/api_server.py`
- `gateway/platforms/base.py`
- `gateway/run.py`
- `agent/chat_completion_helpers.py`
- `tests/agent/test_display.py`
- `tests/gateway/test_display_config.py`
- `tests/gateway/test_platform_base.py`
- `tests/gateway/test_run_progress_topics.py`
- `tests/gateway/test_stream_events.py`
- `tests/run_agent/test_retry_status_buffer.py`

Commits:

- `6adab27d8` - omit chat chunk pagination markers.
- `feabad30f` - improve gateway tool progress labels.
- `7731faed9` - reduce retry and memory-full noise.
- `d321360c6` - make terminal command previews show meaningful command bodies.

Preservation checks:

```bash
python -m pytest tests/agent/test_display.py tests/gateway/test_display_config.py tests/gateway/test_platform_base.py tests/gateway/test_run_progress_topics.py tests/gateway/test_stream_events.py tests/run_agent/test_retry_status_buffer.py -o 'addopts=' -q
```

### 6. Voice note transcription, transcript persistence, transcript echo, and active-run voice steering

Purpose: make Discord/native voice notes useful in Hermes, including local searchable capture and active-run steering.

Core behavior:

- Inbound native voice messages are transcribed and injected into the agent turn.
- Optional local transcript persistence writes daily markdown logs under `stt.voice_transcripts_dir`.
- User-facing transcript echo is a separate toggle: `stt.echo_voice_transcripts`.
- In `busy_input_mode: steer`, voice/audio media is transcribed before `agent.steer(...)`; the steer payload must contain the transcript, not `(The user sent a message with no text content)`.
- Voice attachments bypass Discord text batching and dispatch immediately.

Key files:

- `gateway/config.py`
- `gateway/run.py`
- `plugins/platforms/discord/adapter.py`
- `tests/gateway/test_voice_transcript_persistence.py`
- `tests/gateway/test_voice_transcript_echo.py`
- `tests/gateway/test_discord_voice_steer.py`
- `tests/gateway/test_busy_session_ack.py`
- `tests/gateway/test_stt_config.py`

Commits:

- `2aa6e1cfb` - persist voice transcripts.
- `0774abbbf` - stop hardcoded transcript echo after Jake initially preferred no echo.
- `93ea5e5f8` - add `stt.echo_voice_transcripts` config toggle and re-enable it for Jake when configured.
- `b89de5b55` - transcribe voice notes before steering into active runs.

Preservation checks:

```bash
python -m pytest tests/gateway/test_voice_transcript_persistence.py tests/gateway/test_voice_transcript_echo.py tests/gateway/test_discord_voice_steer.py tests/gateway/test_busy_session_ack.py tests/gateway/test_stt_config.py -o 'addopts=' -q
```

Live smoke:

- Send a Discord voice note while idle. The agent should see the transcript.
- If `stt.persist_voice_transcripts: true`, verify a daily markdown entry is appended.
- If `stt.echo_voice_transcripts: true`, verify the `🎙️ "..."` echo appears.
- In `display.busy_input_mode: steer`, send a voice note during a running tool call. The acknowledgement may say it was steered, and the payload must be the transcript.

### 7. Gateway busy mode and mid-run steering

Purpose: give Jake control over what follow-up messages do while Hermes is already working.

Core behavior:

- `/busy status`, `/busy queue`, `/busy steer`, and `/busy interrupt` work from gateway sessions.
- `display.busy_input_mode` is persisted in config.
- Queue mode avoids interrupting active work and keeps FIFO semantics.
- Steer mode uses `agent.steer(...)` to inject text after the next tool call without canceling the run.
- Interrupt mode remains available for hard turn replacement.
- Active subagent work demotes unsafe interrupts to queue-like behavior.

Key files:

- `gateway/run.py`
- `gateway/slash_commands.py`
- `hermes_cli/commands.py`
- `agent/onboarding.py`
- `tests/gateway/test_busy_command.py`
- `tests/gateway/test_busy_session_ack.py`
- `tests/gateway/test_subagent_protection_30170.py`
- `tests/run_agent/test_steer.py`

Commits:

- `a4bfec013` - enable gateway `/busy` command.
- `73fe8e1a0` - restore `/busy` and related fork-only gateway behavior after the v0.17.0 merge.
- `b89de5b55` - voice-note steering fix, covered under voice but also part of busy/steer behavior.

Preservation checks:

```bash
python -m pytest tests/gateway/test_busy_command.py tests/gateway/test_busy_session_ack.py tests/gateway/test_subagent_protection_30170.py tests/run_agent/test_steer.py -o 'addopts=' -q
```

### 8. Discord approval and clarify UX

Purpose: make approval and clarify prompts visible, auditable, and safe in Discord, especially on mobile.

Core behavior:

- Approval prompts are content-first: critical command/reason text appears in message content, not only in embeds/components.
- Security-scan approvals show detected suspicious strings, command preview, and visible rendering for invisible Unicode characters.
- Sensitive home-path detection is restored.
- Command context remains visible in security approvals.
- Discord clarify prompts are content-first: full question and full numbered choices appear in message content, while buttons are compact selectors (`1`, `2`, `3`, etc.) plus `Other`.
- Clarify open-ended prompts use plain message content with a reply instruction, not embed-only text.
- Clarify question/choice text renders invisible/format Unicode visibly (for example `[U+FE0F]`).
- Approval pings still work when configured and should prepend/augment the rich prompt rather than replacing it.

Key files:

- `tools/approval.py`
- `gateway/run.py`
- `plugins/platforms/discord/adapter.py`
- `tests/gateway/test_discord_exec_approval_prompt.py`
- `tests/gateway/test_discord_clarify_buttons.py`
- `tests/tools/test_approval.py`
- `tests/tools/test_command_guards.py`

Commits:

- `f4f86a67d` - clarify Discord approval prompts.
- `ac989cecc` - make Discord approval prompts content-first.
- `de57a1d77` - clarify security-scan approval prompts.
- `46f0d91b9` - focus security approvals on detected strings.
- `af32a5eda` - render invisible security evidence visibly.
- `99c125601` - keep command context in security approvals.
- `0546e28ca` - make Discord clarify prompts content-first.
- `f9ac959d3` - restore sensitive home-path detection after upstream merge.
- `ce08ddbcf` - restore rich approval prompt after UI regression.
- `bf59e0464` - restore content-first Discord clarify prompt after v0.17.0 merge regression.

Preservation checks:

```bash
python -m pytest tests/gateway/test_discord_exec_approval_prompt.py tests/gateway/test_discord_clarify_buttons.py tests/tools/test_approval.py tests/tools/test_command_guards.py -o 'addopts=' -q
```

Live smoke:

- Trigger a shell command that requires approval.
- Confirm Discord content asks whether to run it, shows the command in a code block, shows the reason, and keeps buttons under that content.
- Trigger a security-scan approval and confirm suspicious evidence is visible.
- Trigger a Discord `clarify` call with a long question and long choices. Confirm the full question and numbered choices are visible in message content, buttons are compact numeric selectors, and `Other` switches to typed-response mode.

### 9. Long-turn mentions and attention behavior

Purpose: ping Jake in Discord when attention is needed without spamming normal replies.

Core behavior:

- Approval requests ping immediately when configured.
- Final response mentions are time-threshold based only.
- Tool-call-count based mention behavior is intentionally ignored/removed.
- Config path: `display.platforms.discord.long_turn_mention`.

Key files:

- `gateway/run.py`
- `gateway/display_config.py`
- `tests/gateway/test_long_turn_mentions.py`

Commits:

- `b4957b99d` - configurable long-turn Discord mentions.
- `d19f60938` - simplify attention mention behavior after merge.

Preservation checks:

```bash
python -m pytest tests/gateway/test_long_turn_mentions.py -o 'addopts=' -q
```

### 10. Discord `/model` status and picker UX

Purpose: keep model state inspectable from Discord without forcing an interactive picker or hiding the current model.

Core behavior:

- Bare `/model` shows current model/provider and picker instructions.
- `/model status`, `/model current`, `/model show`, and `/model info` return text-only status.
- The model picker placeholder/content shows the current model.
- Upstream moved slash-command handling into `gateway/slash_commands.py`; future upgrades must patch the live handler, not stale paths.

Key files:

- `gateway/slash_commands.py`
- `gateway/run.py`
- `plugins/platforms/discord/adapter.py`
- `tests/gateway/test_model_command_status.py`
- `tests/gateway/test_discord_model_picker.py`

Commits:

- `c49bd419a` - add model status command.
- `1f787e01b` - show current model in picker.
- `83df29665` - show model picker status in content.
- `dbbe60625` - restore current-model display after v0.17.0 merge path moved to `gateway/slash_commands.py`.

Preservation checks:

```bash
python -m pytest tests/gateway/test_model_command_status.py tests/gateway/test_discord_model_picker.py -o 'addopts=' -q
```

Live smoke:

- `/model`
- `/model status`
- Change model only after status/picker display is verified.

### 11. Provider, model, reasoning, and routing overrides

Purpose: keep Jake's provider/model setup working across API server, Copilot, auxiliary routing, and reasoning-effort surfaces.

Core behavior:

- API server model and reasoning overrides are honored.
- Copilot model switches canonicalize correctly.
- Copilot/GitHub Models reasoning supports `xhigh`; unsupported `max` should clamp to `xhigh` rather than a lower level when applicable.
- Auxiliary auto routing uses the correct transport and respects the main provider/model path.
- Provider-health cache isolation prevents one test/provider failure from poisoning main-first auxiliary behavior.

Key files:

- `gateway/platforms/api_server.py`
- `hermes_cli/models.py`
- `hermes_cli/providers.py`
- `hermes_cli/runtime_provider.py`
- `plugins/model-providers/copilot/__init__.py`
- `agent/auxiliary_client.py`
- `tests/gateway/test_api_server.py`
- `tests/hermes_cli/test_model_validation.py`
- `tests/hermes_cli/test_model_switch_copilot_api_mode.py`
- `tests/hermes_cli/test_runtime_provider_resolution.py`
- `tests/providers/test_provider_profiles.py`
- `tests/agent/test_auxiliary_main_first.py`

Commits:

- `37b16bb73` - honor API reasoning overrides.
- `6a8ce27a1` - honor API server model overrides.
- `b327035c7` - canonicalize Copilot model switches.
- `73224a581` - route auxiliary auto model overrides through correct transport.
- `789611291` - support Copilot Opus 4.8 max reasoning.
- `73fe8e1a0` - restore Copilot xhigh behavior and isolate auxiliary main-first tests after merge.

Preservation checks:

```bash
python -m pytest tests/gateway/test_api_server.py tests/hermes_cli/test_model_validation.py tests/hermes_cli/test_model_switch_copilot_api_mode.py tests/hermes_cli/test_runtime_provider_resolution.py tests/providers/test_provider_profiles.py tests/agent/test_auxiliary_main_first.py -o 'addopts=' -q
```

### 12. ACP session/provenance/thought-level behavior

Purpose: keep ACP/IDE integrations stable across compression-rotated sessions and expose reasoning/thought-level session config where needed.

Core behavior:

- ACP advertises thought-level session config.
- ACP emits session provenance metadata for compression rotation.
- ACP resolves compression-rotated session IDs/aliases instead of losing session continuity.

Key files:

- `acp_adapter/server.py`
- `acp_adapter/session.py`
- `tests/acp/test_session.py`
- `tests/acp_adapter/test_acp_commands.py`

Commits:

- `9df6ee707` - advertise thought-level ACP session config.
- `ad7a8b61f` - emit session provenance metadata for compression rotation. This appears to be a cherry-pick/forward-port of upstream-style work after the audited release tag, but it is still fork-local relative to `v2026.6.19`.
- `65112b724` - resolve compression-rotated ACP session IDs.

Preservation checks:

```bash
python -m pytest tests/acp/test_session.py tests/acp_adapter/test_acp_commands.py -o 'addopts=' -q
```

### 13. Native image, vision, and OCR routing

Purpose: keep image handling reliable for large screenshots and provide a fast OCR-specific tool.

Core behavior:

- Native image payloads shrink/retry after provider 413 or image-size errors.
- Aggregate native image payloads can be shrunk, not just individual tool images.
- OCR has a dedicated fast `ocr_extract` tool separate from general visual reasoning.
- Vision routing fast path continues to work with native image support.

Key files:

- `run_agent.py`
- `agent/conversation_loop.py`
- `agent/conversation_compression.py`
- `tools/vision_tools.py`
- `tests/run_agent/test_image_shrink_recovery.py`
- `tests/tools/test_ocr_extract_tool.py`
- `tests/tools/test_vision_native_fast_path.py`
- `toolsets.py`

Commits:

- `88228aaeb` - shrink aggregate native image payloads on 413.
- `ab4568296` - add fast OCR extraction tool.

Preservation checks:

```bash
python -m pytest tests/run_agent/test_image_shrink_recovery.py tests/tools/test_ocr_extract_tool.py tests/tools/test_vision_native_fast_path.py -o 'addopts=' -q
```

### 14. Codex / GitHub Responses compatibility

Purpose: avoid provider-specific replay bugs and noisy status while using Codex/GitHub Responses-like transports.

Core behavior:

- Skip GitHub Responses message-item replay when it would corrupt/resend state.
- Reduce no-first-byte/retry noise while preserving useful diagnostics.

Key files:

- `agent/codex_responses_adapter.py`
- `agent/transports/codex.py`
- `agent/chat_completion_helpers.py`
- `tests/run_agent/test_run_agent_codex_responses.py`
- `tests/agent/test_codex_ttfb_watchdog.py`
- `tests/run_agent/test_retry_status_buffer.py`

Commits:

- `89389b4df` - skip GitHub Responses message item replay.
- `7731faed9` - retry/memory-full noise reduction, also listed under display UX.

Preservation checks:

```bash
python -m pytest tests/run_agent/test_run_agent_codex_responses.py tests/agent/test_codex_ttfb_watchdog.py tests/run_agent/test_retry_status_buffer.py -o 'addopts=' -q
```

### 15. Command approval allowlist and command-guard behavior

Purpose: keep Jake's permanent command allowlist usable while preserving hard security blocks.

Core behavior:

- Permanent command allowlist globs such as `podman *` and `bash -c *` can bypass approvals.
- Compound shell operators and hardline dangerous commands remain protected.
- Sensitive path detection still catches absolute home/config paths.

Key files:

- `tools/approval.py`
- `tests/tools/test_command_guards.py`
- `tests/tools/test_approval.py`

Commits:

- `73fe8e1a0` - restore permanent allowlist glob behavior after the merge.
- `f9ac959d3` - restore sensitive home-path detection.

Preservation checks:

```bash
python -m pytest tests/tools/test_command_guards.py tests/tools/test_approval.py -o 'addopts=' -q
```

### 16. Dashboard sessions recent-activity ordering

Purpose: keep the dashboard Sessions page useful by ordering conversations by the most recently messaged / active session rather than original creation time.

Core behavior:

- `/api/sessions` defaults to `order=recent`.
- `order=recent` sorts by latest message/activity across compression chains, so an old but newly-used conversation returns to the first page.
- `order=created` remains available for original-start-time ordering.
- The dashboard frontend API client also defaults to `recent`, so `/sessions` asks for the intended ordering explicitly.

Key files:

- `hermes_cli/web_server.py`
- `web/src/lib/api.ts`
- `tests/hermes_cli/test_web_server.py`

Commits:

- `438431705` - default dashboard sessions to recent activity.

Preservation checks:

```bash
python -m pytest \
  tests/hermes_cli/test_web_server.py::TestWebServerEndpoints::test_get_sessions_defaults_to_recent_message_order \
  tests/hermes_cli/test_web_server.py::TestWebServerEndpoints::test_get_sessions_order_recent_surfaces_compression_tip \
  tests/hermes_cli/test_web_server.py::TestWebServerEndpoints::test_get_sessions_rejects_unknown_order_value \
  -o 'addopts=' -q
npm --workspace web run typecheck
npm --workspace web run build
```

Live smoke:

- Restart the dashboard process/service after rebuilding `web` into `hermes_cli/web_dist`.
- Fetch `/api/sessions?limit=N` and `/api/sessions?limit=N&order=recent` with the dashboard session token; their session id order should match.
- Fetch `/api/sessions?limit=N&order=created`; it may differ and should preserve the explicit old behavior.

### 17. MCP schema normalization for provider-compatible function schemas

Purpose: keep external MCP tools usable across strict tool-schema validators, especially Copilot/Anthropic-style providers that reject invalid function argument property names.

Core behavior:

- Preserve JSON Schema `definitions` keywords as `$defs` only when they are schema keywords.
- Preserve a tool argument literally named `definitions` under a `properties` map instead of rewriting it to `$defs`.
- Keep Azure DevOps MCP `pipelines_get_builds` available; its `definitions` parameter means build definition IDs and must remain model-callable.

Key files:

- `tools/mcp_tool.py`
- `tests/tools/test_mcp_tool.py`

Commits:

- pending current change - preserve MCP properties named `definitions`.

Preservation checks:

```bash
python -m pytest tests/tools/test_mcp_tool.py::TestSchemaConversion -o 'addopts=' -q
```

### 18. Test/environment cleanup and no-net-change commits

Purpose: track commits that matter for future archaeology but are not independent product features.

Commits:

- `cd719b5a9` - isolate Discord reaction env in tests.
- `184ba4ff5` - accept Discord subtitle attachments.
- `60370263d` - revert the subtitle attachment change. Net effect should be treated as no desired product delta unless later work reintroduces it deliberately.
- `61b186361` - stable refresh conflict-fix commit touching memory tool state.
- `6f7057a93` - v2026.6.19 integration merge commit.

Upgrade note: do not spend time preserving the reverted subtitle behavior unless a later branch explicitly revives it.

### 19. MCP self-healing reconnect (standby retry instead of permanent give-up)

Purpose: keep long-lived MCP servers (especially Jake's Windows Agency bridges — EngHub, Teams, M365 Copilot) usable in a long-running gateway after a multi-minute endpoint wedge/outage, without a manual `/reload-mcp` or gateway restart.

Background / root cause: upstream `MCPServerTask.run()` permanently exits its reconnect loop (`return`) once the fast reconnect budget (`_MAX_RECONNECT_RETRIES = 5`) is exhausted. When an Agency MCP's HTTP bridge stays wedged long enough to burn those 5 attempts, the server's background task ends and the server stays dead in the gateway's tool registry even after the endpoint fully recovers — so brand-new sessions inherit a registry missing e.g. all `mcp_teams_*` tools. The only recovery was `/reload-mcp` (which reconnects every server and re-registers all tools) or a full gateway restart. `/reload-mcp` also invalidates the per-conversation prompt cache, so auto-firing it from an external watchdog is the wrong fix.

Core behavior:

- After the fast reconnect budget is exhausted, a previously-healthy HTTP/SSE server drops into a slow "standby" retry loop (`_RECONNECT_STANDBY_INTERVAL`, default 300s) instead of returning permanently.
- Because `_run_http`/`_run_stdio` re-discover tools and `run()` re-registers them on every successful (re)entry, a standby reconnect self-heals AND re-registers the server's tools with no `/reload-mcp` and no prompt-cache invalidation.
- On entering standby the fast-retry counter and backoff are reset, so a later transient blip still gets the full fast backoff ladder before returning to standby.
- Shutdown is still honored promptly (checked after the standby sleep).
- `_RECONNECT_STANDBY_INTERVAL = 0` restores the upstream give-up-permanently behavior (escape hatch / behavior contract for the legacy path).
- Initial-connect failures and OAuth-auth failures are unchanged — they still fail fast (no standby), preserving fast startup and avoiding repeated browser prompts.

Key files:

- `tools/mcp_tool.py` (constant `_RECONNECT_STANDBY_INTERVAL`; standby branch in `MCPServerTask.run()` reconnect loop)
- `tests/tools/test_mcp_tool.py` (`TestReconnection::test_standby_reconnect_self_heals_after_exhausting_retries`, `TestReconnection::test_standby_disabled_restores_give_up_behavior`)

Commits:

- `890a012db` - MCP self-healing standby reconnect.

Preservation checks:

```bash
python -m pytest tests/tools/test_mcp_tool.py::TestReconnection -o 'addopts=' -q
```

Live smoke for Jake's profile:

- Wedge one Agency MCP endpoint (or stop its Windows bridge) long enough to exhaust the fast reconnect retries; confirm the gateway logs `entering standby, retrying every 300s`.
- Bring the endpoint back; within one standby interval the gateway should log the server reconnecting and re-registering its tools, and a new session should see those tools (e.g. `mcp_teams_*`) without any `/reload-mcp`.

## Complete commit ledger by feature bucket

This is the raw commit map from the audited branch, grouped as the recommended history-cleanup overlay. It intentionally avoids rewriting history on a published branch.

### Memory search / local memory cabinet

- `c5b1f45f8` `2026-05-23` - `feat: add local memory search`
- `a099adfd4` `2026-05-23` - `feat: import OpenClaw legacy sessions`
- `a3afaa7bc` `2026-06-12` - `feat: add opt-in TOON memory search rendering`
- `78bdc3986` `2026-06-24` - `feat(memory_search): add observation-granularity search`
- `99cfed0e7` `2026-06-24` - `feat(memory_search): add hybrid semantic search`
- `299ccf8f0` `2026-06-24` - `feat(memory_search): default to hybrid and support Gemini embeddings`
- `706879ded` `2026-06-24` - `feat(memory_search): use Gemini embeddings by default`
- `c78b39c3b` `2026-06-24` - `fix(memory_search): bound cold Gemini semantic rebuilds`
- `796d971d1` `2026-06-24` - `fix(memory_search): persist Gemini embeddings`
- `f7e43b8c5` `2026-06-24` - `fix(memory_search): query Gemini vectors with sqlite-vec`

### Memory write / background review durability

- `e125df23f` `2026-06-11` - `fix(memory): route background review notes to durable storage`
- `affcc80b6` `2026-06-14` - `fix(memory): compact full-store add errors`
- `61b186361` `2026-06-11` - `fix: finish stable refresh conflict fixes`

### Discord tool and gateway display UX

- `a21110f43` `2026-05-23` - `feat: add Discord guild message search`
- `6adab27d8` `2026-05-23` - `fix: omit chat chunk pagination markers`
- `feabad30f` `2026-05-23` - `feat: improve gateway tool progress labels`
- `7731faed9` `2026-05-29` - `fix: reduce retry and memory-full noise`

### Voice / STT / busy steering

- `2aa6e1cfb` `2026-05-23` - `feat: persist voice transcripts`
- `a4bfec013` `2026-05-28` - `feat: enable gateway busy mode command`
- `0774abbbf` `2026-06-25` - `fix(gateway): stop echoing voice transcripts`
- `93ea5e5f8` `2026-06-25` - `feat(gateway): make voice transcript echo configurable`
- `b89de5b55` `2026-06-25` - `fix(gateway): transcribe voice notes before steering`

### Approval, clarify, security prompt, and command guard UX

- `f4f86a67d` `2026-06-15` - `fix: clarify Discord approval prompts`
- `ac989cecc` `2026-06-21` - `fix: make Discord approval prompts content-first`
- `de57a1d77` `2026-06-21` - `fix: clarify security-scan approval prompts`
- `46f0d91b9` `2026-06-21` - `fix: focus security approvals on detected strings`
- `af32a5eda` `2026-06-21` - `fix: render invisible security evidence visibly`
- `99c125601` `2026-06-21` - `fix: keep command context in security approvals`
- `0546e28ca` `2026-06-22` - `fix: make Discord clarify prompts content-first`
- `f9ac959d3` `2026-06-25` - `fix(approval): restore sensitive home-path detection`
- `ce08ddbcf` `2026-06-25` - `fix(discord): restore rich approval prompt`

### Discord model, mentions, and gateway command behavior

- `b4957b99d` `2026-06-19` - `feat: add configurable long-turn Discord mentions`
- `c49bd419a` `2026-06-23` - `fix: add model status command`
- `1f787e01b` `2026-06-23` - `fix: show current model in picker`
- `83df29665` `2026-06-24` - `fix: show model picker status in content`
- `dbbe60625` `2026-06-25` - `fix(discord): show current model in picker`
- `d19f60938` `2026-06-25` - `fix(discord): simplify attention mentions`
- `73fe8e1a0` `2026-06-25` - `fix(merge): restore fork-only gateway behavior`

### Provider/model/routing behavior

- `37b16bb73` `2026-05-24` - `fix: honor API reasoning overrides`
- `6a8ce27a1` `2026-05-28` - `fix: honor API server model overrides`
- `b327035c7` `2026-05-28` - `fix: canonicalize Copilot model switches`
- `73224a581` `2026-06-13` - `fix(aux): route auto model overrides through correct transport`
- `789611291` `2026-06-16` - `fix: support Copilot Opus 4.8 max reasoning`

### ACP behavior

- `9df6ee707` `2026-06-18` - `feat(acp): advertise thought-level session config`
- `ad7a8b61f` `2026-06-07` - `feat(acp): emit session provenance metadata for compression rotation (#41724)`
- `65112b724` `2026-06-19` - `fix(acp): resolve compression-rotated session ids`

### Image, OCR, and Codex/Responses compatibility

- `88228aaeb` `2026-05-28` - `fix: shrink aggregate native image payloads on 413`
- `89389b4df` `2026-05-28` - `fix: skip GitHub Responses message item replay`
- `ab4568296` `2026-06-02` - `feat: add fast OCR extraction tool`

### MCP resilience and schema compatibility

- pending - `fix(mcp): preserve tool properties literally named definitions` (section 17)
- `890a012db` `2026-06-27` - `fix(mcp): self-heal reconnect via standby retry instead of permanent give-up` (section 19)

### Dashboard sessions UI

- `438431705` `2026-06-25` - `fix(dashboard): default sessions to recent activity`

### Test-only / no-net-change / integration bookkeeping

- `cd719b5a9` `2026-05-29` - `test: isolate Discord reaction env`
- `184ba4ff5` `2026-06-12` - `fix: accept Discord subtitle attachments`
- `60370263d` `2026-06-12` - `Revert "fix: accept Discord subtitle attachments"`
- `6f7057a93` `2026-06-24` - `merge: integrate Hermes v2026.6.19`

## Recommended cleanup strategy

The current branch is already pushed and has been live-smoked during the v0.17.0 integration. Do not force-rewrite it casually.

For future cleanup, use this non-destructive policy:

1. Keep this manifest on the fork branch so the messy commit history has a readable overlay.
2. If Jake explicitly wants a rewritten feature branch, create a new branch from the target upstream release and replay/squash by the buckets above.
3. Preserve the original integration branch until the rewritten branch has passed the same preservation gate and a live gateway smoke.
4. If upstream has accepted an equivalent behavior, mark the bucket as upstreamed here and drop the local patch in the next integration branch.
5. Treat `184ba4ff5` + `60370263d` as a no-net-change pair, not something to preserve.

Suggested cleaned commit buckets if rewriting later:

- `feat(memory): add local durable memory search and OpenClaw import`
- `feat(memory_search): add observations, hybrid Gemini retrieval, persisted vectors, and sqlite-vec`
- `fix(memory): harden durable memory writes and background review notes`
- `feat(gateway): add busy modes, voice transcript persistence, transcript echo config, and voice steering`
- `fix(discord): make approvals, clarify, model picker, and long-turn mentions content-first and inspectable`
- `fix(providers): preserve API server overrides, Copilot model/reasoning, and auxiliary routing`
- `feat(acp): preserve thought-level config and compression-session provenance`
- `feat(vision): add OCR and native-image shrink recovery`
- `fix(codex): preserve Responses replay/noise fixes`
- `test: isolate fork-specific gateway/provider regressions`

## Preservation gate command

This is a practical test set for the feature buckets above. It is intentionally focused; future agents can add broader suites after this is green.

```bash
python -m pytest \
  tests/tools/test_memory_search_tool.py \
  tests/tools/test_toon_renderer.py \
  tests/tools/test_memory_tool.py \
  tests/run_agent/test_background_review_summary.py \
  tests/tools/test_discord_tool.py \
  tests/agent/test_display.py \
  tests/gateway/test_display_config.py \
  tests/gateway/test_platform_base.py \
  tests/gateway/test_voice_transcript_persistence.py \
  tests/gateway/test_voice_transcript_echo.py \
  tests/gateway/test_discord_voice_steer.py \
  tests/gateway/test_busy_command.py \
  tests/gateway/test_busy_session_ack.py \
  tests/gateway/test_subagent_protection_30170.py \
  tests/run_agent/test_steer.py \
  tests/gateway/test_discord_exec_approval_prompt.py \
  tests/gateway/test_discord_clarify_buttons.py \
  tests/tools/test_approval.py \
  tests/tools/test_command_guards.py \
  tests/gateway/test_long_turn_mentions.py \
  tests/gateway/test_model_command_status.py \
  tests/gateway/test_discord_model_picker.py \
  tests/gateway/test_api_server.py \
  tests/hermes_cli/test_web_server.py \
  tests/hermes_cli/test_model_validation.py \
  tests/hermes_cli/test_model_switch_copilot_api_mode.py \
  tests/hermes_cli/test_runtime_provider_resolution.py \
  tests/providers/test_provider_profiles.py \
  tests/agent/test_auxiliary_main_first.py \
  tests/acp/test_session.py \
  tests/acp_adapter/test_acp_commands.py \
  tests/run_agent/test_image_shrink_recovery.py \
  tests/tools/test_ocr_extract_tool.py \
  tests/tools/test_vision_native_fast_path.py \
  tests/run_agent/test_run_agent_codex_responses.py \
  tests/agent/test_codex_ttfb_watchdog.py \
  tests/run_agent/test_retry_status_buffer.py \
  tests/tools/test_mcp_tool.py \
  -o 'addopts=' -q
```

## Quick audit commands

```bash
# Local commits relative to an upstream release tag
git cherry -v v2026.6.19 HEAD

# Same idea against current upstream main. If a line changes from + to -, upstream has an equivalent patch.
git cherry -v upstream/main HEAD

# Files touched by the local fork relative to the release tag
git diff --stat v2026.6.19...HEAD

# Merge parent orientation for the v0.17.0 integration
git show -s --format='%H%n%P%n%s' 6f7057a93
```

## Maintenance rule

When a future session adds, drops, or upstreams a fork-only behavior, update this file in the same commit as the relevant code or in a small follow-up docs commit. For Jake-specific Hermes modifications, agents should treat this as automatic work, not optional cleanup: before finalizing, add or update the relevant feature section, commit ledger entry, and preservation check unless Jake explicitly says not to. If the code commit already exists, reference its hash; if the code is still being committed, write the manifest entry before the final commit or make an immediate docs follow-up commit. The file should stay boring and inspectable: feature name, behavior, key files, commit pointers, and verification.
