import argparse
import ctypes as C
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('orca_window', REPO / 'orca-window.py')
window = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = window
spec.loader.exec_module(window)
Rect = window.Rect


class PropertyLibrary:
    """Xlib property buffers, including its sign extension of 32-bit values."""
    def __init__(self, properties):
        self.properties = properties
        self.buffers = []

    def XGetWindowProperty(self, display, identifier, atom, offset, length, delete, requested_type,
                           kind, format_, count, remaining, data):
        values = self.properties.get((identifier, atom), [])
        if not values:
            return 0
        if isinstance(values, bytes):
            buffer = C.create_string_buffer(values)
            bits = 8
        else:
            buffer = (C.c_long * len(values))(*(value if value < 0x80000000 else value - 0x100000000
                                                for value in values))
            bits = 32
        self.buffers.append(buffer)
        C.cast(format_, C.POINTER(C.c_int)).contents.value = bits
        C.cast(count, C.POINTER(C.c_ulong)).contents.value = len(values)
        C.cast(data, C.POINTER(C.c_void_p)).contents.value = C.addressof(buffer)
        return 0

    def XFree(self, data):
        return 0


class X11PropertyTests(unittest.TestCase):
    def display(self, properties):
        display = window.X11.__new__(window.X11)
        display.root = 1
        display.display = None
        display.lib = PropertyLibrary(properties)
        display.atom = lambda name: name
        return display

    def test_32_bit_properties_are_unsigned_despite_xlib_sign_extension(self):
        display = self.display({
            (2, '_NET_WM_DESKTOP'): [0xffffffff],
            (2, 'CARDINALS'): [0, 88, 0x80000000, 0xffffffff],
            (2, 'WM_CLASS'): b'orca\0orca\0',
        })
        self.assertEqual(display.property(2, 'CARDINALS'), [0, 88, 0x80000000, 0xffffffff])
        self.assertEqual(display.property(2, 'WM_CLASS'), b'orca\0orca\0')
        self.assertTrue(display.on_desktop(2))

    def test_bottom_panel_on_all_workspaces_reduces_the_fit_height(self):
        display = self.display({
            (1, '_NET_CLIENT_LIST'): [2, 3],
            (1, '_NET_CURRENT_DESKTOP'): [0],
            (2, '_NET_WM_DESKTOP'): [0],
            (3, '_NET_WM_DESKTOP'): [0xffffffff],
            (3, '_NET_WM_STRUT_PARTIAL'): [0, 0, 0, 88, 0, 0, 0, 0, 0, 0, 0, 1363],
        })
        display.geometry = lambda _: Rect(0, 0, 1364, 1024)
        display.visible = lambda _: Rect(0, 0, 1364, 1024)
        with patch.object(window, 'query', return_value=' 0: +*screen 1364/360x1024/270+0+0 screen\n'):
            self.assertEqual(display.area(2), Rect(0, 0, 1364, 936))


class Desktop:
    """A WM with the geometry and maximization state reported by the user."""
    def __init__(self, *, frame=(0, 0, 0, 0), shadow=(0, 0, 0, 0), bias=(0, 0), ignore_resize=False):
        self.bounds = Rect(88, 88, 1188, 848)
        self.target = Rect(0, 0, 1364, 936)
        self.maximized = True
        self.hidden = True
        self.frame = frame
        self.shadow = shadow
        self.bias = bias
        self.ignore_resize = ignore_resize
        self.requests = []

    def restore(self, identifier):
        self.hidden = False

    def unmaximize(self, identifier):
        self.maximized = False

    def area(self, identifier):
        return self.target

    def geometry(self, identifier):
        return self.bounds

    def visible(self, identifier):
        return window.visible_rect(self.bounds, self.frame, self.shadow)

    def normal(self, identifier):
        return not self.maximized and not self.hidden

    def resize(self, identifier, requested):
        self.requests.append(requested)
        if not self.ignore_resize:
            self.bounds = Rect(requested.x + self.bias[0], requested.y + self.bias[1],
                               requested.width, requested.height)


