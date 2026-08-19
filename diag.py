"""Report what yt-dlp can actually see on this device.

Run it when a download fails for a reason the normal output does not explain:

    python diag.py "VIDEO_URL" > ~/Documents/diag.txt 2>&1
    cat ~/Documents/diag.txt

Prints the state of the jsc challenge solver, mints a PO token to check that path end to
end, then lists every format yt-dlp found, with the client that supplied it and whether its
URL carries an unsolved throttling parameter or a PO token.
"""

import sys
import time
from urllib.parse import parse_qs, urlparse

import ashell_jsc
import ashell_pot

try:
    import yt_dlp
except ImportError:
    print("The 'yt-dlp' Python library is not installed.")
    sys.exit(1)

DEFAULT_URL = 'https://www.youtube.com/watch?v=BaW_jenozKc'


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
    """Print whether PO tokens can be generated, and mint one to prove it end to end."""
    reason = ashell_pot.unavailable_reason()
    print(f'PO token provider available: {reason is None}')
    if reason:
        print(f'reason: {reason}')
        print()
        return

    started = time.monotonic()
    try:
        token, expires_at = ashell_pot.mint('BaW_jenozKc')
    except Exception as exc:  # noqa: BLE001 - report any failure, do not raise
        print(f'minting failed after {time.monotonic() - started:.1f}s: {type(exc).__name__}: {exc}')
    else:
        print(f'minted a {len(token)} char token in {time.monotonic() - started:.1f}s, expires_at={expires_at}')
        print(f'token: {token[:48]}...')
    print()


def describe_format(fmt):
    """One line per format: what it is, which client supplied it, and how it is signed."""
    query = parse_qs(urlparse(fmt.get('url') or '').query)
    return '  {:<14} {:<5} {:<11} {:<9} {:<10} n={:<5} pot={:<5} {}'.format(
        str(fmt.get('format_id'))[:14],
        str(fmt.get('ext'))[:5],
        f'{fmt.get("width")}x{fmt.get("height")}'[:11],
        str(fmt.get('protocol'))[:9],
        str(fmt.get('vcodec') or fmt.get('acodec'))[:10],
        'n' in query,
        'pot' in query,
        str(fmt.get('format_note') or '')[:40],
    )


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    report_solver()
    report_pot()

    ydl_opts = {
        'quiet': True,
        'no_warnings': False,
        'skip_download': True,
        'extractor_args': {'youtube': {'player_client': ['web_safari', 'web_embedded', 'android_vr']}},
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

    selected = ydl_opts.get('format')
    print(f'\nselected by default selector: {selected or "(yt-dlp default)"}')


if __name__ == '__main__':
    main()
