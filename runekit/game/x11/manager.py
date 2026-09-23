import logging
import os
import struct
import threading
from typing import List, Dict, Tuple, Union

import sysv_ipc
import xcffib
import xcffib.composite
import xcffib.shm
import xcffib.xinput
import xcffib.xproto
from PySide6.QtCore import QTimer, Slot, QObject, Signal

from runekit.game import GameManager
from .instance import GameInstance, X11GameInstance
from ..overlay import DesktopWideOverlay

MAX_SHM = 10
NET_ACTIVE_WINDOW = "_NET_ACTIVE_WINDOW"
WM_APP_NAME = os.getenv("RK_WM_APP_NAME", "RuneScape")
WM_NAME = "_NET_WM_NAME"
WM_PID = "_NET_WM_PID"
WM_PROC_NAME = os.getenv("RK_WM_PROC_NAME", "rs2client").lower()

# Window classes used by compositors/XWayland to wrap client windows as
# decoration. These are never the game itself, even if the title matches.
FRAME_CLASSES = {"mutter-x11-frames", "kwin_x11"}


class X11GameManager(GameManager):
    connection: xcffib.Connection

    _instances: Dict[int, X11GameInstance]
    _atom: Dict[bytes, int]
    _shm: List[Tuple[int, sysv_ipc.SharedMemory]]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._instances = {}
        self._atom = {}
        self._shm = []
        self.capture_lock = threading.RLock()
        self._stopped = False

        self.logger = logging.getLogger(__name__ + "." + self.__class__.__name__)
        self.connection = xcffib.Connection()
        self.screen = self.connection.get_screen_pointers()[self.connection.pref_screen]
        self.xcomposite = self.connection(xcffib.composite.key)
        self.xshm = self.connection(xcffib.shm.key)
        self.xinput = self.connection(xcffib.xinput.key)
        self._setup_composite()
        self._setup_overlay()

        self.event_worker = X11EventWorker(self)
        self.event_worker.create_signal.connect(self.on_game_opened)
        self.event_worker.destroy_signal.connect(self.on_game_closed)
        self.event_timer = QTimer(self)
        self.event_timer.timeout.connect(self.event_worker.poll_events)
        self.event_timer.start(10)
        # Window titles and classes are often set after CreateNotify.
        self.discovery_timer = QTimer(self)
        self.discovery_timer.timeout.connect(self.get_instances)
        self.discovery_timer.start(1000)
        try:
            self.event_worker.select_events()
            self.get_instances()
        except Exception:
            self.stop()
            raise

    def stop(self):
        if self._stopped:
            return
        self.event_timer.stop()
        self.discovery_timer.stop()
        with self.capture_lock:
            self._stopped = True
            for instance in list(self._instances.values()):
                instance.close()
            self._instances.clear()
            self.gc_shm(limit=0)
            self.connection.disconnect()
        self.overlay.hide()
        self.overlay.deleteLater()

    def get_instances(self) -> List[GameInstance]:
        if self._stopped:
            return []

        def visit(wid: int):
            try:
                if wid not in self._instances and self.is_game(wid):
                    self.on_game_opened(wid)
                for child in self.connection.core.QueryTree(wid).reply().children:
                    visit(child)
            except (xcffib.xproto.WindowError, xcffib.xproto.DrawableError):
                # A client can disappear between any two X11 requests.
                return

        visit(self.screen.root)
        return list(self._instances.values())

    def get_active_instance(self) -> Union[GameInstance, None]:
        for instance in self._instances.values():
            if instance.is_focused():
                return instance

        return None

    def is_game(self, wid: int) -> bool:
        geom_req = self.connection.core.GetGeometry(wid)
        try:
            wm_class = self.get_property(wid, xcffib.xproto.Atom.WM_CLASS)
        except xcffib.xproto.WindowError:
            geom_req.discard_reply()
            return False

        try:
            geom = geom_req.reply()
            attrs = self.connection.core.GetWindowAttributes(wid).reply()
        except (xcffib.xproto.WindowError, xcffib.xproto.DrawableError):
            return False
        if attrs.map_state != xcffib.xproto.MapState.Viewable:
            return False
        if geom.width == 32 and geom.height == 32:
            # OpenGL test window
            return False

        if not isinstance(wm_class, str) or not wm_class:
            return False

        parts = wm_class.split("\00")
        if len(parts) < 2:
            return False
        app_name = parts[1]

        if app_name == WM_APP_NAME:
            return True

        if app_name in FRAME_CLASSES:
            return False

        # Proton/Wine run the game under a generic WM_CLASS (e.g. "steam_proton")
        # while keeping the title "RuneScape", and the launcher shares that same
        # title. Disambiguate using the game client's process name (rs2client.exe).
        try:
            wm_name = self.get_property(wid, WM_NAME)
        except xcffib.xproto.WindowError:
            return False

        if wm_name != WM_APP_NAME:
            return False

        pid = self._get_window_pid(wid)
        if not pid:
            return False

        proc_name = self._get_process_name(pid)
        return proc_name is not None and WM_PROC_NAME in proc_name.lower()

    def _get_window_pid(self, wid: int):
        try:
            return self.get_property(wid, WM_PID)
        except xcffib.xproto.WindowError:
            return None

    @staticmethod
    def _get_process_name(pid: int):
        try:
            with open(f"/proc/{pid}/comm") as f:
                return f.read().strip()
        except OSError:
            return None

    def get_active_window(self) -> int:
        return self.get_property(self.screen.root, "_NET_ACTIVE_WINDOW")

    def _setup_overlay(self):
        self.overlay = DesktopWideOverlay()
        self.overlay.show()
        self.overlay.check_compatibility()

    def _setup_composite(self):
        self.xcomposite.QueryVersion(0, 4).reply()

    def get_property(
        self,
        wid: int,
        name: str,
        type_=xcffib.xproto.GetPropertyType.Any,
        index=0,
        max_values=1000,
    ):
        reply = self.connection.core.GetProperty(
            False,
            wid,
            self.get_atom(name),
            type_,
            index,
            max_values,
        ).reply()

        if not reply.type:
            return None
        if reply.type == xcffib.xproto.Atom.STRING:
            return reply.value.to_string().rstrip("\0")
        elif reply.type == self.get_atom("UTF8_STRING"):
            return reply.value.to_utf8()
        elif reply.type in (xcffib.xproto.Atom.WINDOW, xcffib.xproto.Atom.CARDINAL):
            data = bytes(reply.value.buf())
            return struct.unpack("=I", data[:4])[0] if len(data) >= 4 else None

        return reply.value

    def get_atom(self, atom: str) -> int:
        if isinstance(atom, int):
            return atom

        if hasattr(xcffib.xproto.Atom, atom):
            return getattr(xcffib.xproto.Atom, atom)

        atom = atom.encode("ascii")

        if atom in self._atom:
            return self._atom[atom]

        out = self.connection.core.InternAtom(False, len(atom), atom).reply().atom
        self._atom[atom] = out

        return out

    def get_shm(self, size: int) -> Tuple[int, sysv_ipc.SharedMemory]:
        for item in self._shm:
            if item[1].size >= size:
                self._shm.remove(item)
                return item

        shm = sysv_ipc.SharedMemory(None, flags=sysv_ipc.IPC_CREX, size=size)
        xid = self.connection.generate_id()
        try:
            self.xshm.Attach(xid, shm.id, False, is_checked=True).check()
            # Both processes are attached now; remove automatically after the
            # final detach, even if RuneKit exits unexpectedly.
            shm.remove()
        except Exception:
            shm.detach()
            shm.remove()
            raise
        return xid, shm

    def free_shm(self, shm: Tuple[int, sysv_ipc.SharedMemory]):
        self._shm.append(shm)
        self.gc_shm()

    def gc_shm(self, limit=MAX_SHM):
        while len(self._shm) > limit:
            xid, shm = self._shm.pop(0)
            self.xshm.Detach(xid)
            shm.detach()

    @Slot(int)
    def on_game_opened(self, wid: int):
        if self._stopped or wid in self._instances:
            return
        try:
            instance = X11GameInstance(self, wid, parent=self)
        except xcffib.XcffibException:
            self.logger.debug("Game window disappeared during discovery", exc_info=True)
            return
        self._instances[wid] = instance
        self.instance_added.emit(instance)
        self.instance_changed.emit()

    @Slot(int)
    def on_game_closed(self, wid: int):
        instance = self._instances.pop(wid, None)
        if instance is None:
            return
        self.instance_removed.emit(instance)
        instance.close(window_destroyed=True)
        instance.deleteLater()
        self.instance_changed.emit()


