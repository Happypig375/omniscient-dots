#!/usr/bin/env python3
"""Fit Orca's visible X11 window to its monitor, leaving desktop panels visible."""

import argparse
import ctypes as C
from ctypes.util import find_library
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time


class WindowError(Exception):
    pass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self):
        return self.x + self.width

    @property
    def bottom(self):
        return self.y + self.height

    def overlap(self, other):
        return max(0, min(self.right, other.right) - max(self.x, other.x)) * max(
            0, min(self.bottom, other.bottom) - max(self.y, other.y))

    def describe(self):
        return f'{self.width}x{self.height} at ({self.x}, {self.y})'


@dataclass(frozen=True)
class OrcaWindow:
    identifier: int
    pid: int | None
    profile_match: bool | None
    current_desktop: bool
    bounds: Rect


def query(*arguments):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=5,
                                env={**os.environ, 'LC_ALL': 'C'})
        return result.stdout if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def monitor_rects(output, fallback):
    monitors = []
    for line in output.splitlines():
        match = re.match(r'\s*\d+:\s+\S+\s+(\d+)(?:/\d+)?x(\d+)(?:/\d+)?([+-]\d+)([+-]\d+)', line)
        if match:
            width, height, x, y = map(int, match.groups())
            if width > 0 and height > 0:
                monitors.append(Rect(x, y, width, height))
    return monitors or [fallback]


def usable_area(monitor, root, struts):
    """Deduct real panel reservations; WM maximization margins are not panels."""
    x, y, right, bottom = monitor.x, monitor.y, monitor.right, monitor.bottom
    for values in struts:
        if len(values) == 4:
            values = [*values, root.y, root.bottom - 1, root.y, root.bottom - 1,
                      root.x, root.right - 1, root.x, root.right - 1]
        if len(values) != 12 or any(type(value) is not int or value < 0 for value in values):
            continue
        left, reserved_right, top, reserved_bottom, ly1, ly2, ry1, ry2, tx1, tx2, bx1, bx2 = values
        if left and ly1 <= ly2 and ly1 < monitor.bottom and ly2 >= monitor.y:
            x = max(x, root.x + left)
        if reserved_right and ry1 <= ry2 and ry1 < monitor.bottom and ry2 >= monitor.y:
            right = min(right, root.right - reserved_right)
        if top and tx1 <= tx2 and tx1 < monitor.right and tx2 >= monitor.x:
            y = max(y, root.y + top)
        if reserved_bottom and bx1 <= bx2 and bx1 < monitor.right and bx2 >= monitor.x:
            bottom = min(bottom, root.bottom - reserved_bottom)
    if right <= x or bottom <= y:
        raise WindowError('Desktop panels leave no usable area on this monitor.')
    return Rect(x, y, right - x, bottom - y)


def visible_rect(client, frame, shadow):
    left, right, top, bottom = frame
    sl, sr, st, sb = shadow
    return Rect(client.x - left + sl, client.y - top + st,
                client.width + left + right - sl - sr, client.height + top + bottom - st - sb)


class MessageData(C.Union):
    _fields_ = [('b', C.c_char * 20), ('s', C.c_short * 10), ('l', C.c_long * 5)]


class ClientMessage(C.Structure):
    _fields_ = [('type', C.c_int), ('serial', C.c_ulong), ('send_event', C.c_int),
                ('display', C.c_void_p), ('window', C.c_ulong), ('message_type', C.c_ulong),
                ('format', C.c_int), ('data', MessageData)]


class Event(C.Union):
    _fields_ = [('client', ClientMessage), ('padding', C.c_long * 24)]


