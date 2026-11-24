# Jake Hermes fork divergence manifest

Last audited: 2026-11-24

## Current anchor

- Upstream base: `v0.23.4` at `8fc0374447`
- Clean candidate branch: `jake/v0.23.4-clean`
- Rollback ref for the previous live head: `jake/rollback-before-v0.23.4-20261124-1401` (`2740e78204`)
- Upgrade ledger: `ChatWorkspace/hermes/v0.23.4-upgrade-ledger.md`

Replay hashes below are the v0.23.4 ports on the lane branches (`jake/v0.23.4-lane-{L1,L2a,L2b,L3,L4}`), all merged into `jake/v0.23.4-clean`; the v0.22.2 commit each one ports is in parentheses. `git log v0.23.4..jake/v0.23.4-clean` lists them.

This file lists only behavior intentionally retained beyond upstream. It is not a history of old integrations or dropped code. During the next upgrade, start from the new upstream tag and replay the commits below. Before carrying a patch forward, run its gate against plain upstream: green on plain upstream means upstream now owns the behavior, so drop the fork code.

## Preservation gate

`tests/fork_preservation/` now holds six files: `test_acp_commands_v019.py`, `test_agent_display_v019.py`, `test_background_review_fallback_chain.py`, `test_image_shrink_session_cache.py`, `test_notification_silence_marker.py` and `test_voice_transcript_persistence.py`. The v019 image-shrink, native fast-path and ACP-session files were retired on v0.23.4 because upstream's own tests cover what they still asserted. Treat stale tests as migration clues rather than proof that a feature is missing. Run upstream's current tests first, then adapt or retain only the assertions that still describe intentional behavior.

## Keep buckets

### 1. Durable hybrid memory search

**Purpose:** Search Jake's canonical Markdown memory and project files through rebuildable keyword and semantic indexes.

**Behavior to preserve:**

- `memory_search` over ChatWorkspace, Hermes memories, and curated LocalOps notes
- Keyword, Gemini, and local sklearn semantic modes
- Chunk and observation granularity, source/path filters, and TOON rendering
- Persisted sqlite-vec vectors, coverage status, bounded preindex, and provenance
- Tool exposure in the appropriate agent toolsets; `memory_search` is a configurable toolset, so an explicit pin without it drops it
- Kept out of upstream's shared-metrics name sets (`toolsets._FORK_ONLY_NAMES`): the fork tool/toolset reports as `custom`, so the v4 telemetry schema stays upstream's
- Explicit dependencies for sqlite-vec (`sys_platform != 'android'`: no Android wheel), NumPy 2.5.4 (matches upstream's extras pin), scikit-learn, google-genai 2.34.0 (2.12.1 capped google-auth below upstream's pin), and CairoSVG

**Replay commits:**

- `7d0617f0a2` + ruff follow-up `2e7d41f2a7` (`fab9ff4309`) — restore durable memory search and declare fork dependencies
- `92ca2015bf` (`aad8762834`) — declare scikit-learn for the local fallback
- `e829188029` (`a7046fafe3`) — keep fork names out of shared metrics
- `727ba78c40` (`42293ceb99`) — configurable toolset
- `a609cf9ee2` (`9f320f090b` lock half) — relock. On v0.23.4 relock with `scripts/relock.sh` (`pm.cli lock`); the uv.lock diff is additions only, and the old CairoSVG exclude-newer exception is no longer needed.

**Primary paths:** `tools/memory_search_tool.py`, `tools/toon_renderer.py`, `toolsets.py`, `hermes_cli/tools_config.py`, `pyproject.toml`, and `uv.lock`.

**Gates:** `tests/tools/test_memory_search_tool.py`, `tests/tools/test_toon_renderer.py`, `tests/hermes_cli/test_shared_metrics_efficiency.py`, and `tests/tui_gateway/test_profiles_toolset_pin.py`.

### 2a. Delegation budget visibility

**Purpose:** Put each child's iteration limit and inactivity limit directly in the model-facing `delegate_task` description so task sizing happens before a child is spawned. Upstream states only concurrency.

**Behavior to preserve:** The dynamic tool schema reads `delegation.max_iterations` and `delegation.child_timeout_seconds` from live config and renders `Limits per child: N iterations / Ns without progress. Reserve room to summarize.` (`no inactivity cap` when the timeout is unset). `child_timeout_seconds` is an inactivity cap that resets on progress, not wall-clock. The late-result continuation budget is gone from the text with §2's removal.

