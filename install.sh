#!/usr/bin/env dash
# Set -e is recommended for robust shell scripting
set -e

# Get the script's absolute directory using $0
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR"

# Install required Python packages
pip install --user --upgrade -r requirements.txt
