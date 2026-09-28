#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew가 없어 공식 설치 프로그램을 내려받습니다."
  installer="$(mktemp)"
  trap 'rm -f "$installer"' EXIT
  curl -fsSLo "$installer" https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh
  /bin/bash "$installer"

  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
fi

if ! command -v python3 >/dev/null 2>&1; then
  brew install python
fi

if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
  python_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  brew install "python-tk@${python_version}"
fi

echo
echo "Ollama, 온도 센서, 모델을 확인하고 필요한 항목을 준비합니다."
python3 hotpack.py --prepare-only

log_file="${TMPDIR:-/tmp}/hotpack-gui.log"
nohup python3 gui.py </dev/null >"$log_file" 2>&1 &
gui_pid=$!
disown "$gui_pid" 2>/dev/null || true

sleep 1
if kill -0 "$gui_pid" 2>/dev/null; then
  echo "GUI가 시작되었습니다. 터미널 창을 닫습니다."
  osascript -e 'tell application "Terminal" to close front window' >/dev/null 2>&1 &
  exit 0
fi

echo "GUI 시작에 실패했습니다. 로그: $log_file"
tail -n 30 "$log_file"
read -r -p "Enter를 누르면 창을 닫습니다."
exit 1
