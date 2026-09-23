"""Opt in with RUNEKIT_X11_TESTS=1 QT_QPA_PLATFORM=xcb on a desktop."""

import os
import time
import unittest

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import xcffib
import xcffib.xproto as x

from runekit.game.x11.manager import X11GameManager


@unittest.skipUnless(
    os.environ.get("RUNEKIT_X11_TESTS") == "1", "requires live X11/XWayland"
)
class X11IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["runekit-tests"])

    def setUp(self):
        self.client = xcffib.connect()
        screen = self.client.get_setup().roots[self.client.pref_screen]
        self.wid = self.client.generate_id()
        self.client.core.CreateWindow(
            screen.root_depth,
            self.wid,
            screen.root,
            80,
            80,
            64,
            64,
            0,
            x.WindowClass.InputOutput,
            screen.root_visual,
            x.CW.BackPixel | x.CW.OverrideRedirect,
            [0x336699, 1],
        )
        self.client.core.MapWindow(self.wid)
        self.client.flush()
        self.manager = X11GameManager()
        # Set metadata after mapping, as real launchers do.
        data = b"review\0RuneScape\0"
        self.client.core.ChangeProperty(
            x.PropMode.Replace,
            self.wid,
            x.Atom.WM_CLASS,
            x.Atom.STRING,
            8,
            len(data),
            data,
        )
        self.client.flush()
        self.manager.get_instances()
        self.instance = self.manager._instances[self.wid]

    def tearDown(self):
        self.manager.stop()
        self.manager.stop()  # Shutdown must be idempotent.
        self.client.core.DestroyWindow(self.wid)
        self.client.flush()
        self.client.disconnect()
        QTest.qWait(30)

    def test_capture_move_resize_and_remap(self):
        self.assertEqual(self.instance.get_position().getRect(), (80, 80, 64, 64))
        pixels = self.instance.grab_game()
        self.assertEqual(pixels.shape, (64, 64, 4))
        self.assertEqual(pixels[20, 20].tolist(), [0x99, 0x66, 0x33, 255])
        self.client.core.ConfigureWindow(
            self.wid,
            x.ConfigWindow.X
            | x.ConfigWindow.Y
            | x.ConfigWindow.Width
            | x.ConfigWindow.Height,
            [180, 180, 96, 80],
        )
        self.client.flush()
        QTest.qWait(100)
        self.assertEqual(self.instance.get_position().getRect(), (180, 180, 96, 80))
        self.assertEqual(self.instance.grab_game().shape, (80, 96, 4))
        self.client.core.UnmapWindow(self.wid)
        self.client.flush()
        QTest.qWait(50)
        with self.assertRaises(RuntimeError):
            self.instance.grab_game()
        self.client.core.MapWindow(self.wid)
        self.client.flush()
        QTest.qWait(50)
        self.assertEqual(self.instance.grab_game().shape, (80, 96, 4))

    def test_shutdown_removes_shared_memory(self):
        self.instance.grab_game()
        ids = {shm.id for _, shm in self.manager._shm}
        self.assertTrue(ids)
        self.manager.stop()
        remaining = set(ids)
        for _ in range(50):
            with open("/proc/sysvipc/shm") as f:
                rows = [line.split() for line in f.readlines()[1:]]
            remaining = ids & {int(row[1]) for row in rows}
            if not remaining:
                break
            time.sleep(0.01)
        self.assertFalse(remaining, f"Leaked shared-memory IDs: {remaining}")
        with self.assertRaises(RuntimeError):
            self.instance.grab_game()

    def test_vanished_and_malformed_windows_do_not_break_discovery(self):
        self.assertFalse(self.manager.is_game(0))
        data = b"badclass"
        self.client.core.ChangeProperty(
            x.PropMode.Replace,
            self.wid,
            x.Atom.WM_CLASS,
            x.Atom.STRING,
            8,
            len(data),
            data,
        )
        self.client.flush()
        self.assertFalse(self.manager.is_game(self.wid))


if __name__ == "__main__":
    unittest.main()
