"""Fork: opt-in Discord mentions for long turns and blocking clarify prompts.

Upstream has no equivalent. A long agent turn or a blocking clarify question can
sit unnoticed in a busy channel; an opt-in mention pulls the requester back.
Final responses are time-gated (only after ``display.long_turn_mention.elapsed_seconds``);
clarify prompts mention immediately because they BLOCK the run. The gateway puts
the clarify mention in ``metadata["mention_text"]``; the Discord adapter's
``send_clarify`` prepends it with users-only ``allowed_mentions``.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional


def final_response_mention(response: Any, source: Any, elapsed_seconds: float) -> Any:
    """Best-effort: prefix ``response`` with the long-turn mention when the policy asks for one.
    A mention failure must never stop the answer from going out."""
    if not response:
        return response
    try:
        from gateway.run import _load_gateway_config, _platform_config_key
        return _apply_long_turn_mention_to_response(response, _long_turn_mention_text_for_source(
            source, _load_gateway_config(), _platform_config_key(source.platform),
            elapsed_seconds=elapsed_seconds, surface="final"))
    except Exception as err:  # pragma: no cover - defensive
        import logging
        logging.getLogger(__name__).debug("long-turn mention resolution failed: %s", err)
        return response


def clarify_metadata(metadata: Optional[Dict[str, Any]], source: Any, user_config: Any) -> Optional[Dict[str, Any]]:
    """Best-effort: ``metadata`` plus ``mention_text`` for a blocking clarify prompt."""
    if source is None or not isinstance(user_config, dict):
        return metadata
    try:
        from gateway.run import _platform_config_key
        return _metadata_with_long_turn_mention(metadata, _long_turn_mention_text_for_source(
            source, user_config, _platform_config_key(source.platform), surface="clarify"))
    except Exception as err:  # pragma: no cover - defensive
        import logging
        logging.getLogger(__name__).debug("clarify mention resolution failed: %s", err)
        return metadata


def _nonnegative_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number < 0 else number


def _truthy_config_value(value: Any, *, default: bool = False) -> bool:
    """Coerce common config boolean spellings."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _long_turn_mention_policy(user_config: dict, platform_key: str) -> dict:
    """Resolve the opt-in Discord mention policy.

    Final-response mentions are time-only: configure one elapsed threshold via
    ``elapsed_seconds`` / ``ping_after_seconds`` / ``min_elapsed_seconds`` or via
    the first rule with one of those keys. Clarification prompts ignore elapsed
    time: a blocking question mentions the requesting user immediately.

    Approval prompts are deliberately NOT handled here. Upstream owns that path
    via ``discord.approval_mentions`` (``_approval_mention_content``), which
    already prefixes the prompt and sets ``allowed_mentions``. The fork carried
    an ``on_approval`` key whose branch NO call site ever reached — plumbed but
    dead — so it is dropped rather than revived into a second, competing
    mention system.
    """
    if not isinstance(user_config, dict):
        return {"enabled": False, "elapsed_seconds": None}
    display_cfg = user_config.get("display") or {}
    if not isinstance(display_cfg, dict):
        return {"enabled": False, "elapsed_seconds": None}

    policy: dict = {}
    global_cfg = display_cfg.get("long_turn_mention")
    if isinstance(global_cfg, bool):
        policy["enabled"] = global_cfg
    elif isinstance(global_cfg, dict):
        policy.update(global_cfg)

    platforms_cfg = display_cfg.get("platforms") or {}
    platform_cfg = platforms_cfg.get(platform_key) if isinstance(platforms_cfg, dict) else None
    platform_policy = platform_cfg.get("long_turn_mention") if isinstance(platform_cfg, dict) else None
    if isinstance(platform_policy, bool):
        policy["enabled"] = platform_policy
    elif isinstance(platform_policy, dict):
        policy.update(platform_policy)

    elapsed = _nonnegative_float(
        policy.get("elapsed_seconds", policy.get("ping_after_seconds", policy.get("min_elapsed_seconds")))
    )
    rules_raw = policy.get("rules")
    if elapsed is None:
        if isinstance(rules_raw, dict):
            rules_iter = [rules_raw]
        elif isinstance(rules_raw, list):
            rules_iter = rules_raw
        else:
            rules_iter = []
        for raw_rule in rules_iter:
            if not isinstance(raw_rule, dict):
                continue
            elapsed = _nonnegative_float(
                raw_rule.get("elapsed_seconds", raw_rule.get("ping_after_seconds", raw_rule.get("min_elapsed_seconds")))
            )
            if elapsed is not None:
                break

    return {
        **policy,
        "enabled": _truthy_config_value(policy.get("enabled"), default=False),
        "on_final": _truthy_config_value(policy.get("on_final"), default=True),
        "on_clarify": _truthy_config_value(policy.get("on_clarify"), default=True),
        "elapsed_seconds": elapsed,
    }


def _discord_user_mention_from_policy(source: Any, policy: dict) -> str:
    """Resolve the mention string, refusing @everyone/@here by construction."""
    configured = str(policy.get("mention") or "").strip()
    if configured:
        if configured in {"@everyone", "@here"}:
            return ""
        if re.fullmatch(r"<@!?\d+>", configured):
            return configured
    user_id = str(policy.get("mention_user_id") or getattr(source, "user_id", "") or "").strip()
    if re.fullmatch(r"\d{5,25}", user_id):
        return f"<@{user_id}>"
    return ""


def _long_turn_mention_text_for_source(
    source: Any, user_config: dict, platform_key: str, *,
    elapsed_seconds: float = 0.0, surface: str,
) -> str:
    """Return a Discord mention for long finals or blocking clarify prompts."""
    platform = getattr(source, "platform", platform_key)
    platform_value = str(getattr(platform, "value", platform) or "").strip().lower() or platform_key
    if platform_value != "discord":
        return ""
    policy = _long_turn_mention_policy(user_config, platform_key)
    if not policy.get("enabled"):
        return ""
    if surface == "clarify":
        return _discord_user_mention_from_policy(source, policy) if policy.get("on_clarify", True) else ""
    if surface == "final":
        if not policy.get("on_final", True):
            return ""
        threshold = policy.get("elapsed_seconds")
        if threshold is None or max(0.0, float(elapsed_seconds or 0.0)) < float(threshold):
            return ""
        return _discord_user_mention_from_policy(source, policy)
    return ""


def _metadata_with_long_turn_mention(
    metadata: Optional[Dict[str, Any]], mention_text: str,
) -> Optional[Dict[str, Any]]:
    if not mention_text:
        return metadata
    merged = dict(metadata or {})
    merged["mention_text"] = mention_text
    return merged


def _apply_long_turn_mention_to_response(response: str, mention_text: str) -> str:
    """Prefix a final response with a mention exactly once."""
    if not response or not mention_text:
        return response
    text = str(response)
    mention = str(mention_text).strip()
    if not mention or text.lstrip().startswith(mention):
        return text
    return f"{mention} {text}"
