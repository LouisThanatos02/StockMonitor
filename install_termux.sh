#!/data/data/com.termux/files/usr/bin/bash
set -e
pkg update -y
pkg upgrade -y
pkg install -y python python-numpy python-pandas
cd "$(dirname "$0")"
python -m pip install --upgrade pip
pip install Flask yfinance

echo
 echo "Install finished. Start with: bash start.sh"
