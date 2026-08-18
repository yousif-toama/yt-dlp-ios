"""Register a-Shell's built-in ``jsc`` JavaScript runtime with yt-dlp.

Since yt-dlp 2025.11.12, full YouTube support needs an external JavaScript runtime to solve
the n-sig/sig challenges. None of the runtimes yt-dlp supports natively work on iOS:

- Deno, Node and Bun have no iOS builds at all.
- ``qjs`` installs via ``pkg install qjs``, but it is a WebAssembly command. a-Shell executes
  wasm by handing it to the WKWebView JS engine, so when yt-dlp spawns it as a subprocess of
  Python -- which is already occupying that thread -- the call deadlocks and never returns.

a-Shell's ``jsc`` command does work when spawned from Python. It runs the script through
``wasmWebView.evaluateJavaScript`` (see ``SceneDelegate.swift:executeJavascript``), where the
page is ``wasm.html``. Two consequences drive the design here:

- **Output does not come back on stdout.** ``evaluateJavaScript`` only forwards the script's
  completion value, and in a Shortcut run even a bare ``42;`` comes back as "a result of an
  unsupported type". ``wasm.html`` instead provides ``println()`` and a ``jsc`` file API, so
  the result is written to a file and read back from Python. That works in every context.
- **``wasm.html`` declares ``const jsc``.** yt-dlp's solver bundle declares ``var jsc`` at top
  level, which would be a redeclaration conflict against that lexical binding, so the program
  is wrapped in a function to scope it.

Import this module before constructing ``yt_dlp.YoutubeDL``; registration is global.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import time

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

# a-Shell can only read and write ~/Documents, ~/Library and ~/tmp.
_SCRIPT_DIR = os.path.expanduser('~/Documents')

_PROBE_SENTINEL = 'ashell_jsc_ok'
_PROBE_SCRIPT = f'console.log("{_PROBE_SENTINEL}");\n'

_PROBE_TIMEOUT = 60
_SOLVE_TIMEOUT = 600

# The first attempt runs as-is; later ones reload a-Shell's webview and wait for it.
_PROBE_ATTEMPTS = 3
_RESET_SETTLE_SECONDS = 3

_runtime_probe: bool | None = None
_probe_detail: str | None = None


def _wrap_program(program: str, result_path: str) -> str:
    """Wrap the solver program so its output reaches a file rather than stdout.

    The a-Shell APIs are captured as parameters before the program runs, because the solver
    bundle shadows the name ``jsc`` with its own function inside this same scope.
    """
    return (
        '(function (__api, __println, __console) {\n'
        '  var __out = [];\n'
        '  var console = {\n'
        "    log: function () { __out.push(Array.prototype.join.call(arguments, ' ')); },\n"
        '    info: function () {}, warn: function () {},\n'
        '    error: function () {}, debug: function () {},\n'
        '  };\n'
        '  try {\n'
        f'{program}\n'
        '  } finally {\n'
        "    var __text = __out.join('\\n');\n"
        f'    try {{ if (__api) __api.writeFile({json.dumps(result_path)}, __text); }} catch (e) {{}}\n'
        '    try { if (__println) __println(__text); } catch (e) {}\n'
        '    try { if (!__api && !__println && __console) __console.log(__text); } catch (e) {}\n'
        '  }\n'
        "})(typeof jsc !== 'undefined' ? jsc : null,\n"
        "   typeof println !== 'undefined' ? println : null,\n"
        "   typeof console !== 'undefined' ? console : null);\n"
    )


def _script_dir() -> str | None:
    """Return the directory to write temporary files into, or None for the system default."""
    return _SCRIPT_DIR if os.path.isdir(_SCRIPT_DIR) else None


def _run_jsc_raw(program: str, timeout: int = _PROBE_TIMEOUT) -> tuple[str, str, int]:
    """Run ``program`` exactly as written, returning (stdout, stderr, returncode).

    Used by the diagnostic, which needs to test one output mechanism per run. Anything that
    goes through ``_run_jsc`` is wrapped identically and so cannot tell the mechanisms apart.
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


def _run_jsc(program: str, timeout: int = _SOLVE_TIMEOUT) -> tuple[str, str, int]:
    """Run ``program`` under a-Shell's jsc, returning (output, stderr, returncode).

    ``output`` is whatever the program logged, read back from the result file when jsc's file
    API is available and falling back to stdout otherwise.

    Deliberately plain ``subprocess.run`` rather than ``yt_dlp.utils.Popen``: that wrapper
    passes an explicit ``env=os.environ.copy()``, and ``jsc`` is an ios_system builtin
    resolved through a-Shell's own command dictionary rather than a file on PATH.
    """
    directory = _script_dir()
    script = tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8', dir=directory)
    result = tempfile.NamedTemporaryFile(mode='w', suffix='.out', delete=False, encoding='utf-8', dir=directory)
    result.close()
    try:
        script.write(_wrap_program(program, result.name))
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
        output = pathlib.Path(result.name).read_text(encoding='utf-8', errors='replace')
        if not output.strip():
            output = completed.stdout or ''
    finally:
        pathlib.Path(script.name).unlink(missing_ok=True)
        pathlib.Path(result.name).unlink(missing_ok=True)
    return output, completed.stderr or '', completed.returncode


