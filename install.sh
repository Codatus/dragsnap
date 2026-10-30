#!/bin/sh
# Installs DragSnap for the current user.
# ./install.sh              install and start
# ./install.sh --uninstall  remove everything again
set -e
cd "$(dirname "$0")"

BIN="$HOME/.local/bin/dragsnap"
UNIT="$HOME/.config/systemd/user/dragsnap.service"
AUTOSTART="$HOME/.config/autostart/dragsnap-start.desktop"

if [ "$1" = "--uninstall" ]; then
    systemctl --user stop dragsnap.service 2>/dev/null || true
    rm -f "$BIN" "$UNIT" "$AUTOSTART"
    systemctl --user daemon-reload
    echo "DragSnap removed."
    exit 0
fi

for p in python3 xrandr; do
    command -v "$p" >/dev/null || { echo "Missing: $p (sudo apt install python3 x11-xserver-utils)"; exit 1; }
done
for lib in Xi Xfixes Xtst; do
    python3 -c "import ctypes.util, sys; sys.exit(not ctypes.util.find_library('$lib'))" ||
        { echo "Missing: lib$lib (sudo apt install libxi6 libxfixes3 libxtst6)"; exit 1; }
done

install -Dm755 dragsnap.py "$BIN"
install -Dm644 dragsnap.service "$UNIT"
install -Dm644 dragsnap-start.desktop "$AUTOSTART"
systemctl --user daemon-reload
systemctl --user import-environment DISPLAY XAUTHORITY
systemctl --user restart dragsnap.service
systemctl --user --no-pager status dragsnap.service | head -3
