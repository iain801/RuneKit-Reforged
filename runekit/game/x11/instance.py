import logging
import time
from typing import TYPE_CHECKING

import cv2
import xcffib.composite
import xcffib.xproto
from PySide6.QtCore import QRect, Slot
from PySide6.QtGui import QGuiApplication, QWindow

from runekit.game.instance import GameInstance
from runekit.game.psutil_mixins import PsUtilNetStat
from runekit.game.qt import QtEmbedMixin, QtGrabMixin
from .ximage import zpixmap_shm_to_image

if TYPE_CHECKING:
    from .manager import X11GameManager


class X11GameInstance(QtGrabMixin, QtEmbedMixin, PsUtilNetStat, GameInstance):
    refresh_rate = 100

    def __init__(self, manager: "X11GameManager", wid: int, **kwargs):
        super().__init__(**kwargs)
        self.manager = manager
        self.wid = wid
        self.pid = manager.get_property(wid, "_NET_WM_PID")
        self.qwindow = QWindow.fromWinId(wid)
        self.logger = logging.getLogger(__name__ + "." + self.__class__.__name__)
        self.embedded_windows = []
        self.pixmap_id = None
        self._closed = False
        self._mapped = True
        self._overlay_disconnect = None
        self.game_last_grab = 0.0
        self.game_last_image = None
        self.cached_position = None
        self._scaling = 1.0
        self._update_is_focused()
        try:
            self._setup_x()
            self.overlay, self._overlay_disconnect = manager.overlay.add_instance(self)
        except Exception:
            self.close()
            raise

    def _setup_x(self):
        self.manager.xcomposite.RedirectWindow(
            self.wid, xcffib.composite.Redirect.Automatic, is_checked=True
        ).check()
        self.name_pixmap()
        # Core ButtonPress is exclusive; mouse activity uses XI2 raw events.
        self.manager.connection.core.ChangeWindowAttributesChecked(
            self.wid,
            xcffib.xproto.CW.EventMask,
            [
                xcffib.xproto.EventMask.KeyPress
                | xcffib.xproto.EventMask.StructureNotify
            ],
        ).check()

    def close(self, window_destroyed=False):
        with self.manager.capture_lock:
            if self._closed:
                return
            self._closed = True
            self.stop_world_tracking()
            if self._overlay_disconnect is not None:
                self._overlay_disconnect()
                self._overlay_disconnect = None
            if self.pixmap_id is not None:
                self.manager.connection.core.FreePixmap(self.pixmap_id)
                self.pixmap_id = None
            if not window_destroyed:
                try:
                    self.manager.xcomposite.UnredirectWindow(
                        self.wid, xcffib.composite.Redirect.Automatic, is_checked=True
                    ).check()
                except xcffib.XcffibException:
                    pass  # The client may have exited before DestroyNotify arrives.
            if self.qwindow is not None:
                self.qwindow.deleteLater()
                self.qwindow = None
            self.game_last_image = None

    def name_pixmap(self):
        pixmap = self.manager.connection.generate_id()
        self.manager.xcomposite.NameWindowPixmap(
            self.wid, pixmap, is_checked=True
        ).check()
        if self.pixmap_id is not None:
            self.manager.connection.core.FreePixmap(self.pixmap_id)
        self.pixmap_id = pixmap
        self.game_last_image = None
        self.game_last_grab = 0.0

    def get_position(self) -> QRect:
        if self.cached_position is None:
            self.cached_position = self._read_position()
        return QRect(self.cached_position)

    def _read_position(self) -> QRect:
        geom = self.manager.connection.core.GetGeometry(self.wid).reply()
        translated = self.manager.connection.core.TranslateCoordinates(
            self.wid, self.manager.screen.root, 0, 0
        ).reply()
        x, y = translated.dst_x, translated.dst_y
        # On X11 Qt scales screen sizes, but retains native screen origins.
        screen = QGuiApplication.primaryScreen()
        for candidate in QGuiApplication.screens():
            rect = candidate.geometry()
            dpr = candidate.devicePixelRatio()
            native = QRect(
                rect.x(),
                rect.y(),
                round(rect.width() * dpr),
                round(rect.height() * dpr),
            )
            if native.contains(x + geom.width // 2, y + geom.height // 2):
                screen = candidate
                break
        self._scaling = screen.devicePixelRatio()
        origin = screen.geometry().topLeft()
        return QRect(
            origin.x() + round((x - origin.x()) / self._scaling),
            origin.y() + round((y - origin.y()) / self._scaling),
            round(geom.width / self._scaling),
            round(geom.height / self._scaling),
        )

    def get_scaling(self) -> float:
        self.get_position()
        return self._scaling

    def is_focused(self) -> bool:
        return self._is_focused and self._mapped

    def _update_is_focused(self):
        self._is_focused = self.manager.get_active_window() == self.wid

    def set_mapped(self, mapped):
        self._mapped = mapped
        self._update_is_focused()
        self.focusChanged.emit(self.is_focused())

    def grab_game(self):
        with self.manager.capture_lock:
            if self._closed or self.manager._stopped or not self._mapped:
                raise RuntimeError("The game window is closed or minimized")
            if (
                self.game_last_image is not None
                and (time.monotonic() - self.game_last_grab) * 1000 < self.refresh_rate
            ):
                return self.game_last_image
            geom = self.manager.connection.core.GetGeometry(self.pixmap_id).reply()
            xid, shm = self.manager.get_shm(geom.width * geom.height * 4)
            try:
                size = (
                    self.manager.xshm.GetImage(
                        self.pixmap_id,
                        0,
                        0,
                        geom.width,
                        geom.height,
                        0xFFFFFF,
                        xcffib.xproto.ImageFormat.ZPixmap,
                        xid,
                        0,
                    )
                    .reply()
                    .size
                )
                out = zpixmap_shm_to_image(shm, size, geom.width, geom.height)
                if self._scaling != 1:
                    pos = self.cached_position
                    out = cv2.resize(
                        out,
                        (pos.width(), pos.height()),
                        interpolation=cv2.INTER_NEAREST,
                    )
                self.game_last_image = out
                self.game_last_grab = time.monotonic()
                return out
            finally:
                self.manager.free_shm((xid, shm))

    def embed_window(self, window: QWindow):
        super().embed_window(window)
        self.embedded_windows.append(window)

    def get_overlay_area(self):
        return self.overlay

    @Slot(xcffib.Event)
    def on_config(self, evt):
        with self.manager.capture_lock:
            if self._closed:
                return
            attrs = self.manager.connection.core.GetWindowAttributes(self.wid).reply()
            if attrs.map_state != xcffib.xproto.MapState.Viewable:
                self.set_mapped(False)
                return
            self.name_pixmap()
            old_scaling = self._scaling
            self.cached_position = self._read_position()
            self.positionChanged.emit(QRect(self.cached_position))
            if self._scaling != old_scaling:
                self.scalingChanged.emit(self._scaling)
            self.set_mapped(True)

    @Slot(xcffib.Event)
    def on_input(self, evt):
        if self._closed:
            return
        self.game_activity.emit()
        if (
            isinstance(evt, xcffib.xproto.KeyPressEvent)
            and evt.detail == 10
            and evt.state & xcffib.xproto.KeyButMask.Mod1
        ):
            self.alt1_pressed.emit()
