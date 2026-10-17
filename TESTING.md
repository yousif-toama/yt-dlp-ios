# Testing

Most of this project only does its real work inside a-Shell on iOS, where `jsc` evaluates
scripts in a live WKWebView. That cannot run anywhere else, so testing is split in two: a
desktop suite that runs the real Python against a fake webview, and a short list of checks
that only the device can answer.

## On a desktop

Needs Python 3.13, [uv](https://docs.astral.sh/uv/), Node (for the fake webview) and ffmpeg
(for the download test). Tests that need Node or ffmpeg skip without them; CI installs both.

```sh
uv venv -p 3.13
uv pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/ty check
```

The suite never touches the network.

### The fake webview

`tests/fake_webview/webview.mjs` is a Node process standing in for a-Shell's webview, and
`tests/fake_webview/jsc` is an executable that the tests put first on `PATH` in place of
a-Shell's `jsc`. So `ashell_webview`, `ashell_jsc` and `ashell_pot` run unmodified, spawning
`jsc` as they do on device. The fake reproduces the behaviour those modules were built around:

| a-Shell behaviour | In the fake |
| --- | --- |
| `jsc FILE` evaluates FILE as a classic script and prints its completion value | Same; `undefined` and objects fail with "a result of an unsupported type" |
| The page outlives each `jsc` call and the Python process | One long-lived page; globals, timers and promises carry over between calls |
| `wasm.html` has `const jsc` (file API), `println`, and `console.log` rebound to `println` | Same, so a top-level `var jsc` fails with "already been declared" |
| Other pages (youtube.com) have none of those | Same |
| Assigning `location` navigates the page, asynchronously | Same; inside an iframe it navigates nothing |
| `history.back()` returns to the previous page | Same |
| `jsc --reset` reloads the current URL, whatever it is, and returns at once | Same |
| iOS kills the web content process; nothing answers until a reload | `kill` control; `wedge` never comes back; `freeze` answers but ignores reloads |

`tests/fake_webview/bgutils_stub.js` replaces the vendored BgUtils bundle in the PO token
tests. It has the same shape and is just as asynchronous, so `ashell_pot.js` runs unchanged
on top of it.

### What the fake cannot tell you

These rest on assumptions about the device, which the fake builds in rather than tests:

- Whether `jsc.writeFile` finishes before `jsc` returns (the fake writes synchronously).
- Where `println` output goes when `jsc` is run from Python, and what exit status `jsc` gives
  for an error or an unsupported completion value.
- How a sandboxed iframe, BotGuard, `fetch` to Google, and WKWebView's real navigation and
  history behave. The fake has none of WebKit.
- Whether a real YouTube player solves, and which formats each client gets today.
- How a Shortcut run differs from a terminal run, such as a page that is still loading.

## On the device

a-Shell copes badly with multi-line commands, pipes, redirection and long paths. Every
device command is therefore **one line**, ideally a `python -c` one-liner. Nothing may assume
where the repository was cloned: first `cd` into your clone, which is one command on its own,
then run commands that import from the current directory. `diag.py` copies its output into
`diag.txt` next to itself, so nothing needs redirecting. You can read that file with
`cat diag.txt` or open it in the Files app.

Solver, PO tokens, formats, and which page the webview ends up on:

```sh
python -c "import diag; diag.main()"
```

Sending the page away from `wasm.html` on purpose, then checking that a reset brings it back:

```sh
python -c "import diag; diag.check_recovery()"
```

A full download through `main.py`, without the pip upgrade or the return to Shortcuts that
`yt-dl.sh` adds:

```sh
python -c "import main; main.main(['https://www.youtube.com/watch?v=jNQXAC9IVRw'])"
```

The same download with the webview treated as dead from the start, which must still succeed
through the `visionos` client:

```sh
python -c "import ashell_webview, main; ashell_webview._dead = True; main.main(['https://www.youtube.com/watch?v=jNQXAC9IVRw'])"
```

The cold-start case, where a Shortcut runs before a-Shell's page has loaded, has no command:
close a-Shell completely, then run the Shortcut from the Share Sheet.
