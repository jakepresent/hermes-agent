# Jake Hermes fork divergence manifest

Last audited: 2026-09-09

## Current anchor

- Upstream base: `v2026.9.7` at `2237be3559`
- Clean candidate branch: `jake/v2026.9.7-clean`
- Latest verified behavior commit: `0117914da4`
- Package version: Hermes Agent 0.21.1
- Cleanup ledger: `ChatWorkspace/hermes/v2026.9.7-cleanup-ledger.md`, rev 13

This file lists only behavior intentionally retained beyond upstream. It is not a history of old integrations or dropped code. During the next upgrade, start from the new upstream tag and replay the commits below. Before carrying a patch forward, check whether upstream now owns the behavior and prefer upstream's implementation when it does.

## Preservation gate

Commit `c10a0ad576` restores the still-relevant fork-preservation tests on top of the upstream tag. Treat stale tests as migration clues rather than proof that a feature is missing. Run upstream's current tests first, then adapt or retain only the assertions that still describe intentional behavior.

## Keep buckets

### 1. Durable hybrid memory search

**Purpose:** Search Jake's canonical Markdown memory and project files through rebuildable keyword and semantic indexes.

**Behavior to preserve:**

- `memory_search` over ChatWorkspace, Hermes memories, and curated LocalOps notes
- Keyword, Gemini, and local sklearn semantic modes
- Chunk and observation granularity, source/path filters, and TOON rendering
- Persisted sqlite-vec vectors, coverage status, bounded preindex, and provenance
- Tool exposure in the appropriate agent toolsets
- Explicit dependencies for sqlite-vec, NumPy, scikit-learn, google-genai, and CairoSVG

**Replay commits:**

- `e35946ff2b` — restore durable memory search and declare fork dependencies
- `35473edb54` — declare scikit-learn for the local fallback

**Primary paths:** `tools/memory_search_tool.py`, `tools/toon_renderer.py`, `toolsets.py`, `pyproject.toml`, and their focused tests.

### 2. Bounded late delegation completion

**Purpose:** Allow a completed child task to re-enter a conversation after the commissioning turn has ended, without creating an unbounded autonomous loop.

**Behavior to preserve:** bounded continuation rounds, parent-session delivery, and protection against repeated late completions.

**Replay commit:** `0898fd3737`

**Primary gates:** `tests/fork_preservation/test_async_delegation_v019.py` and `tests/gateway/test_subagent_protection_30170.py`.

### 3. Session-local image shrink recovery

**Purpose:** Recover from provider HTTP 413 image-size failures without repeatedly recompressing the same image or mutating unrelated sessions.

**Behavior to preserve:** session-local shrink cache and native-image fast path.

**Replay commit:** `b70e715acc`

**Primary paths:** `agent/vision_message_prep.py`, `tests/fork_preservation/test_image_shrink_recovery_v019.py`, and `tests/fork_preservation/test_vision_native_fast_path_v019.py`.

### 4. ACP session provenance and thought-level controls

**Purpose:** Preserve Aside-compatible ACP behavior while using upstream's ACP architecture.

**Behavior to preserve:** thought-level selection, provider snapshot correctness, adopted session IDs, remote-host working directories, and title update behavior.

**Replay commit:** `2db1801eb4`

**Primary paths:** `acp_adapter/server.py`, `acp_adapter/session.py`, and ACP preservation tests.

### 5. Cached gateway replay consistency

**Purpose:** Avoid false gateway restarts when cached and live histories differ only because cleanup was applied asymmetrically.

**Behavior to preserve:** normalize both sides with the same replay cleanup before comparison.

**Replay commit:** `b99afda9c5`

**Primary gate:** `tests/gateway/test_cached_history_replay_guard.py`.

### 6. Approval and command-safety UX

**Purpose:** Show users the actual security finding and make safe correction possible without weakening command guards.

**Behavior to preserve:** content-complete Discord approval prompts, Tirith finding detail, invisible-character surfacing, self-correctable rule classification, and hardline command blocking even when a command is allowlisted.

**Replay commit:** `a885165f07`

**Primary paths:** `tools/approval.py`, `tools/tirith_security.py`, `tests/gateway/test_discord_prompt_content_payload.py`, and command-guard tests.

### 7. Discord long-turn and blocking-prompt mentions

**Purpose:** Notify Jake when a long response finishes or when a clarification blocks progress.

**Behavior to preserve:** configurable elapsed-time rules for finals and immediate mentions for blocking clarification prompts. Approval mentions remain upstream-owned through `discord.approval_mentions`.

**Replay commit:** `62a6d18927`

**Primary paths:** `plugins/platforms/discord/adapter.py`, gateway display/config plumbing, and `tests/gateway/test_long_turn_mention.py`.

### 8. Read-only `/model` status

**Purpose:** Make `/model` useful as a status command without forcing a model change.

**Behavior to preserve:** current model/provider/reasoning display and session-only hints.

**Replay commit:** `d909bb540c`

**Primary gates:** `tests/gateway/test_model_command_status.py`, `tests/gateway/test_discord_model_picker.py`, and reasoning-command tests.

### 9. Gateway liveness and shutdown forensics

**Purpose:** Detect a wedged event loop or a broken Discord REST delivery path even when the WebSocket appears healthy.

**Behavior to preserve:** out-of-loop event-loop watchdog, Discord REST liveness probe, thread-stack diagnostics, and restart exit semantics.