**Replay commit:** `901687ca10` (`8ccef5f3cc`)

**Primary path:** `tools/delegate_tool.py` (`_child_budget_rule`).

**Primary gate:** `tests/tools/test_delegate_budget_visibility_fork.py` (red on plain v0.23.4). The rendered wording is snapshotted in `tests/fixtures/prompt_surface/tools/delegate_task.json` (`tests/ci/test_prompt_surface.py`).

### 2b. Silent-reply hint on internal notices

**Purpose:** A background-process or delegation notice that changes nothing must not post a "nothing new" reply to the chat.

**Behavior to preserve:** Chat-platform push notices (`_inject_watch_notification` → `handle_message`) carry `SILENT_REPLY_HINT` ("reply with exactly [SILENT]…") just above the internal-notification footer; api_server self-post wakes do not. Upstream v0.23.4 has no equivalent hint. Suppressing an exact `[SILENT]` reply is upstream-owned.

**Replay commit:** `53cb1291fe` (partial port of `ed45fcc202`: the hint half only; the `[NO_USER_VISIBLE_UPDATE]` sentinel went with §2).

**Key file:** `gateway/run_notifications.py`

**Primary gate:** `tests/fork_preservation/test_notification_silence_marker.py` (drives the real push and self-post paths; red on plain v0.23.4).

### 2c. Background-review fallback chain

**Purpose:** The background review runs on Jake's subscription model (luna) with a paid DeepSeek backup for when the subscription hits its usage limit.

**Behavior to preserve:** a routed review fork passes `auxiliary.background_review.fallback_chain` (entries with provider + model) as the forked AIAgent's `fallback_model`. Without it the forked agent has no fallback and an over-limit review fails. Compression uses upstream `auxiliary.compression.fallback_chain` and needs no fork code.

**Replay commit:** `550d16eb64` (`361b012443`)

**Key file:** `agent/background_review.py` (`_fork_init_kwargs`, `_review_fallback_chain`)

**Primary gate:** `tests/fork_preservation/test_background_review_fallback_chain.py`

### 3. Session-local image shrink cache

**Purpose:** Recover from provider HTTP 413 image-size failures without recompressing the same image on every later turn.

**Behavior to preserve:** a session-local shrink cache scoped per (provider, model) that `build_api_kwargs` replays, so an image shrunk once stays shrunk for the session. Upstream v0.23.4 still has no cache: it shrinks once per turn on the request clone only. The per-turn shrink and the native-image fast path are upstream-owned (`tests/agent/test_image_shrink_recovery.py`, `tests/tools/test_vision_native_fast_path.py`); the fork's two v019 tests for them were deleted on v0.23.4 because they passed on plain upstream.

**Replay commit:** `fc258bb673` (`d2741d7543`)