class X11:
    def __init__(self):
        self.lib = C.CDLL(find_library('X11') or 'libX11.so.6')
        signatures = {
            'XOpenDisplay': (C.c_void_p, [C.c_char_p]),
            'XDefaultRootWindow': (C.c_ulong, [C.c_void_p]),
            'XInternAtom': (C.c_ulong, [C.c_void_p, C.c_char_p, C.c_int]),
            'XGetWindowProperty': (C.c_int, [C.c_void_p, C.c_ulong, C.c_ulong, C.c_long, C.c_long,
                                           C.c_int, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_int),
                                           C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.c_void_p)]),
            'XGetGeometry': (C.c_int, [C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_int),
                                     C.POINTER(C.c_int), C.POINTER(C.c_uint), C.POINTER(C.c_uint),
                                     C.POINTER(C.c_uint), C.POINTER(C.c_uint)]),
            'XTranslateCoordinates': (C.c_int, [C.c_void_p, C.c_ulong, C.c_ulong, C.c_int, C.c_int,
                                              C.POINTER(C.c_int), C.POINTER(C.c_int), C.POINTER(C.c_ulong)]),
            'XSendEvent': (C.c_int, [C.c_void_p, C.c_ulong, C.c_int, C.c_long, C.POINTER(Event)]),
            'XFlush': (C.c_int, [C.c_void_p]),
            'XMapWindow': (C.c_int, [C.c_void_p, C.c_ulong]),
            'XFree': (C.c_int, [C.c_void_p]),
            'XCloseDisplay': (C.c_int, [C.c_void_p]),
            'XSetErrorHandler': (C.c_void_p, [C.c_void_p]),
        }
        for name, (restype, argtypes) in signatures.items():
            function = getattr(self.lib, name)
            function.restype, function.argtypes = restype, argtypes
        self.display = self.lib.XOpenDisplay(None)
        if not self.display:
            raise WindowError('Cannot connect to the X11 desktop display.')
        # Windows may disappear between listing them and reading their properties.
        self.error_handler = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(lambda *_: 0)
        self.previous_handler = self.lib.XSetErrorHandler(C.cast(self.error_handler, C.c_void_p))
        self.root = self.lib.XDefaultRootWindow(self.display)
        self.atoms = {}

    def close(self):
        self.lib.XCloseDisplay(self.display)
        self.lib.XSetErrorHandler(self.previous_handler)

    def atom(self, name):
        if name not in self.atoms:
            self.atoms[name] = self.lib.XInternAtom(self.display, name.encode(), 0)
        return self.atoms[name]

    def property(self, window, name):
        kind, count, remaining = C.c_ulong(), C.c_ulong(), C.c_ulong()
        format_ = C.c_int()
        data = C.c_void_p()
        status = self.lib.XGetWindowProperty(self.display, window, self.atom(name), 0, 4096, 0, 0,
                                            C.byref(kind), C.byref(format_), C.byref(count),
                                            C.byref(remaining), C.byref(data))
        try:
            if status or not data.value:
                return []
            if format_.value == 8:
                return C.string_at(data, count.value)
            if format_.value == 32:
                # Xlib expands wire values to C longs, which can sign-extend on LP64.
                # Keep CARDINAL/32 values such as the all-workspaces marker unsigned.
                return [value & 0xffffffff for value in C.cast(data, C.POINTER(C.c_ulong))[:count.value]]
            return []
        finally:
            if data.value:
                self.lib.XFree(data)

    def geometry(self, window):
        root, child = C.c_ulong(), C.c_ulong()
        x, y = C.c_int(), C.c_int()
        width, height, border, depth = C.c_uint(), C.c_uint(), C.c_uint(), C.c_uint()
        if not self.lib.XGetGeometry(self.display, window, C.byref(root), C.byref(x), C.byref(y),
                                    C.byref(width), C.byref(height), C.byref(border), C.byref(depth)):
            raise WindowError('The Orca window is no longer available.')
        if window != self.root and not self.lib.XTranslateCoordinates(
                self.display, window, self.root, 0, 0, C.byref(x), C.byref(y), C.byref(child)):
            raise WindowError('Cannot read the Orca window position.')
        return Rect(x.value, y.value, width.value, height.value)

    def extents(self, window, name):
        values = self.property(window, name)
        return values if len(values) == 4 and all(0 <= value < 1000 for value in values) else [0] * 4

    def visible(self, window):
        return visible_rect(self.geometry(window), self.extents(window, '_NET_FRAME_EXTENTS'),
                            self.extents(window, '_GTK_FRAME_EXTENTS'))

    def windows(self):
        return self.property(self.root, '_NET_CLIENT_LIST')

    def on_desktop(self, window):
        desktop = self.property(window, '_NET_WM_DESKTOP')
        current = self.property(self.root, '_NET_CURRENT_DESKTOP') or [0]
        return not desktop or desktop[0] in (current[0], 0xffffffff)

    def area(self, window):
        root = self.geometry(self.root)
        bounds = self.visible(window)
        monitors = monitor_rects(query('xrandr', '--listactivemonitors'), root)
        monitor = max(monitors, key=bounds.overlap)
        struts = []
        for panel in self.windows():
            if self.on_desktop(panel):
                # Honor the whole reservation, including floating-dock padding.
                strut = self.property(panel, '_NET_WM_STRUT_PARTIAL') or self.property(panel, '_NET_WM_STRUT')
                if strut:
                    struts.append(strut)
        return usable_area(monitor, root, struts)

    def send(self, window, name, values):
        event = Event()
        event.client.type = 33  # ClientMessage
        event.client.display = self.display
        event.client.window = window
        event.client.message_type = self.atom(name)
        event.client.format = 32
        for index, value in enumerate(values):
            event.client.data.l[index] = value
        if not self.lib.XSendEvent(self.display, self.root, 0, (1 << 19) | (1 << 20), C.byref(event)):
            raise WindowError('The window manager did not accept the window request.')
        self.lib.XFlush(self.display)

    def unmaximize(self, window):
        self.send(window, '_NET_WM_STATE', [0, self.atom('_NET_WM_STATE_MAXIMIZED_VERT'),
                                          self.atom('_NET_WM_STATE_MAXIMIZED_HORZ'), 2, 0])

    def restore(self, window):
        if (not self.on_desktop(window)
                or self.atom('_NET_WM_STATE_HIDDEN') in self.property(window, '_NET_WM_STATE')):
            self.lib.XMapWindow(self.display, window)
            self.send(window, '_NET_ACTIVE_WINDOW', [2, 0, 0, 0, 0])

    def normal(self, window):
        states = self.property(window, '_NET_WM_STATE')
        return self.on_desktop(window) and not any(self.atom(name) in states for name in (
            '_NET_WM_STATE_HIDDEN', '_NET_WM_STATE_MAXIMIZED_VERT', '_NET_WM_STATE_MAXIMIZED_HORZ'))

    def resize(self, window, bounds):
        # StaticGravity, all four geometry fields, and a window-controller source.
        # https://specifications.freedesktop.org/wm/latest/ar01s04.html
        self.send(window, '_NET_MOVERESIZE_WINDOW', [10 | (15 << 8) | (2 << 12),
                                                   bounds.x, bounds.y, bounds.width, bounds.height])