class WindowGeometryTests(unittest.TestCase):
    def test_reported_bottom_panel_leaves_the_entire_width_available(self):
        root = Rect(0, 0, 1364, 1024)
        strut = [0, 0, 0, 88, 0, 0, 0, 0, 0, 0, 0, 1363]
        self.assertEqual(window.usable_area(root, root, [strut]), Rect(0, 0, 1364, 936))

    def test_reported_maximized_hidden_window_is_restored_and_fitted(self):
        desktop = Desktop()
        actual = window.fit_window(desktop, 42, sleep=lambda _: None)
        self.assertEqual(actual, Rect(0, 0, 1364, 936))
        self.assertEqual(desktop.visible(42), actual)
        self.assertFalse(desktop.maximized)
        self.assertFalse(desktop.hidden)

    def test_panel_on_one_monitor_does_not_reduce_the_other_monitor(self):
        root = Rect(0, 0, 2728, 1024)
        left, right = Rect(0, 0, 1364, 1024), Rect(1364, 0, 1364, 1024)
        strut = [0, 0, 0, 88, 0, 0, 0, 0, 0, 0, 1364, 2727]
        self.assertEqual(window.usable_area(left, root, [strut]), left)
        self.assertEqual(window.usable_area(right, root, [strut]), Rect(1364, 0, 1364, 936))

    def test_legacy_and_multiple_panel_reservations(self):
        root = Rect(0, 0, 1920, 1080)
        self.assertEqual(window.usable_area(root, root, [[60, 0, 32, 0], [0, 0, 0, 48]]),
                         Rect(60, 32, 1860, 1000))
        with self.assertRaises(window.WindowError):
            window.usable_area(root, root, [[1920, 0, 0, 0]])

    def test_monitor_dimensions_and_negative_positions_are_not_hard_coded(self):
        output = ('Monitors: 2\n'
                  ' 0: +DP-1 1920/510x1080/290-1920+0 DP-1\n'
                  ' 1: +*HDMI-1 2560/600x1440/340+0+0 HDMI-1\n')
        root = Rect(0, 0, 4480, 1440)
        self.assertEqual(window.monitor_rects(output, root),
                         [Rect(-1920, 0, 1920, 1080), Rect(0, 0, 2560, 1440)])
        self.assertEqual(window.monitor_rects('', root), [root])

    def test_invisible_borders_do_not_leave_visible_gaps(self):
        desktop = Desktop(shadow=(8, 8, 8, 8))
        self.assertEqual(window.fit_window(desktop, 42, sleep=lambda _: None), desktop.target)
        self.assertEqual(desktop.bounds, Rect(-8, -8, 1380, 952))
        self.assertEqual(desktop.visible(42), Rect(0, 0, 1364, 936))

    def test_visible_decorations_stay_inside_the_available_area(self):
        desktop = Desktop(frame=(4, 4, 24, 4))
        self.assertEqual(window.fit_window(desktop, 42, sleep=lambda _: None), desktop.target)
        self.assertEqual(desktop.bounds, Rect(4, 24, 1356, 908))

    def test_position_is_verified_and_corrected_after_wm_adjustments(self):
        desktop = Desktop(bias=(4, 2))
        self.assertEqual(window.fit_window(desktop, 42, sleep=lambda _: None), desktop.target)
        self.assertEqual(len(desktop.requests), 2)
        self.assertEqual(desktop.visible(42), Rect(0, 0, 1364, 936))

    def test_late_startup_maximization_is_corrected_before_reporting_success(self):
        desktop = Desktop()
        bounced = False

        def settle(seconds):
            nonlocal bounced
            if seconds == 0.6 and not bounced:
                desktop.bounds = Rect(88, 88, 1188, 848)
                desktop.maximized = True
                bounced = True

        self.assertEqual(window.fit_window(desktop, 42, sleep=settle), desktop.target)
        self.assertTrue(bounced)
        self.assertGreater(len(desktop.requests), 1)
        self.assertFalse(desktop.maximized)

    def test_ignored_resize_is_reported_as_failure(self):
        desktop = Desktop(ignore_resize=True)
        with self.assertRaisesRegex(window.WindowError, 'kept Orca at 1188x848'):
            window.fit_window(desktop, 42, sleep=lambda _: None)

    def test_actual_monitor_and_panel_geometry_override_maximization_margins(self):
        desktop = window.X11.__new__(window.X11)
        desktop.root = 0
        desktop.geometry = lambda _: Rect(0, 0, 1364, 1024)
        desktop.visible = lambda _: Rect(88, 88, 1188, 848)
        desktop.windows = lambda: [42, 43]
        desktop.on_desktop = lambda _: True
        desktop.property = lambda identifier, name: (
            [0, 0, 0, 88, 0, 0, 0, 0, 0, 0, 0, 1363]
            if identifier == 43 and name == '_NET_WM_STRUT_PARTIAL' else [])
        with patch.object(window, 'query', return_value=' 0: +*screen 1364/360x1024/270+0+0 screen\n'):
            self.assertEqual(desktop.area(42), Rect(0, 0, 1364, 936))

    def test_floating_dock_keeps_its_full_reservation_including_padding(self):
        desktop = window.X11.__new__(window.X11)
        desktop.root = 0
        desktop.atom = lambda name: name
        desktop.geometry = lambda _: Rect(0, 0, 1364, 1024)
        desktop.visible = lambda identifier: Rect(546, 944, 272, 72) if identifier == 43 else Rect(0, 0, 1364, 936)
        desktop.windows = lambda: [42, 43]
        desktop.on_desktop = lambda _: True
        properties = {
            (43, '_NET_WM_STRUT_PARTIAL'): [0, 0, 0, 88, 0, 0, 0, 0, 0, 0, 0, 1363],
            (43, '_NET_WM_WINDOW_TYPE'): ['_NET_WM_WINDOW_TYPE_DOCK'],
        }
        desktop.property = lambda identifier, name: properties.get((identifier, name), [])
        with patch.object(window, 'query', return_value=''):
            self.assertEqual(desktop.area(42), Rect(0, 0, 1364, 936))
        self.assertEqual(desktop.property(43, '_NET_WM_STRUT_PARTIAL')[3], 88)


class WindowSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='orca-window-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profile = self.root / 'bin/orca.AppImage.config/orca'
        self.proc = self.root / 'proc'
        self.proc.mkdir()

    def process(self, pid, environment):
        process = self.proc / str(pid)
        process.mkdir()
        (process / 'environ').write_bytes(b'\0'.join(f'{key}={value}'.encode() for key, value in environment.items()))

    def display(self, properties, *, other_desktops=()):
        display = window.X11.__new__(window.X11)
        display.atom = lambda name: name
        display.windows = lambda: list(properties)
        display.property = lambda identifier, name: properties.get(identifier, {}).get(name, [])
        display.on_desktop = lambda identifier: identifier not in other_desktops
        display.visible = lambda _: Rect(88, 88, 1188, 848)
        return display

    def test_profile_identity_uses_the_saved_profile_or_portable_appimage(self):
        self.process(123, {'ORCA_USER_DATA_PATH': self.profile})
        self.process(456, {'APPIMAGE': self.root / 'bin/orca.AppImage'})
        self.process(789, {'ORCA_USER_DATA_PATH': self.root / 'other-profile'})
        self.assertTrue(window.profile_matches(123, self.profile, self.proc))
        self.assertTrue(window.profile_matches(456, self.profile, self.proc))
        self.assertFalse(window.profile_matches(789, self.profile, self.proc))
        self.assertIsNone(window.profile_matches(999, self.profile, self.proc))

    def test_minimized_main_window_is_found_without_touching_other_profiles_or_dialogs(self):
        self.process(123, {'ORCA_USER_DATA_PATH': self.profile})
        self.process(456, {'ORCA_USER_DATA_PATH': self.root / 'other-profile'})
        properties = {
            1: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_PID': [123],
                '_NET_WM_STATE': ['_NET_WM_STATE_HIDDEN']},
            2: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_PID': [456]},
            3: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_PID': [123], 'WM_TRANSIENT_FOR': [1]},
            4: {'WM_CLASS': b'other\0Other\0', '_NET_WM_PID': [123]},
        }
        display = window.X11.__new__(window.X11)
        display.atom = lambda name: name
        display.windows = lambda: list(properties)
        display.property = lambda identifier, name: properties[identifier].get(name, [])
        display.on_desktop = lambda _: True
        display.visible = lambda _: Rect(88, 88, 1188, 848)
        self.assertEqual(window.find_window(display, self.profile, self.proc), 1)

    def test_single_orca_window_is_found_when_its_pid_is_not_locally_visible(self):
        display = self.display({
            0x2800004: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_PID': [987654],
                        '_NET_WM_STATE': ['_NET_WM_STATE_HIDDEN', '_NET_WM_STATE_MAXIMIZED_HORZ',
                                          '_NET_WM_STATE_MAXIMIZED_VERT']},
        })
        self.assertEqual(window.find_window(display, self.profile, self.proc), 0x2800004)

    def test_single_orca_window_is_found_without_an_advertised_pid_or_profile(self):
        for pid in (None, 123):
            with self.subTest(pid=pid):
                if pid:
                    self.process(pid, {})
                properties = {'WM_CLASS': b'orca\0orca\0'}
                if pid:
                    properties['_NET_WM_PID'] = [pid]
                display = self.display({42: properties})
                self.assertEqual(window.find_window(display, self.profile, self.proc), 42)

    def test_orca_on_another_workspace_can_be_found_for_restoring(self):
        self.process(123, {'ORCA_USER_DATA_PATH': self.profile})
        display = self.display({42: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_PID': [123]}},
                               other_desktops=(42,))
        self.assertEqual(window.find_window(display, self.profile, self.proc), 42)

    def test_known_other_profile_is_not_used_as_a_fallback(self):
        self.process(123, {'ORCA_USER_DATA_PATH': self.root / 'other-profile'})
        display = self.display({42: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_PID': [123]}})
        self.assertIsNone(window.find_window(display, self.profile, self.proc))

    def test_multiple_unidentified_orca_processes_require_a_window_selection(self):
        display = self.display({
            42: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_PID': [987654]},
            43: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_PID': [987655]},
        })
        self.assertIsNone(window.find_window(display, self.profile, self.proc))
        self.assertEqual(window.find_window(display, self.profile, self.proc, requested=43), 43)

    def test_windows_from_one_unidentified_orca_process_select_its_largest_window(self):
        display = self.display({
            42: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_PID': [987654]},
            43: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_PID': [987654]},
        })
        display.visible = lambda identifier: Rect(88, 88, 1188, 848) if identifier == 42 else Rect(0, 0, 800, 600)
        self.assertEqual(window.find_window(display, self.profile, self.proc), 42)

    def test_explicit_selection_accepts_orca_but_excludes_other_apps_and_dialogs(self):
        self.process(123, {'ORCA_USER_DATA_PATH': self.root / 'other-profile'})
        display = self.display({
            42: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_PID': [123]},
            43: {'WM_CLASS': b'other\0Other\0', '_NET_WM_PID': [123]},
            44: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_PID': [123], 'WM_TRANSIENT_FOR': [42]},
        })
        self.assertEqual(window.find_window(display, self.profile, self.proc, requested=42), 42)
        with self.assertRaisesRegex(window.WindowError, 'does not identify Orca'):
            window.find_window(display, self.profile, self.proc, requested=43)
        with self.assertRaisesRegex(window.WindowError, 'transient for window 0x2a'):
            window.find_window(display, self.profile, self.proc, requested=44)

    def test_current_window_is_found_after_its_predecessor_has_closed(self):
        display = self.display({
            0x2a00004: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_PID': [987654]},
        })
        self.assertEqual(window.find_window(display, self.profile, self.proc), 0x2a00004)
        with self.assertRaisesRegex(window.WindowError, '0x2800004 has no WM_CLASS; it may have closed'):
            window.find_window(display, self.profile, self.proc, requested=0x2800004)

    def test_explicit_id_is_inspected_even_when_absent_from_the_managed_list(self):
        display = self.display({42: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_PID': [987654]}})
        display.windows = lambda: []
        self.assertEqual(window.find_window(display, self.profile, self.proc, requested=42), 42)

    def test_none_transient_target_does_not_exclude_a_normal_window(self):
        display = self.display({42: {'WM_CLASS': b'orca\0orca\0', 'WM_TRANSIENT_FOR': [0]}})
        self.assertEqual(window.find_window(display, self.profile, self.proc), 42)

    def test_explicit_id_reports_a_non_main_orca_window_type(self):
        display = self.display({
            42: {'WM_CLASS': b'orca\0orca\0', '_NET_WM_WINDOW_TYPE': ['_NET_WM_WINDOW_TYPE_DIALOG']},
        })
        with self.assertRaisesRegex(window.WindowError, 'non-main window type'):
            window.find_window(display, self.profile, self.proc, requested=42)

    def test_window_id_parsing_accepts_diagnostic_ids_and_rejects_invalid_values(self):
        self.assertEqual(window.parse_window_id('0x2800004'), 0x2800004)
        self.assertEqual(window.parse_window_id('41943044'), 0x2800004)
        for value in ('0', '-1', '0x100000000', 'not-a-window'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                window.parse_window_id(value)


if __name__ == '__main__':
    unittest.main()