**Primary paths:** `agent/conversation_compression_image_shrink.py` (cache helpers, reusing upstream's `_source_to_data_url`/`_write_data_url_to_source`), `agent/vision_message_prep.py`, and `agent/chat_completion_helpers.py` (`build_api_kwargs`).

**Primary gate:** `tests/fork_preservation/test_image_shrink_session_cache.py` (red on plain v0.23.4).

### 4. ACP Aside compatibility

**Purpose:** Keep Aside able to open and steer Hermes ACP sessions while using upstream's ACP architecture.

**Behavior to preserve:** From v0.23, `hermes acp` returns `GatewayACPAgent`, a viewer attached to the gateway; the local `SessionManager` agent is used only by tests. On that live path the fork keeps:

- Remote-host cwd fallback: `_tool_cwd_for_this_host` in `acp_adapter/session.py`, used for gateway `session.create`, workspace moves and `_moved`. Plain v0.23.4 refuses Aside's `/Users/...` cwd with `invalid_params`, so Aside cannot open a session at all.
- The `thought_level` id override: the gateway effort picker is advertised under id `thought_level` (upstream advertises `reasoning_effort` with category `thought_level`); both ids are accepted on writes. Aside's client (`Scripts/aside-acp-client.mjs`) sends `--config-option thought_level=<v>` and skips any id the agent does not advertise. Open decision: change Aside to select by category and drop this override at the next rebuild (recommended), or keep it.

Upstream-owned or obsolete: the local-agent selector in `acp_adapter/server.py` (dead in production), provider snapshot (upstream create writes the full model_config, #139908), adopted session IDs, and title updates.

**Replay commit:** `2856a1eef7` (partial port of `e308a8286a`). One upstream assertion was edited: `tests/acp_adapter/test_gateway_service_tier.py` expects id `thought_level`.

**Primary paths:** `acp_adapter/gateway_server.py`, `acp_adapter/gateway_session_meta.py`, and `acp_adapter/session.py`.

**Gates:** `tests/acp_adapter/test_fork_aside_compat.py` (remote-host cwd end to end and the `thought_level` id; red with the source stashed) and `tests/fork_preservation/test_acp_commands_v019.py` (steer/queue). `test_acp_session_v019.py` is retired: its passing tests duplicated upstream's and its failures covered upstream-owned behavior.

### 5. Cached gateway replay consistency

**Purpose:** Avoid false gateway restarts when cached and live histories differ only because cleanup was applied asymmetrically.

**Behavior to preserve:** normalize both sides with the same replay cleanup before comparison.

**Replay commit:** `b99afda9c5`

**Status (v0.22.2 onward): converged upstream; no fork code and no fork gate.** Archaeology only (coverage audit: `--allow-no-tests-section 5`); kept so the next upgrade does not resurrect it.

### 6. Approval and command-safety UX

**Purpose:** Show users the actual security finding and make safe correction possible without weakening command guards.

**Behavior to preserve:** content-complete Discord approval and clarify prompts, invisible-character surfacing, and readable choice lists. On v0.23.4 this is a union with upstream's `numbered_clarify_choices` (long labels): the fork adds a numbered list only when upstream did not number the choices (labels up to 40 columns), never a double list, and applies the invisible-codepoint escape to upstream's list too. The header uses the localized `clarify_title`; the English `**Choices:**` label is gone.

**Replay commit:** `157e55a16f` (`b7c5d6b30a`). Upstream deleted the Tirith scanner on v0.22.2, so only the Discord prompt fixes remain.

**Primary paths/gate:** `plugins/platforms/discord/adapter.py`; `tests/gateway/test_discord_prompt_content_payload.py`.

### 7. Discord long-turn and blocking-prompt mentions

**Purpose:** Notify Jake when a long response finishes or when a clarification blocks progress.

**Behavior to preserve:** configurable elapsed-time rules (`display.long_turn_mention.*`) for finals and immediate mentions for blocking clarification prompts. Approval mentions remain upstream-owned through `discord.approval_mentions`. Both final-delivery paths apply the elapsed gate: the normal completion path and the queued-follow-up "deliver first response before continuing" path, timed from `TurnContext.started_monotonic` (read with `getattr`, since upstream fakes lack it). The final mention is applied before upstream's `FooteredReply` wrapper so footer-as-caption keeps working.

**Replay commits:** `d58403bec6` (gateway half, `590ba9eadf` + `49f7eb48a4`) and `76ad673277` (adapter half, `b993cf66cd`).

The clarify ping has two halves, and both must survive a replay: the gateway resolves the requester's mention into `metadata["mention_text"]` on the shared send kwargs in `_ask_clarify_question` (`gateway/run_turn_runner.py`), and the Discord adapter's `send_clarify` must read it, accept only a bare `<@digits>`/`<@!digits>`, reserve its length before truncation, prepend it to the same content-first prompt, and set users-only `allowed_mentions`. A past replay dropped the adapter half and clarify prompts silently stopped pinging; `tests/gateway/test_discord_clarify_mention.py` guards it.

**Primary paths:** `gateway/long_turn_mention.py` (fork-only helpers, moved out of `gateway/run.py`), `gateway/run_turn.py`, `gateway/run_turn_runner.py`, `gateway/turn_context.py`, `hermes_cli/config_defaults.py`, and `plugins/platforms/discord/adapter.py`.

**Gates:** `tests/gateway/test_long_turn_mention.py` and `tests/gateway/test_discord_clarify_mention.py`.

### 8. Read-only `/model` status

**Purpose:** Make `/model` useful as a status command without forcing a model change.

**Behavior to preserve:** `/model status` shows the current model/provider/reasoning and session-only hints instead of switching to a model named "status" (which plain v0.23.4 still does). It runs after the channel and session overrides apply and before any switch, and lists upstream's `/model reset`. `usage_open_picker` exists in en + 16 locales.

**Replay commit:** `466c4ebfbf` (`1aedbf4925`)

**Primary path:** `gateway/slash_commands_model.py`.

**Primary gate:** `tests/gateway/test_model_command_status.py` (the canonical Copilot provider id is now `copilot`). The fork's additions to the Discord model-picker tests were dropped: upstream covers them.

### 9. Gateway liveness and shutdown forensics

**Purpose:** Detect a broken Discord REST delivery path even when the WebSocket appears healthy.

**Behavior to preserve:** independent Discord REST liveness probe (`discord.rest_liveness_*`, in the docs-drift baseline). Upstream v0.23.4 is still WebSocket-only; upstream owns the out-of-loop event-loop watchdog.

**Replay commit:** `2616172f90` (`bb33dca7d9`)

**Primary gates:** `tests/gateway/test_discord_liveness_watchdog.py` and `tests/gateway/test_liveness_probes_fork.py`.

### 10. Busy-mode steering compatibility

**Purpose:** Preserve mid-run steering and busy acknowledgements while using upstream's current gateway ownership.

**Behavior to preserve:** upstream's runtime mechanism. Do not restore the old duplicate implementation. The fork commit was test adaptation only and is not replayed on v0.23.4; upstream's own tests are the gates.

**Replay commit:** none (historic `cdce2ff98d`, tests only).

**Primary gates:** `tests/gateway/test_busy_command.py`, `tests/gateway/test_busy_session_ack.py`, and `tests/agent/test_steer.py`.

### 10a. Steering survives tool-result compaction

**Purpose:** Keep runtime-appended user corrections from disappearing when their surrounding tool output is shortened.

**Behavior to preserve:** Exact canonical complete steering blocks remain at their original tool positions through ordinary, protected-tail, duplicate-result, native-envelope, and lean-tail pruning. Summary-input slicing retains whole blocks and source tool labels; compacted corrections are quoted as historical context without creating fresh user deliveries. Ordinary tool bodies remain prunable, stale API sidecars are discarded after rewrites, and existing strict summary secret redaction still applies. On v0.23.4 upstream adds an archive pointer to every pruned or demoted stub; `tools/tool_result_storage._with_pointer` places the pointer before the steering block so the block survives.

**Replay commit:** `a274b5adfe` (`c05ffe5397`). Bare v0.23.4 fails 83 of 104 cases.

**Primary paths/gate:** `agent/compaction_steering.py`, `agent/context_compressor.py`, `agent/context_compressor_tail.py`, `agent/context_compressor_sampling.py`, `tools/tool_result_storage.py`; `scripts/run_tests.sh tests/agent/test_compaction_steering_preservation.py`.

### 10b. Full-conversation, single-call compaction

**Purpose:** Preserve useful outcomes and later corrections before deterministic pruning can turn them into metadata or recovery stubs.

**Status (v0.23.4): upstream now owns part of this; that fork code was dropped.** Upstream `compress()` summarizes the window's original rows (`summary_view`), archives and publishes reductions only after a summary exists (`_commit_window`), and filters clarify notices. The fork no longer replaces `compress()` (`FullConversationCompactionMixin` is gone); it keeps upstream's flow and adds hooks.

**Behavior to preserve:**

- `FullSummaryInput` (`agent/context_compressor_input.py`): the head, replaced region and retained tail go to the summarizer unshortened and uncapped, in one no-tools auxiliary call. The newest user or steering input grounds the task. No iterative PREVIOUS SUMMARY field.
- Fail closed: no deterministic fallback after a failure, stall pin, sustained overload, feasibility skip or content-policy refusal, and no aux→main retry. Failures and no-ops return the exact input and restore the checkpoint state.
- `clarify_result_text` (`agent/context_compressor.py`): the uncapped shared clarify filter, so headless runtime notices never reach the summarizer as user decisions.
- `prune_tool_results_only` (`agent/context_compressor_prune.py`) retires stale images only; proactive text pruning stays disabled.
- Prepared compaction (`compression.prepare_ahead`, opt-in, off in live config) adopts its background summary without full input.

**Open product decision (prompt-cache controller):** the controller's cold-cache steps (idle offload, cold return, route-change offload) all call `prune_tool_results_only`, so under the fork contract they run but shorten no text and usually stop at `reclaim_below_minimum`; keep-alive warming and idle compaction are untouched. Current behavior: the fork keeps proactive prune image-only, pending Jake's decision. The lane recommendation is to allow upstream's cold-cache text prune (byte-exact archives in `cache/pruned/`, no cache break) and keep warm text prune off with `compression.proactive_prune_tokens: 0`, which would drop this hunk and the skips below. It changes what the live model sees between a cache expiry and the next compaction, so it is Jake's call.

**Replay commits:** `c8125ba651` (`7aad3cae60` + `a540caeb73`), `a07d035575` (content-policy refusal fails closed), `3be01d9161` + `2f08d5f933` (tail-tag half of `d90a35c2a3`), and `b6e0ea77a5` (extra text-prune skips).

**Primary paths:** `agent/context_compressor_input.py`, `agent/context_compressor_prune.py`, `agent/context_compressor.py`.

**Gates:** `scripts/run_tests.sh tests/agent/test_full_conversation_compaction.py` (10 of 12 red on bare v0.23.4), plus the fork-adapted fail-closed assertions in `tests/agent/test_context_compressor.py`, `tests/agent/test_context_compressor_overload.py`, `tests/agent/test_compress_tail_tags.py`, `tests/agent/test_auxiliary_error_policy.py`, `tests/agent/test_compaction_events.py`, `tests/agent/test_compression_attempt_effect.py`, `tests/agent/test_compression_stall_deterministic_fallback.py`, `tests/agent/test_split_turn_compaction.py`, `tests/agent/test_relay_compaction_atof.py`, `tests/agent/test_proactive_tool_result_pruning.py`, `tests/agent/test_proactive_prune_rearm_threshold.py`, and `tests/agent/test_proactive_prune_restart_safety.py`. Upstream text-prune tests are skipped with `FORK_TEXT_PRUNE_DISABLED` in `tests/agent/test_proactive_prune_archive.py`, `tests/agent/test_proactive_prune_archive_durability.py`, `tests/agent/test_proactive_prune_cold_cache.py`, `tests/agent/test_proactive_prune_readback.py`, `tests/agent/test_proactive_prune_round_guards.py`, `tests/agent/test_compression_skill_safety_single_copy.py`, `tests/agent/test_route_change_offload.py`, `tests/agent/test_oneshot_interactive_surface.py`, `tests/gateway/test_message_timestamps.py`, and `tests/tui_gateway/test_compression_config_hot_reload.py`; revisit them if the decision above lands.

### 11. Auxiliary provider and prefill timeout behavior

**Purpose:** Avoid false auxiliary-stream timeouts before expected prefills arrive.

**Behavior to preserve:** prefill-aware first-token window on the Codex Responses auxiliary adapter only, which is Jake's compression route (openai-codex). Upstream's chat-completions watchdog (`chat_stream_windows`) now scales its own first-token window by context, so the fork residue lives only on the Codex adapter. An explicit `auxiliary.<task>.no_progress_timeout` wins outright. The retry gate reads upstream's classified `aux_errors.Failure` (`agent/auxiliary_error_policy.py`). Copilot-specific residue stays dropped.

**Replay commit:** `ce157a29e5` (`44cf87ebee` + `90dfabde79`)

**Primary path:** `agent/auxiliary_client.py`.

**Gates:** `tests/agent/test_aux_prefill_timeout_fork.py` (fails at import on bare v0.23.4) and `tests/agent/test_auxiliary_client.py::TestCodexAuxiliaryAdapterTimeout::test_no_progress_timeout_kwarg_overrides_default_window`.

### 12. Terminal progress expansion and friendly labels

**Purpose:** Show complete terminal commands without enabling globally verbose tool output.

**Behavior to preserve:** `display.expand_terminal_commands`, default off (in the docs-drift baseline); per-platform override; shell strict-mode line removal; upstream-owned preview rendering; and consistent friendly progress labels, including the `memory_search` verb in en + 16 locales.

**Replay commit:** `320ce1495b` (`3349b50a71` + `313de66157`)

**Primary paths:** `gateway/run_turn_progress.py` (upstream's extraction), with the flag plumbed through `gateway/display_config.py`, `gateway/run.py`, `gateway/run_turn.py`, `gateway/turn_context.py`, `hermes_cli/config_defaults.py`, and `agent/display.py`.

**Primary gates:** `tests/gateway/test_terminal_expansion_and_labels_fork.py`, `tests/gateway/test_run_progress_topics.py`, and `tests/fork_preservation/test_agent_display_v019.py` (passes on plain upstream; kept as a regression guard because upstream removed those tests). `test_run_progress_topics.py::test_base_processing_stops_typing_before_hung_post_delivery_callback` fails identically on plain v0.23.4.

### 13. Conservative background curation

**Purpose:** Prevent routine conversations from creating noisy durable memories or unnecessary skill edits.

**Behavior to preserve:** save only genuinely new durable facts, route detail to canonical files, and update skills only when there is reusable procedural learning. Skill edits merge into the existing rule instead of appending clauses, and a candidate rule must pass a generalization test (would it change behavior on a different task of the same class?) or be restated as its principle.

**Replay commits:**

- `0124dbedde` (`0ac5e6009b`) — conservative curator prompts
- `f69226d75d` (`43036da45c`) — merge-don't-accrete and generalization test
- `8dbc443448` (`76858f25c1` test half) — class-first prompt gate

**Primary gates:** `tests/agent/test_background_review_prompt_hygiene.py` and `tests/agent/test_review_prompt_class_first.py` (both red on plain v0.23.4).

### 14. Recall-layer routing and session-scoped skill reuse

**Purpose:** Use the right source for durable context, transcript recall, live files, and current web facts without repeatedly reloading an unchanged skill.

**Behavior to preserve:** memory_search as durable cache, session_search as transcript archive, file tools as live disk, web tools for current external facts, and skill reuse within an ongoing task.

**Replay commit:** `71a3720334` (`de5758519e`)

**Primary paths:** `agent/prompt_builder.py`.

**Gates:** `tests/agent/test_prompt_builder.py` (recall-layer routing and session-scoped skill reuse), with `tests/fixtures/prompt_surface/` carrying the rendered wording (`tests/ci/test_prompt_surface.py`).

### 15. Codex Responses TTFB handling

**Purpose:** Avoid repeated time-to-first-byte status messages for one request.

**Behavior to preserve:** deduplicate TTFB status by model/input while still reconnecting. Upstream owns the Responses alias de-duplication.

**Replay commit:** `a1459ba2de` (`7251e84b63`)

**Primary path:** `agent/chat_completion_nonstream.py`.

**Primary gates:** `tests/agent/test_codex_ttfb_watchdog.py` (red on plain v0.23.4), `tests/agent/test_codex_responses_settle_pending_tool_calls.py`, and `tests/agent/test_nonstream_wait_notice.py`.

### 16. Discord SVG source ingestion

**Purpose:** Make SVG attachments readable without depending on Discord's native-image cache or a rasterizer.

**Behavior to preserve:** detect SVG by MIME type or filename, cache it through the authenticated document path, classify it as a document, and inject small UTF-8 SVG source into the prompt. SVGs above the 100 KB text-inline cap must reach the agent as readable document-path notes with `media_text_inlined=false`. Never route `image/svg+xml` through native-image handling or the raster magic-byte validator, including in mixed SVG+raster messages.

**Replay commits:** `a336145336`, `a4004247cd`, `aa8e594de1` (`0e9c92f4a6`, `97a32bd5a9`, `5e638bee99`)

**Primary paths:** `plugins/platforms/discord/adapter.py`, `gateway/platforms/base.py`, `gateway/run.py`, `tests/gateway/test_discord_attachment_download.py`, and `tests/gateway/test_mixed_attachment_routing.py`.

### 17. Configurable Discord long-response delivery

**Purpose:** Let Jake choose how many inline messages a long reply may use before it becomes a file, and drop visual chunk counters. Not losing the tail is now upstream's: past `MAX_SPLIT_MESSAGES` upstream hands the reply off as a `.md` file (#152012) instead of truncating, so the fork's cap plumbing was dropped.

**Behavior to preserve:** `discord.max_split_messages` sets the instance `MAX_SPLIT_MESSAGES` (clamped 2..100), the point where upstream's file handoff starts; unset keeps upstream's 8. `discord.chunk_indicators: false` strips chunk counters through a `DiscordAdapter.truncate_message` override. Both bridge into the Discord platform's extra config via `gateway/config_loader.py`. Jake's local config uses 16 messages and disables indicators, so 9-16 message replies split inline and only longer ones go out as a file.

**Replay commit:** `5e53f6fad6` (`7904670f82` + `2b5758b3cb`, shrunk)

**Primary paths:** `plugins/platforms/discord/adapter.py`, `gateway/config_loader.py`, and `tests/gateway/test_discord_split_cap.py` (upstream file plus fork cases).

### 18. Avoid redundant paid vision analysis for native images

**Purpose:** When a user image is already attached natively to the model, do not suggest an extra `vision_analyze` call just because the user referenced that image.

**Behavior to preserve:** The tool schema reserves `vision_analyze` for images not already in the model's context, new image files/URLs, or a genuinely needed closer crop. Its first sentence, "Load an image not already attached to your context.", fits the 80-character `tool_search` catalog line. Native user-message routing remains unchanged; the auxiliary vision fallback remains available for sessions that need it. Auxiliary vision and compression routing lives in `~/.hermes/config.yaml` (local config, not a fork commit).

**Replay commits:** `4ac1ac0165` (`e7e199b339`) and follow-up `8d2a354f16`

**Primary gate:** `tests/tools/test_vision_tools.py::TestVisionRegistration::test_vision_analyze_registered_with_schema`, co-gated by `tests/tools/test_tool_search.py::test_builtin_catalog_lines_are_whole_first_sentences`; upstream's `tests/tools/test_vision_native_fast_path.py` stays green.

### 20. Voice transcripts saved to daily logs

**Purpose:** Keep a durable, `memory_search`-indexed record of what Jake said in Discord voice notes.

**Behavior to preserve:** When `stt.persist_voice_transcripts: true`, every successful inbound transcription (normal turns AND mid-run voice steering) appends `## <timestamp>`, the audio filename, and the verbatim transcript to `<stt.voice_transcripts_dir>/<YYYY-MM-DD>.md` (default `<HERMES_HOME>/memories/memory-media/transcripts`). Off by default. Empty/failed transcriptions write nothing; memo hits from v0.23's clip_memo re-pass are not persisted again. The write runs in a background thread; a disk error is logged and never blocks the turn. Independent of `stt.echo_transcripts`.

**Replay commit:** `a10d157cfb` (`4eacc56e0e`)

**Primary paths:** `gateway/run_inbound_transcription.py` and `gateway/config.py` (STT fields, loader, `to_dict`).

**Primary gates:** `tests/fork_preservation/test_voice_transcript_persistence.py` (drives the real config loader and the real transcription loop, including the busy-steer path, so a rebuild that keeps the helper but drops the call site or config wiring fails).

### 21. Reasoning effort in gateway status

**Purpose:** Show the current session's reasoning effort below its model in `/status`.

**Behavior to preserve:** During a turn, use the running agent's reasoning config and transport. While idle, resolve session override > per-model override > global config, not the stale cached agent's effort. Preserve disabled and unset/default labels and the canonical route-clamping annotation (for example `ultra (sends max on this route)`). Reading status must not change reasoning settings. `gateway.status.reasoning{,_default}` exist in en + 16 locales (the non-English strings were agent-written; worth a review).

**Replay commit:** `06c2725d37` (`3217fb68ae`)

**Primary paths:** `gateway/slash_commands_status.py` and `locales/`.

**Preservation gates:** `tests/gateway/test_status_reasoning.py` (red on plain v0.23.4) and `tests/gateway/test_status_command.py` (existing status/context behavior).

### 22. ChatGPT Codex bare-400 recovery

**Purpose:** Stop ChatGPT Codex's bare HTTP 400 `{"detail":"Bad Request"}` from killing subagents on the first hit and from dropping a long Discord session's user turn as a fake context overflow. The body names no field; reconstructed replays of failed requests were accepted later, and upstream reports the same error (openai/codex#57874, #59621). Cause unconfirmed.

**Behavior to preserve:** Only `openai-codex` on `chatgpt.com/backend-api/codex` with that exact body (or the SDK's string form when no body parsed) classifies as `server_error` with an `error_context["codex_opaque_bad_request"]` marker; other providers, `openai-codex` on another host, and detailed Codex 400s keep their own rules. Per API call, recovery runs two one-shot rungs before credential-pool rotation: (1) a new prompt-cache scope salt (sticks for the session, Codex route only) plus client rebuild; (2) a resend from the request copy without replayed reasoning and message items, keeping native compaction checkpoints, skipped when nothing was replayed. Then fallback or the terminal copy. The failed turn is persisted, and the gateway's bare-`400` + long-history overflow heuristic ignores `failure_reason == "server_error"` while explicit overflow phrases still win. No new `FailoverReason` value.

**Why upstream does not cover it:** upstream `0513e714a5` resends a zstd body as plain JSON after a bare 400, but the live venv is Python 3.11 with no stdlib zstd, so requests were already plain JSON when the bare 400s were logged (Nov 11, subagents on gpt-6.1-sol). The port is hand-merged with upstream's `_codex_soft_failure_error` / `classify_codex_soft_failure` and the `TerminalFailureText` wrapper.

**Replay commit:** `ada9d54701` (`0698e05399`)

**Primary paths:** `agent/error_classifier.py`, `agent/turn_recovery_codex.py`, `agent/turn_recovery.py`, `agent/turn_retry_state.py`, `agent/chat_completion_helpers.py`, `agent/turn_failure_copy.py`, and `gateway/run_turn.py`.

**Preservation gate:** `tests/agent/test_codex_opaque_bad_request.py` (red on plain v0.23.4: the classifier helper is missing upstream).

### 23. Cron worker startup-kill retry and sender trace

**Purpose:** A restart-safe cron worker is occasionally killed by SIGTERM a second or two after it starts, before it adopts its claim (seen on the 15-minute `ballpark-gateway-health-ping`: Oct 19 twice, Oct 28, Nov 8, 2026). systemd, the OOM killer and the process sweeps leave no trace of it, and the alert read like a Ballpark outage. Cause still unknown.

**Behavior to preserve:** When the worker dies by a signal before acknowledging, the dispatcher re-fences the execution row with `mark_execution_handoff_pending`. If the row is still claimed by the dispatcher (proof that nothing adopted it, so nothing ran), it relaunches once under the unit suffix `...-retry`. If the row was adopted, or the retry also dies, the fire fails with "the job did not run: its worker process was killed by SIGTERM while starting up ... (N attempts)". Ordinary nonzero exits are never retried. In the worker, `cron/worker_signal_trace.py` blocks SIGTERM from package init until the ownership acknowledgement. A watcher thread takes it with `sigtimedwait`, writes the sender's pid, uid, command line and parent chain to `$HERMES_HOME/logs/cron-worker-signals.log` and stderr, then re-sends the signal so the worker dies exactly as before (exit -15). `disarm()` runs right after the acknowledgement and before any job code, because a blocked mask is inherited by child processes. Upstream's `# detached-spawn: ok` marker in `_spawn` stays.

**Replay commit:** `0b1ecfd08a` (`2740e78204`)

**Primary paths:** `cron/scheduler.py` (`_launch_external_cron_worker`, `_run_external_worker_payload`), `cron/worker_bootstrap.py` and `cron/worker_signal_trace.py`.

**Preservation gate:** `tests/cron/test_worker_pre_ack_signal.py` (5 of 6 red on plain v0.23.4; the ordinary-exit preservation test passes on both).

### 24. Filler-word strip for Discord transcript echoes

**Purpose:** Make the echoed `🎙️ "…"` transcript of a voice note readable without altering what the agent receives.

**Behavior to preserve:** With `stt.echo_strip_fillers: true` (off by default), `_echo_stt_transcripts` removes standalone filler words (case-insensitive; default `um`, `umm`, `uh`, `uhh`, `uhm`, `erm`, `mm-hmm`, `mm-hm`, replaceable as a whole via `stt.echo_filler_words`) from the echo only, never from words that merely contain one (`umbrella`), and tidies spacing and sentence capitals. The agent-facing and persisted transcripts stay verbatim. Merged into upstream's reworked echo, which returns the accepted count and honors `automatic_notices_allowed`.

**Replay commit:** `fc523b43a7` (`0f44149cdf`)

**Primary paths:** `gateway/run_inbound.py` (`_strip_stt_filler_words`, `_echo_stt_transcripts`), `gateway/run_voice.py`, and `gateway/config.py`.

**Gates:** `tests/gateway/test_stt_echo_filler_strip.py` and `tests/gateway/test_stt_echo_filler_strip_echopath.py`.

## Intentionally absent

Do not revive these from the old fork unless Jake makes a new product decision:

- Bounded late delegation completion (old §2, with its `[NO_USER_VISIBLE_UPDATE]` sentinel): its injected prompt forbade new delegation and stalled chained work; live config runs `delegation.completion_max_turns: 0` (upstream full-turn behavior).
- Legacy `...[truncated]` tool-argument guard (old §19): no legacy-suffix tool calls in live `state.db` since Nov 1 apart from one probe; upstream's guard on its non-imitable marker stays.
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
2. Run each fork gate against plain upstream first; green means upstream owns the behavior and the fork code is dropped.
3. Replay these focused commits in order, dropping any commit whose behavior has converged upstream.
4. Hand-port conflicts into the current upstream module that owns the runtime seam. Never restore whole files from an old fork. Confirm the production entry point actually builds the class you patch.
5. Verify behavior at runtime and retain a focused permanent gate for each surviving fork behavior.
6. Run owner suites serially. The repository's monolithic suite currently leaks process state across suites, and xdist is not valid evidence.
7. Push the candidate before changing the live checkout.
8. Keep a rollback ref for the old live head, refresh the live environment, let Jake restart the gateway, and verify health plus real Discord and attachment traffic.
