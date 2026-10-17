"""The a-Shell webview that ``jsc`` evaluates against, shared by both providers.

``ashell_jsc`` and ``ashell_pot`` run in the same WKWebView page, so they share its failure
modes. iOS can kill that page's web content process -- a multi-megabyte solver script and a
BotGuard VM make it a large one -- and a-Shell has no handler that brings it back, so every
later ``jsc`` call fails. ``jsc --reset`` reloads it, but asynchronously: the command returns
before the page has started loading, let alone finished.

So recovery is a reset followed by probing until the page answers again. When it never does,
the page is recorded as dead and both providers stop claiming requests for the rest of the
run, leaving yt-dlp to the clients that need neither a JS runtime nor a PO token. Otherwise
yt-dlp asks again for every format, and each request waits out its own timeout.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import time

# a-Shell can only read and write ~/Documents, ~/Library and ~/tmp.
_SCRIPT_DIR = os.path.expanduser('~/Documents')

_RESET_TIMEOUT = 60
_READY_TIMEOUT = 30
_PROBE_TIMEOUT = 10
_PROBE_INTERVAL = 1

_READY_SENTINEL = 'ashell_webview_ready'
# Set on the old page before a reset. A probe can be evaluated before the reload has replaced
# that page, and must not mistake it for the fresh one.
_STALE_FLAG = '__ashell_webview_stale'

_dead = False


def script_dir() -> str | None:
    """Return the directory to write temporary files into, or None for the system default."""
    return _SCRIPT_DIR if os.path.isdir(_SCRIPT_DIR) else None


def run(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Run ``jsc`` with ``args``, capturing its output.

    Deliberately plain ``subprocess.run`` rather than ``yt_dlp.utils.Popen``: that wrapper
    passes an explicit ``env=os.environ.copy()``, and ``jsc`` is an ios_system builtin resolved
    through a-Shell's own command dictionary rather than a file on PATH.
    """
    return subprocess.run(
        ['jsc', *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def run_script(program: str, timeout: int) -> subprocess.CompletedProcess[str]:
    """Write ``program`` to a temporary file and run it with ``jsc``."""
    script = tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8', dir=script_dir())
    try:
        script.write(program)
        script.close()
        return run([script.name], timeout)
    finally:
        pathlib.Path(script.name).unlink(missing_ok=True)


def is_dead() -> bool:
    """Whether a reset has already failed to bring the page back during this run."""
    return _dead


def _page_answers() -> bool:
    """Whether a freshly loaded page evaluates scripts, by any output route it offers."""
    marker = tempfile.NamedTemporaryFile(mode='w', suffix='.ready', delete=False, dir=script_dir())
    marker.close()
    marker_path = pathlib.Path(marker.name)
    program = (
        '(function () {\n'
        f"  if (globalThis.{_STALE_FLAG}) return 'stale';\n"
        f'  try {{ jsc.writeFile({json.dumps(marker.name)}, {json.dumps(_READY_SENTINEL)}); }} catch (e) {{}}\n'
        f'  return {json.dumps(_READY_SENTINEL)};\n'
        '})();\n'
    )
    try:
        completed = run_script(program, _PROBE_TIMEOUT)
        written = marker_path.read_text(encoding='utf-8', errors='replace')
    except Exception:  # noqa: BLE001 - a page that cannot be probed is a page that is not ready
        return False
    finally:
        marker_path.unlink(missing_ok=True)
    return _READY_SENTINEL in written or _READY_SENTINEL in (completed.stdout or '')


def reset() -> bool:
    """Reload the page and wait until it evaluates scripts again.

    This discards everything parked on the page, including a minted BotGuard session, so it
    is only worth doing once a call has already failed.

    Returns:
        Whether the page came back. When it did not, the page is recorded as dead.
    """
    global _dead
    try:
        run_script(f'globalThis.{_STALE_FLAG} = true; "ok";', _PROBE_TIMEOUT)
    except Exception:  # noqa: BLE001 - a page too broken to flag is about to be reloaded anyway
        pass
    try:
        run(['--reset'], _RESET_TIMEOUT)
    except Exception:  # noqa: BLE001 - the probes below decide whether the reset worked
        pass

    deadline = time.monotonic() + _READY_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(_PROBE_INTERVAL)
        if _page_answers():
            return True
    _dead = True
    return False