**Replay commits:**

- `e11af4dd3d` — add event-loop and Discord REST probes
- `c39a5ae5f1` — remove a duplicate heartbeat method that shadowed upstream's owner

**Primary gates:** `tests/gateway/test_gateway_event_loop_watchdog.py`, `tests/gateway/test_discord_liveness_watchdog.py`, and `tests/gateway/test_liveness_probes_fork.py`.

### 10. Busy-mode steering compatibility

**Purpose:** Preserve mid-run steering and busy acknowledgements while using upstream's current gateway ownership.

**Behavior to preserve:** upstream's runtime mechanism plus fork-sensitive compatibility gates. Do not restore the old duplicate implementation.

**Replay commit:** `cdce2ff98d`

**Primary gates:** `tests/gateway/test_busy_command.py`, `tests/gateway/test_busy_session_ack.py`, and `tests/run_agent/test_steer.py`.

### 11. Auxiliary provider and prefill timeout behavior

**Purpose:** Avoid false auxiliary-stream timeouts before expected prefills arrive, while retaining model/provider routing used by Jake's setup.

**Behavior to preserve:** prefill-aware progress timers, model-aware auxiliary clients, and current provider/reasoning selection. Copilot-specific residue was intentionally dropped.

**Replay commit:** `1d36d1334b`

**Primary paths:** `agent/auxiliary_client.py`, `agent/chat_completion_helpers.py`, `agent/conversation_compression.py`, and focused auxiliary timeout tests.

### 12. Terminal progress expansion and friendly labels

**Purpose:** Show complete terminal commands without enabling globally verbose tool output.

**Behavior to preserve:** `expand_terminal_commands`, default off; per-platform override; shell strict-mode line removal; upstream-owned preview rendering; and consistent friendly progress labels.

**Replay commits:**

- `76923cb3bb` — expand terminal progress independently of verbose mode
- `87d0c180ad` — finish friendly progress-label migration

**Primary gates:** `tests/gateway/test_terminal_expansion_and_labels_fork.py`, `tests/gateway/test_run_progress_topics.py`, and `tests/fork_preservation/test_agent_display_v019.py`.

### 13. Conservative background curation

**Purpose:** Prevent routine conversations from creating noisy durable memories or unnecessary skill edits.

**Behavior to preserve:** save only genuinely new durable facts, route detail to canonical files, and update skills only when there is reusable procedural learning.

**Replay commits:**

- `ba94e76b7c` — conservative curator prompts
- `533557994e` — align class-first prompt gates with that policy

**Primary gates:** `tests/agent/test_background_review_prompt_hygiene.py` and `tests/run_agent/test_review_prompt_class_first.py`.

### 14. Recall-layer routing and session-scoped skill reuse

**Purpose:** Use the right source for durable context, transcript recall, live files, and current web facts without repeatedly reloading an unchanged skill.

**Behavior to preserve:** memory_search as durable cache, session_search as transcript archive, file tools as live disk, web tools for current external facts, and skill reuse within an ongoing task.

**Replay commit:** `697f0aad07`

**Primary paths:** `agent/prompt_builder.py` and its prompt/toolset tests.

### 15. Codex Responses alias and TTFB handling

**Purpose:** Prevent duplicate tool calls when Responses events use different item IDs for the same `call_id`, and avoid repeated time-to-first-byte status messages for one request.

**Behavior to preserve:** coalesce pending function-call aliases by `call_id`, retain announced order, and deduplicate TTFB status by model/input while still reconnecting.

**Replay commit:** `3ca41c4420`

**Primary gates:** `tests/agent/test_codex_responses_settle_pending_tool_calls.py` and `tests/agent/test_codex_ttfb_watchdog.py`.

### 16. Discord SVG source ingestion

**Purpose:** Make SVG attachments readable without depending on Discord's native-image cache or a rasterizer.

**Behavior to preserve:** detect SVG by MIME type or filename, cache it through the authenticated document path, classify it as a document, and inject small UTF-8 SVG source into the prompt. Do not route SVG XML through the raster magic-byte validator.

**Replay commit:** `0117914da4`

**Primary paths:** `plugins/platforms/discord/adapter.py`, `gateway/platforms/base.py`, and `tests/gateway/test_discord_attachment_download.py`.

## Intentionally absent

Do not revive these from the old fork unless Jake makes a new product decision:

- Discord guild-history search bridge
- Heredoc-specific terminal summarizer
- MCP reconnect/schema fork
- Copilot compatibility residue
- OCR fallback for non-vision models
- Dashboard recent-session ordering fork
- Fork-local MoA safeguards
- Historical integration notes and superseded implementation paths

ACP remains required because Aside depends on it.

## Upgrade method

1. Create a clean branch from the new upstream release tag.
2. Run upstream's copy of any apparently stale test before concluding behavior is missing.
3. Replay these focused commits in order, dropping any commit whose behavior has converged upstream.
4. Hand-port conflicts into the current upstream module that owns the runtime seam. Never restore whole files from an old fork.
5. Verify behavior at runtime and retain a focused permanent gate for each surviving fork behavior.
6. Run owner suites serially. The repository's monolithic suite currently leaks process state across suites, and xdist is not valid evidence.
7. Push the candidate before changing the live checkout.
8. Keep a rollback ref for the old live head, refresh the live environment, let Jake restart the gateway, and verify health plus real Discord and attachment traffic.
