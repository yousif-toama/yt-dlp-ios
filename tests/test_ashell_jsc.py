"""The jsc challenge solver: output routes, page isolation, probing, and giving up."""

import time

import pytest
import yt_dlp
from yt_dlp.extractor.youtube import YoutubeIE
from yt_dlp.extractor.youtube.jsc.provider import (
    JsChallengeProviderError,
    JsChallengeRequest,
    JsChallengeType,
    NChallengeInput,
)
from yt_dlp.extractor.youtube.pot._director import YoutubeIEContentProviderLogger

import ashell_jsc
import ashell_webview
from tests.conftest import PAGE_URL

PLAYER_URL = 'https://www.youtube.com/s/player/00000000/player_ias.vflset/en_US/base.js'


def unavailable_reason():
    reason = ashell_jsc.unavailable_reason()
    assert reason is not None, 'expected the provider to be unavailable'
    return reason


@pytest.fixture
def provider():
    ydl = yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True})
    ie = ydl.get_info_extractor(YoutubeIE.ie_key())
    return ashell_jsc.AShellJscJCP(ie, YoutubeIEContentProviderLogger(ie, 'jsc'), {})


def solver_program(provider):
    request = JsChallengeRequest(JsChallengeType.N, NChallengeInput(PLAYER_URL, ['abc']))
    return provider._construct_stdin('', False, [request])


def run_wrapped(program):
    output, _, _ = ashell_jsc._run_jsc(program, 5)
    return output


def settle():
    """Give a navigation the page may have started time to complete."""
    time.sleep(0.2)


class TestExtractJson:
    def test_returns_the_last_json_line(self):
        output = 'noise\n{"type": "result", "responses": []}\nmore noise\n'
        assert ashell_jsc.AShellJscJCP._extract_json(output, '') == '{"type": "result", "responses": []}'

    def test_strips_surrounding_whitespace(self):
        assert ashell_jsc.AShellJscJCP._extract_json('   {"a": 1}  \n', '') == '{"a": 1}'

    @pytest.mark.parametrize('output', ['', '\n\n', 'no json here'])
    def test_raises_without_a_json_line(self, output):
        with pytest.raises(JsChallengeProviderError, match='no JSON output'):
            ashell_jsc.AShellJscJCP._extract_json(output, 'stderr text')

    def test_error_carries_output_and_stderr(self):
        with pytest.raises(JsChallengeProviderError, match="got 'garbage': boom"):
            ashell_jsc.AShellJscJCP._extract_json('garbage', 'boom')


def test_construct_stdin_stops_the_player_coming_back(provider):
    program = solver_program(provider)
    assert '"output_preprocessed": false' in program
    assert '"output_preprocessed": true' not in program


class TestWrapper:
    def test_output_comes_back_through_the_file_api(self, webview):
        assert run_wrapped('console.log("one"); console.log("two", 3);') == 'one\ntwo 3'

    def test_output_comes_back_through_the_completion_value_off_wasm_html(self, webview):
        webview.control('navigate', url='https://www.youtube.com/')
        assert run_wrapped('console.log("from elsewhere");').strip() == 'from elsewhere'

    def test_a_thrown_error_is_reported_not_raised(self, webview):
        assert run_wrapped('throw new Error("bad player");').startswith('__ashell_error__ Error: bad player')

    def test_the_program_cannot_navigate_the_page(self, webview):
        run_wrapped('globalThis.location = new URL("https://www.youtube.com/watch?v=yt-dlp-wins");')
        settle()
        assert webview.state()['url'] == PAGE_URL

    def test_the_real_solver_runs_without_colliding_with_the_page_jsc(self, webview, provider):
        output = run_wrapped(solver_program(provider))
        settle()
        # An empty player cannot be solved; reaching the solver's own complaint shows that both
        # scripts loaded and `jsc(...)` ran.
        assert 'unexpected structure' in output
        assert 'has already been declared' not in output
        assert webview.state()['url'] == PAGE_URL


class TestProbe:
    def test_probe_passes_on_wasm_html(self, webview):
        assert ashell_jsc.unavailable_reason() is None

    def test_probe_is_off_without_jsc_and_never_resets(self, no_jsc, monkeypatch):
        resets = []
        monkeypatch.setattr(ashell_webview, 'reset', lambda: resets.append(1) or False)

        assert 'did not run (FileNotFoundError' in unavailable_reason()
        assert resets == []

    def test_probe_resets_a_page_that_does_not_answer(self, webview):
        webview.control('kill')

        assert ashell_jsc.unavailable_reason() is None
        assert webview.state()['mode'] == 'alive'

    def test_probe_gives_up_on_a_dead_page_and_marks_it(self, webview):
        webview.control('wedge')

        assert 'did not come back after a reset' in unavailable_reason()
        assert ashell_webview.is_dead()

    def test_probe_does_not_wait_on_a_page_already_known_dead(self, webview, monkeypatch):
        webview.control('wedge')
        monkeypatch.setattr(ashell_webview, '_dead', True)

        started = time.monotonic()
        assert 'stopped answering earlier' in unavailable_reason()
        assert time.monotonic() - started < 0.5


class TestSolve:
    def test_solve_returns_the_json_line(self, webview, provider):
        program = 'console.log("noise"); console.log(JSON.stringify({type: "result", responses: []}));'
        assert provider._run_js_runtime(program) == '{"type":"result","responses":[]}'

    def test_solve_recovers_a_page_killed_mid_run(self, webview, provider, monkeypatch):
        monkeypatch.setattr(ashell_jsc, '_SOLVE_TIMEOUT', 0.5)
        webview.control('kill')

        assert provider._run_js_runtime('console.log("{}");') == '{}'

    def test_repeated_failure_turns_the_solver_off_for_the_run(self, webview, provider):
        assert provider.is_available()

        with pytest.raises(JsChallengeProviderError, match='off for the rest of this run'):
            provider._run_js_runtime('console.log("not json");')
        assert not provider.is_available()

    def test_a_dead_page_turns_the_solver_off(self, webview, provider, monkeypatch):
        monkeypatch.setattr(ashell_jsc, '_SOLVE_TIMEOUT', 0.5)
        assert provider.is_available()
        webview.control('wedge')

        with pytest.raises(JsChallengeProviderError, match='did not come back after a reset'):
            provider._run_js_runtime('console.log("{}");')
        assert ashell_webview.is_dead()
        assert not provider.is_available()


def test_diagnostic_reports_every_route(webview):
    report = '\n'.join(ashell_jsc.diagnostic_report())
    assert "jsc.writeFile: rc=1 file='ashell_jsc_marker'" in report
    assert "println: rc=1 file='' out='ashell_jsc_marker'" in report
    assert "completion value: rc=0 file='' out='ashell_jsc_marker'" in report
    assert 'globals -> completion: rc=0' in report


def test_diagnostic_is_skipped_on_a_dead_page(webview, monkeypatch):
    webview.control('wedge')
    monkeypatch.setattr(ashell_webview, '_dead', True)

    started = time.monotonic()
    assert ashell_jsc.diagnostic_report() == ['  skipped: the webview stopped answering, so no route can be tested']
    assert time.monotonic() - started < 0.5
