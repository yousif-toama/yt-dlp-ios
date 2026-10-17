"""Report what yt-dlp can actually see on this device.

Run it when a download fails for a reason the normal output does not explain. a-Shell handles
neither redirection nor multi-line commands well, so it is meant to be called from a one-line
``python -c`` (see TESTING.md), and it copies everything it prints into ``diag.txt`` next to
this file itself.

``main`` prints the state of the jsc challenge solver, mints two PO tokens to check that path
end to end and that the second reuses the first's session, then lists every format yt-dlp
found, with the client that supplied it and whether its URL carries an unsolved throttling
parameter or a PO token. Before and after, it reports which page the webview is on: the
solver must leave it on wasm.html. ``check_recovery`` sends the page away deliberately and
checks that a reset brings it back.
"""

import contextlib
import os
import sys
import time
from urllib.parse import parse_qs, urlparse

try:
    import yt_dlp
except ImportError:
    print("The 'yt-dlp' Python library is not installed.")
    sys.exit(1)

import ashell_jsc  # noqa: E402 - only importable once yt-dlp is known to be installed
import ashell_pot  # noqa: E402 - only importable once yt-dlp is known to be installed
import ashell_webview  # noqa: E402 - only importable once yt-dlp is known to be installed
from main import PLAYER_CLIENTS  # noqa: E402 - only importable once yt-dlp is known to be installed

DEFAULT_VIDEO_ID = 'jNQXAC9IVRw'
DEFAULT_URL = f'https://www.youtube.com/watch?v={DEFAULT_VIDEO_ID}'
REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'diag.txt')
_AWAY_URL = 'https://www.youtube.com/'
_NAVIGATION_WAIT = 5
_PAGE_TIMEOUT = 10


class _Tee:
    """A text stream that writes to every stream it was given."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


@contextlib.contextmanager
def _reporting():
    """Copy stdout and stderr into REPORT_PATH for the duration of the block."""
    with open(REPORT_PATH, 'w', encoding='utf-8') as report:
        stdout, stderr = _Tee(sys.stdout, report), _Tee(sys.stderr, report)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            yield
    print(f'report written to {REPORT_PATH}')


def page_url():
    """Return the URL of the page jsc evaluates against, or a description of the failure."""
    try:
        completed = ashell_webview.run_script('location.href;', _PAGE_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - report any failure, do not raise
        return f'(no answer: {type(exc).__name__}: {exc})'
    return (completed.stdout or '').strip() or f'(empty, stderr {(completed.stderr or "").strip()[:150]!r})'


def report_solver():
    """Print whether the jsc challenge solver is usable, and why not when it is not."""
    reason = ashell_jsc.unavailable_reason()
    print(f'jsc solver available: {reason is None}')
    if reason:
        print(f'reason: {reason}')
        for line in ashell_jsc.diagnostic_report():
            print(line)
    print()


def report_pot():
    """Print whether PO tokens can be generated, and mint two to prove it end to end."""
    reason = ashell_pot.unavailable_reason()
    print(f'PO token provider available: {reason is None}')
    if reason:
        print(f'reason: {reason}')
        print()
        return

    for attempt in ('first', 'second'):
        started = time.monotonic()
        try:
            token, expires_at = ashell_pot.mint(DEFAULT_VIDEO_ID)
        except Exception as exc:  # noqa: BLE001 - report any failure, do not raise
            print(f'{attempt} mint failed after {time.monotonic() - started:.1f}s: {type(exc).__name__}: {exc}')
            break
        print(f'{attempt} mint: {len(token)} chars in {time.monotonic() - started:.1f}s, expires_at={expires_at}')
        print(f'token: {token[:48]}...')
    print()


def describe_format(fmt):
    """One line per format: what it is, which client supplied it, and how it is signed."""
    query = parse_qs(urlparse(fmt.get('url') or '').query)
    # Media URLs name the client they were issued to; manifest-backed formats have no `c`.
    client = query.get('c', ['-'])[0]
    return '  {:<14} {:<5} {:<11} {:<9} {:<10} {:<19} n={:<5} pot={:<5} {}'.format(
        str(fmt.get('format_id'))[:14],
        str(fmt.get('ext'))[:5],
        f'{fmt.get("width")}x{fmt.get("height")}'[:11],
        str(fmt.get('protocol'))[:9],
        str(fmt.get('vcodec') or fmt.get('acodec'))[:10],
        client[:19],
        'n' in query,
        'pot' in query,
        str(fmt.get('format_note') or '')[:40],
    )


def report_formats(url):
    """Extract ``url`` without downloading and print one line per format."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': False,
        'skip_download': True,
        'extractor_args': {'youtube': {'player_client': PLAYER_CLIENTS}},
        'remote_components': ['ejs:github'],
    }
    print(f'extracting: {url}')
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 - report any extraction failure, do not raise
        print(f'extraction failed: {type(exc).__name__}: {exc}')
        return

    formats = info.get('formats') or []
    print(f'\n{len(formats)} formats\n')
    for fmt in formats:
        print(describe_format(fmt))
    print()


def main(url=DEFAULT_URL):
    """Report on the solver, PO tokens and formats for ``url``, and where the page ends up."""
    with _reporting():
        print(f'page before: {page_url()}\n')
        report_solver()
        report_pot()
        report_formats(url)
        print(f'page after: {page_url()}')


def check_recovery():
    """Send the page away from wasm.html, then check that a reset brings it back."""
    with _reporting():
        print(f'page before: {page_url()}')
        try:
            ashell_webview.run_script(f'location = {_AWAY_URL!r}; "navigating";', _PAGE_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - report any failure, do not raise
            print(f'could not navigate away: {type(exc).__name__}: {exc}')
        time.sleep(_NAVIGATION_WAIT)
        print(f'page after navigating away: {page_url()}')
        started = time.monotonic()
        recovered = ashell_webview.reset()
        print(f'reset returned {recovered} after {time.monotonic() - started:.1f}s')
        print(f'page after reset: {page_url()}')
        print(f'jsc solver available: {ashell_jsc.unavailable_reason() is None}')
        print(f'PO token provider available: {ashell_pot.unavailable_reason() is None}')


if __name__ == '__main__':
    main(*sys.argv[1:2])
