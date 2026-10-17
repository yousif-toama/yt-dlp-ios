"""Recovering the webview: send it home, reload it, or give up on it for the run."""

import ashell_webview
from tests.conftest import PAGE_URL


def test_reset_sends_a_page_that_left_back_through_history(webview):
    webview.control('navigate', url='https://www.youtube.com/watch?v=x')

    assert ashell_webview.reset() is True
    assert webview.state()['url'] == PAGE_URL
    assert not ashell_webview.is_dead()


def test_reset_reloads_a_killed_page(webview):
    webview.control('kill')

    assert ashell_webview.reset() is True
    assert webview.state()['mode'] == 'alive'
    assert not ashell_webview.is_dead()


def test_reset_marks_a_page_that_never_answers_as_dead(webview, capsys):
    webview.control('wedge')

    assert ashell_webview.reset() is False
    assert ashell_webview.is_dead()
    assert 'jsc is off for the rest of this run' in capsys.readouterr().err


def test_a_page_that_answers_but_never_reloads_is_not_dead(webview, capsys):
    webview.control('freeze')

    assert ashell_webview.reset() is False
    assert not ashell_webview.is_dead()
    assert 'did not reload' in capsys.readouterr().err


def test_reset_without_jsc_marks_the_page_dead(no_jsc):
    assert ashell_webview.reset() is False
    assert ashell_webview.is_dead()


def test_script_dir_falls_back_to_the_system_default(monkeypatch, tmp_path):
    monkeypatch.setattr(ashell_webview, '_SCRIPT_DIR', str(tmp_path / 'missing'))
    assert ashell_webview.script_dir() is None
