# yt-dlp-ios

A script to download videos using `yt-dlp` on iOS via the [a-Shell app](https://apps.apple.com/us/app/a-shell/id1473805438).

While this project is packaged for use with a-Shell on iOS, the core Python script (`main.py`) is cross-platform and will run on any system where Python and [yt-dlp](https://github.com/yt-dlp/yt-dlp) are supported. The script is configured to download the best available format (up to 1080p, 60fps) and save it as an `.mkv` file in your `Documents` folder.

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