def profile_matches(pid, profile, proc=Path('/proc')):
    """Return None when process metadata cannot establish a profile identity."""
    if pid is None:
        return None
    try:
        environment = dict(entry.split(b'=', 1) for entry in (proc / str(pid) / 'environ').read_bytes().split(b'\0')
                           if b'=' in entry)
        configured = environment.get(b'ORCA_USER_DATA_PATH')
        if configured:
            return Path(os.fsdecode(configured)).resolve() == profile.resolve()
        image = environment.get(b'APPIMAGE')
        if image:
            return Path(os.fsdecode(image)).resolve() == (profile.parents[1] / 'orca.AppImage').resolve()
        return None
    except (OSError, IndexError, ValueError):
        return None


def is_orca_class(names):
    return isinstance(names, bytes) and any(name.lower() in (b'orca', b'orca-ide') for name in names.split(b'\0'))


def inspect_window(display, window, profile, proc=Path('/proc')):
    names = display.property(window, 'WM_CLASS')
    if not names:
        raise WindowError(f'Window 0x{window:x} has no WM_CLASS; it may have closed or its ID changed.')
    if not is_orca_class(names):
        raise WindowError(f'Window 0x{window:x} does not identify Orca (WM_CLASS={names!r}).')
    types = display.property(window, '_NET_WM_WINDOW_TYPE')
    if types and display.atom('_NET_WM_WINDOW_TYPE_NORMAL') not in types:
        raise WindowError(f'Orca window 0x{window:x} has a non-main window type ({types}).')
    transient = display.property(window, 'WM_TRANSIENT_FOR')
    if transient and transient[0]:
        raise WindowError(f'Orca window 0x{window:x} is transient for window 0x{transient[0]:x}.')
    pid_property = display.property(window, '_NET_WM_PID')
    pid = pid_property[0] if pid_property and pid_property[0] > 0 else None
    try:
        bounds = display.visible(window)
    except WindowError as error:
        raise WindowError(f'Window 0x{window:x}: {error}') from error
    return OrcaWindow(window, pid, profile_matches(pid, profile, proc), display.on_desktop(window), bounds)


