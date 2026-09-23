import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from shiboken6 import isValid

from runekit.browser.api import Alt1Api
from runekit.browser.overlay import OverlayApi
from runekit.game.instance import GameInstance
from runekit.game.overlay import DesktopWideOverlay
from runekit.game.x11.manager import X11EventWorker

APP = QApplication.instance() or QApplication(["tests"])


class FakeGame(GameInstance):
    wid = 42
    focused = True

    def get_position(self):
        return QRect(80, 60, 100, 100)

    def get_scaling(self):
        return 1.0

    def is_focused(self):
        return self.focused

    def get_world(self):
        return 84

    def get_overlay_area(self):
        return self.area


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.game = FakeGame()
        self.desktop = DesktopWideOverlay()
        self.game.area, self.disconnect = self.desktop.add_instance(self.game)
        self.api = OverlayApi(
            SimpleNamespace(app=SimpleNamespace(game_instance=self.game))
        )

    def tearDown(self):
        self.api.reset()
        self.disconnect()
        self.desktop.close()
        self.desktop.deleteLater()
        QTest.qWait(1)

    def test_focus_loss_hides_and_restores_overlay(self):
        self.game.focusChanged.emit(False)
        self.assertFalse(self.game.area.isVisible())
        self.game.focusChanged.emit(True)
        self.assertTrue(self.game.area.isVisible())

    def test_initial_unfocused_window_is_hidden(self):
        self.disconnect()
        self.game.focused = False
        self.game.area, self.disconnect = self.desktop.add_instance(self.game)
        self.assertFalse(self.game.area.isVisible())

    def test_reset_clears_active_and_frozen_items_and_timers(self):
        self.api.overlay_rect(0xFFFF0000, 0, 0, 5, 5, 10, 10)
        item = self.api.groups[""][0]
        self.api.overlay_freeze_group("pending")
        self.api.overlay_set_group("pending")
        self.api.overlay_rect(0xFFFF0000, 0, 0, 5, 5, 10, 10)
        frozen = self.api.frozen_group["pending"]
        self.api.reset()
        self.assertFalse(isValid(item))
        self.assertFalse(isValid(frozen))
        self.assertEqual(self.api.current_group, "")
        self.assertEqual(self.api._timers, {})
        QTest.qWait(30)
        self.api.enqueue(0, "overlay_rect", 0xFFFF0000, 0, 0, 5, 5, 100, 10)
        self.api.process_queue()
        self.assertEqual(len(self.api.groups[""]), 1)

    def test_clear_then_expiry_is_safe(self):
        self.api.overlay_rect(0xFFFF0000, 0, 0, 5, 5, 10, 10)
        self.api.overlay_clear_group("")
        QTest.qWait(30)
        self.assertEqual(self.api._timers, {})

    def test_frozen_group_expiry_is_safe(self):
        self.api.overlay_freeze_group("g")
        self.api.overlay_set_group("g")
        self.api.overlay_rect(0xFFFF0000, 0, 0, 5, 5, 10, 10)
        self.api.overlay_clear_group("g")
        QTest.qWait(30)
        self.assertEqual(self.api._timers, {})

    def test_image_validation_and_dimensions(self):
        image = self.api.get_qimage(bytes([0, 0, 255, 255]) * 6, 3)
        self.assertEqual((image.width(), image.height()), (3, 2))
        self.assertEqual(image.pixelColor(0, 0).red(), 255)
        for data, width in [(b"", 1), (b"abc", 1), (b"abcd", 0)]:
            with self.assertRaises(ValueError):
                self.api.get_qimage(data, width)

    def test_world_and_initial_focus_reach_qt_properties(self):
        api = Alt1Api(
            SimpleNamespace(
                manifest={"appName": "test"},
                game_instance=self.game,
                has_permission=lambda _: False,
            )
        )
        self.assertEqual(api.property("world"), 84)
        self.assertIs(api.property("gameActive"), True)
        api._overlay.reset()
        api.deleteLater()


class InputTests(unittest.TestCase):
    def test_mouse_activity_only_inside_active_game(self):
        instance = SimpleNamespace(wid=42, game_activity=Mock())
        core = Mock()
        core.GetGeometry.return_value.reply.return_value = SimpleNamespace(
            width=100, height=100
        )
        manager = SimpleNamespace(
            _instances={42: instance},
            get_active_window=lambda: 42,
            connection=SimpleNamespace(core=core),
        )
        worker = SimpleNamespace(manager=manager)
        for x, expected in [(20, 1), (-1, 0), (100, 0)]:
            instance.game_activity.reset_mock()
            core.QueryPointer.return_value.reply.return_value = SimpleNamespace(
                same_screen=True, win_x=x, win_y=20
            )
            X11EventWorker.on_mouse_input(worker, None)
            self.assertEqual(instance.game_activity.emit.call_count, expected)
        manager.get_active_window = lambda: 99
        instance.game_activity.reset_mock()
        X11EventWorker.on_mouse_input(worker, None)
        instance.game_activity.emit.assert_not_called()


