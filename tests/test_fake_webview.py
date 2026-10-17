"""The fake webview reproduces the a-Shell behaviours the providers were built around.

If these fail, the other tests are exercising something other than what the device does.
"""

import subprocess
import time

import pytest

import ashell_webview
from tests.conftest import PAGE_URL


def wait_for_url(webview, url, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if webview.state()['url'] == url:
            return
        time.sleep(0.02)
    raise AssertionError(f'page stayed on {webview.state()["url"]}, expected {url}')


def test_wasm_page_has_the_jsc_file_api_and_println(webview, tmp_path):
    target = tmp_path / 'out.txt'
    stdout = webview.evaluate(f'jsc.writeFile({str(target)!r}, "written"); println("printed"); "done";')
    assert target.read_text() == 'written'
    assert stdout == 'printed\ndone\n'


def test_var_jsc_collides_with_the_page_const(webview):
    completed = ashell_webview.run_script('var jsc = 1; "unreachable";', 5)
    assert completed.returncode != 0
    assert "Identifier 'jsc' has already been declared" in completed.stderr


def test_undefined_completion_value_is_rejected(webview):
    completed = ashell_webview.run_script('undefined;', 5)
    assert 'unsupported type' in completed.stderr


def test_assigning_location_navigates_the_page_away(webview):
    webview.evaluate('globalThis.location = new URL("https://www.youtube.com/watch?v=x"); "ok";')
    wait_for_url(webview, 'https://www.youtube.com/watch?v=x')
    assert webview.evaluate('typeof jsc + " " + typeof println;') == 'undefined undefined\n'


def test_history_back_returns_to_wasm_html(webview):
    webview.control('navigate', url='https://www.youtube.com/')
    webview.evaluate('history.back(); "ok";')
    wait_for_url(webview, PAGE_URL)


def test_reset_reloads_whatever_url_the_page_is_on(webview):
    webview.control('navigate', url='https://www.youtube.com/')
    loads = webview.state()['loads']
    ashell_webview.run(['--reset'], 5)
    deadline = time.monotonic() + 2
    while webview.state()['loads'] == loads and time.monotonic() < deadline:
        time.sleep(0.02)
    assert webview.state()['url'] == 'https://www.youtube.com/'
    assert webview.state()['loads'] == loads + 1


def test_a_killed_page_does_not_answer(webview):
    webview.control('kill')
    with pytest.raises(subprocess.TimeoutExpired):
        ashell_webview.run_script('"hello";', 0.5)
