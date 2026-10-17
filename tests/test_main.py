"""main.py: the options it hands yt-dlp, and the files it reports after a real download."""

import functools
import http.server
import shutil
import subprocess
import threading

import pytest

import main


class TestDownloadedFiles:
    def test_a_single_video(self):
        info = {'requested_downloads': [{'filepath': '/out/a.mkv'}]}
        assert main.downloaded_files(info) == ['/out/a.mkv']

    def test_a_playlist_reports_each_entry(self):
        info = {
            'entries': [
                {'requested_downloads': [{'filepath': '/out/a.mkv'}]},
                None,
                {'title': 'failed, nothing recorded'},
                {'requested_downloads': [{'filepath': '/out/b.mkv'}]},
            ]
        }
        assert main.downloaded_files(info) == ['/out/a.mkv', '/out/b.mkv']

    def test_a_concatenated_playlist_reports_its_one_output(self):
        info = {
            'requested_downloads': [{'filepath': '/out/joined.mkv'}],
            'entries': [{'requested_downloads': [{'filepath': '/out/part.mkv'}]}],
        }
        assert main.downloaded_files(info) == ['/out/joined.mkv']

    def test_nested_playlists(self):
        info = {'entries': [{'entries': [{'requested_downloads': [{'filepath': '/out/deep.mkv'}]}]}]}
        assert main.downloaded_files(info) == ['/out/deep.mkv']

    @pytest.mark.parametrize('info', [None, {}, {'entries': None}, {'requested_downloads': [{}]}])
    def test_nothing_downloaded(self, info):
        assert main.downloaded_files(info) == []


class TestBuildOptions:
    def test_youtube_falls_back_to_visionos_last(self):
        clients = main.build_options('/out', is_youtube=True)['extractor_args']['youtube']['player_client']
        assert clients[0] == 'web_safari'
        assert clients[-1] == 'visionos'

    def test_youtube_caps_the_format_and_allows_playlists(self):
        options = main.build_options('/out', is_youtube=True)
        assert 'height<=?1080' in options['format']
        assert options['noplaylist'] is False

    def test_other_sites_keep_the_default_format_and_one_video(self):
        options = main.build_options('/out', is_youtube=False)
        assert 'format' not in options
        assert 'extractor_args' not in options
        assert options['noplaylist'] is True

    def test_output_goes_into_the_given_directory(self):
        assert main.build_options('/out', is_youtube=False)['outtmpl'].startswith('/out/')


@pytest.fixture
def served_video(tmp_path):
    """A one-second clip served over HTTP from a local server."""
    if shutil.which('ffmpeg') is None:
        pytest.skip('needs ffmpeg')
    root = tmp_path / 'served'
    root.mkdir()
    subprocess.run(
        [
            'ffmpeg',
            '-loglevel',
            'error',
            '-f',
            'lavfi',
            '-i',
            'testsrc=duration=1:size=64x64:rate=10',
            '-pix_fmt',
            'yuv420p',
            str(root / 'clip.mp4'),
        ],
        check=True,
    )
    handler = functools.partial(QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/clip.mp4'
    finally:
        server.shutdown()
        server.server_close()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - the base class names it `format`
        pass


def test_download_reports_the_file_it_wrote(served_video, tmp_path):
    output = tmp_path / 'downloads'
    output.mkdir()
    (output / 'an older download.mkv').write_bytes(b'not this one')

    paths = main.download_video_with_library(served_video, str(output), is_youtube=False)

    assert len(paths) == 1
    assert paths[0].endswith('.mkv')
    assert 'older' not in paths[0]
    assert (output / paths[0]).stat().st_size > 0


def test_a_failed_download_reports_nothing(tmp_path, capsys):
    paths = main.download_video_with_library('http://127.0.0.1:9/missing.mp4', str(tmp_path), is_youtube=False)

    assert paths == []
    assert 'without writing a video file' in capsys.readouterr().out


def test_main_rejects_a_missing_url(capsys):
    with pytest.raises(SystemExit):
        main.main([])
    assert 'url' in capsys.readouterr().err
