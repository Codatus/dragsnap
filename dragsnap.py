#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Carl Kliefert
"""DragSnap: snap windows to screen edges while dragging, for Openbox and
other X11 window managers that cannot do it themselves.

Drag a window by its title bar and touch an outer screen edge:
  - left or right edge: the window fills that half of the monitor
  - top edge: the window is maximized
It happens as soon as the pointer touches the edge, not only on release.
Edges between two monitors are left alone, so windows can still be dragged
across.

How it works: raw XInput2 events are delivered even while the window manager
grabs the pointer for a move. While the left button is held, DragSnap checks
on every motion whether the pointer is at an outer edge and the window
manager is moving a window: it has then actively grabbed the pointer (the X
server reports that as an enter event with grab mode on the grab window,
for Openbox its supporting window;
applications dragging inside their window, e.g. rotating a map, do not), and
either shows its move cursor ("fleur") or the window frame has moved as a
whole. DragSnap only listens, it never grabs anything itself. It then ends
the move with a synthetic Return key and sends the new geometry as EWMH
requests (_NET_WM_STATE, _NET_MOVERESIZE_WINDOW), respecting panels.

Usage: dragsnap.py [-v]
"""
import ctypes
import ctypes.util
import os
import re
import signal
import subprocess
import sys
import time

__version__ = "0.1.1"

LEFT_BUTTON = 1
EDGE = 2  # pixels counted as "at the edge"
MONITOR_CACHE = 5  # seconds
MOVE_CURSORS = {b"fleur", b"move", b"all-scroll"}

GENERIC_EVENT = 35
CLIENT_MESSAGE = 33
XI_ALL_MASTER_DEVICES = 1
XI_ENTER, XI_LEAVE = 7, 8
RAW_BUTTON_PRESS, RAW_BUTTON_RELEASE, RAW_MOTION = 15, 16, 17
XI_NOTIFY_GRAB, XI_NOTIFY_UNGRAB = 1, 2
SUBSTRUCTURE_MASK = (1 << 19) | (1 << 20)  # SubstructureNotify | SubstructureRedirect
NET_WM_STATE_REMOVE, NET_WM_STATE_ADD = 0, 1
# _NET_MOVERESIZE_WINDOW: NorthWest gravity, x/y/width/height given, source: pager
MOVERESIZE_FLAGS = 1 | (0xF << 8) | (2 << 12)
XA_ATOM, XA_CARDINAL, XA_WINDOW = 4, 6, 33

if "--version" in sys.argv[1:]:
    print("dragsnap", __version__)
    sys.exit(0)

VERBOSE = "-v" in sys.argv[1:]


def log(*parts):
    if VERBOSE:
        print(*parts, file=sys.stderr, flush=True)


# --- Xlib (only what is needed, via ctypes, no extra packages) ---

class XGenericEventCookie(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("extension", ctypes.c_int), ("evtype", ctypes.c_int),
                ("cookie", ctypes.c_uint), ("data", ctypes.c_void_p)]


class XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xcookie", XGenericEventCookie),
                ("pad", ctypes.c_long * 24)]


class XIRawEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("extension", ctypes.c_int), ("evtype", ctypes.c_int),
                ("time", ctypes.c_ulong), ("deviceid", ctypes.c_int),
                ("sourceid", ctypes.c_int), ("detail", ctypes.c_int)]


class XIEnterEvent(ctypes.Structure):
    _fields_ = XIRawEvent._fields_ + [
        ("root", ctypes.c_ulong), ("event", ctypes.c_ulong), ("child", ctypes.c_ulong),
        ("root_x", ctypes.c_double), ("root_y", ctypes.c_double),
        ("event_x", ctypes.c_double), ("event_y", ctypes.c_double),
        ("mode", ctypes.c_int)]


class XClientMessageEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int), ("data", ctypes.c_long * 5)]


