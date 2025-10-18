#!/usr/bin/env dash
pip install --user --upgrade yt-dlp
python ~/Documents/yt-dlp-ios.git/main.py "$1"
open shortcuts://
