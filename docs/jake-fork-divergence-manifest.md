# Jake Hermes fork divergence manifest

Last audited: 2026-07-26 (v2026.7.20 integrated and preservation-tested)

This document is the durable orientation map for Jake's Hermes fork. Its job is to save future upgrade sessions from rediscovering the fork's local feature set from raw `git log` every time.

It is intentionally a feature manifest, not a perfect design doc. Use it to answer: "what does this fork intentionally carry that upstream Hermes may not?" and "what tests/symbols should an upgrade preserve?"

## Scope and anchors

### v2026.7.20 integration (2026-07-26)

- Integration branch: `jake/integrate-v2026.7.20-20260726-033213`
- Pre-merge fork HEAD: `34e62bfe1`
- Upstream release integrated: `v2026.7.20` (peeled commit `3ef6bbd201263d354fd83ec55b3c306ded2eb72a`)
- Merge commit: `62fc9178c86129e4a47860eb704f9fe66782c66a` (`merge: integrate Hermes v2026.7.20`)
- Rollback marker: `jake/rollback-before-v2026.7.20-20260726-033213` at `34e62bfe1`
- The merge was performed in isolated worktree `/tmp/hermes-v2026.7.20-integration-20260726-033213`; Jake's live checkout and gateway were not switched during conflict resolution or tests.
- 29 files conflicted. The important resolutions were feature convergences rather than side-picks:
  - upstream's timed parked-state MCP self-probe replaces the fork's duplicate standby timer; the fork still keeps bounded per-tool reconnect/retry for a registered-but-sessionless server (§19);
  - upstream's newer Discord WebSocket ACK/open-state watchdog replaces the fork's older passive watchdog; the fork still keeps an independent REST delivery-path probe plus its process-level event-loop watchdog (§20);
  - API-server `model_routes` were combined with fork per-request model/reasoning overrides, with session `/model` taking precedence;
  - upstream once-only queued voice transcription/echo handling was combined with fork transcript persistence and opt-in echo policy;
  - upstream smart-DENY/admin approval semantics were combined with fork content-first, security-scan-aware Discord prompts and compact numeric clarify buttons;
  - upstream auxiliary client cache isolation by model was combined with fork provider normalization and secret-safe cache discriminators.
- Dependency resolution was regenerated from upstream `uv.lock` with `uv lock`; tests run in an isolated Python 3.11 `.venv` inside the integration worktree.
- Preservation result: focused fork gate passed **2,846 tests with 2 environment skips** in the isolated worktree. Conflict-seam reruns also passed independently: full MCP tool suite (219), reasoning/provider core (799), voice/config (156), Discord liveness/connect (50), auxiliary-provider cache (238), and the previously order-sensitive busy/model/OCR/retry tests (51). The local semantic-memory tests require optional `scikit-learn`, which was installed only in the isolated test venv; no live environment was changed.
- Live cutover completed 2026-07-27: `/home/jakepresent/.hermes/hermes-agent` switched to this branch at `8ffe9d46d`, the live venv was refreshed from `.[all]`, and `hermes-gateway.service` restarted from that checkout. Runtime verification reports `Hermes Agent v0.19.0 (2026.7.20)`, health endpoint `status: ok`, and a clean checkout whose local/remote HEADs match. Live Discord traffic and model switching are working; hybrid memory retrieval returned complete Gemini/sqlite-vec coverage; ADO, IcM, Teams, and M365 Copilot MCP handshakes succeeded after the Windows Agency hosts were repaired. EngHub alone continued returning its independent upstream 403 and was not treated as a fork-cutover regression. Voice/config behavior is preservation-tested, but a fresh user voice-note smoke was not required for the cutover verdict. Rollback remains `jake/rollback-before-v2026.7.20-20260726-033213` at `34e62bfe1`.

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

Preservation check (heredoc body summarization):