class XFixesCursorImage(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short),
                ("width", ctypes.c_ushort), ("height", ctypes.c_ushort),
                ("xhot", ctypes.c_ushort), ("yhot", ctypes.c_ushort),
                ("cursor_serial", ctypes.c_ulong), ("pixels", ctypes.c_void_p),
                ("atom", ctypes.c_ulong), ("name", ctypes.c_char_p)]


class XIEventMask(ctypes.Structure):
    _fields_ = [("deviceid", ctypes.c_int), ("mask_len", ctypes.c_int),
                ("mask", ctypes.POINTER(ctypes.c_ubyte))]


IO_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


@IO_ERROR_HANDLER
def connection_lost(dpy):
    # The X server went away, usually at logout. That is a normal end, not a
    # crash, so exit with 0 and let systemd not restart the service.
    log("X connection lost, exiting")
    os._exit(0)


@ERROR_HANDLER
def ignore_error(dpy, err):
    # e.g. BadWindow when a window was closed in the meantime
    return 0


def library(name, fallback):
    return ctypes.CDLL(ctypes.util.find_library(name) or fallback)


class X:
    def __init__(self):
        self.xlib = xlib = library("X11", "libX11.so.6")
        self.xi = xi = library("Xi", "libXi.so.6")
        self.xfixes = xfixes = library("Xfixes", "libXfixes.so.3")
        self.xtst = xtst = library("Xtst", "libXtst.so.6")
        p, i, u, ul = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong
        P = ctypes.POINTER
        xlib.XOpenDisplay.restype = p
        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XDefaultRootWindow.restype = ul
        xlib.XDefaultRootWindow.argtypes = [p]
        xlib.XQueryExtension.argtypes = [p, ctypes.c_char_p] + [P(i)] * 3
        xlib.XNextEvent.argtypes = [p, P(XEvent)]
        xlib.XGetEventData.argtypes = [p, P(XGenericEventCookie)]
        xlib.XFreeEventData.argtypes = [p, P(XGenericEventCookie)]
        xlib.XFlush.argtypes = [p]
        xlib.XSetIOErrorHandler.argtypes = [IO_ERROR_HANDLER]
        xlib.XSetErrorHandler.argtypes = [ERROR_HANDLER]
        xlib.XQueryPointer.argtypes = [p, ul, P(ul), P(ul)] + [P(i)] * 4 + [P(u)]
        xlib.XGetGeometry.argtypes = [p, ul, P(ul)] + [P(i)] * 2 + [P(u)] * 4
        xlib.XInternAtom.restype = ul
        xlib.XInternAtom.argtypes = [p, ctypes.c_char_p, i]
        xlib.XSendEvent.argtypes = [p, ul, i, ctypes.c_long, P(XClientMessageEvent)]
        xlib.XGetWindowProperty.argtypes = [p, ul, ul, ctypes.c_long, ctypes.c_long, i, ul,
                                            P(ul), P(i), P(ul), P(ul), P(p)]
        xlib.XFree.argtypes = [p]
        xlib.XStringToKeysym.restype = ul
        xlib.XStringToKeysym.argtypes = [ctypes.c_char_p]
        xlib.XKeysymToKeycode.restype = ctypes.c_ubyte
        xlib.XKeysymToKeycode.argtypes = [p, ul]
        xfixes.XFixesGetCursorImage.restype = P(XFixesCursorImage)
        xfixes.XFixesGetCursorImage.argtypes = [p]
        xtst.XTestFakeKeyEvent.argtypes = [p, u, i, ul]
        xi.XIQueryVersion.argtypes = [p, P(i), P(i)]
        xi.XISelectEvents.argtypes = [p, ul, P(XIEventMask), i]

        xlib.XSetIOErrorHandler(connection_lost)
        xlib.XSetErrorHandler(ignore_error)
        self.dpy = xlib.XOpenDisplay(None)
        if not self.dpy:
            sys.exit("Cannot connect to the X server (is DISPLAY set?)")
        self.root = xlib.XDefaultRootWindow(self.dpy)

    def atom(self, name):
        return self.xlib.XInternAtom(self.dpy, name.encode(), False)

    def pointer(self):
        """(x, y, top-level window under the pointer - the frame with reparenting WMs)."""
        root, child = ctypes.c_ulong(), ctypes.c_ulong()
        rx, ry, wx, wy = (ctypes.c_int() for _ in range(4))
        mask = ctypes.c_uint()
        self.xlib.XQueryPointer(self.dpy, self.root, ctypes.byref(root), ctypes.byref(child),
                                ctypes.byref(rx), ctypes.byref(ry), ctypes.byref(wx),
                                ctypes.byref(wy), ctypes.byref(mask))
        return rx.value, ry.value, child.value

    def geometry(self, window):
        root = ctypes.c_ulong()
        x, y = ctypes.c_int(), ctypes.c_int()
        w, h, border, depth = (ctypes.c_uint() for _ in range(4))
        if not self.xlib.XGetGeometry(self.dpy, window, ctypes.byref(root), ctypes.byref(x),
                                      ctypes.byref(y), ctypes.byref(w), ctypes.byref(h),
                                      ctypes.byref(border), ctypes.byref(depth)):
            return None
        return x.value, y.value, w.value, h.value

    def property(self, window, name, type_):
        """32-bit property as a list of ints (empty if missing)."""
        actual, fmt = ctypes.c_ulong(), ctypes.c_int()
        count, after = ctypes.c_ulong(), ctypes.c_ulong()
        data = ctypes.c_void_p()
        if self.xlib.XGetWindowProperty(self.dpy, window, self.atom(name), 0, 1024, False, type_,
                                        ctypes.byref(actual), ctypes.byref(fmt),
                                        ctypes.byref(count), ctypes.byref(after),
                                        ctypes.byref(data)) != 0 or not data.value:
            return []
        try:
            if fmt.value != 32:
                return []
            # format 32 is returned as an array of C longs
            return list(ctypes.cast(data, ctypes.POINTER(ctypes.c_long))[:count.value])
        finally:
            self.xlib.XFree(data)

    def cursor_name(self):
        image = self.xfixes.XFixesGetCursorImage(self.dpy)
        if not image:
            return b""
        name = image.contents.name or b""
        self.xlib.XFree(image)
        return name

    def key(self, name):
        code = self.xlib.XKeysymToKeycode(self.dpy, self.xlib.XStringToKeysym(name.encode()))
        self.xtst.XTestFakeKeyEvent(self.dpy, code, True, 0)
        self.xtst.XTestFakeKeyEvent(self.dpy, code, False, 0)

    def send(self, window, message, *data):
        ev = XClientMessageEvent(type=CLIENT_MESSAGE, send_event=True, window=window,
                                 message_type=self.atom(message), format=32)
        ev.data[:] = list(data) + [0] * (5 - len(data))
        self.xlib.XSendEvent(self.dpy, self.root, False, SUBSTRUCTURE_MASK, ctypes.byref(ev))

    def flush(self):
        self.xlib.XFlush(self.dpy)

    def _select(self, window, *evtypes):
        mask = (ctypes.c_ubyte * 4)()
        for evtype in evtypes:
            mask[evtype >> 3] |= 1 << (evtype & 7)
        em = XIEventMask(XI_ALL_MASTER_DEVICES, len(mask), mask)
        self.xi.XISelectEvents(self.dpy, window, ctypes.byref(em), 1)

    def select(self, motion):
        """Motion and grab events are only wanted while the button is held.

        Openbox grabs the pointer for a move on its supporting window
        (_NET_SUPPORTING_WM_CHECK); the enter event announcing the grab goes
        there."""
        if motion:
            self._select(self.root, RAW_BUTTON_PRESS, RAW_BUTTON_RELEASE, RAW_MOTION)
            support = (self.property(self.root, "_NET_SUPPORTING_WM_CHECK", XA_WINDOW) or [0])[0]
            if support:
                self._select(support, XI_ENTER, XI_LEAVE)
        else:
            self._select(self.root, RAW_BUTTON_PRESS, RAW_BUTTON_RELEASE)
        self.flush()

    def raw_events(self):
        """Yields (evtype, detail, mode) for raw button and motion events, regardless
        of grabs, and for enter/leave events on the selected windows (mode tells
        whether a grab caused them)."""
        xlib, xi, dpy = self.xlib, self.xi, self.dpy
        opcode, event, error = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        if not xlib.XQueryExtension(dpy, b"XInputExtension", ctypes.byref(opcode),
                                    ctypes.byref(event), ctypes.byref(error)):
            sys.exit("X server has no XInput extension")
        # Since XI 2.1, raw events are delivered even during other clients' grabs
        major, minor = ctypes.c_int(2), ctypes.c_int(2)
        if xi.XIQueryVersion(dpy, ctypes.byref(major), ctypes.byref(minor)) != 0:
            sys.exit("XInput 2.2 is required")
        self.select(motion=False)

        ev = XEvent()
        while True:
            xlib.XNextEvent(dpy, ctypes.byref(ev))  # calls connection_lost if the server goes away
            c = ev.xcookie
            if c.type != GENERIC_EVENT or c.extension != opcode.value:
                continue
            if not xlib.XGetEventData(dpy, ctypes.byref(ev.xcookie)):
                continue
            try:
                raw = ctypes.cast(ev.xcookie.data, ctypes.POINTER(XIRawEvent)).contents
                evtype, detail, mode = raw.evtype, raw.detail, None
                if evtype in (XI_ENTER, XI_LEAVE):
                    mode = ctypes.cast(ev.xcookie.data, ctypes.POINTER(XIEnterEvent)).contents.mode
            finally:
                xlib.XFreeEventData(dpy, ctypes.byref(ev.xcookie))
            yield evtype, detail, mode


