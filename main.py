import argparse
import os
import sys

try:
    import yt_dlp
except ImportError:
    print("The 'yt-dlp' Python library is not installed.")
    print("Please install it in your Python environment (e.g., 'pip install yt-dlp').")
    sys.exit(1)

# YouTube needs an external JS runtime to solve its n-sig challenges, and a PO token to serve
# the media URLs to a web client at all. Importing these registers a-Shell's built-in `jsc` as
# the runtime for both; they are inert on every other platform.
import ashell_jsc  # noqa: E402 - only importable once yt-dlp is known to be installed
import ashell_pot  # noqa: E402 - only importable once yt-dlp is known to be installed

# web_safari carries the full format ladder and needs a PO token, which ashell_pot supplies.
# The other two need no token and cover the cases it cannot: web_embedded when BotGuard fails,
# visionos when the jsc webview fails as well -- it needs neither a token nor a JS runtime, but
# cannot play "made for kids" videos. yt-dlp keeps the first client's copy of each format, so
# visionos only fills in what the others could not provide. `tv` is deliberately absent --
# without account cookies every format it returns is DRM'd.
PLAYER_CLIENTS = ['web_safari', 'web_embedded', 'visionos']


def get_documents_folder():
    """Return the path to the user's Documents folder, creating it if needed, or None."""
    documents_folder = os.path.join(os.path.expanduser('~'), 'Documents')
    try:
        os.makedirs(documents_folder, exist_ok=True)
    except OSError as e:
        print(f'Error creating Documents folder at {documents_folder}: {e}')
        print('Please ensure you have permissions to create this directory, or create it manually.')
        return None
    return documents_folder


def build_options(output_dir, is_youtube):
    """Return the YoutubeDL options for a download into ``output_dir``."""
    options = {
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'noplaylist': True,
        'quiet': False,
        'extract_flat': 'discard_in_playlist',
        'final_ext': 'mkv',
        # Lets yt-dlp fetch solver scripts matching its own version when the installed
        # yt-dlp-ejs package has drifted out of step. Works with any runtime; only 'ejs:npm' is
        # Deno/Bun-only.
        'remote_components': ['ejs:github'],
        # Chunked requests keep a stall or a mid-download rejection to one 10 MB retry rather
        # than the whole file.
        'http_chunk_size': 10485760,
        'fragment_retries': 10,
        'ignoreerrors': 'only_download',
        'merge_output_format': 'mkv',
        'postprocessors': [
            {'key': 'FFmpegVideoRemuxer', 'preferedformat': 'mkv'},
            {'key': 'FFmpegConcat', 'only_multi_video': True, 'when': 'playlist'},
        ],
        'retries': 10,
    }
    if is_youtube:
        options.update(
            {
                'format': 'bestvideo[height<=?1080][fps<=?60][vcodec!*=av0]+bestaudio/best',
                'noplaylist': False,
                'extractor_args': {'youtube': {'player_client': PLAYER_CLIENTS}},
            }
        )
    return options


def downloaded_files(info):
    """Return the final paths of every file yt-dlp wrote for ``info``, a video or a playlist.

    A concatenated playlist records its single output on the playlist itself; otherwise each
    video records its own. Entries that failed to download are None, or have no record.
    """
    if not info:
        return []
    if info.get('requested_downloads'):
        return [download['filepath'] for download in info['requested_downloads'] if download.get('filepath')]
    paths = []
    for entry in info.get('entries') or []:
        paths.extend(downloaded_files(entry))
    return paths


def report_providers():
    """Say whether the a-Shell JS challenge solver and PO token provider can run."""
    reason = ashell_jsc.unavailable_reason()
    if reason:
        print(f'Warning: cannot solve YouTube JS challenges because {reason}.')
        print('Falling back to the visionos client, which cannot download "made for kids" videos.')
        print('jsc diagnostic:')
        for line in ashell_jsc.diagnostic_report():
            print(line)
    else:
        print("JS challenge solver ready: a-Shell's jsc.")

    pot_reason = ashell_pot.unavailable_reason()
    if pot_reason:
        print(f'Warning: cannot generate PO tokens because {pot_reason}.')
        print('Falling back to the web_embedded and visionos clients, which need none.')
    else:
        print("PO token provider ready: BotGuard under a-Shell's jsc.")


def download_video_with_library(url, output_dir, is_youtube=True):
    """Download ``url`` into ``output_dir`` and return the paths of the files written.

    Returns an empty list when nothing was downloaded.
    """
    if is_youtube:
        report_providers()

    print(f'Starting download for URL: {url} with yt-dlp library.')
    print(f'Video will be saved to: {output_dir}')
    try:
        with yt_dlp.YoutubeDL(build_options(output_dir, is_youtube)) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        print(f'A yt-dlp download error occurred: {e}')
        return []

    paths = [path for path in downloaded_files(info) if os.path.exists(path)]
    for path in paths:
        print(f'Download successful. Video saved to: {path}')
    if not paths:
        print('yt-dlp finished without writing a video file.')
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Download a video using the yt-dlp Python library and save it to your Documents folder.'
    )
    parser.add_argument('url', help='The URL of the video to download.')
    args = parser.parse_args(argv)
    is_youtube = 'youtube.com' in args.url or 'youtu.be' in args.url

    documents_folder = get_documents_folder()
    if not documents_folder:
        print('Could not determine or create Documents folder. Exiting.')
        sys.exit(1)

    print(f'Attempting to save video to: {documents_folder}')
    if download_video_with_library(args.url, documents_folder, is_youtube):
        print('\nProcess complete.')
    else:
        print('\nFailed to download the video.')


if __name__ == '__main__':
    main()
