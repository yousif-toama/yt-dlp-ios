#!/usr/bin/env dash
set -eu

# yt-dlp-ejs carries the JavaScript challenge solver scripts YouTube needs. Install it
# directly rather than via the yt-dlp[default] extra, which pulls in pycryptodomex -- a C
# extension that cannot be installed on a-Shell.
pip install --user --upgrade "yt-dlp[curl-cffi]" yt-dlp-ejs || echo 'Warning: could not update yt-dlp, using the installed version.'

python ~/Documents/yt-dlp-ios.git/main.py "$1"
open shortcuts://
