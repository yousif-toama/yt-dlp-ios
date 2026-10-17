"""The a-Shell webview that ``jsc`` evaluates against, shared by both providers.

``ashell_jsc`` and ``ashell_pot`` run in the same WKWebView page, ``wasm.html``, so they share
its failure modes. The page can navigate away -- yt-dlp's solver used to send it to YouTube
-- after which scripts still run but the ``jsc`` file API is gone. iOS can also kill the
page's web content process, and a-Shell has no handler that brings it back. ``jsc --reset``
reloads the page, but asynchronously, and it reloads whatever URL the webview is on, so a
page that has navigated away has to be sent back to ``wasm.html`` explicitly.

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
import sys
import tempfile
import time

# a-Shell can only read and write ~/Documents, ~/Library and ~/tmp.
_SCRIPT_DIR = os.path.expanduser('~/Documents')

# Where a-Shell loads its page from (SceneDelegate.swift, sceneWillEnterForeground). a-Shell
# mini serves it on port 8334, but has no Python to run this.
_PAGE_URL = 'https://localhost:8443/wasm.html'

_RESET_TIMEOUT = 60
_READY_TIMEOUT = 30
_PROBE_TIMEOUT = 10
_PROBE_INTERVAL = 1

_READY_SENTINEL = 'ashell_webview_ready'
_STALE_SENTINEL = 'ashell_webview_stale'
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


def _page_state() -> str | None:
    """Probe the page, by any output route it offers.

    Returns:
        ``'fresh'`` when a newly loaded page evaluates scripts, ``'stale'`` when the page from
        before the reset still does, or None when no page answers.
    """
    marker = tempfile.NamedTemporaryFile(mode='w', suffix='.ready', delete=False, dir=script_dir())
    marker.close()
    marker_path = pathlib.Path(marker.name)
    program = (
        '(function () {\n'
        f'  if (globalThis.{_STALE_FLAG}) return {json.dumps(_STALE_SENTINEL)};\n'
        f'  try {{ jsc.writeFile({json.dumps(marker.name)}, {json.dumps(_READY_SENTINEL)}); }} catch (e) {{}}\n'
        f'  return {json.dumps(_READY_SENTINEL)};\n'
        '})();\n'
    )
    try:
        completed = run_script(program, _PROBE_TIMEOUT)
        written = marker_path.read_text(encoding='utf-8', errors='replace')
    except Exception:  # noqa: BLE001 - a page that cannot be probed is a page that does not answer
        return None
    finally:
        marker_path.unlink(missing_ok=True)
    stdout = completed.stdout or ''
    if _READY_SENTINEL in written or _READY_SENTINEL in stdout:
        return 'fresh'
    if _STALE_SENTINEL in stdout:
        return 'stale'
    return None


def reset() -> bool:
    """Reload the page and wait until it evaluates scripts again.

    This discards everything parked on the page, including a minted BotGuard session, so it
    is only worth doing once a call has already failed.

    Returns:
        Whether a freshly loaded page came back. When no page answers at all, the page is
        also recorded as dead. A page that still answers but was not replaced is not: it
        can still serve whatever does not need the reload.
    """
    global _dead
    _notice('jsc failed; reloading its webview')
    if not _send_page_home():
        try:
            run(['--reset'], _RESET_TIMEOUT)
        except Exception:  # noqa: BLE001 - the probes below decide whether the reset worked
            pass

    started = time.monotonic()
    state = None
    while time.monotonic() - started < _READY_TIMEOUT:
        time.sleep(_PROBE_INTERVAL)
        state = _page_state()
        if state == 'fresh':
            _notice(f'webview answered again after {time.monotonic() - started:.1f}s')
            return True
    if state == 'stale':
        _notice(f'webview still answers but did not reload within {_READY_TIMEOUT}s')
    else:
        _dead = True
        _notice(f'webview did not answer within {_READY_TIMEOUT}s; jsc is off for the rest of this run')
    return False


def _send_page_home() -> bool:
    """Flag the current page as stale, and send it back to ``wasm.html`` if it has left.

    Returns:
        Whether a navigation back was started. When it was not -- the page is ``wasm.html``,
        or did not answer at all -- ``jsc --reset`` is the way to reload it.
    """
    page_url = json.dumps(_PAGE_URL)
    program = (
        f'globalThis.{_STALE_FLAG} = true;\n'
        f"location.origin + location.pathname === {page_url} ? 'home' : (location.replace({page_url}), 'away');\n"
    )
    try:
        completed = run_script(program, _PROBE_TIMEOUT)
    except Exception:  # noqa: BLE001 - a page too broken to answer is left to `jsc --reset`
        return False
    return 'away' in (completed.stdout or '')


def _notice(message: str) -> None:
    """Report a webview reset on stderr, where yt-dlp's own warnings go."""
    print(f'[a-Shell] {message}', file=sys.stderr, flush=True)
