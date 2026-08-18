"""Register a-Shell's built-in ``jsc`` JavaScript runtime with yt-dlp.

Since yt-dlp 2025.11.12, full YouTube support needs an external JavaScript runtime to solve
the n-sig/sig challenges. None of the runtimes yt-dlp supports natively work on iOS:

- Deno, Node and Bun have no iOS builds at all.
- ``qjs`` installs via ``pkg install qjs``, but it is a WebAssembly command. a-Shell executes
  wasm by handing it to the WKWebView JS engine, so when yt-dlp spawns it as a subprocess of
  Python -- which is already occupying that thread -- the call deadlocks and never returns.

a-Shell does ship Apple's JavaScriptCore as the ``jsc`` command, which is native, JIT
compiled, and works fine when spawned from Python. This module teaches yt-dlp to use it, via
the JS Challenge Provider plugin API documented in
``yt_dlp/extractor/youtube/jsc/README.md``.

Import this module before constructing ``yt_dlp.YoutubeDL``; registration is global.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile

# ``EJSBaseJCP`` is internal yt-dlp API -- only ``...jsc.provider`` is public -- and yt-dl.sh
# upgrades yt-dlp on every run, so a future release may move or rename it. Failing soft here
# costs the high quality formats but keeps the download working.
try:
    from yt_dlp.extractor.youtube.jsc._builtin.ejs import EJSBaseJCP
    from yt_dlp.extractor.youtube.jsc.provider import (
        JsChallengeProviderError,
        register_preference,
        register_provider,
    )
    from yt_dlp.utils import Popen
except ImportError as exc:  # pragma: no cover - depends on the installed yt-dlp
    _IMPORT_ERROR: ImportError | None = exc
else:
    _IMPORT_ERROR = None

_PROBE_SENTINEL = 'ashell_jsc_ok'
_PROBE_SCRIPT = f'console.log("{_PROBE_SENTINEL}");'

# a-Shell can only read and write ~/Documents, ~/Library and ~/tmp. The default temp directory
# is inside the app container and readable, but keeping scripts next to everything else this
# project writes avoids depending on that.
_SCRIPT_DIR = os.path.expanduser('~/Documents')

_runtime_probe: bool | None = None


def _script_dir() -> str | None:
    """Return the directory to write temporary scripts into, or None for the system default."""
    return _SCRIPT_DIR if os.path.isdir(_SCRIPT_DIR) else None


def _run_jsc(program: str) -> tuple[str, str, int]:
    """Run ``program`` under a-Shell's jsc, returning (stdout, stderr, returncode)."""
    script = tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8', dir=_script_dir())
    try:
        script.write(program)
        script.close()
        return Popen.run(
            ['jsc', script.name],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    finally:
        pathlib.Path(script.name).unlink(missing_ok=True)


def _probe_runtime() -> bool:
    """Check that a usable ``jsc`` exists, caching the result.

    main.py is cross platform, so this must be false everywhere except a-Shell -- otherwise
    the provider would claim every YouTube challenge on a desktop and then fail it, shutting
    out a perfectly good Deno. ``shutil.which`` is no help: ``jsc`` is an ios_system builtin
    rather than a file on PATH, so the only reliable test is to run it.
    """
    global _runtime_probe
    if _IMPORT_ERROR is not None:
        return False
    if _runtime_probe is None:
        try:
            stdout, _, returncode = _run_jsc(_PROBE_SCRIPT)
        except (OSError, ValueError):
            _runtime_probe = False
        else:
            _runtime_probe = returncode == 0 and _PROBE_SENTINEL in stdout
    return _runtime_probe


if _IMPORT_ERROR is None:

    @register_provider
    class AShellJscJCP(EJSBaseJCP):
        PROVIDER_VERSION = '1.0.0'
        PROVIDER_NAME = 'ashell-jsc'
        JS_RUNTIME_NAME = 'jsc'
        BUG_REPORT_LOCATION = 'https://github.com/yousif-toama/yt-dlp-ios/issues'

        def is_available(self, /) -> bool:
            # Deliberately does not call super(): the base implementation resolves
            # self.runtime_info against the runtimes yt-dlp knows how to probe, and jsc is not
            # one of them. self._available is set to False by the base class when it cannot
            # find a usable solver script.
            return self._available and _probe_runtime()

        def _run_js_runtime(self, stdin: str, /) -> str:
            self.logger.debug(f'Running a-Shell jsc on a {len(stdin)} byte script')
            stdout, stderr, returncode = _run_jsc(stdin)

            # yt-dlp's own QuickJS provider also treats any stderr output as a failure. jsc is
            # noisier than that, so only the exit status is fatal here.
            if returncode:
                message = f'Error running a-Shell jsc (returncode: {returncode})'
                if stderr:
                    message = f'{message}: {stderr.strip()}'
                raise JsChallengeProviderError(message)

            return self._extract_json(stdout, stderr)

        @staticmethod
        def _extract_json(stdout: str, stderr: str, /) -> str:
            """Return the solver's JSON result line from jsc's output.

            The caller json.loads() this directly, so anything the environment prints
            alongside the result -- a banner, a stray console.log -- would break parsing.
            JSON.stringify never emits a raw newline, so the result is always one line.
            """
            for line in reversed(stdout.splitlines()):
                if line.startswith('{'):
                    return line

            message = 'a-Shell jsc produced no JSON output'
            if stderr:
                message = f'{message}: {stderr.strip()}'
            raise JsChallengeProviderError(message)

    @register_preference(AShellJscJCP)
    def _ashell_jsc_preference(provider, requests) -> int:
        # Below yt-dlp's own providers (Deno 1000 down to QuickJS 850), so that an officially
        # supported runtime wins if one is ever available alongside this one.
        return 500


def is_available() -> bool:
    """Whether the provider is registered and a-Shell's jsc can actually run."""
    return _IMPORT_ERROR is None and _probe_runtime()


def unavailable_reason() -> str | None:
    """A short explanation of why the provider is unusable, or None if it is usable."""
    if _IMPORT_ERROR is not None:
        return f'this yt-dlp version has no compatible JS challenge provider API ({_IMPORT_ERROR})'
    if not _probe_runtime():
        return "a-Shell's 'jsc' command is not available"
    return None
