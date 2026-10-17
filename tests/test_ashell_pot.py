"""The PO token provider: the async file bridge, session reuse, and giving up.

BotGuard itself is replaced by bgutils_stub.js; only the device can run the real one.
"""

import time

import pytest
import yt_dlp
from yt_dlp.extractor.youtube import YoutubeIE
from yt_dlp.extractor.youtube.pot._director import YoutubeIEContentProviderLogger
from yt_dlp.extractor.youtube.pot.provider import PoTokenProviderError

import ashell_pot
import ashell_webview
from tests.conftest import FAKE_DIR, PAGE_URL


def unavailable_reason():
    reason = ashell_pot.unavailable_reason()
    assert reason is not None, 'expected the provider to be unavailable'
    return reason


@pytest.fixture
def provider():
    ydl = yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True})
    ie = ydl.get_info_extractor(YoutubeIE.ie_key())
    return ashell_pot.AShellPotPTP(ie, YoutubeIEContentProviderLogger(ie, 'pot'), {})


@pytest.fixture
def stub_bundle(monkeypatch):
    monkeypatch.setattr(ashell_pot, '_BUNDLE_PATH', FAKE_DIR / 'bgutils_stub.js')


def page_global(webview, name):
    return webview.evaluate(f'String(globalThis.{name});').strip()


def test_mint_returns_the_token_and_session_expiry(webview, stub_bundle):
    token, expires_at = ashell_pot.mint('video-id')

    assert token == 'token-for-video-id'
    assert expires_at == pytest.approx(time.time() + 3600 - 300, abs=60)


def test_a_second_mint_reuses_the_parked_session(webview, stub_bundle):
    ashell_pot.mint('first')
    ashell_pot.mint('second')

    assert page_global(webview, '__stub_challenges') == '1'


def test_a_rejected_session_is_replaced_once(webview, stub_bundle):
    ashell_pot.mint('first')
    webview.evaluate('globalThis.__stub_fail = "mint"; "ok";')

    assert ashell_pot.mint('second')[0] == 'token-for-second'
    assert page_global(webview, '__stub_challenges') == '2'


def test_a_failed_botguard_run_is_retried_once_in_the_page(webview, stub_bundle):
    webview.evaluate('globalThis.__stub_fail = "challenge"; "ok";')

    assert ashell_pot.mint('video-id')[0] == 'token-for-video-id'
    assert page_global(webview, '__stub_challenges') == '2'


def test_repeated_failure_turns_the_provider_off_for_the_run(webview, provider, monkeypatch, tmp_path):
    # A reset reloads the page and clears its globals, so the failure has to ship with the bundle.
    failing = tmp_path / 'failing_stub.js'
    failing.write_text('globalThis.__stub_fail = "always";\n' + (FAKE_DIR / 'bgutils_stub.js').read_text())
    monkeypatch.setattr(ashell_pot, '_BUNDLE_PATH', failing)
    assert provider.is_available()

    with pytest.raises(PoTokenProviderError, match='off for the rest of this run: .*stub challenge failed'):
        ashell_pot.mint('video-id')
    assert not provider.is_available()


def test_a_hung_botguard_run_times_out(webview, stub_bundle, monkeypatch):
    monkeypatch.setattr(ashell_pot, '_ATTEMPTS', 1)
    webview.evaluate('globalThis.__stub_never = true; "ok";')

    with pytest.raises(PoTokenProviderError, match='wrote no result within'):
        ashell_pot.mint('video-id')


def test_a_page_that_never_runs_the_script_fails_fast(webview, stub_bundle):
    # Two pages deep, so going back once still leaves the page without the jsc file API.
    webview.control('navigate', url='https://www.youtube.com/')
    webview.control('navigate', url='https://www.youtube.com/watch?v=x')

    started = time.monotonic()
    with pytest.raises(PoTokenProviderError, match='did not run the script'):
        ashell_pot.mint('video-id')
    # The start marker cuts each attempt short instead of waiting out the result timeout.
    assert time.monotonic() - started < 2 * ashell_pot._RESULT_TIMEOUT


def test_mint_recovers_a_page_that_left_wasm_html(webview, stub_bundle):
    webview.control('navigate', url='https://www.youtube.com/')

    assert ashell_pot.mint('video-id')[0] == 'token-for-video-id'
    assert webview.state()['url'] == PAGE_URL


def test_a_dead_page_stops_minting(webview, stub_bundle, provider):
    assert provider.is_available()
    webview.control('wedge')

    with pytest.raises(PoTokenProviderError, match='did not come back after a reset'):
        ashell_pot.mint('video-id')
    assert ashell_webview.is_dead()
    assert not provider.is_available()


class TestProbe:
    def test_probe_passes_on_wasm_html(self, webview):
        assert ashell_pot.unavailable_reason() is None

    def test_probe_resets_a_page_that_left_wasm_html(self, webview):
        webview.control('navigate', url='https://www.youtube.com/')

        assert ashell_pot.unavailable_reason() is None
        assert webview.state()['url'] == PAGE_URL

    def test_probe_is_off_without_jsc(self, no_jsc):
        assert 'FileNotFoundError' in unavailable_reason()

    def test_probe_is_off_when_the_bundle_is_missing(self, webview, monkeypatch, tmp_path):
        monkeypatch.setattr(ashell_pot, '_BUNDLE_PATH', tmp_path / 'missing.js')
        assert 'is missing' in unavailable_reason()

    def test_probe_does_not_wait_on_a_page_already_known_dead(self, webview, monkeypatch):
        webview.control('wedge')
        monkeypatch.setattr(ashell_webview, '_dead', True)

        started = time.monotonic()
        assert 'stopped answering earlier' in unavailable_reason()
        assert time.monotonic() - started < 0.5
