#!/usr/bin/env dash
set -eu

# yt-dlp-ejs carries the JavaScript challenge solver scripts YouTube needs. Install it
# directly rather than via the yt-dlp[default] extra, which pulls in pycryptodomex -- a C
# extension that cannot be installed on a-Shell.
# --disable-pip-version-check: pip's "a new release is available" check calls os.lstat on a
# 'python3' executable that does not exist in a-Shell, and the resulting FileNotFoundError is
# reported as a long, harmless logging traceback on every run.
pip install --user --upgrade --disable-pip-version-check "yt-dlp[curl-cffi]" yt-dlp-ejs ||
	echo 'Warning: could not update yt-dlp, using the installed version.'

python ~/Documents/yt-dlp-ios.git/main.py "$1"
open shortcuts://
