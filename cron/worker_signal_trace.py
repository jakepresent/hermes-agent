"""Record who sends SIGTERM to a cron external worker during its startup window.

A restart-safe worker is occasionally killed by SIGTERM a second or two after it starts,
before it adopts its claim. systemd, the OOM killer and the gateway's own process sweeps
leave no trace of it, and a Python signal handler cannot see the sender. So, from package
init until the ownership acknowledgement, the worker blocks SIGTERM and a dedicated thread
collects it with ``sigtimedwait``, whose siginfo carries the sender's pid and uid. The
thread appends the sender's command line and parent chain to
``$HERMES_HOME/logs/cron-worker-signals.log`` and to stderr (which the dispatcher quotes in
its failure report), then re-sends the signal to the process so it dies exactly as it would
have.

The window ends at ``disarm()``, before the worker runs anything for the job: a blocked
signal mask is inherited across fork/exec, so leaving it in place would make job scripts
immune to SIGTERM. Only threads started during the window keep the blocked mask; the
worker starts none that spawn children before the acknowledgement.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

_LOCK = threading.Lock()
_STOP: Optional[threading.Event] = None
_THREAD: Optional[threading.Thread] = None
_PARENT_DEPTH = 4


def _supported() -> bool:
    return (
        sys.platform.startswith("linux")
        and hasattr(signal, "sigtimedwait")
        and hasattr(signal, "pthread_sigmask")
    )


def _describe_pid(pid: int) -> str:
    """``pid=N comm=... cmd=...`` for *pid*, best effort (the sender may already be gone)."""
    proc = Path("/proc") / str(pid)
    try:
        cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        cmd = cmd.strip()[:300]
    except OSError:
        return f"pid={pid} (gone)"
    try:
        comm = (proc / "comm").read_text().strip()
    except OSError:
        comm = "?"
    return f"pid={pid} comm={comm} cmd={cmd}"


def _parent_of(pid: int) -> Optional[int]:
    try:
        for line in (Path("/proc") / str(pid) / "status").read_text().splitlines():
            if line.startswith("PPid:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def describe_sender(pid: int, uid: int) -> str:
    """One line naming the sender and up to ``_PARENT_DEPTH`` of its ancestors."""
    parts = [f"sender uid={uid} {_describe_pid(pid)}"]
    current = pid
    for _ in range(_PARENT_DEPTH):
        parent = _parent_of(current)
        if not parent or parent == current:
            break
        parts.append(f"parent {_describe_pid(parent)}")
        if parent == 1:
            break
        current = parent
    return " <- ".join(parts)


def _record(line: str) -> None:
    try:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    try:
        log_path = Path(home) / "logs" / "cron-worker-signals.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def _watch(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            info = signal.sigtimedwait({signal.SIGTERM}, 0.2)
        except Exception:
            return
        if info is None:
            continue
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        _record(
            f"{stamp} cron worker pid={os.getpid()} received SIGTERM before adopting its job: "
            f"{describe_sender(info.si_pid, info.si_uid)}"
        )
        # Die the way the signal would have killed us: unblock it in this thread and
        # re-send it to the process. Whatever disposition the worker installed applies.
        try:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            os._exit(128 + signal.SIGTERM)
        return


def arm() -> bool:
    """Block SIGTERM in the calling thread and start the sender watcher. Idempotent."""
    global _STOP, _THREAD
    if not _supported():
        return False
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return True
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        except Exception:
            return False
        stop = threading.Event()
        thread = threading.Thread(
            target=_watch, args=(stop,), name="cron-worker-sigterm-trace", daemon=True)
        _STOP, _THREAD = stop, thread
        thread.start()
        return True


def disarm() -> None:
    """End the trace window: stop the watcher, then unblock SIGTERM.

    The watcher stops first so a SIGTERM already pending is delivered normally by the
    unblock below rather than lost between the two.
    """
    global _STOP, _THREAD
    if not _supported():
        return
    with _LOCK:
        stop, thread = _STOP, _THREAD
        _STOP = _THREAD = None
        if stop is not None:
            stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        try:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
        except Exception:
            pass
