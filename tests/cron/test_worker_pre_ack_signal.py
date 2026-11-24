"""A cron worker killed by a signal before it adopts its claim is relaunched once, and
the startup window records who sent the signal."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


class _Process:
    pid = 999

    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout)
        return self.returncode


def _install(scheduler, monkeypatch, tmp_path, outcomes, handoff_results):
    """Each spawn consumes one outcome: an int is a pre-ack exit code, "ack" acknowledges."""
    from tools.process_registry import GatewayChildDispatch

    suffixes, spawned, handoffs = [], [], []
    monkeypatch.setattr(scheduler, "_get_hermes_home", lambda: tmp_path)

    def wrap(command, *, unit_suffix, require_restart_safe_scope=False):
        suffixes.append(unit_suffix)
        return GatewayChildDispatch("scoped", ["scope", "--", *command])

    monkeypatch.setattr("tools.process_registry.restart_safe_gateway_child_argv", wrap)

    def handoff(execution_id):
        handoffs.append(execution_id)
        result = handoff_results.pop(0)
        return {"id": execution_id, "handoff_pending": 1} if result else None

    monkeypatch.setattr(scheduler, "mark_execution_handoff_pending", handoff)
    outcomes = list(outcomes)

    def popen(command, **_kwargs):
        payload = Path(command[command.index("--external-worker-file") + 1])
        spawned.append(json.loads(payload.read_text()))
        outcome = outcomes.pop(0)
        if outcome == "ack":
            Path(command[command.index("--ack-file") + 1]).write_text(
                json.dumps({"pid": 4321, "execution_id": "exec-1"}), encoding="utf-8")
            return _Process()
        payload.unlink()  # a real worker consumes its payload as soon as it starts
        return _Process(outcome)

    monkeypatch.setattr(scheduler.subprocess, "Popen", popen)
    monkeypatch.setattr(scheduler, "get_execution",
                        lambda _eid: {"id": "exec-1", "status": "completed"})
    return suffixes, spawned, handoffs


JOB = {"id": "job-1", "execution_id": "exec-1", "prompt": "work"}


def test_signal_kill_before_adoption_relaunches_once(tmp_path, monkeypatch):
    from cron import scheduler

    suffixes, spawned, handoffs = _install(
        scheduler, monkeypatch, tmp_path, [-signal.SIGTERM, "ack"], [True, True])

    assert scheduler._launch_external_cron_worker(dict(JOB)) is True
    assert len(spawned) == 2 and spawned[1]["job"]["id"] == "job-1"
    # The relaunch re-fences the row (proving nobody adopted it) under a fresh unit name.
    assert handoffs == ["exec-1", "exec-1"]
    assert suffixes == ["cron-job-1-exec-exec-1", "cron-job-1-exec-exec-1-retry"]


def test_second_signal_kill_reports_a_plain_failure(tmp_path, monkeypatch):
    from cron import scheduler

    _suffixes, spawned, _handoffs = _install(
        scheduler, monkeypatch, tmp_path, [-signal.SIGTERM, -signal.SIGTERM], [True, True])

    with pytest.raises(RuntimeError) as excinfo:
        scheduler._launch_external_cron_worker(dict(JOB))
    message = str(excinfo.value)
    assert "did not run" in message and "SIGTERM" in message and "2 attempts" in message
    assert len(spawned) == 2
    assert not list((tmp_path / "cron" / "external-workers").glob("exec-1.*"))


def test_no_relaunch_once_another_process_adopted_the_row(tmp_path, monkeypatch):
    """If the row is no longer claimed by the dispatcher, the dead worker may have adopted it:
    relaunching could repeat side effects, so the fire fails without a second spawn."""
    from cron import scheduler

    _suffixes, spawned, _handoffs = _install(
        scheduler, monkeypatch, tmp_path, [-signal.SIGTERM], [True, False])

    with pytest.raises(RuntimeError, match="1 attempt\\b"):
        scheduler._launch_external_cron_worker(dict(JOB))
    assert len(spawned) == 1


def test_ordinary_nonzero_exit_is_not_retried(tmp_path, monkeypatch):
    from cron import scheduler

    _suffixes, spawned, _handoffs = _install(scheduler, monkeypatch, tmp_path, [1], [True])

    with pytest.raises(RuntimeError, match="exited before ownership acknowledgement"):
        scheduler._launch_external_cron_worker(dict(JOB))
    assert len(spawned) == 1


_VICTIM = textwrap.dedent("""
    import subprocess, sys, time
    from cron.worker_signal_trace import arm, disarm
    arm()
    if sys.argv[1] == "disarm":
        disarm()
        out = subprocess.run(["grep", "SigBlk", "/proc/self/status"],
                             capture_output=True, text=True).stdout
        print("CHILD", out.split()[1], flush=True)
    print("ready", flush=True)
    time.sleep(30)
""")


def _victim(tmp_path, mode):
    script = tmp_path / "victim.py"
    script.write_text(_VICTIM, encoding="utf-8")
    env = dict(os.environ, HERMES_HOME=str(tmp_path),
               PYTHONPATH=os.pathsep.join([str(REPO_ROOT), *sys.path]))
    env.pop("_HERMES_CRON_WORKER_BOOT", None)
    return subprocess.Popen([sys.executable, str(script), mode], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="sigtimedwait trace is Linux-only")
def test_startup_sigterm_records_its_sender_and_still_kills(tmp_path):
    proc = _victim(tmp_path, "armed")
    try:
        assert proc.stdout.readline().strip() == "ready"
        sender = subprocess.Popen(
            [sys.executable, "-c", "import os,sys,time,signal; os.kill(int(sys.argv[1]), signal.SIGTERM); time.sleep(2)",
             str(proc.pid)])
        _out, err = proc.communicate(timeout=15)
        sender.wait(timeout=10)
    finally:
        proc.kill()
    assert proc.returncode == -signal.SIGTERM
    log = (tmp_path / "logs" / "cron-worker-signals.log").read_text(encoding="utf-8")
    assert f"pid={sender.pid}" in log and "os.kill" in log
    assert "received SIGTERM before adopting its job" in err


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="sigtimedwait trace is Linux-only")
def test_disarm_restores_sigterm_for_job_children(tmp_path):
    proc = _victim(tmp_path, "disarm")
    try:
        child_mask = proc.stdout.readline().split()[1]
        assert proc.stdout.readline().strip() == "ready"
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=15)
    finally:
        proc.kill()
    assert int(child_mask, 16) & (1 << (signal.SIGTERM - 1)) == 0
    assert proc.returncode == -signal.SIGTERM
    assert not (tmp_path / "logs" / "cron-worker-signals.log").exists()
