"""Fork-preservation gate: Discord REST liveness probe (area 20 residue).

Upstream v0.22 ships its own out-of-loop gateway loop watchdog
(``gateway.loop_watchdog`` -> ``gateway/shutdown_watchdog.start_loop_liveness_watchdog``),
which supersedes the fork's former event-loop watchdog. What remains fork-only is
the Discord REST probe: upstream samples the WebSocket, but a revoked token,
permission loss, or REST outage leaves the socket receiving events while every
outbound action fails.
"""


import plugins.platforms.discord.adapter as discord_adapter


def test_rest_probe_has_its_own_task_and_config():
    """Shared state would let one probe mask the other's failure."""
    adapter_cls = discord_adapter.DiscordAdapter
    assert callable(getattr(adapter_cls, "_start_rest_liveness_probe", None))
    assert callable(getattr(adapter_cls, "_cancel_rest_liveness_task", None))


def test_rest_probe_defaults_are_slower_than_the_websocket_probe():
    """A REST blip is common; only sustained failure should mean anything."""
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    discord_cfg = DEFAULT_CONFIG["discord"]
    assert discord_cfg["rest_liveness_interval_seconds"] > discord_cfg["websocket_liveness_interval_seconds"]
    assert discord_cfg["rest_liveness_failure_threshold"] > discord_cfg["websocket_liveness_failure_threshold"]


def test_upstream_loop_watchdog_is_the_wedge_detector():
    """The fork's event-loop watchdog was dropped because upstream owns this now.

    Guard against silently losing BOTH: if upstream removes its loop watchdog,
    this fails and the fork watchdog must be reconsidered.
    """
    from gateway.config import GatewayConfig
    from gateway.shutdown_watchdog import start_loop_liveness_watchdog

    assert callable(start_loop_liveness_watchdog)
    assert GatewayConfig().loop_watchdog is True
