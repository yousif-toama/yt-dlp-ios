#!/usr/bin/env dash
set -eu

DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR"

# See yt-dl.sh for why the version check is disabled.
pip install --user --upgrade --disable-pip-version-check -r requirements.txt