# --- Monitors, edges and work areas ---

_monitors = (0.0, [])


def monitors(refresh=False):
    """[(x, y, w, h)] of all monitors, cached for a few seconds."""
    global _monitors
    if not refresh and time.monotonic() - _monitors[0] < MONITOR_CACHE:
        return _monitors[1]
    out = subprocess.run(["xrandr", "--listmonitors"], capture_output=True, text=True).stdout
    mons = [(int(x), int(y), int(w), int(h)) for w, h, x, y in
            re.findall(r"(\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)", out)]
    _monitors = (time.monotonic(), mons)
    return mons


def monitor_at(mons, x, y):
    for m in mons:
        mx, my, w, h = m
        if mx <= x < mx + w and my <= y < my + h:
            return m
    return None


def outer_edge(x, y):
    """('left' | 'right' | 'top', monitor) if the pointer is at an edge without
    a neighbouring monitor, else None."""
    mons = monitors()
    m = monitor_at(mons, x, y)
    if not m:
        return None
    mx, my, w, h = m
    if x <= mx + EDGE - 1 and not monitor_at(mons, mx - 1, y):
        return "left", m
    if x >= mx + w - EDGE and not monitor_at(mons, mx + w, y):
        return "right", m
    if y <= my + EDGE - 1 and not monitor_at(mons, x, my - 1):
        return "top", m
    return None