```bash
python -m pytest tests/agent/test_display.py::TestBuildToolPreview::test_terminal_preview_summarizes_python_heredoc_body tests/gateway/test_run_progress_topics.py -o 'addopts=' -q
```

#### Terminal-only command expansion (fork-only)

`display.expand_terminal_commands` defaults to `false`. When enabled globally or through `display.platforms.<platform>.expand_terminal_commands`, a markdown-capable gateway keeps its normal `all`/`new` tool-progress mode but renders each terminal call as a full, cleaned code block. Other tools retain their compact previews; this is deliberately narrower than global `verbose`, which emits every tool's full arguments.

The flag is useful when `summarize_shell_command` collapses a compound invocation into `cmd + N commands` and the user needs to audit every shell action without flooding the chat with every other tool's inputs. Plain-text adapters keep the ordinary compact terminal preview.

Key files:

- `gateway/display_config.py` (`expand_terminal_commands` resolution and normalization)
- `gateway/run.py` (terminal code-block selection)
- `hermes_cli/config.py`
- `tests/gateway/test_display_config.py`, `tests/gateway/test_run_progress_topics.py`
- `website/docs/user-guide/configuration.md`, `website/docs/user-guide/messaging/index.md`

Commit: `f9e2493f4` - terminal-only command expansion in compact gateway progress.

Preservation checks:

```bash
python -m pytest tests/gateway/test_display_config.py tests/gateway/test_run_progress_topics.py -o 'addopts=' -q
```

Live smoke:

- Set `display.platforms.discord.expand_terminal_commands: true` while Discord tool progress remains `all`.
- Trigger a terminal invocation with several shell actions. Discord must show the full cleaned command block, while a subsequent web/file tool call remains compact.

#### Markdown-safe MCP progress labels (fork-only)

Discord interprets the double-underscore separators in raw MCP registry names such as `mcp__ado__wiki_get_page_content` as Markdown emphasis, producing smashed progress text like `mcpadowiki_get_page_content`. Gateway/API display labels now generically split MCP server and operation identifiers, preserve common acronyms, handle snake_case and CamelCase, and render stable labels such as `ADO · Wiki Get Page Content`. The compact preview path uses the same display label instead of falling back to the raw registry name.

Key files:

- `agent/display.py` (`get_tool_display_label`, `_humanize_tool_identifier`)
- `gateway/run.py` (compact custom/plugin/MCP preview rendering)
- `tests/agent/test_display.py`

Commit: `82e63bfc2` - human-readable, Markdown-safe MCP progress labels.

Preservation checks:

```bash
python -m pytest tests/agent/test_display.py tests/gateway/test_run_progress_topics.py -o 'addopts=' -q
```

Expected Discord shape: `⚙️ ADO · Wiki Get Page Content...` rather than the raw MCP registry name.

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

Status note (2026-07-07): upstream PR #60245 merged the narrow Discord embed-invisibility fix from Jake's salvaged #52518 and generalized it to the sibling prompt surfaces. On the next upstream update, do **not** reapply the baseline change that mirrors `send_exec_approval`, `send_slash_confirm`, `send_clarify`, and `send_update_prompt` payloads into plain Discord message content. Preserve only the fork-unique residue below: richer security evidence, full numbered clarify choices, invisible-Unicode rendering, sensitive-path detection, Tirith fatigue reduction, and configured approval pings.

Core behavior:

- Baseline now upstream: interactive Discord prompts carry their primary payload in plain message content next to buttons, not only in embeds/components. Do not re-preserve this baseline after PR #60245 is in the integrated upstream base.
- Fork-unique approval prompt behavior: security-scan approvals show detected suspicious strings, command preview, and visible rendering for invisible Unicode characters.
- Sensitive home-path detection is restored.
- Command context remains visible in security approvals.
- Avoidable Tirith findings that the agent can rewrite safely, currently `pipe_to_interpreter`, are model-facing self-correction blocks instead of user approval prompts.
- Tirith wrapper suppresses known false-positive warn-only findings before they reach the approval UI, including exact package-name self-matches from `threat_package_similar_name` (for example `aiohttp ≈ aiohttp`).
- Fork-unique clarify behavior: full question and full numbered choices appear in message content, while buttons are compact selectors (`1`, `2`, `3`, etc.) plus `Other`.
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
- OCR exposes a dedicated fast `ocr_extract` transcription tool separate from general visual reasoning, but native-vision sessions hide it from the model-facing schema by default so already-attached images are read directly. `agent.expose_ocr_extract_with_native_vision: true` opts the tool back in for legacy/automation use.
- `vision_analyze` is framed as an escape hatch for non-visible images, non-vision fallback, or targeted second-pass inspection when native vision is insufficient, not as a routine re-check for already-attached images. When `ocr_extract` is hidden, the `vision_analyze` schema also stops mentioning the missing tool name.
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
- `78fac29d5` - hide `ocr_extract` from native-vision tool schemas by default; add `agent.expose_ocr_extract_with_native_vision` opt-in.
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

### 19. MCP self-heal: bounded tool-call reconnect (fork-unique residue)

Status: **the standby auto-retry timer landed upstream as of v2026.7.20.** Upstream now parks a server after the fast retry budget, deregisters phantom tools, and wakes on either an explicit reconnect event or `_PARKED_RETRY_INTERVAL` for an autonomous self-probe. The fork's `_RECONNECT_STANDBY_INTERVAL` and `_wait_for_reconnect_or_standby` were duplicate machinery and were removed during this integration. Upstream's `tests/tools/test_mcp_parked_self_probe.py` and retry-reset tests now own that baseline.

Fork-unique residue to keep for Jake's Windows Agency bridges (EngHub, Teams, M365 Copilot): **bounded tool-call reconnect and one retry.** When a model-facing handler reaches a registered-but-sessionless server, or receives a recoverable "not connected / session missing" exception, it calls `MCPServerTask.request_reconnect()` with `_TOOL_CALL_RECONNECT_TIMEOUT` (default 15s), wakes the existing lifecycle task through `_recover_now_event`, waits for a new connection generation, and retries the operation once. It reuses the same server object and schema, so there is no global `/reload-mcp` or prompt-cache churn.

Merge hazards:

- Do not restore an early `if not server.session: return reconnect_requested` branch in `_make_tool_handler`; that fire-and-forget path shadows the fork's bounded wait/retry.
- Keep `_connection_generation`, `_reconnect_request_lock`, `_recover_now_event`, `request_reconnect`, `_recover_disconnected_server_and_retry`, and the broader disconnected-error recognizer.
- `_initialize_and_discover` is the shared handshake/discovery helper after v2026.7.20. It preserves the fork generation bookkeeping and timeout boundary while also running upstream lifecycle/recycle bookkeeping and resetting `_reconnect_retries` plus the circuit breaker on success.
- Keep upstream's `_reconnect_retries` and timed parked-state engine; do not reintroduce the fork's removed second standby counter/timer.

Key files:

- `tools/mcp_tool.py`
- `tests/tools/test_mcp_tool.py` (`TestToolHandler::test_disconnected_server_reconnects_and_retries_once`, `::test_not_connected_exception_reconnects_and_retries_once`, `::test_disconnected_server_reconnect_timeout_returns_clear_error`, and reconnect-backoff coverage)
- upstream baseline: `tests/tools/test_mcp_parked_self_probe.py`, `tests/tools/test_mcp_reconnect_retry_reset.py`, `tests/tools/test_mcp_stdio_init_timeout.py`

Commits: `890a012db` (historical standby implementation, now upstream-covered), `adaf93285` (bounded tool-call retry), `8bb82aca1` (v2026.7.1 early-return removal).

Preservation checks:

```bash
python -m pytest tests/tools/test_mcp_tool.py::TestReconnection tests/tools/test_mcp_tool.py::TestToolHandler tests/tools/test_mcp_parked_self_probe.py tests/tools/test_mcp_reconnect_retry_reset.py -o 'addopts=' -q
```

Live smoke for Jake's profile:

- Stop one Agency bridge long enough to exhaust fast retries, restore it, and confirm upstream's parked-state timer reconnects and re-registers tools without `/reload-mcp`.
- Call a model-facing tool while its server object is registered but `session` is missing; confirm one bounded reconnect/retry (or the clear bounded-timeout error), not an immediate fire-and-forget failure.

### 20. Gateway liveness: upstream WebSocket probe + fork REST and event-loop probes

Purpose: recover from both "green service, dead Discord bot" and "green process, dead event loop" without manual restarts.

Status after v2026.7.20: **the fork's original passive Discord WebSocket watchdog is upstream-covered and was removed.** Upstream now owns a stronger WebSocket-level probe that checks `Client.is_ready()`, socket open state, heartbeat ACK age, and latency, then closes the stale client and routes a retryable fatal error through the gateway reconnect supervisor. The removed fork symbols (`_DISCORD_LIVENESS_*`, `_last_liveness_ok`, `_gateway_is_live`, `_liveness_watchdog_loop`, and `on_resumed` clock refresh) must not be resurrected as a second WebSocket engine.

Fork-unique residue:

1. **Independent REST delivery-path probe.** WebSocket health does not prove Discord REST calls still work. `connect()` starts `_start_rest_liveness_probe` alongside upstream `_start_liveness_probe`; `_rest_liveness_loop` periodically calls `fetch_user`, resets on success, and after `rest_liveness_failure_threshold` consecutive failures sets retryable fatal code `discord_rest_health_stale` and reuses upstream's bounded close/notify path. Configuration lives in Discord platform `extra` as `rest_liveness_interval_seconds` (default 60) and `rest_liveness_failure_threshold` (default 3). `_rest_liveness_task` is separate from upstream `_liveness_task` / `_liveness_notification_task`, and `disconnect()` cancels both engines.
2. **Process-level event-loop watchdog.** `gateway/run.py` keeps an in-loop heartbeat plus an out-of-loop monitor thread. If the asyncio loop stops advancing past `gateway.event_loop_watchdog.threshold_seconds`, it dumps all-thread stacks to `~/.hermes/logs/gateway-event-loop-watchdog.log` and exits with `GATEWAY_SERVICE_RESTART_EXIT_CODE` so systemd can restart the half-dead gateway. Draining and intentional shutdown suppress the watchdog.

Key files:

- `plugins/platforms/discord/adapter.py` (upstream WebSocket liveness plus fork `_rest_liveness_*` state/methods)
- `gateway/run.py` (process event-loop heartbeat/monitor and restart exit)
- `hermes_cli/config.py` (event-loop watchdog config)
- `tests/gateway/test_discord_liveness.py` (upstream WebSocket baseline)
- `tests/gateway/test_discord_liveness_watchdog.py` (fork REST probe isolation, success/failure, and cancellation)
- `tests/gateway/test_gateway_event_loop_watchdog.py`

Commits: `d1ff7aba6` and the 2026-07-06 event-loop watchdog follow-up; v2026.7.20 integration intentionally shrinks the old Discord-specific residue to REST-only.

Preservation checks:

```bash
python -m pytest tests/gateway/test_discord_liveness.py tests/gateway/test_discord_liveness_watchdog.py tests/gateway/test_gateway_event_loop_watchdog.py tests/gateway/test_discord_connect.py -o 'addopts=' -q
```

Live smoke for Jake's profile:

- On a true Gateway socket wedge, expect upstream's `Discord Gateway WebSocket unhealthy` / `forcing reconnect` path and a fresh Discord connection without a manual service restart.
- On repeated REST delivery failures with the WebSocket still alive, expect `Discord REST liveness probe failed` followed by `discord_rest_health_stale` and the same reconnect supervisor path.
- For a full-loop wedge (API port accepts TCP but `/health` times out and gateway housekeeping stops), confirm systemd restarts the service after the event-loop threshold and the forensics log contains all-thread stacks.

### 21. MoA gateway safeguards (prompt echo + non-streaming acting turns)

Purpose: keep `/moa` usable on messaging gateways for real work, especially design-doc / demo review prompts where the aggregator is expected to call tools.

Core behavior:

- `/moa <prompt>` echoes the exact prompt back into the originating chat before the fan-out starts, marked with non-conversational metadata so Discord history backfill does not treat the echo as user context. This gives Jake mobile/Discord observability for the expensive one-shot run.
- MoA acting turns intentionally use the complete-response path even when a gateway/TUI stream consumer exists. Observed bug (2026-07-06): Copilot Claude Opus 4.8 streamed an aggregator preface and terminated with `finish_reason=tool_calls` but no usable streamed tool-call deltas. The outer loop then surfaced only the pre-tool preface ("Let me ground...") and never executed tools. Non-streaming MoA returns the full ChatCompletion with `message.tool_calls` intact, so the normal tool loop can run.
- General guard: if any provider/MoA path reports `finish_reason=tool_calls` but the normalized response has no executable `tool_calls`, Hermes must not finalize the pre-tool narration. It appends a valid assistant→user nudge asking the model to emit real tool calls or answer in text, then retries; after bounded retries it surfaces a clear partial failure instead of silently treating the preface as final.

Key files:

- `agent/conversation_loop.py` (forces `agent.provider == "moa"` down the complete-response path)
- `gateway/run.py` (`/moa` prompt echo)
- `tests/run_agent/test_moa_no_streaming.py`
- `tests/gateway/test_slash_access_dispatch.py::test_moa_echoes_prompt_non_conversational_before_agent_run`

Preservation checks:

```bash
python -m pytest tests/run_agent/test_moa_no_streaming.py tests/gateway/test_slash_access_dispatch.py::test_moa_echoes_prompt_non_conversational_before_agent_run tests/cli/test_moa_command.py tests/tui_gateway/test_goal_command.py::test_moa_arg_is_always_one_shot tests/tui_gateway/test_moa_reference_emit.py -o 'addopts=' -q
```

Live smoke:

- Run `/moa <prompt>` in Discord and confirm a `MoA prompt:` echo appears first.
- Use a prompt that requires reading files or checking the repo; confirm the run continues into tool execution rather than stopping after a planning preface.

### 22. Cached gateway-history replay consistency

Purpose: distinguish a real lagging session write from the intentional cleanup performed before replaying persisted transcript rows.

Core behavior:

- The gateway compares persisted and cached histories only after both have passed through the same replay conversion. Session metadata and interrupted/dangling tool tails are intentionally excluded from a model replay and must not look like missing SQLite rows.
- A genuinely longer live replay history still wins, preserving in-process context when disk persistence really has not caught up.
- The guard no longer emits false `possible FTS write corruption` warnings for healthy transcripts, and it does not reintroduce tool tails that replay cleanup deliberately removed.

Key files:

- `gateway/run.py` (`_select_cached_replay_history`)
- `tests/gateway/test_cached_history_replay_guard.py`

Commits:

- `bafe9702c` - compare cached and persisted histories after identical replay cleanup.

Preservation checks:

```bash
python -m pytest tests/gateway/test_cached_history_replay_guard.py tests/gateway/test_agent_cache.py tests/agent/test_replay_cleanup.py -o 'addopts=' -q
```

### 23. Session-scoped skill reuse

Purpose: preserve mandatory skill discovery without making the agent reload the same skill for every follow-up in one continuous task.

Core behavior:

- The agent loads a matching skill before first using it in a session, then reuses the loaded instructions for later turns in the same ongoing task.
- A simple follow-up does not trigger another `skill_view` call or another full skill payload in the conversation.
- Reload remains required when the task materially changes, a linked reference is needed, the skill may have changed, or Jake asks for a reload.
- This is prompt policy only. It does not hide skills, weaken first-use loading, or mutate a cached system prompt mid-session.

Key files:

- `agent/prompt_builder.py` (`build_skills_system_prompt`)
- `tests/agent/test_prompt_builder.py::TestBuildSkillsSystemPrompt::test_instructs_agent_to_reuse_skill_for_follow_up_turns`

Commits:

- `93295c914` - reuse a loaded skill for follow-up turns in the same ongoing task.

Preservation checks:

```bash
python -m pytest tests/agent/test_prompt_builder.py -o 'addopts=' -q
```

### 24. Inline top-level delegation mode

Purpose: keep delegated review/research results inside the turn that commissioned
them, rather than letting a stale completion wake a second full agent loop after
the parent task has already finished or exhausted its turn budget.

Core behavior:

- `delegation.top_level_mode` accepts `background` (upstream-compatible default)
  or `inline`.
- In `inline` mode, model-facing top-level single tasks and batches wait inside
  the current `delegate_task` tool call and return their final summary there. No
  durable async completion is queued, so nothing can arrive later as a synthetic
  user turn.
- The model cannot override the operator's mode through the deprecated
  `background` argument. Orchestrator children remain inline in either mode.
- Dynamic tool-schema text reflects the configured mode, and changing the mode
  invalidates the gateway's cached agent so future turns do not retain stale
  background-delivery guidance.

Key files:

- `tools/delegate_tool.py` (`_get_top_level_mode`, `_model_background_value`, dynamic schema text)
- `run_agent.py` (`_dispatch_delegate_task`)
- `gateway/run.py` (`_CACHE_BUSTING_CONFIG_KEYS`)
- `hermes_cli/config.py` (`delegation.top_level_mode` default)
- `tests/tools/test_async_delegation.py`
- `tests/gateway/test_agent_cache.py`

Commits:

- `a5a03d980` - add operator-controlled inline top-level delegation.

Preservation checks:

```bash
python -m pytest tests/tools/test_async_delegation.py tests/gateway/test_agent_cache.py tests/hermes_cli/test_config.py -o 'addopts=' -q
cd website && npm run build
```

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
- `82e63bfc2` `2026-07-27` - `fix(discord): humanize MCP progress labels` (prevents Markdown from consuming `mcp__server__operation` separators)
- `7731faed9` `2026-05-29` - `fix: reduce retry and memory-full noise`
- `d1ff7aba6` `2026-07-02` - `fix(discord): watchdog for silently-wedged gateway (green service, dead bot)`
- `87b33d621` - Gateway watchdog follow-up: reject stale finite Discord heartbeat latency and restart on wedged gateway event loop.
- `bafe9702c` `2026-07-15` - `fix(gateway): compare cached replay history consistently`.

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

### Skills system-prompt policy

- `93295c914` `2026-07-18` - `fix(skills): reuse loaded skills for follow-ups`

### Delegation lifecycle behavior

- `a5a03d980` `2026-08-06` - `feat(delegation): add inline top-level mode`

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
  tests/gateway/test_config.py \
  tests/gateway/test_stt_config.py \
  tests/gateway/test_stt_transcript_echo_config.py \
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
  tests/agent/test_auxiliary_runtime_cache_key.py \
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
  tests/tools/test_mcp_parked_self_probe.py \
  tests/tools/test_mcp_reconnect_retry_reset.py \
  tests/gateway/test_discord_liveness.py \
  tests/gateway/test_discord_liveness_watchdog.py \
  tests/gateway/test_gateway_event_loop_watchdog.py \
  tests/gateway/test_discord_connect.py \
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
