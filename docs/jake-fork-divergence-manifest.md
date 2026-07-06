# Jake Hermes fork divergence manifest

Last audited: 2026-07-06 (v2026.7.1 integration)

This document is the durable orientation map for Jake's Hermes fork. Its job is to save future upgrade sessions from rediscovering the fork's local feature set from raw `git log` every time.

It is intentionally a feature manifest, not a perfect design doc. Use it to answer: "what does this fork intentionally carry that upstream Hermes may not?" and "what tests/symbols should an upgrade preserve?"

## Scope and anchors

### v2026.7.1 integration (2026-07-06)

- Integration branch: `jake/integrate-v2026.7.1-20260706-103904`
- Pre-merge fork HEAD: `f7dec8a28` (`docs: record gateway watchdog follow-up`; carries the 2026-07-06 event-loop watchdog + Discord keepalive-freshness fix)
- Upstream release integrated: `v2026.7.1` (peeled commit `7c1a029553d87c43ecff8a3821336bc95872213b`)
- Merge commit: `6ab280039` (`merge: integrate Hermes v2026.7.1`), plus follow-ups `8bb82aca1` (MCP §19 tool-call reconnect) and `b83f0b868` (§5 progress-topic test alignment)
- Rollback marker: `jake/rollback-before-v2026.7.1-20260706-103904` at `f7dec8a28`
- 21 files conflicted. Three were genuine **feature convergences** (fork and upstream independently built overlapping features in the same area) — resolved by keeping both behaviors, not picking a side. See the "v2026.7.1 convergence notes" under §5, §13, §19, and §20.
- Preservation gate result: all fork-feature suites pass (memory/session/mcp/display/image/approval/model/voice/busy/provider/acp/codex/dashboard). Two non-merge failures remain and are explicitly OUT of scope: (a) `test_memory_search_tool.py::test_default_roots_include_neutral_chatworkspace_and_hermes_memories` passes with the real `HERMES_HOME` (only fails under a temp-home test override), and (b) `test_background_review_summary.py::test_durable_notes_update_is_surfaced_as_durable_notes_not_memory` fails identically on pre-merge fork HEAD `f7dec8a28` (pre-existing trailing-period strip bug in `_summarize`, unrelated to the upgrade).

### v2026.6.19 integration (prior)

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
- `memory_search(action='status', granularity='all')` exposes semantic-cache coverage, missing counts, and top missing path prefixes so degradation is visible to the agent/user.
- `memory_search(action='preindex', granularity='all')` is the one-call cache-regenerate path agents should use when status reports large missing chunks/observations; it is resumable and can be bounded with `max_batches`.
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

Live repair/status commands:

```text
memory_search action=status granularity=all
memory_search action=preindex granularity=all
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

### 5. Terminal heredoc preview summarizer (fork-unique residue)

Status: **mostly landed upstream as of v2026.7.1.** Upstream now owns friendly tool-verb progress labels (`get_tool_verb`/`tool_verb_connector`/`verb_drops_preview`, e.g. `💻 Running pwd`), terminal-preview boilerplate stripping + plumbing compaction (`summarize_shell_command`: drops `set -euo pipefail`, collapses `| tail -N` tails, renders `cmd + N commands`), progress-arg secret redaction (`redact_tool_args_for_display`), chat-chunk pagination-marker suppression, and retry/memory-full noise reduction. Do not re-preserve those — they are baseline.

Fork-unique residue to keep: **heredoc body summarization.** `build_terminal_command_preview` summarizes a `python - <<'PY'` command by its first meaningful script line (`_extract_heredoc_preview` → e.g. `python: print(Path.cwd()) ...`) instead of the unhelpful wrapper line. Upstream regresses this (its summarizer shows the `<<'PY'` wrapper). The fork path runs the heredoc summarizer first, then falls through to upstream's `summarize_shell_command` for non-heredoc commands. The fenced-block progress header uses the capitalized `get_tool_display_label` ("Terminal").

Key files:

- `agent/display.py` (`_extract_heredoc_preview`, `_strip_shell_strict_mode`, `build_terminal_command_preview`, `clean_terminal_command_for_display`)
- `tests/agent/test_display.py`, `tests/gateway/test_run_progress_topics.py`

Commits (pre-v2026.7.1; most equivalents now upstream): `6adab27d8`, `feabad30f`, `7731faed9`, `d321360c6`.

Preservation check (the heredoc residue is the only fork-unique assertion left):

```bash
python -m pytest tests/agent/test_display.py::TestBuildToolPreview::test_terminal_preview_summarizes_python_heredoc_body tests/gateway/test_run_progress_topics.py -o 'addopts=' -q
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
- Avoidable Tirith findings that the agent can rewrite safely, currently `pipe_to_interpreter`, are model-facing self-correction blocks instead of user approval prompts.
- Tirith wrapper suppresses known false-positive warn-only findings before they reach the approval UI, including exact package-name self-matches from `threat_package_similar_name` (for example `aiohttp ≈ aiohttp`).
- Discord clarify prompts are content-first: full question and full numbered choices appear in message content, while buttons are compact selectors (`1`, `2`, `3`, etc.) plus `Other`.
- Clarify open-ended prompts use plain message content with a reply instruction, not embed-only text.
- Clarify question/choice text renders invisible/format Unicode visibly (for example `[U+FE0F]`).
- Approval pings still work when configured and should prepend/augment the rich prompt rather than replacing it.

Key files:

- `tools/approval.py`
- `tools/tirith_security.py`
- `tools/terminal_tool.py`
- `gateway/run.py`
- `plugins/platforms/discord/adapter.py`
- `tests/gateway/test_discord_exec_approval_prompt.py`
- `tests/gateway/test_discord_clarify_buttons.py`
- `tests/tools/test_approval.py`
- `tests/tools/test_command_guards.py`
- `tests/tools/test_tirith_security.py`
- `tests/tools/test_terminal_tool_schema.py`

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
- `be81d7e5d` - reduce approval fatigue for avoidable Tirith findings and package self-match false positives.

Preservation checks:

```bash
python -m pytest tests/gateway/test_discord_exec_approval_prompt.py tests/gateway/test_discord_clarify_buttons.py tests/tools/test_approval.py tests/tools/test_command_guards.py tests/tools/test_tirith_security.py tests/tools/test_terminal_tool_schema.py -o 'addopts=' -q
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
- `max` is a real, selectable reasoning-effort level (not just an on-the-wire alias). Copilot's Claude Opus 4.8 accepts `max` as a distinct tier above `xhigh` (server-verified: `supported values: [low medium high xhigh max]`). The selection gate `VALID_REASONING_EFFORTS` in `hermes_constants.py` MUST include `max`; every user-facing gate (`gateway/slash_commands.py` `/reasoning`, `gateway/platforms/api_server.py`, the CLI which delegates to `parse_reasoning_effort`) must accept it. For models that do NOT advertise `max`, the runtime clamp in `run_agent.py` and `plugins/model-providers/copilot/__init__.py` downgrades `max` → `xhigh` → `high`. Clamp is the *fallback*, not the default behavior.
- Preservation gate: hardcoded effort-level sets are a recurring merge-revert hazard — upstream `1780ad24b` reset `VALID_REASONING_EFFORTS` and re-dropped `max` during the v2026.6.19 integration. Gates must reference the canonical `VALID_REASONING_EFFORTS` tuple rather than re-listing `{"minimal", "low", "medium", "high", "xhigh"}` inline, so a future merge cannot silently revert the code path without also touching the tuple (which is test-guarded).
- Auxiliary auto routing uses the correct transport and respects the main provider/model path.
- Provider-health cache isolation prevents one test/provider failure from poisoning main-first auxiliary behavior.

Key files:

- `hermes_constants.py`
- `gateway/slash_commands.py`
- `gateway/run.py`
- `gateway/platforms/api_server.py`
- `hermes_cli/models.py`
- `hermes_cli/providers.py`
- `hermes_cli/runtime_provider.py`
- `plugins/model-providers/copilot/__init__.py`
- `run_agent.py`
- `agent/auxiliary_client.py`
- `tests/gateway/test_api_server.py`
- `tests/gateway/test_reasoning_command.py`
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
- _(pending)_ - re-restore `max` reasoning gate after v2026.6.19 merge reverted `VALID_REASONING_EFFORTS`; route gateway/slash_commands `/reasoning` gate through the canonical tuple to harden against future reverts.

Preservation checks:

```bash
python -m pytest tests/test_hermes_constants.py tests/gateway/test_api_server.py tests/gateway/test_reasoning_command.py tests/cli/test_reasoning_command.py tests/hermes_cli/test_model_validation.py tests/hermes_cli/test_model_switch_copilot_api_mode.py tests/hermes_cli/test_runtime_provider_resolution.py tests/hermes_cli/test_reasoning_effort_menu.py tests/providers/test_provider_profiles.py tests/agent/test_auxiliary_main_first.py tests/run_agent/test_run_agent.py -o 'addopts=' -q
```

Quick gate-integrity check (catches a merge silently dropping `max` from the canonical tuple):

```bash
python -c "from hermes_constants import VALID_REASONING_EFFORTS as v; assert 'max' in v, 'max reasoning tier reverted'; print('ok:', v)"
```

### 12. ACP session/provenance/thought-level behavior

Purpose: keep ACP/IDE integrations stable across compression-rotated sessions and expose reasoning/thought-level session config where needed.

Core behavior:

- ACP advertises thought-level session config.
- ACP emits session provenance metadata for compression rotation.
- ACP resolves compression-rotated session IDs/aliases instead of losing session continuity.
- ACP preserves explicit provider-prefixed model selections, including same-provider choices like `copilot:gpt-5.5`, so `session/set_model` cannot be hijacked to another static-catalog provider such as `openai-api`.
- ACP recomputes runtime api_mode from the model requested by `session/set_model`, not the persisted default model. This is required for same-provider Copilot switches where the default is a chat-completions model such as Claude Opus but the requested model is a Responses-only GPT-5.x model.
- ACP treats redundant `session/set_model` requests for the already-active provider/model as a successful no-op.

Key files:

- `acp_adapter/server.py`
- `acp_adapter/session.py`
- `hermes_cli/runtime_provider.py`
- `tests/acp/test_server.py`
- `tests/acp/test_session.py`
- `tests/acp_adapter/test_acp_commands.py`
- `tests/hermes_cli/test_runtime_provider_resolution.py`

Commits:

- `9df6ee707` - advertise thought-level ACP session config.
- `ad7a8b61f` - emit session provenance metadata for compression rotation. This appears to be a cherry-pick/forward-port of upstream-style work after the audited release tag, but it is still fork-local relative to `v2026.6.19`.
- `65112b724` - resolve compression-rotated ACP session IDs.
- `d223b2c7f` - preserve explicit ACP provider-prefixed model IDs and no-op same-provider/current-model switches.
- `563de2e2b` - recompute ACP model-switch api_mode from the requested target model so Copilot GPT-5.x selections use Responses even when the persisted default is a chat-completions model.

Preservation checks:

```bash
scripts/run_tests.sh tests/acp/test_server.py -- -q -k 'resolve_model_selection or set_session_model_preserves_provider_prefixed_current_model or set_session_model_replaces_agent_with_explicit_same_provider or set_session_model_derives_api_mode_from_requested_model or set_session_model_accepts_provider_prefixed_choice'
python -m pytest tests/hermes_cli/test_runtime_provider_resolution.py tests/hermes_cli/test_model_switch_copilot_api_mode.py -o 'addopts=' -q
scripts/run_tests.sh tests/acp/test_session.py tests/acp_adapter/test_acp_commands.py
```

### 13. Native image, vision, and OCR routing

Purpose: keep image handling reliable for large screenshots and provide a fast OCR-specific tool.

Core behavior:

- Native image payloads shrink/retry after provider 413 or image-size errors.
- Aggregate native image payloads can be shrunk, not just individual tool images.
- OCR exposes a dedicated fast `ocr_extract` transcription tool separate from general visual reasoning, but its schema tells vision-capable main models not to use it as a redundant confidence check for already-attached images.
- `vision_analyze` is framed as an escape hatch for non-visible images, non-vision fallback, or targeted second-pass inspection when native vision is insufficient, not as a routine re-check for already-attached images.
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
- `716e70408` - route the 413 image-shrink guard through `TurnRetryState` so native-image 413 recovery cannot crash with `UnboundLocalError` before retrying.
- `ab55c4655` - thread the aggregate image-payload budget through the shrink helper so native-image 413 recovery cannot crash with a keyword mismatch and can shrink multi-image batches below the request budget.

Preservation checks:

```bash
python -m pytest tests/agent/test_turn_retry_state.py tests/run_agent/test_image_shrink_recovery.py tests/tools/test_ocr_extract_tool.py tests/tools/test_vision_native_fast_path.py -o 'addopts=' -q
```

**v2026.7.1 convergence note:** upstream added transcoding of non-universal image formats (BMP/TIFF/HEIC/AVIF/ICO → PNG via Pillow, `_transcode_to_png` + `_UNIVERSALLY_SUPPORTED_MIMES`) so providers that reject those formats (Anthropic HTTP 400 "Could not process image") still work. The fork's contribution (`f93fd7510`, 2026-07-06) is the opposite guard: genuine non-images (text/log/PDF/document attachments sent alongside an image in a PHOTO message) must be REJECTED from native routing rather than mislabeled as `image/jpeg`. Resolution keeps BOTH: `_guess_mime` now returns a real image MIME for any actual image (including rare formats, so they can be transcoded) but `None` for genuine non-images (so they are skipped), and `_file_to_data_url` skips on `None` first, then transcodes non-universal formats. `tests/agent/test_image_routing.py` carries both sets (non-image-rejection AND transcode) and both pass. Also verify `gateway/run.py` classifies per-attachment via `_event_media_is_image/_audio/_video` (upstream helpers) rather than the fork's inline `_is_image_media_attachment` — the merged code uses the upstream helpers, and `tests/gateway/test_mixed_attachment_routing.py` asserts both the end-to-end buffering and the per-attachment classification.

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

### 19. MCP self-heal: standby auto-retry + bounded tool-call reconnect (fork-unique residue)

Status: **core orphan-fix landed upstream as of v2026.7.1.** Upstream `MCPServerTask.run()` no longer gives up permanently after the fast-retry budget (`_MAX_RECONNECT_RETRIES`) — it drops phantom tools (`_deregister_tools`) and parks as a dormant listener on `_reconnect_event` (`_wait_for_reconnect_or_shutdown`), so a later breaker half-open probe / OAuth recovery / manual `/mcp` refresh can rebuild the transport (#16788). That baseline is upstream; do not re-preserve it.

Two fork-unique deltas to keep (upstream lacks both), for Jake's Windows Agency bridges (EngHub, Teams, M365 Copilot) which must self-heal with **zero manual action**:

1. **Standby auto-retry timer.** Upstream's park only wakes on an explicit event; the fork also wakes on a timer via `_wait_for_reconnect_or_standby(_RECONNECT_STANDBY_INTERVAL)` (default 300s), which races shutdown/reconnect events against an `asyncio.sleep(interval)` (patchable, so self-heal tests stay fast). So a recovered endpoint reconnects on its own with no `/reload-mcp` and no prompt-cache churn (`_run_http`/`_run_stdio` re-discover tools on re-entry while preserving registry handlers). Entering standby resets the fast-retry budget. `_RECONNECT_STANDBY_INTERVAL = 0` restores upstream's park-forever behavior.
2. **Bounded tool-call reconnect.** When a model-facing tool call hits a registered-but-sessionless server (or a "not connected / session missing" exception), the handler triggers a server-local bounded reconnect (`_TOOL_CALL_RECONNECT_TIMEOUT`, default 15s) and retries once, waking a sleeping lifecycle task via `_recover_now_event` without arming `_reconnect_event` (avoids tearing down the fresh session). Reuses the same `MCPServerTask` — no `/reload-mcp`, no schema invalidation. **Merge hazard:** upstream added an early `if not server.session: _signal_reconnect(...)` guard in `_make_tool_handler` that fire-and-forgets and short-circuits this bounded retry. The v2026.7.1 follow-up (`8bb82aca1`) removed that early return so a dead session flows through `_handle_session_expired_and_retry` → `_recover_disconnected_server_and_retry`. A future merge that reintroduces the early guard silently disables delta #2 — the `TestToolHandler::test_disconnected_server_*` tests are the guard.

Also: init/discovery uses the fork's timeout-bounded `_initialize_and_discover` helper (upstream inlines it), now also calling `_reset_server_error` on success.

Key files:

- `tools/mcp_tool.py` (`_RECONNECT_STANDBY_INTERVAL`, `_TOOL_CALL_RECONNECT_TIMEOUT`, `_wait_for_reconnect_or_standby`, `request_reconnect`, `_recover_now_event`, `_initialize_and_discover`, `_recover_disconnected_server_and_retry`, `_make_tool_handler` — no early sessionless return)
- `tests/tools/test_mcp_tool.py` (`TestReconnection::test_standby_reconnect_self_heals_after_exhausting_retries`, `::test_standby_disabled_restores_give_up_behavior`, `::test_tool_call_recovery_wakes_reconnect_backoff_without_tearing_down_fresh_session`; `TestToolHandler::test_disconnected_server_reconnects_and_retries_once`, `::test_disconnected_server_reconnect_timeout_returns_clear_error`)

Commits: `890a012db` (standby), `adaf93285` (bounded tool-call retry), `8bb82aca1` (v2026.7.1: drop upstream early-return that shadowed the bounded retry).

Preservation checks:

```bash
python -m pytest tests/tools/test_mcp_tool.py::TestReconnection tests/tools/test_mcp_tool.py::TestToolHandler -o 'addopts=' -q
```

Live smoke for Jake's profile:

- Wedge one Agency MCP endpoint (or stop its Windows bridge) past the fast-retry budget; bring it back — within one standby interval the gateway reconnects and a new session sees the existing tool names (e.g. `mcp_teams_*`) with no `/reload-mcp`.
- For the tool-call path, call a model-facing tool while `server.session` is missing; confirm one bounded reconnect/retry (or the clean bounded-timeout error), not an immediate fire-and-forget "not connected".

### 20. Discord gateway liveness watchdog (silent-wedge recovery)

Purpose: recover automatically from the "green service, dead bot" state where the systemd gateway service is `active`, the Python process is alive, but the Discord bot shows offline and never comes back without a manual restart. As of the 2026-07-06 follow-up, this feature also includes a process-level event-loop watchdog for the sibling "green service, dead gateway" state where the main asyncio loop stops making progress, starving the Discord watchdog and the API server together.

Background / root cause: Discord recovery in the gateway is entirely event-driven and has exactly one trigger — the discord.py `Bot.start()` task actually *exiting*, which fires `DiscordAdapter._handle_bot_task_done`, marks a retryable fatal error, and lets `GatewayRunner._handle_adapter_fatal_error` queue the platform for the existing background reconnect watcher. But discord.py runs with `reconnect=True`, so on a *silent* websocket death (half-open TCP after a WSL/host network or interop blip, or an internal reconnect loop that never re-establishes) the task never exits. Task-never-exits → no fatal error → `_failed_platforms` stays empty → the reconnect watcher (which only ever iterates `_failed_platforms`) sleeps forever. Confirmed live 2026-07-02: an overnight WSL-interop loss drove a Windows-MCP reconnect storm and a ~27GB/252-task event-loop balloon, during which the Discord websocket went non-live with zero `Bot.start()` exit and zero reconnect attempts logged for ~13.5h until a manual `hermes gateway restart`. There was previously **no liveness probe on the main gateway websocket** (the only keepalive in the adapter is the unrelated voice-UDP one), so a wedged-but-not-exited connection was invisible to recovery. This is the "liveness is a lie if it's just `connected && process != nil`" failure class.

Core behavior:

- On `connect()` success the adapter seeds a monotonic last-live timestamp and starts a background `_liveness_watchdog_loop`.
- The loop first probes discord.py's public signals — `Client.is_closed()` and `Client.latency` (finite on a healthy connection; `inf`/NaN when there is no live heartbeat) — and then, when available, checks discord.py's keepalive timestamps (`_last_ack` / `_last_recv`) so a stale finite `Client.latency` from a half-dead/CLOSE-WAIT socket cannot refresh liveness forever. The keepalive read is best-effort and falls back to public signals if discord.py changes.
- Healthy probe refreshes the last-live timestamp. `on_ready` and `on_resumed` also refresh it, so a routine RESUME/heartbeat gap never trips the watchdog.
- Once the connection has been continuously non-live past `_DISCORD_LIVENESS_STALE_THRESHOLD_SECONDS` (default 150s, comfortably beyond discord.py's ~41.25s heartbeat interval + a RESUME window), the watchdog marks a RETRYABLE fatal error (`discord_gateway_wedged`) and calls `_notify_fatal_error()` — the exact same path `_handle_bot_task_done` uses — so the adapter is removed and Discord is re-queued for the existing reconnect watcher. It fires at most once per wedge (returns after notifying); the fresh reconnect adapter starts its own watchdog.
- The watchdog defers (returns without firing) when `_disconnecting`, when `_running` is false, or when the bot task is already done (that case is owned by `_handle_bot_task_done`), so it never double-reports.
- `disconnect()` cancels the watchdog before teardown so an intentional shutdown never fires a spurious wedge.
- This adds the missing SENSOR only; it reuses the existing fatal-error/reconnect machinery and does not add a new recovery path, does not touch prompt caching, and does not change the healthy-path lifecycle.

Key files:

- `plugins/platforms/discord/adapter.py` (constants `_DISCORD_LIVENESS_PROBE_INTERVAL_SECONDS`, `_DISCORD_LIVENESS_STALE_THRESHOLD_SECONDS`; state `_liveness_task`, `_last_liveness_ok`; `_start_liveness_watchdog`, `_cancel_liveness_watchdog`, `_gateway_is_live`, `_gateway_heartbeat_age_seconds`, `_liveness_watchdog_loop`; `on_ready`/`on_resumed` refresh; `connect()` start; `disconnect()` cancel)
- `gateway/run.py` (process-level event-loop watchdog: in-loop heartbeat task, out-of-loop monitor thread, `gateway.event_loop_watchdog.*` config, stack dump to `~/.hermes/logs/gateway-event-loop-watchdog.log`, and exit via `GATEWAY_SERVICE_RESTART_EXIT_CODE` so systemd restarts a half-dead gateway)
- `hermes_cli/config.py` (default `gateway.event_loop_watchdog` config block)
- `tests/gateway/test_discord_liveness_watchdog.py` (healthy never-fires, wedged fires retryable, closed-ws non-live, stale finite `Client.latency` rejected via keepalive age, grace-window suppression, done-bot-task defer, disconnecting defer, `_gateway_is_live` classification)
- `tests/gateway/test_gateway_event_loop_watchdog.py` (stale heartbeat exits with service-restart code, fresh heartbeat does not fire, draining suppresses the watchdog, heartbeat task updates the tick)

Commits:

- `d1ff7aba6` - Discord gateway liveness watchdog for silently-wedged connections.

Preservation checks:

```bash
python -m pytest tests/gateway/test_discord_liveness_watchdog.py tests/gateway/test_gateway_event_loop_watchdog.py tests/gateway/test_discord_connect.py -o 'addopts=' -q
```

Live smoke for Jake's profile:

- Confirm the current gateway process is running the fixed adapter and, on a real transient network drop, the gateway logs either a normal discord.py RESUME (no watchdog action) or, on a true wedge, `Discord gateway wedged: websocket non-live for Ns` followed by the reconnect watcher re-queuing Discord and a fresh `Connected as hermes#...`.
- For Discord-only stale-socket wedges, simulate/observe a finite stale `Client.latency` with old keepalive timestamps and confirm the adapter logs `Discord gateway wedged: websocket non-live for Ns`, then reconnects. For full-loop wedges like 2026-07-06 (API `:8642` accepts TCP but `/health` times out, and normal gateway housekeeping logs stop), confirm no manual restart is needed: after `gateway.event_loop_watchdog.threshold_seconds`, systemd should restart the service and `gateway-event-loop-watchdog.log` should contain all-thread stack forensics.

