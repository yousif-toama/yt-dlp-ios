#!/usr/bin/env dash
pip install --user --upgrade "yt-dlp[curl-cffi]"
python ~/Documents/yt-dlp-ios.git/main.py "$1"
open shortcuts://