def _reset_runtime() -> None:
    """Ask a-Shell to reload the webview jsc runs against, and give it time to load."""
    try:
        subprocess.run(
            ['jsc', '--reset'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
    except Exception:  # noqa: BLE001 - best effort recovery
        pass
    time.sleep(_RESET_SETTLE_SECONDS)


def _probe_runtime() -> bool:
    """Check that a usable ``jsc`` exists, caching the result.

    main.py is cross platform, so this must be false everywhere except a-Shell -- otherwise
    the provider would claim every YouTube challenge on a desktop and then fail it, shutting
    out a perfectly good Deno. ``shutil.which`` is no help: ``jsc`` is an ios_system builtin
    rather than a file on PATH, so the only reliable test is to run it. The probe deliberately
    exercises the same wrapper and output channel the real solve uses.
    """
    global _runtime_probe, _probe_detail
    if _IMPORT_ERROR is not None:
        return False
    if _runtime_probe is not None:
        return _runtime_probe

    for attempt in range(_PROBE_ATTEMPTS):
        if attempt:
            # jsc evaluates against a-Shell's wasm.html, which supplies every route out of the
            # script: the jsc file API, println(), and console.log (rebound to println on
            # wasm.html:141). A Shortcut can run before that page has finished loading, in
            # which case all three are missing at once. `jsc --reset` reloads the webview.
            _reset_runtime()
        try:
            output, stderr, returncode = _run_jsc(_PROBE_SCRIPT, timeout=_PROBE_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - any failure here just means no usable jsc
            _runtime_probe = False
            _probe_detail = f'{type(exc).__name__}: {exc}'
            return _runtime_probe

        if _PROBE_SENTINEL in output:
            _runtime_probe = True
            return _runtime_probe
        _probe_detail = (
            f'after {attempt + 1} attempt(s): exit {returncode}, '
            f'output {output.strip()[:150]!r}, stderr {stderr.strip()[:150]!r}'
        )

    _runtime_probe = False
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
            output, stderr, returncode = _run_jsc(stdin)

            # yt-dlp's own QuickJS provider also treats any stderr output as a failure. jsc is
            # noisier than that, so only the exit status is fatal here.
            if returncode:
                message = f'Error running a-Shell jsc (returncode: {returncode})'
                if stderr:
                    message = f'{message}: {stderr.strip()}'
                raise JsChallengeProviderError(message)

            return self._extract_json(output, stderr)

        @staticmethod
        def _extract_json(output: str, stderr: str, /) -> str:
            """Return the solver's JSON result line from jsc's output.

            The caller json.loads() this directly, so anything the environment emits alongside
            the result would break parsing. JSON.stringify never emits a raw newline, so the
            result is always confined to one line.
            """
            for line in reversed(output.splitlines()):
                if line.strip().startswith('{'):
                    return line.strip()

            message = 'a-Shell jsc produced no JSON output'
            if stderr:
                message = f'{message}: {stderr.strip()}'
            raise JsChallengeProviderError(message)

    @register_preference(AShellJscJCP)
    def _ashell_jsc_preference(provider, requests) -> int:
        # Below yt-dlp's own providers (Deno 1000 down to QuickJS 850), so that an officially
        # supported runtime wins if one is ever available alongside this one.
        return 500


# a-Shell's jsc behaves differently between a terminal session and a Shortcut run. Each case
# below is run RAW -- no wrapper -- so exactly one output mechanism is under test at a time,
# and each writes a marker file where it can, so a result survives stdout being unusable.
_MARKER = 'ashell_jsc_marker'
_GLOBALS_EXPR = (
    '"jsc=" + (typeof jsc) + " println=" + (typeof println) + " print=" + (typeof print)'
    ' + " console=" + (typeof console) + " process=" + (typeof process)'
    ' + " globalThis=" + (typeof globalThis)'
)
_DIAGNOSTIC_CASES = (
    ('jsc.writeFile', lambda p: f'jsc.writeFile({json.dumps(p)}, "{_MARKER}");'),
    ('println', lambda p: f'println("{_MARKER}");'),
    ('print', lambda p: f'print("{_MARKER}\\n");'),
    ('console.log', lambda p: f'console.log("{_MARKER}");'),
    ('completion value', lambda p: f'"{_MARKER}";'),
    ('globals -> writeFile', lambda p: f'try {{ jsc.writeFile({json.dumps(p)}, {_GLOBALS_EXPR}); }} catch (e) {{}}'),
    ('globals -> println', lambda p: f'try {{ println({_GLOBALS_EXPR}); }} catch (e) {{}}'),
    ('globals -> completion', lambda p: _GLOBALS_EXPR + ';'),
)


def diagnostic_report() -> list[str]:
    """Run each output mechanism on its own and report what came back, and by which route."""
    report = []
    for label, build in _DIAGNOSTIC_CASES:
        marker = tempfile.NamedTemporaryFile(mode='w', suffix='.marker', delete=False, dir=_script_dir())
        marker.close()
        marker_path = pathlib.Path(marker.name)
        try:
            stdout, stderr, returncode = _run_jsc_raw(build(marker.name))
            written = marker_path.read_text(encoding='utf-8', errors='replace').strip()
        except Exception as exc:  # noqa: BLE001 - the report is the point, not the failure
            report.append(f'  {label}: {type(exc).__name__}: {exc}')
            continue
        finally:
            marker_path.unlink(missing_ok=True)
        report.append(
            f'  {label}: rc={returncode} file={written[:90]!r} out={stdout.strip()[:90]!r} err={stderr.strip()[:90]!r}'
        )
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
