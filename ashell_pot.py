"""Generate YouTube PO tokens on iOS, using a-Shell's ``jsc`` to run BotGuard.

YouTube attests its web clients with BotGuard, an obfuscated JavaScript VM whose output is a
proof-of-origin token. Without one, ``web``/``web_safari`` return HTTP 403 on the media URLs,
which leaves only clients that need no token. YouTube keeps retiring those: ``tv`` serves
nothing but DRM without cookies, and ``android_vr`` is now enforced too (yt-dlp#17395). So
``visionos``, the one still standing, is the fallback rather than the plan.

yt-dlp cannot generate these itself; it expects an external provider. Every existing provider
needs Node or a headless browser, neither of which exists on iOS. But BotGuard wants a real
browser, and a-Shell's ``jsc`` evaluates against a live WKWebView page served over HTTPS from
localhost, so on this platform the requirement is already satisfied:

- ``document``, ``HTMLElement``, ``getComputedStyle``, ``requestAnimationFrame`` and
  ``matchMedia`` are all genuine, so ``bgutils-js`` needs none of the ``jsdom`` shimming that
  the Node-based providers do.
- ``window.isSecureContext`` is true and ``crypto.subtle`` is present.
- ``fetch`` reaches Google's attestation API directly; it answers a cross-origin request from
  localhost, so the whole exchange happens in JavaScript.

Two properties of the runtime shape the implementation. ``evaluateJavaScript`` returns as soon
as the top-level statements finish and does not await promises, so the result cannot come back
through ``jsc`` itself -- the driver writes it to a file and this module polls for it. And the
webview outlives the Python process, so a minted session parked on ``globalThis`` is reused
across runs, and all but the first token cost one round of minting instead of a BotGuard run.

Import this module before constructing ``yt_dlp.YoutubeDL``; registration is global.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
import time

import ashell_webview

# The PO token provider framework is public API, but it only exists in yt-dlp 2025.05.22 and
# later, and yt-dl.sh upgrades yt-dlp on every run. Failing soft costs the token, not the run.
try:
    from yt_dlp.extractor.youtube.pot.provider import (
        PoTokenContext,
        PoTokenProvider,
        PoTokenProviderError,
        PoTokenProviderRejectedRequest,
        PoTokenRequest,
        PoTokenResponse,
        register_preference,
        register_provider,
    )
    from yt_dlp.extractor.youtube.pot.utils import WEBPO_CLIENTS, get_webpo_content_binding
except ImportError as exc:  # pragma: no cover - depends on the installed yt-dlp
    _IMPORT_ERROR: ImportError | None = exc
else:
    _IMPORT_ERROR = None

_HERE = pathlib.Path(__file__).resolve().parent
_BUNDLE_PATH = _HERE / 'bgutils.bundle.js'
_DRIVER_PATH = _HERE / 'ashell_pot.js'

# The public request key YouTube's own web player uses for its BotGuard challenge.
_REQUEST_KEY = 'O43z0dpjhgX20SCx4KAo'

_PROBE_SENTINEL = 'ashell_pot_ok'
_LAUNCH_TIMEOUT = 60

# A cold start is a network round trip plus a BotGuard VM run; on a warm page it is just a
# mint. Poll rather than sleep so the warm case stays fast.
_RESULT_TIMEOUT = 120
_POLL_INTERVAL = 0.25

# The driver writes its start marker before any asynchronous work, so on a live page it
# appears almost at once. Its absence means the script never ran.
_START_TIMEOUT = 10

_ATTEMPTS = 2

_runtime_probe: bool | None = None
_probe_detail: str | None = None
# Set once minting has failed every attempt. yt-dlp requests a token for each format and only
# remembers one that succeeded, so without this a broken BotGuard is retried per format.
_gave_up = False


def _wait_for(path: pathlib.Path, timeout: float, complete) -> str | None:
    """Poll for ``path`` until ``complete`` accepts its contents, or return None on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            content = path.read_text(encoding='utf-8', errors='replace')
            if complete(content):
                return content
        time.sleep(_POLL_INTERVAL)
    return None


def _launch(program: str, workdir: pathlib.Path, timeout: int, started: bool) -> str:
    """Run ``program`` under jsc and wait for it to write ``result.json`` into ``workdir``.

    Returns the file's contents. ``jsc`` exits long before the program has finished, because
    ``evaluateJavaScript`` does not await the promise chain the driver starts -- the webview
    keeps running it afterwards. So the exit status says nothing about the outcome and the
    result file is the only signal that matters. With ``started``, the program must also
    write a ``started`` marker, and its absence fails the run without waiting for a result.
    """
    completed = ashell_webview.run_script(program, _LAUNCH_TIMEOUT)
    stderr = (completed.stderr or '').strip()
    detail = f': {stderr[:300]}' if stderr else ''

    if started and _wait_for(workdir / 'started', _START_TIMEOUT, bool) is None:
        raise RuntimeError(f'a-Shell jsc did not run the script{detail}')
    # The write is not atomic, so a poll can land on a partial file.
    content = _wait_for(workdir / 'result.json', timeout, lambda text: text.strip().endswith('}'))
    if content is None:
        raise TimeoutError(f'a-Shell jsc wrote no result within {timeout}s{detail}')
    return content


def _build_program(request_body: dict) -> str:
    """Assemble the full script: the request, the vendored bgutils bundle, then the driver."""
    return '\n'.join(
        (
            f'var __ASHELL_POT_REQUEST = {json.dumps(request_body)};',
            _BUNDLE_PATH.read_text(encoding='utf-8'),
            _DRIVER_PATH.read_text(encoding='utf-8'),
        )
    )


