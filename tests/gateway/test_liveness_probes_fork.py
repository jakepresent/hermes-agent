"""Fork gate: liveness probes upstream does not have (area 20).

Two independent failure detectors, both absent from v2026.9.7:

1. Out-of-loop event-loop watchdog. Upstream's liveness checks are coroutines,
   so a WEDGED event loop (a sync call in async context, a C-extension deadlock)
   stops the detector along with everything else. This watchdog runs on a real OS
   thread and hard-exits with the service-restart code.

2. Discord REST liveness probe. Upstream samples only the WEBSOCKET, which stays
   healthy while the REST API is degraded or the token is revoked — the gateway
   sits connected but unable to act.
"""
import inspect
import subprocess
import sys
import tempfile
import textwrap

import gateway.run_shutdown as run_shutdown
import gateway.run_startup as run_startup
import plugins.platforms.discord.adapter as discord_adapter
from gateway.restart import GATEWAY_SERVICE_RESTART_EXIT_CODE
from gateway.run import GatewayRunner

REPO = "/tmp/hermes-clean-20260909-075848"


def _run_watchdog(*, stale_seconds: float, draining: bool, threshold: float):
    """Drive the watchdog thread in a subprocess (it calls os._exit)."""
    child = textwrap.dedent(f"""
        import sys, os, time, threading
        sys.path.insert(0, {REPO!r})
        os.environ["HERMES_HOME"] = sys.argv[1]
        from gateway.run import GatewayRunner

        class Fake(GatewayRunner):
            def __init__(self):
                self._running = True
                self._draining = {draining}
                self._event_loop_watchdog_stop_event = threading.Event()
                self._event_loop_watchdog_last_tick = time.monotonic()
                self.name = "probe"

        r = Fake()
        r._event_loop_watchdog_last_tick = time.monotonic() - {stale_seconds}
        threading.Timer(1.5, r._event_loop_watchdog_stop_event.set).start()
        r._event_loop_watchdog_thread_main(threshold_seconds={threshold}, check_seconds=0.2)
        print("SURVIVED")
    """)
    proc = subprocess.run(
        [sys.executable, "-c", child, tempfile.mkdtemp()],
        capture_output=True, text=True, timeout=60,
    )
    return proc.returncode, "SURVIVED" in proc.stdout


def test_wedged_loop_exits_with_service_restart_code():
    code, survived = _run_watchdog(stale_seconds=30.0, draining=False, threshold=1.0)
    assert code == GATEWAY_SERVICE_RESTART_EXIT_CODE
    assert not survived


def test_healthy_loop_is_left_alone():
    code, survived = _run_watchdog(stale_seconds=0.0, draining=False, threshold=5.0)
    assert code == 0
    assert survived


def test_draining_gateway_is_never_killed():
    """A slow but legitimate shutdown must not be mistaken for a wedge."""
    code, survived = _run_watchdog(stale_seconds=30.0, draining=True, threshold=1.0)
    assert code == 0
    assert survived


def test_watchdog_is_wired_into_the_gateway_lifecycle():
    """The methods existing is not enough — they must actually be called."""
    startup = inspect.getsource(run_startup)
    shutdown = inspect.getsource(run_shutdown)
    assert "_start_event_loop_watchdog()" in startup
    assert "_start_loop_heartbeat_task()" in startup, "no heartbeat -> instant false wedge"
    assert "_stop_event_loop_watchdog" in shutdown


def test_runner_exposes_the_watchdog_surface():
    for name in (
        "_start_event_loop_watchdog",
        "_stop_event_loop_watchdog",
        "_event_loop_watchdog_thread_main",
        "_event_loop_watchdog_heartbeat",
        "_start_loop_heartbeat_task",
        "_write_event_loop_watchdog_forensics",
    ):
        assert hasattr(GatewayRunner, name), name


def test_rest_probe_is_independent_of_the_websocket_probe():
    """Shared state would let one probe mask the other's failure."""
    source = inspect.getsource(discord_adapter)
    assert "def _start_rest_liveness_probe" in source
    assert "self._start_rest_liveness_probe()" in source
    assert "await self._cancel_rest_liveness_task()" in source
    # distinct task slots and distinct config keys
    assert "_rest_liveness_task" in source and "_liveness_task" in source
    assert "rest_liveness_interval_seconds" in source
    assert "websocket_liveness_interval_seconds" in source


def test_rest_probe_defaults_are_slower_than_the_websocket_probe():
    """A REST blip is common; only sustained failure should mean anything."""
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    discord_cfg = DEFAULT_CONFIG["discord"]
    assert discord_cfg["rest_liveness_interval_seconds"] > discord_cfg["websocket_liveness_interval_seconds"]
    assert discord_cfg["rest_liveness_failure_threshold"] > discord_cfg["websocket_liveness_failure_threshold"]


def test_event_loop_watchdog_threshold_tolerates_long_turns():
    """A generous threshold is the point: a long agent turn is not a wedge."""
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    watchdog = DEFAULT_CONFIG["gateway"]["event_loop_watchdog"]
    assert watchdog["enabled"] is True
    assert watchdog["threshold_seconds"] >= 300
    assert watchdog["heartbeat_seconds"] < watchdog["threshold_seconds"]
