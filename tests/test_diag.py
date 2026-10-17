"""diag.py's offline parts: the page report and the recovery check, against the fake webview."""

import diag
from tests.conftest import PAGE_URL


def test_page_url_reports_the_page(webview):
    assert diag.page_url() == PAGE_URL


def test_page_url_reports_a_page_that_does_not_answer(webview, monkeypatch):
    monkeypatch.setattr(diag, '_PAGE_TIMEOUT', 0.5)
    webview.control('kill')
    assert diag.page_url().startswith('(no answer: TimeoutExpired')


def test_check_recovery_brings_the_page_back_and_writes_the_report(webview, monkeypatch, tmp_path, capsys):
    report = tmp_path / 'diag.txt'
    monkeypatch.setattr(diag, 'REPORT_PATH', str(report))
    monkeypatch.setattr(diag, '_NAVIGATION_WAIT', 0.3)

    diag.check_recovery()

    text = report.read_text()
    assert f'page after navigating away: {diag._AWAY_URL}' in text
    assert 'reset returned True' in text
    assert f'page after reset: {PAGE_URL}' in text
    assert 'jsc solver available: True' in text
    assert 'PO token provider available: True' in text
    assert f'report written to {report}' in capsys.readouterr().out