def _run_in_workdir(build_body, timeout: int, started: bool) -> str:
    """Run a program in a scratch directory, cleaning it up afterwards.

    The files the program writes must not exist when jsc starts, since their appearance is
    the signal, so they get a private directory rather than pre-created temporary files.
    ``build_body`` receives the paths of the result file and the start marker.
    """
    workdir = pathlib.Path(tempfile.mkdtemp(prefix='ashell_pot_', dir=ashell_webview.script_dir()))
    try:
        program = build_body(str(workdir / 'result.json'), str(workdir / 'started'))
        return _launch(program, workdir, timeout, started)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def mint(content_binding: str) -> tuple[str, int | None]:
    """Return a PO token for ``content_binding``, and when it expires.

    Raises:
        PoTokenProviderError: Every attempt failed. The provider then stays unavailable for
            the rest of the run.
    """
    global _gave_up

    def build(result_path: str, started_path: str) -> str:
        return _build_program(
            {
                'request_key': _REQUEST_KEY,
                'content_binding': content_binding,
                'result_path': result_path,
                'started_path': started_path,
            }
        )

    error: Exception | None = None
    for attempt in range(_ATTEMPTS):
        if attempt and not ashell_webview.reset():
            error = RuntimeError(f'{error}; the webview did not come back after a reset')
            break
        try:
            payload = json.loads(_run_in_workdir(build, _RESULT_TIMEOUT, started=True))
        except Exception as exc:  # noqa: BLE001 - retry once, then report whatever happened
            error = exc
            continue
        if payload.get('ok'):
            return payload['po_token'], payload.get('expires_at')
        error = RuntimeError(payload.get('error') or 'BotGuard failed without a message')

    _gave_up = True
    raise PoTokenProviderError(
        f'Could not mint a PO token with a-Shell jsc, so it is off for the rest of this run: {error}'
    )


def _probe_runtime() -> bool:
    """Check that jsc runs and its file bridge works, caching the result.

    main.py is cross platform, so this must be false everywhere except a-Shell. ``shutil.which``
    is no help: ``jsc`` is an ios_system builtin rather than a file on PATH, so the only
    reliable test is to run it. The probe exercises the file bridge specifically, because that
    is the one channel this provider depends on.
    """
    global _runtime_probe, _probe_detail
    if _IMPORT_ERROR is not None:
        return False
    if _runtime_probe is not None:
        return _runtime_probe

    if not (_BUNDLE_PATH.exists() and _DRIVER_PATH.exists()):
        _runtime_probe = False
        _probe_detail = f'{_BUNDLE_PATH.name} or {_DRIVER_PATH.name} is missing'
        return _runtime_probe

    def build(result_path: str, started_path: str) -> str:
        return f'jsc.writeFile({json.dumps(result_path)}, {json.dumps(f"{{{_PROBE_SENTINEL}}}")});'

    try:
        _runtime_probe = _PROBE_SENTINEL in _run_in_workdir(build, timeout=10, started=False)
    except Exception as exc:  # noqa: BLE001 - any failure here just means no usable jsc
        _runtime_probe = False
        _probe_detail = f'{type(exc).__name__}: {exc}'
    return _runtime_probe


if _IMPORT_ERROR is None:

    @register_provider
    class AShellPotPTP(PoTokenProvider):
        PROVIDER_VERSION = '1.0.0'
        PROVIDER_NAME = 'ashell-pot'
        BUG_REPORT_LOCATION = 'https://github.com/yousif-toama/yt-dlp-ios/issues'

        _SUPPORTED_CONTEXTS = (PoTokenContext.GVS, PoTokenContext.PLAYER, PoTokenContext.SUBS)
        _SUPPORTED_CLIENTS = WEBPO_CLIENTS
        # BotGuard is called from JavaScript inside the webview, which has no way to honour
        # yt-dlp's proxy, source address or TLS settings. Declaring no features makes the
        # framework reject the request rather than silently leak around them.
        _SUPPORTED_EXTERNAL_REQUEST_FEATURES = ()

        def is_available(self, /) -> bool:
            return not _gave_up and not ashell_webview.is_dead() and _probe_runtime()

        def _real_request_pot(self, request: PoTokenRequest, /) -> PoTokenResponse:
            content_binding, binding_type = get_webpo_content_binding(request)
            if not content_binding:
                raise PoTokenProviderRejectedRequest(
                    f'No content binding available for context {request.context.value}'
                )

            self.logger.info(f'Minting a {request.context.value} PO token with a-Shell jsc')
            po_token, expires_at = mint(content_binding)
            self.logger.debug(f'Minted a PO token bound to {binding_type.value}')
            return PoTokenResponse(po_token=po_token, expires_at=expires_at)

    @register_preference(AShellPotPTP)
    def _ashell_pot_preference(provider, request) -> int:
        # Above nothing in particular; this is the only provider that can run here. Kept low
        # so that a real bgutil server, if one is ever configured, wins.
        return 100


def is_available() -> bool:
    """Whether the provider is registered and can actually run."""
    return _IMPORT_ERROR is None and _probe_runtime()


def unavailable_reason() -> str | None:
    """A short explanation of why the provider is unusable, or None if it is usable."""
    if _IMPORT_ERROR is not None:
        return f'this yt-dlp version has no PO token provider API ({_IMPORT_ERROR})'
    if not _probe_runtime():
        detail = f' ({_probe_detail})' if _probe_detail else ''
        return f"a-Shell's 'jsc' command did not run{detail}"
    return None
