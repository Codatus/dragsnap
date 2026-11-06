# DragSnap

**Snap windows to the screen edges while dragging – for Openbox and other X11
window managers that cannot do it themselves.**

[Deutsche Fassung](README.de.md)

Drag a window by its title bar and touch a screen edge:

- **left or right edge:** the window fills that half of the monitor
- **top edge:** the window is maximized

It happens as soon as the pointer touches the edge – no need to let go first.

- Works with maximized windows too: drag one to the left edge and it becomes
  the left half.
- With several monitors, only outer edges count. The edge between two monitors
  is left alone, so you can still drag windows across.
- Panels and taskbars are respected, the window fills exactly the free area.
- Resizing a window by its border, selecting text or dragging files does not
  trigger anything.

## Requirements

- an X11 session (Wayland compositors handle this themselves)
- **Openbox** (LXQt, Lubuntu, LXDE). DragSnap recognises a move by the way
  Openbox grabs the pointer for it (on its `_NET_SUPPORTING_WM_CHECK` window).
  Other window managers without edge snapping are untested and may grab
  differently – feedback is welcome.
- Python 3, `xrandr`, and libXi, libXfixes, libXtst

On Debian/Ubuntu:

```sh
sudo apt install python3 x11-xserver-utils libxi6 libxfixes3 libxtst6
```

Tested on Ubuntu 22.04 with LXQt and Openbox 3.6, two monitors.

## Installation

```sh
git clone https://github.com/Codatus/dragsnap.git
cd dragsnap
./install.sh
```

Without git:

```sh
wget -qO- https://github.com/Codatus/dragsnap/archive/refs/heads/main.tar.gz | tar xz && cd dragsnap-main && ./install.sh
```

The script installs for the current user:

| File | Purpose |
|---|---|
| `~/.local/bin/dragsnap` | the program |
| `~/.config/systemd/user/dragsnap.service` | systemd user service, restarts after crashes |
| `~/.config/autostart/dragsnap-start.desktop` | starts the service at login |

The autostart entry is needed because many lightweight desktops (LXQt, Xfce, …)
never activate systemd's `graphical-session.target`.

To remove it:

```sh
./install.sh --uninstall
```

## How it works

While a window manager moves a window, it grabs the pointer – other programs
see no mouse events. Raw XInput2 events are delivered anyway. While the left
button is held, DragSnap checks whether the pointer is at an outer screen edge
and the window manager is moving a window: it has then grabbed the pointer on
its own helper window, and either shows its move cursor or the window frame has
moved as a whole. Applications that drag inside their own window – rotating a
map or Street View, for example – never grab the pointer that way, so they do
not trigger snapping. DragSnap only listens to the X server's notifications; it
never grabs the pointer or keyboard itself.

When both conditions hold, DragSnap ends the move with a synthetic Return key –
the window manager's own key for "finish moving" – and sends the new geometry
as standard EWMH requests.

DragSnap does not poll: without a pressed mouse button it only receives button
events, and only reads the pointer position while a button is held.

## Troubleshooting

```sh
systemctl --user status dragsnap
dragsnap -v     # in the foreground, with log output
```

## Limitations

- Snapping ends the drag. To put the window somewhere else, grab it again.
- If your window manager uses a different move cursor than `fleur`/`move`,
  maximized windows are not recognised while being dragged (normal windows
  still are).
- Window managers that snap on their own (Xfwm, KWin, Mutter, Muffin, …) do not
  need DragSnap – both would act at the same time.

## License

GPL-3.0-or-later, see [LICENSE](LICENSE).