def window_candidates(display, profile, proc=Path('/proc')):
    candidates = []
    for window in display.windows():
        try:
            candidates.append(inspect_window(display, window, profile, proc))
        except WindowError:
            continue
    return candidates


def select_window(candidates):
    matching = [candidate for candidate in candidates if candidate.profile_match is True]
    if not matching:
        unknown = [candidate for candidate in candidates if candidate.profile_match is None]
        if len(unknown) == 1:
            matching = unknown
        elif unknown and all(candidate.pid is not None for candidate in unknown) and len({c.pid for c in unknown}) == 1:
            matching = unknown  # Main window and popouts belonging to one Orca process.
    if not matching:
        return None
    current = [candidate for candidate in matching if candidate.current_desktop]
    return max(current or matching, key=lambda candidate: candidate.bounds.width * candidate.bounds.height).identifier


def find_window(display, profile, proc=Path('/proc'), *, requested=None):
    if requested is not None:
        # An explicit X11 ID can be inspected even when the WM omits it from its list.
        return inspect_window(display, requested, profile, proc).identifier
    return select_window(window_candidates(display, profile, proc))


def parse_window_id(value):
    try:
        identifier = int(value, 16 if value.lower().startswith('0x') else 10)
    except ValueError:
        raise argparse.ArgumentTypeError('Use a hexadecimal or decimal X11 window ID.') from None
    if not 0 < identifier <= 0xffffffff:
        raise argparse.ArgumentTypeError('The X11 window ID must be between 1 and 0xffffffff.')
    return identifier


def fit_window(display, window, *, sleep=time.sleep):
    display.restore(window)
    display.unmaximize(window)
    sleep(0.2)
    target = display.area(window)
    client, visible = display.geometry(window), display.visible(window)
    requested = Rect(client.x + target.x - visible.x, client.y + target.y - visible.y,
                     client.width + target.width - visible.width, client.height + target.height - visible.height)
    for _ in range(5):
        display.unmaximize(window)
        display.resize(window, requested)
        sleep(0.2)
        actual = display.visible(window)
        if actual == target:
            # Check again after Orca's startup handlers and the WM have settled.
            sleep(0.6)
            if display.visible(window) == target and display.area(window) == target and display.normal(window):
                return target
        target = display.area(window)
        requested = Rect(requested.x + target.x - actual.x, requested.y + target.y - actual.y,
                         requested.width + target.width - actual.width, requested.height + target.height - actual.height)
    raise WindowError(f'The window manager kept Orca at {display.visible(window).describe()}; '
                      f'the available area is {target.describe()}.')