def work_area(x, monitor):
    """The monitor minus panels, from the _NET_WM_STRUT(_PARTIAL) of all clients."""
    mx, my, w, h = monitor
    left, top, right, bottom = mx, my, mx + w, my + h
    _, _, sw, sh = x.geometry(x.root)
    for client in x.property(x.root, "_NET_CLIENT_LIST", XA_WINDOW):
        s = x.property(client, "_NET_WM_STRUT_PARTIAL", XA_CARDINAL)
        if len(s) < 12:
            s = x.property(client, "_NET_WM_STRUT", XA_CARDINAL)
            if len(s) < 4:
                continue
            s = s[:4] + [0, sh - 1, 0, sh - 1, 0, sw - 1, 0, sw - 1]
        sl, sr, st, sb = s[:4]
        # each strut reserves a band at a root window edge, limited to a range
        if sl and s[4] < bottom and s[5] >= top and sl > left:
            left = min(sl, right)
        if sr and s[6] < bottom and s[7] >= top and sw - sr < right:
            right = max(sw - sr, left)
        if st and s[8] < right and s[9] >= left and st > top:
            top = min(st, bottom)
        if sb and s[10] < right and s[11] >= left and sh - sb < bottom:
            bottom = max(sh - sb, top)
    return left, top, right - left, bottom - top