class CaptureTests(unittest.TestCase):
    def test_crop_outside_each_edge_has_requested_shape(self):
        import numpy as np
        from runekit.image.np_utils import np_crop

        image = np.arange(4 * 5 * 4, dtype=np.uint8).reshape(4, 5, 4)
        for x, y, w, h in [
            (-8, 0, 3, 3),
            (10, 0, 3, 3),
            (0, -8, 3, 3),
            (0, 10, 3, 3),
            (-2, -1, 5, 4),
            (3, 2, 5, 4),
            (0, 0, 0, 0),
        ]:
            with self.subTest(region=(x, y, w, h)):
                result = np_crop(image, x, y, w, h)
                self.assertEqual(result.shape, (h, w, 4))
                for dy in range(h):
                    for dx in range(w):
                        expected = (
                            image[y + dy, x + dx]
                            if 0 <= y + dy < 4 and 0 <= x + dx < 5
                            else np.zeros(4, dtype=np.uint8)
                        )
                        np.testing.assert_array_equal(result[dy, dx], expected)


class ModelTests(unittest.TestCase):
    def test_leaf_and_invalid_indexes(self):
        from PySide6.QtCore import QObject, Signal, QModelIndex
        from runekit.host.appstore_model import AppStoreModel

        class Store(QObject):
            app_change = Signal()

            def list_app(self, root):
                return [
                    (
                        "one",
                        dict(
                            appName="One",
                            appUrl="https://example.com",
                            description="test",
                        ),
                    )
                ]

        store = Store()
        model = AppStoreModel(None, store)
        root = QModelIndex()
        leaf = model.index(0, 0, root)
        self.assertEqual(model.rowCount(leaf), 0)
        self.assertFalse(model.index(0, 0, leaf).isValid())
        for row, col in [(-1, 0), (0, -1), (1, 1), (0, 3)]:
            self.assertFalse(model.index(row, col, root).isValid())
        self.assertFalse(model.parent(root).isValid())
        self.assertFalse(model.parent(leaf).isValid())
        model.deleteLater()
        QTest.qWait(1)


class RegistryTests(unittest.TestCase):
    def test_failed_registry_can_be_retried(self):
        import tempfile
        from PySide6.QtCore import QSettings
        from runekit.app.store import AppStore

        for outcome in ["failure", "success"]:
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temp:
                settings = lambda *a, **k: QSettings(
                    temp + "/settings.ini", QSettings.Format.IniFormat
                )
                with (
                    patch("runekit.app.store.QSettings", side_effect=settings),
                    patch(
                        "runekit.app.store.QStandardPaths.writableLocation",
                        return_value=temp,
                    ),
                    patch("runekit.app.store.QMessageBox.warning"),
                ):
                    store = AppStore()
                    with patch(
                        "runekit.app.store.fetch_bom_json",
                        side_effect=(
                            RuntimeError("offline") if outcome == "failure" else None
                        ),
                        return_value=[],
                    ):
                        store.load_default_apps()
                        for _ in range(100):
                            QTest.qWait(10)
                            if store.add_app_thread is None:
                                break
                        self.assertIsNone(store.add_app_thread)
                        self.assertEqual(store.has_default_apps(), outcome == "success")
                    store.stop()
                    store.deleteLater()
                    QTest.qWait(1)

    def test_missing_app_raises_key_error(self):
        from runekit.app.store import AppStore

        with self.assertRaises(KeyError):
            AppStore.__getitem__(
                SimpleNamespace(settings=SimpleNamespace(value=lambda _: None)),
                "missing",
            )

    def test_registry_installs_on_gui_thread_and_cancellation_does_not_mark_loaded(
        self,
    ):
        import tempfile
        import threading
        from PySide6.QtCore import QSettings, QThread
        from runekit.app.store import AppStore, app_id

        for cancel in [False, True]:
            with self.subTest(cancel=cancel), tempfile.TemporaryDirectory() as temp:
                settings = lambda *a, **k: QSettings(
                    temp + "/settings.ini", QSettings.Format.IniFormat
                )
                with (
                    patch("runekit.app.store.QSettings", side_effect=settings),
                    patch(
                        "runekit.app.store.QStandardPaths.writableLocation",
                        return_value=temp,
                    ),
                    patch("runekit.app.store.QMessageBox.warning"),
                ):
                    store = AppStore()
                    seen = []
                    store.app_change.connect(
                        lambda: seen.append(QThread.currentThread() == APP.thread())
                    )
                    gate = threading.Event()
                    url = "https://example.com/appconfig.json"
                    manifest = dict(
                        appName="Test",
                        appUrl="index.html",
                        configUrl="appconfig.json",
                        permissions="pixel",
                    )

                    def fetch(address):
                        if address == url:
                            return manifest
                        gate.wait(1)
                        return [url]

                    with patch("runekit.app.store.fetch_bom_json", side_effect=fetch):
                        store.load_default_apps()
                        if cancel:
                            store.add_app_thread.cancel()
                        gate.set()
                        for _ in range(100):
                            QTest.qWait(10)
                            if store.add_app_thread is None:
                                break
                        self.assertIsNone(store.add_app_thread)
                        self.assertEqual(store.has_default_apps(), not cancel)
                        if not cancel:
                            self.assertEqual(
                                store[app_id(url)]["appUrl"],
                                "https://example.com/index.html",
                            )
                            self.assertTrue(seen)
                            self.assertTrue(all(seen))
                    store.stop()
                    store.deleteLater()
                    QTest.qWait(1)


if __name__ == "__main__":
    unittest.main()
