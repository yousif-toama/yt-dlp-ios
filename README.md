# yt-dlp-ios

A script to download videos using `yt-dlp` on iOS via the [a-Shell app](https://apps.apple.com/us/app/a-shell/id1473805438).

While this project is packaged for use with a-Shell on iOS, the core Python script (`main.py`) is cross-platform and will run on any system where Python and [yt-dlp](https://github.com/yt-dlp/yt-dlp) are supported. The script is configured to download the best available format (up to 1080p, 60fps) and save it as an `.mkv` file in your `Documents` folder.

`yt-dlp` is installed with the `curl-cffi` extra, which enables browser impersonation to avoid being blocked by some sites. This requires a build of `curl-cffi` that runs on iOS, available in a-Shell v2 and later.

## JavaScript runtime

Since `yt-dlp` 2025.11.12, YouTube requires an external JavaScript runtime to solve the n-sig challenges. Without one, `yt-dlp` falls back to a reduced set of formats and the 1080p60 selector above cannot be satisfied.

None of the runtimes `yt-dlp` supports natively work on iOS. Deno, Node and Bun have no iOS builds. QuickJS can be installed with `pkg install qjs`, but it is a WebAssembly command — a-Shell runs wasm on the WKWebView JavaScript engine, so invoking it as a subprocess of Python, which already occupies that thread, deadlocks and hangs forever at "downloading webpage".

Instead, `ashell_jsc.py` registers a-Shell's built-in `jsc` command (Apple's JavaScriptCore) with `yt-dlp` as a challenge solver. It is native, JIT compiled, and needs no installation. The solver scripts come from the `yt-dlp-ejs` package, which is installed alongside `yt-dlp`.

On other platforms `ashell_jsc.py` detects that `jsc` is absent and disables itself, leaving `yt-dlp` to use Deno or whatever else is installed.

`jsc` runs the script through `wasmWebView.evaluateJavaScript` (see `SceneDelegate.swift:executeJavascript`). Which routes out of the JS engine exist depends on what page that webview has loaded, and none can be assumed:

| Page loaded | Available routes |
| --- | --- |
| a-Shell's `wasm.html` | `jsc` file API, `println()`, and `console.log` (rebound to `println` at `wasm.html:141`) |
| anything else | only the script's completion value |

So `ashell_jsc.py` wraps the solver program to collect its output and then write it to a file, pass it to `println()`, *and* return it as the completion value. `evaluateJavaScript` rejects an `undefined` completion value with "a result of an unsupported type", which is why simply logging is not enough.

The wrapper is a function rather than a prelude because `wasm.html` declares `const jsc` while the solver bundle declares `var jsc` at top level; unscoped, that is a redeclaration `SyntaxError`.

yt-dlp also asks the solver to return the entire transformed player and then discards it. That is suppressed here, cutting roughly 4 MB per solve off the round trip across the webview bridge.

## iOS Installation

1.  Download the **[a-Shell](https://apps.apple.com/us/app/a-shell/id1473805438)** app from the App Store.
2.  Open a-Shell.
3.  Navigate to the `Documents` directory by typing:
    ```sh
    cd ~/Documents
    ```
4.  Clone this repository using a-Shell's built-in git tool (`lg2`):
    ```sh
    lg2 clone https://github.com/yousif-toama/yt-dlp-ios.git
    ```
5.  Run the installation script:
    ```sh
    ~/Documents/yt-dlp-ios.git/install.sh
    ```

## How to Use

There are two primary ways to run the script:

### 1. From a-Shell Terminal

You can call the script directly from the a-Shell terminal, passing the video URL as an argument:

```sh
yt-dl.sh "VIDEO_URL_HERE"
```

The script will download the video and save it to your ~/Documents folder. After the download, it will automatically open the iOS Shortcuts app, allowing you to chain it into other actions, like saving the file to your Photos.

### 2. From an iOS Shortcut
You can use this script as part of an iOS Shortcut for a one-tap download from the Share Sheet. Please note that this will transfer the downloaded file to [VLC](https://itunes.apple.com/app/apple-store/id650377962?pt=454758&ct=vodownloadpage&mt=8), so ensure that you have it installed.

[Link to iOS Shortcut](https://www.icloud.com/shortcuts/4917cfda8a3f4ddfa886781581f76a45)

## Disclaimer
This script uses yt-dlp under the hood, which supports a vast number of websites beyond YouTube.

Please be responsible and respect the Terms of Service (ToS) of any website you download from. Ensure you have the right to download and store the content.
