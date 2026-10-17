"""Fixtures that stand a fake a-Shell webview up behind a fake ``jsc`` command.

See TESTING.md for what the fake covers and what only the device can show.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import subprocess

import pytest

import ashell_jsc
import ashell_pot
import ashell_webview

FAKE_DIR = pathlib.Path(__file__).resolve().parent / 'fake_webview'
PAGE_URL = ashell_webview._PAGE_URL


class Webview:
    """Controls the fake webview process: inspect the page, or break it in a chosen way."""

    def __init__(self, socket_path: pathlib.Path):
        self.socket_path = socket_path

    def _send(self, message: dict) -> dict:
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(str(self.socket_path))
            connection.sendall(json.dumps(message).encode() + b'\n')
            reply = b''
            while chunk := connection.recv(65536):
                reply += chunk
        return json.loads(reply)

    def state(self) -> dict:
        """The page's URL, mode, how many times it has loaded, and its history length."""
        return self._send({'op': 'state'})

    def control(self, action: str, **fields) -> dict:
        """``kill``, ``wedge`` or ``freeze`` the page, or ``navigate`` it to ``url``."""
        return self._send({'op': 'control', 'action': action, **fields})

    def evaluate(self, source: str) -> str:
        """Run ``source`` in the page the way ``jsc`` does, returning its stdout."""
        return ashell_webview.run_script(source, 5).stdout


@pytest.fixture(autouse=True)
def fresh_modules(monkeypatch, tmp_path):
    """Give every test fresh provider state, short timeouts, and a private scratch directory."""
    for module in (ashell_jsc, ashell_pot):
        monkeypatch.setattr(module, '_runtime_probe', None)
        monkeypatch.setattr(module, '_probe_detail', None)
        monkeypatch.setattr(module, '_gave_up', False)
    monkeypatch.setattr(ashell_webview, '_dead', False)

    scratch = tmp_path / 'Documents'
    scratch.mkdir()
    monkeypatch.setattr(ashell_webview, '_SCRIPT_DIR', str(scratch))

    monkeypatch.setattr(ashell_webview, '_RESET_TIMEOUT', 2)
    monkeypatch.setattr(ashell_webview, '_READY_TIMEOUT', 1.5)
    monkeypatch.setattr(ashell_webview, '_PROBE_TIMEOUT', 1)
    monkeypatch.setattr(ashell_webview, '_PROBE_INTERVAL', 0.05)
    monkeypatch.setattr(ashell_jsc, '_PROBE_TIMEOUT', 2)
    monkeypatch.setattr(ashell_jsc, '_SOLVE_TIMEOUT', 30)
    monkeypatch.setattr(ashell_pot, '_LAUNCH_TIMEOUT', 5)
    monkeypatch.setattr(ashell_pot, '_PROBE_TIMEOUT', 1)
    monkeypatch.setattr(ashell_pot, '_RESULT_TIMEOUT', 3)
    monkeypatch.setattr(ashell_pot, '_START_TIMEOUT', 1)
    monkeypatch.setattr(ashell_pot, '_POLL_INTERVAL', 0.02)


@pytest.fixture
def no_jsc(monkeypatch, tmp_path):
    """A PATH with no ``jsc`` on it, as on any platform but a-Shell."""
    empty = tmp_path / 'empty-bin'
    empty.mkdir()
    monkeypatch.setenv('PATH', str(empty))


@pytest.fixture
def webview(monkeypatch, tmp_path):
    """A fresh fake webview on wasm.html, with the fake ``jsc`` first on PATH."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('the fake webview needs Node')
    socket_path = tmp_path / 'webview.sock'
    server = subprocess.Popen(
        [node, str(FAKE_DIR / 'webview.mjs'), str(socket_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert server.stdout is not None
        assert server.stdout.readline().strip() == 'ready'
        monkeypatch.setenv('FAKE_WEBVIEW_SOCKET', str(socket_path))
        monkeypatch.setenv('PATH', f'{FAKE_DIR}{os.pathsep}{os.environ["PATH"]}')
        yield Webview(socket_path)
    finally:
        server.kill()
        server.wait()