**v2026.7.1 convergence note:** upstream independently added a Discord silent-wedge detector (#26656) — an active REST `fetch_user` probe on a timer that catches a socket wedged behind a dead proxy/NAT that never delivers a RST. The fork's detector is passive: it reads discord.py's public `is_closed()`/`latency` plus keepalive-timestamp freshness (`_gateway_is_live`/`_gateway_heartbeat_age_seconds`) to catch a half-open/CLOSE-WAIT websocket. These catch DIFFERENT failure modes, so resolution keeps BOTH as complementary detectors with separate task handles: the fork's passive watchdog on `self._liveness_task` (started via `_start_liveness_watchdog`, cancelled via `_cancel_liveness_watchdog`) and upstream's active REST probe on the new `self._rest_liveness_task` (started via `_start_liveness_probe`, cancelled via `_cancel_liveness_task`). `connect()` starts both; `disconnect()` cancels both. The process-level event-loop watchdog in `gateway/run.py` (2026-07-06, this section's sibling) is unaffected and survived the merge intact. `tests/gateway/test_discord_liveness_watchdog.py` + `test_discord_connect.py` pass.

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
- `d1ff7aba6` `2026-07-02` - `fix(discord): watchdog for silently-wedged gateway (green service, dead bot)`
- `87b33d621` - Gateway watchdog follow-up: reject stale finite Discord heartbeat latency and restart on wedged gateway event loop.

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
- `ab55c4655` `2026-07-04` - `fix: thread aggregate image shrink budget`
- `f93fd7510` `2026-07-05` - `fix(gateway): keep text attachments out of image routing` (tightens `vision_analyze` fallback-only guidance and keeps text/log/document attachments out of native image parts).

### MCP resilience and schema compatibility

- pending - `fix(mcp): preserve tool properties literally named definitions` (section 17)
- `890a012db` `2026-06-27` - `fix(mcp): self-heal reconnect via standby retry instead of permanent give-up` (section 19)
- `adaf93285` `2026-06-29` - `fix(mcp): retry disconnected tool calls after bounded reconnect` (section 19)

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
  tests/agent/test_image_routing.py \
  tests/gateway/test_mixed_attachment_routing.py \
  tests/gateway/test_discord_document_handling.py \
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