def snap(x, side, monitor, window=None):
    window = window or (x.property(x.root, "_NET_ACTIVE_WINDOW", XA_WINDOW) or [0])[0]
    if not window:
        return
    max_v, max_h = "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ"
    if side == "top":
        x.send(window, "_NET_WM_STATE", NET_WM_STATE_ADD, x.atom(max_v), x.atom(max_h), 2)
        return
    ax, ay, aw, ah = work_area(x, monitor)
    half = aw // 2
    fx = ax if side == "left" else ax + half
    fw = half if side == "left" else aw - half
    extents = lambda: (x.property(window, "_NET_FRAME_EXTENTS", XA_CARDINAL) or [0, 0, 0, 0])[:4]
    state = x.property(window, "_NET_WM_STATE", XA_ATOM)
    if x.atom(max_v) in state or x.atom(max_h) in state:
        maximized = extents()
        x.send(window, "_NET_WM_STATE", NET_WM_STATE_REMOVE, x.atom(max_v), x.atom(max_h), 2)
        x.flush()
        # the frame usually gets its borders back; wait for the new extents
        # (windows without decoration keep theirs, then this times out)
        deadline = time.monotonic() + 0.1
        while extents() == maximized and time.monotonic() < deadline:
            time.sleep(0.005)
    fl, fr, ft, fb = extents()
    log("tiling", hex(window), side, "in", (ax, ay, aw, ah))
    x.send(window, "_NET_MOVERESIZE_WINDOW", MOVERESIZE_FLAGS,
           fx, ay, fw - fl - fr, ah - ft - fb)


def main():
    x = X()
    pressed = None  # (frame, geometry) at button press, until snapped or released
    wm_grab = False  # the window manager grabbed the pointer during this press
    log("ready")
    for evtype, detail, mode in x.raw_events():
        if evtype == XI_ENTER and mode == XI_NOTIFY_GRAB:
            wm_grab = True
            continue
        if evtype == XI_LEAVE and mode == XI_NOTIFY_UNGRAB:
            wm_grab = False
            continue
        if evtype == RAW_BUTTON_PRESS and detail == LEFT_BUTTON:
            wm_grab = False
            _, _, frame = x.pointer()
            before = x.geometry(frame) if frame else None
            pressed = (frame, before) if before else None
            if pressed:
                monitors()  # refresh the cache now, while nothing is urgent
                x.select(motion=True)
            continue
        if evtype == RAW_BUTTON_RELEASE and detail == LEFT_BUTTON:
            if pressed:
                x.select(motion=False)
            pressed = None
            continue
        if evtype != RAW_MOTION or not pressed:
            continue
        px, py, _ = x.pointer()
        edge = outer_edge(px, py)
        if not edge:
            continue
        frame, before = pressed
        after = x.geometry(frame)
        if not after:
            continue
        # moved as a whole: both the left and the right side changed (not a resize)
        moved = after[0] != before[0] and after[0] + after[2] != before[0] + before[2]
        cursor = x.cursor_name()
        # maximized windows stay put while Openbox moves them, so only the
        # cursor tells that a move is going on
        if not moved and cursor not in MOVE_CURSORS:
            continue
        # applications may show a move cursor too (e.g. Street View in a
        # browser), but only a window manager move grabs the pointer outside
        # the application's window
        if not wm_grab:
            log("no window manager grab, not a window move:", cursor)
            continue
        log("move", hex(frame), before, "->", after, "pointer", (px, py), cursor, "edge", edge[0])
        pressed = None
        x.select(motion=False)
        # Return ends the move in the window manager; requests sent before
        # would be overwritten when the move ends
        x.key("Return")
        snap(x, *edge)
        x.flush()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    main()