class X11EventWorker(QObject):
    create_signal = Signal(int)
    destroy_signal = Signal(int)

    def __init__(self, manager: "X11GameManager", **kwargs):
        super().__init__(parent=manager, **kwargs)
        self.manager = manager
        self.logger = logging.getLogger(__name__ + "." + self.__class__.__name__)

        self.handlers = {
            xcffib.xproto.PropertyNotifyEvent: self.on_property_change,
            xcffib.xproto.ConfigureNotifyEvent: self.on_configure_event,
            xcffib.xproto.KeyPressEvent: self.on_input_event,
            xcffib.xinput.RawButtonPressEvent: self.on_mouse_input,
            xcffib.xproto.MapNotifyEvent: self.on_map,
            xcffib.xproto.UnmapNotifyEvent: self.on_unmap,
            xcffib.xproto.DestroyNotifyEvent: self.on_destroy,
        }
        self.active_win_id = self.manager.get_active_window()

    def select_events(self):
        self.manager.connection.core.ChangeWindowAttributesChecked(
            self.manager.screen.root,
            xcffib.xproto.CW.EventMask,
            [
                xcffib.xproto.EventMask.PropertyChange
                | xcffib.xproto.EventMask.SubstructureNotify,
            ],
        ).check()
        self.manager.xinput.XIQueryVersion(2, 1).reply()
        mask = xcffib.xinput.EventMask.synthetic(
            xcffib.xinput.Device.AllMaster,
            1,
            [xcffib.xinput.XIEventMask.RawButtonPress],
        )
        self.manager.xinput.XISelectEvents(
            self.manager.screen.root, 1, [mask], is_checked=True
        ).check()

    @Slot()
    def poll_events(self):
        for _ in range(100):
            try:
                evt = self.manager.connection.poll_for_event()
                if evt is None:
                    break
                for event_type, handler in self.handlers.items():
                    if isinstance(evt, event_type):
                        handler(evt)
                        break
            except xcffib.XcffibException:
                self.logger.debug(
                    "X11 window changed during event handling", exc_info=True
                )
        self.manager.connection.flush()

    def on_mouse_input(self, evt):
        active = self.manager.get_active_window()
        instance = self.manager._instances.get(active)
        if instance is None:
            return
        pointer = self.manager.connection.core.QueryPointer(instance.wid).reply()
        geometry = self.manager.connection.core.GetGeometry(instance.wid).reply()
        if (
            pointer.same_screen
            and 0 <= pointer.win_x < geometry.width
            and 0 <= pointer.win_y < geometry.height
        ):
            instance.game_activity.emit()

    def on_property_change(self, evt: xcffib.xproto.PropertyNotifyEvent):
        if (
            evt.atom == self.manager.get_atom(NET_ACTIVE_WINDOW)
            and evt.window == self.manager.screen.root
        ):
            active_win_id = self.manager.get_active_window()

            if self.active_win_id == active_win_id:
                return

            self.active_win_id = active_win_id

            for id_, instance in self.manager._instances.items():
                active = active_win_id == id_

                if active != instance._is_focused:
                    instance._is_focused = active
                    instance.focusChanged.emit(instance.is_focused())

    def on_input_event(
        self, evt: Union[xcffib.xproto.KeyPressEvent, xcffib.xproto.ButtonPressEvent]
    ):
        try:
            self.manager._instances[evt.event].on_input(evt)
        except KeyError:
            self.logger.debug("Got input event for %d but is not registered", evt.event)

    def on_configure_event(self, evt: xcffib.xproto.ConfigureNotifyEvent):
        try:
            self.manager._instances[evt.window].on_config(evt)
        except KeyError:
            pass

    def on_map(self, evt):
        instance = self.manager._instances.get(evt.window)
        if instance is not None:
            instance.on_config(evt)
        elif self.manager.is_game(evt.window):
            self.create_signal.emit(evt.window)

    def on_unmap(self, evt):
        instance = self.manager._instances.get(evt.window)
        if instance is not None:
            instance.set_mapped(False)

    def on_destroy(self, evt: xcffib.xproto.DestroyNotifyEvent):
        if evt.window in self.manager._instances:
            self.destroy_signal.emit(evt.window)
