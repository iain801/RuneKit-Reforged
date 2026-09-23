import logging
from typing import TYPE_CHECKING, Callable, Tuple, Dict

from PySide6.QtCore import Qt, QRect, QTimer
from PySide6.QtGui import QGuiApplication, QPen
from PySide6.QtWidgets import (
    QMainWindow,
    QFrame,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsItem,
    QGraphicsRectItem,
)

from shiboken6 import delete, isValid

from .qt import qpixmap_to_np, is_wayland
from ..image import is_color_percent_gte

if TYPE_CHECKING:
    from .instance import GameInstance


class DesktopWideOverlay(QMainWindow):
    _instances: Dict[int, QGraphicsItem]
    view: QGraphicsView
    scene: QGraphicsScene

    def __init__(self):
        super().__init__(
            flags=Qt.WindowType.Widget
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.BypassWindowManagerHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.logger = logging.getLogger(__name__ + "." + self.__class__.__name__)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent")
        self._instances = {}
        self._compatibility_timer = QTimer(self)
        self._compatibility_timer.setSingleShot(True)
        self._compatibility_timer.timeout.connect(self._check_compatibility)

        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene, self)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setStyleSheet("background: transparent")
        self.view.setInteractive(False)
        self.setCentralWidget(self.view)
        self.transparent_pen = QPen(Qt.PenStyle.NoPen)
        app = QGuiApplication.instance()
        app.screenAdded.connect(self._watch_screen)
        app.screenRemoved.connect(self._update_geometry)
        for screen in app.screens():
            self._watch_screen(screen)

    def _watch_screen(self, screen):
        screen.geometryChanged.connect(self._update_geometry)
        self._update_geometry()

    def _update_geometry(self, *_):
        virtual_screen = QRect()
        for screen in QGuiApplication.screens():
            virtual_screen = virtual_screen.united(screen.geometry())
        self.scene.setSceneRect(virtual_screen)
        self.setGeometry(virtual_screen)

    def add_instance(
        self, instance: "GameInstance"
    ) -> Tuple[QGraphicsItem, Callable[[], None]]:
        """Add instance to manage, return a disconnect function and the canvas"""
        positionChanged = lambda rect: self.on_instance_moved(instance, rect)
        instance.positionChanged.connect(positionChanged)

        focusChanged = lambda focus: self.on_instance_focus_change(instance, focus)
        instance.focusChanged.connect(focusChanged)

        instance_pos = instance.get_position()
        gfx = QGraphicsRectItem(0, 0, instance_pos.width(), instance_pos.height())
        gfx.setPen(self.transparent_pen)
        gfx.setVisible(instance.is_focused())
        gfx.setPos(instance_pos.x(), instance_pos.y())
        self.scene.addItem(gfx)
        self._instances[instance.wid] = gfx

        def disconnect():
            self._instances.pop(instance.wid, None)
            if isValid(gfx):
                delete(gfx)
            instance.positionChanged.disconnect(positionChanged)
            instance.focusChanged.disconnect(focusChanged)

        return gfx, disconnect

    def on_instance_focus_change(self, instance, focus):
        self._instances[instance.wid].setVisible(focus)

    def on_instance_moved(self, instance, pos: QRect):
        rect = self._instances[instance.wid]
        rect.setRect(0, 0, pos.width(), pos.height())
        rect.setPos(pos.x(), pos.y())

    def check_compatibility(self):
        self._compatibility_timer.start(300)

    def _check_compatibility(self):
        # If we cause black screen then hide ourself out of shame...
        screenshot = QGuiApplication.primaryScreen().grabWindow(0)
        if screenshot.isNull():
            self.logger.warning(
                "Screen grab unavailable; skipping compatibility check."
            )
            return
        image = qpixmap_to_np(screenshot)
        if is_color_percent_gte(image, color=[0, 0, 0], percent=0.95):
            if is_wayland():
                # XWayland's root window is always black, so this check can't
                # tell whether the overlay is the culprit. Never hide there.
                self.logger.warning(
                    "Detected black screen condition. Ignoring compatibility check."
                )
                return
            self.logger.warning("Detected black screen condition. Disabling overlay")
            self.hide()