def diagnose(display, profile, requested=None):
    root = display.geometry(display.root)
    print('Desktop pixels: ' + root.describe())
    print('Reported maximization work area: ' + str(display.property(display.root, '_NET_WORKAREA')))
    print('Active monitors:\n' + (query('xrandr', '--listactivemonitors').strip() or root.describe()))
    print(query('xprop', '-root', '-notype', '_NET_CLIENT_LIST', '_NET_CLIENT_LIST_STACKING').strip())
    for window in display.windows():
        strut = display.property(window, '_NET_WM_STRUT_PARTIAL') or display.property(window, '_NET_WM_STRUT')
        if strut and display.on_desktop(window):
            print(f'Panel 0x{window:x} reservations: {strut}')
            try:
                print('Panel window bounds: ' + display.geometry(window).describe())
            except WindowError:
                pass
    inspected = list(dict.fromkeys([*display.windows(), *([] if requested is None else [requested])]))
    for window in inspected:
        if window != requested and not is_orca_class(display.property(window, 'WM_CLASS')):
            continue
        print(f'Raw properties for window 0x{window:x}:')
        print(query('xprop', '-id', hex(window), '-notype', 'WM_CLASS', '_NET_WM_WINDOW_TYPE',
                    'WM_TRANSIENT_FOR', '_NET_WM_PID', '_NET_WM_DESKTOP', '_NET_WM_STATE').strip())
        try:
            inspect_window(display, window, profile)
        except WindowError as error:
            print('Selection: ' + str(error))
    candidates = window_candidates(display, profile)
    for candidate in candidates:
        identity = {True: 'matches this setup', False: 'different profile', None: 'metadata unavailable'}[candidate.profile_match]
        desktop = 'current workspace' if candidate.current_desktop else 'another workspace'
        print(f'Orca candidate 0x{candidate.identifier:x}: PID={candidate.pid or "unavailable"}; '
              f'profile={identity}; {desktop}; {candidate.bounds.describe()}')
    window = find_window(display, profile, requested=requested) if requested is not None else select_window(candidates)
    if window is None:
        if candidates:
            print('Select a window with ./setup.sh desktop fit --window <window-id>.')
        else:
            print('No Orca main window found on this display.')
        return
    target, actual = display.area(window), display.visible(window)
    print(f'Orca window: 0x{window:x}')
    print('Client bounds: ' + display.geometry(window).describe())
    print('Visible bounds: ' + actual.describe())
    print('Available area above panels: ' + target.describe())
    print(f'Unused pixels: left={actual.x-target.x}, top={actual.y-target.y}, '
          f'right={target.right-actual.right}, bottom={target.bottom-actual.bottom}')
    print('Window frame extents: ' + str(display.extents(window, '_NET_FRAME_EXTENTS')))
    print('Invisible GTK frame extents: ' + str(display.extents(window, '_GTK_FRAME_EXTENTS')))
    print(query('xprop', '-id', hex(window), '-notype', '_NET_WM_STATE', 'WM_NORMAL_HINTS').strip())


def main():
    parser = argparse.ArgumentParser(prog='./setup.sh desktop', description=__doc__)
    parser.add_argument('--profile', type=Path, default=Path(__file__).resolve().parent / 'bin/orca.AppImage.config/orca',
                        help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest='command', required=True)
    fitting = commands.add_parser('fit', help='Fit the visible Orca window above desktop panels.')
    fitting.add_argument('--timeout', metavar='<seconds>', type=float, default=20,
                         help='Wait for the window to appear (default: 20 seconds, maximum: 60).')
    diagnosing = commands.add_parser('diagnose', help='Report desktop, panel, and Orca window geometry.')
    for command in (fitting, diagnosing):
        command.add_argument('--window', metavar='<window-id>', type=parse_window_id,
                             help='Select an Orca window explicitly, using its hexadecimal or decimal X11 ID.')
    args = parser.parse_args()
    if args.command == 'fit' and (not math.isfinite(args.timeout) or not 0 < args.timeout <= 60):
        parser.error('--timeout must be greater than zero and at most 60 seconds.')
    display = X11()
    try:
        if args.command == 'diagnose':
            diagnose(display, args.profile, args.window)
            return
        deadline = time.monotonic() + args.timeout
        while (window := find_window(display, args.profile, requested=args.window)) is None:
            if time.monotonic() >= deadline:
                candidates = window_candidates(display, args.profile)
                if candidates:
                    identifiers = ', '.join(f'0x{candidate.identifier:x}' for candidate in candidates)
                    raise WindowError(f'Orca windows {identifiers} could not be matched automatically. '
                                      'Select one with ./setup.sh desktop fit --window <window-id>.')
                raise WindowError('No Orca main window appeared on this display before the deadline.')
            time.sleep(0.2)
        actual = fit_window(display, window)
        print(f'Orca visible window: {actual.describe()}; fitted and verified above desktop panels.')
    finally:
        display.close()


if __name__ == '__main__':
    try:
        main()
    except (WindowError, OSError) as error:
        print(f'Orca window error: {error}', file=sys.stderr)
        sys.exit(1)
