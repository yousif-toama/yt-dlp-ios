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

import json
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
except ImportError as exc:  # pragma: no cover - depends on the installed yt-dlp
    _IMPORT_ERROR: ImportError | None = exc
else:
    _IMPORT_ERROR = None

# a-Shell exposes two different JavaScript engines under the name `jsc`, and they report
# results differently:
#
# - In the app, `jsc` runs in a hidden WKWebView. It is JIT compiled and console.log() goes to
#   stdout.
# - Inside a Shortcut extension, `jsc` degrades to `jsc_core`, a minimal unoptimised context.
#   console.log() output is discarded; stdout gets the script's *completion value* instead. A
#   script ending in console.log(...) completes with `undefined`, which it rejects outright
#   with "JavaScript execution returned a result of an unsupported type".
#
# The probe runs both channels at once and records which one produced output.
_CONSOLE_SENTINEL = 'ashell_jsc_console_ok'
_COMPLETION_SENTINEL = 'ashell_jsc_completion_ok'
_PROBE_SCRIPT = f'try {{ console.log("{_CONSOLE_SENTINEL}"); }} catch (e) {{}}\n"{_COMPLETION_SENTINEL}";\n'

_OUTPUT_CONSOLE = 'console'
_OUTPUT_COMPLETION = 'completion'

# yt-dlp's solver program has no "use strict" prologue (checked against yt-dlp-ejs), so
# prepending cannot silently drop the whole program out of strict mode.
#
# The solver's core bundle declares `var jsc = ...`, which collides with the `jsc` file API
# object a-Shell injects as a global. A top-level `var` cannot overwrite a non-writable global
# property, and in sloppy mode that failure is silent -- the name would still refer to
# a-Shell's object and calling it would fail. Removing the global first sidesteps that.
_COMMON_PREAMBLE = 'try { delete globalThis.jsc; } catch (e) {}\n'

# Completion-value channel only: collect what the solver logs, then end the script with the
# collected text so it becomes the completion value.
_COMPLETION_PREAMBLE = (
    'var __ashellOut = [];\n'
    'var console = {\n'
    "  log: (...a) => { __ashellOut.push(a.join(' ')); },\n"
    '  info: () => {}, warn: () => {}, error: () => {}, debug: () => {},\n'
    '};\n'
)
_COMPLETION_EPILOGUE = "\n;__ashellOut.join('\\n');\n"

# a-Shell can only read and write ~/Documents, ~/Library and ~/tmp. The default temp directory
# is inside the app container and readable, but keeping scripts next to everything else this
# project writes avoids depending on that.
_SCRIPT_DIR = os.path.expanduser('~/Documents')

_PROBE_TIMEOUT = 60
_SOLVE_TIMEOUT = 600

_runtime_probe: bool | None = None
_probe_detail: str | None = None
_output_channel: str | None = None


def _script_dir() -> str | None:
    """Return the directory to write temporary scripts into, or None for the system default."""
    return _SCRIPT_DIR if os.path.isdir(_SCRIPT_DIR) else None


def _run_jsc(program: str, timeout: int = _SOLVE_TIMEOUT) -> tuple[str, str, int]:
    """Run ``program`` under a-Shell's jsc, returning (stdout, stderr, returncode).

    Deliberately plain ``subprocess.run`` rather than ``yt_dlp.utils.Popen``. That wrapper
    passes an explicit ``env=os.environ.copy()``, and ``jsc`` is an ios_system builtin
    resolved through a-Shell's own command dictionary rather than a file on PATH, so handing
    it a rebuilt environment stops it being found. This is the exact call shape verified to
    work on-device.
    """
    script = tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8', dir=_script_dir())
    try:
        script.write(program)
        script.close()
        completed = subprocess.run(
            ['jsc', script.name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    finally:
        pathlib.Path(script.name).unlink(missing_ok=True)
    return completed.stdout or '', completed.stderr or '', completed.returncode


def _probe_runtime() -> bool:
    """Check that a usable ``jsc`` exists, caching the result.

    main.py is cross platform, so this must be false everywhere except a-Shell -- otherwise
    the provider would claim every YouTube challenge on a desktop and then fail it, shutting
    out a perfectly good Deno. ``shutil.which`` is no help: ``jsc`` is an ios_system builtin
    rather than a file on PATH, so the only reliable test is to run it.
    """
    global _runtime_probe, _probe_detail, _output_channel
    if _IMPORT_ERROR is not None:
        return False
    if _runtime_probe is not None:
        return _runtime_probe

    try:
        stdout, stderr, returncode = _run_jsc(_PROBE_SCRIPT, timeout=_PROBE_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - any failure here just means no usable jsc
        _runtime_probe = False
        _probe_detail = f'{type(exc).__name__}: {exc}'
        return _runtime_probe

    if _CONSOLE_SENTINEL in stdout:
        _output_channel = _OUTPUT_CONSOLE
    elif _COMPLETION_SENTINEL in stdout:
        _output_channel = _OUTPUT_COMPLETION

    _runtime_probe = _output_channel is not None
    if not _runtime_probe:
        _probe_detail = f'exit {returncode}, stdout {stdout.strip()!r}, stderr {stderr.strip()!r}'
    return _runtime_probe


def _wrap_program(program: str) -> str:
    """Adapt the solver program to whichever output channel this jsc reports results on."""
    if _output_channel == _OUTPUT_COMPLETION:
        return _COMMON_PREAMBLE + _COMPLETION_PREAMBLE + program + _COMPLETION_EPILOGUE
    return _COMMON_PREAMBLE + program


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
            self.logger.debug(f'Running a-Shell jsc ({_output_channel} output) on a {len(stdin)} byte script')
            stdout, stderr, returncode = _run_jsc(_wrap_program(stdin))

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

            On the completion-value channel the result may also arrive quoted, since jsc is
            rendering a JavaScript string rather than echoing what was printed.
            """
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith('{'):
                    return line
                if line.startswith('"'):
                    try:
                        unquoted = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(unquoted, str) and unquoted.lstrip().startswith('{'):
                        return unquoted.strip()

            message = 'a-Shell jsc produced no JSON output'
            if stderr:
                message = f'{message}: {stderr.strip()}'
            raise JsChallengeProviderError(message)

    @register_preference(AShellJscJCP)
    def _ashell_jsc_preference(provider, requests) -> int:
        # Below yt-dlp's own providers (Deno 1000 down to QuickJS 850), so that an officially
        # supported runtime wins if one is ever available alongside this one.
        return 500


# Probing a-Shell's jsc by reading its source only gets so far: `jsc` hands the file to
# WKWebView.evaluateJavaScript, whose result must be a type WebKit can serialise, and the
# observable behaviour differs between a terminal session and a Shortcut run. When the probe
# fails, run a spread of one-liners so the failure reports which channel works rather than
# just that none did.
_DIAGNOSTIC_CASES = (
    ('bare string', '"diag_value";\n'),
    ('bare number', '42;\n'),
    ('console only', 'console.log("diag_value");\n'),
    ('console then string', 'console.log("diag_console"); "diag_value";\n'),
    ('typeof console', 'typeof console;\n'),
    ('typeof globalThis.jsc', 'typeof globalThis.jsc;\n'),
    ('typeof process', 'typeof process;\n'),
    ('call returning string', 'JSON.stringify({a: 1});\n'),
    ('var then string', 'var diag = 1; "diag_value";\n'),
    ('current probe', _PROBE_SCRIPT),
)


def diagnostic_report() -> list[str]:
    """Run each candidate output channel and describe what jsc did with it."""
    report = []
    for label, script in _DIAGNOSTIC_CASES:
        try:
            stdout, stderr, returncode = _run_jsc(script, timeout=_PROBE_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - the report is the point, not the failure
            report.append(f'  {label}: {type(exc).__name__}: {exc}')
        else:
            report.append(f'  {label}: rc={returncode} out={stdout.strip()!r} err={stderr.strip()!r}')
    return report


def is_available() -> bool:
    """Whether the provider is registered and a-Shell's jsc can actually run."""
    return _IMPORT_ERROR is None and _probe_runtime()


def unavailable_reason() -> str | None:
    """A short explanation of why the provider is unusable, or None if it is usable."""
    if _IMPORT_ERROR is not None:
        return f'this yt-dlp version has no compatible JS challenge provider API ({_IMPORT_ERROR})'
    if not _probe_runtime():
        detail = f' ({_probe_detail})' if _probe_detail else ''
        return f"a-Shell's 'jsc' command did not run{detail}"
    return None
