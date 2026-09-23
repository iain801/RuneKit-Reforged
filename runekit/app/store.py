import sys
from pathlib import Path
import json
import logging
import hashlib
from typing import Iterator, Tuple, Optional, Union, List
from urllib.parse import urljoin

import requests
from PySide6.QtCore import (
    QObject,
    QSettings,
    QThread,
    Signal,
    Slot,
    QStandardPaths,
    Qt,
)
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QProgressDialog, QMessageBox

from runekit.alt1.schema import AppManifest
from runekit.alt1.utils import fetch_bom_json

REGISTRY_URL = "https://runeapps.org/data/alt1/defaultapps.json"

logger = logging.getLogger(__name__)


def app_id(manifest_url):
    assert "://" in manifest_url
    return hashlib.blake2b(manifest_url.encode("utf8"), digest_size=16).hexdigest()


class _FetchRegistryThread(QThread):
    progress = Signal(int)
    items = Signal(int)
    label = Signal(str)
    failed = Signal(tuple)
    app_ready = Signal(str, dict, str, bytes)

    def __init__(
        self,
        parent: "AppStore",
        apps_manifest: Optional[str] = None,
        apps: Optional[List[str]] = None,
    ):
        super().__init__(parent=parent)
        assert (not apps_manifest and apps) or (
            not apps and apps_manifest
        ), "One of apps_manifest or apps must be set but not both. apps_manifest={}, apps={}".format(
            apps_manifest, apps
        )

        self.apps_manifest = apps_manifest
        self.apps = apps
        self.succeeded = False
        self.canceled = False

    def run(self):
        try:
            apps = (
                fetch_bom_json(self.apps_manifest) if self.apps_manifest else self.apps
            )
            self.items.emit(len(apps))
            failed = False
            for index, app in enumerate(apps):
                if self.isInterruptionRequested() or self.canceled:
                    return
                url = (
                    app
                    if isinstance(app, str)
                    else urljoin(self.apps_manifest, app["url"])
                )
                folder = "" if isinstance(app, str) else app.get("folder", "")
                self.label.emit(f"Installing app {url}")
                try:
                    manifest = fetch_bom_json(url)
                    icon = b""
                    if manifest.get("iconUrl"):
                        response = requests.get(
                            urljoin(url, manifest["iconUrl"]), timeout=15
                        )
                        response.raise_for_status()
                        icon = response.content
                    self.app_ready.emit(url, manifest, folder, icon)
                except Exception:
                    failed = True
                    self.failed.emit(sys.exc_info())
                self.progress.emit(index + 1)
            self.succeeded = (
                not failed and not self.canceled and not self.isInterruptionRequested()
            )
        except Exception:
            self.failed.emit(sys.exc_info())

    @Slot()
    def cancel(self):
        self.canceled = True
        self.requestInterruption()


class AppStore(QObject):
    app_change = Signal()

    def __init__(self):
        super().__init__()
        self.settings = QSettings(self)

        qt_write_base = Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppConfigLocation
            )
        )
        qt_write_base.mkdir(parents=True, exist_ok=True)

        self.icon_write_dir = qt_write_base / "app_icons"
        self.icon_write_dir.mkdir(exist_ok=True)

    def has_default_apps(self) -> bool:
        return self.settings.value("apps/_meta/isDefaultLoaded", False, type=bool)

    def load_default_apps(self):
        self._start_install(apps_manifest=REGISTRY_URL)

    def add_app_ui(self, manifests: List[str]):
        self._start_install(apps=manifests)

    def _start_install(self, apps_manifest=None, apps=None):
        if getattr(self, "add_app_thread", None) is not None:
            self.app_progress.show()
            return
        progress = self.app_progress = QProgressDialog(
            "Installing apps", "Cancel", 0, 0
        )
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        worker = self.add_app_thread = _FetchRegistryThread(
            self, apps_manifest=apps_manifest, apps=apps
        )
        errors = []

        def on_ready(url, manifest, folder, icon):
            try:
                if "/" in folder:
                    raise ValueError("Application folder must be a single name")
                self.add_app(url, manifest, icon_data=icon)
                self.add_app_to_folder(app_id(url), folder)
            except Exception as exc:
                errors.append(str(exc))
                logger.exception("Failed to install %s", url)

        def on_failed(exc_info):
            errors.append(str(exc_info[1]))
            logger.warning("Application download failed: %s", exc_info[1])

        def on_finished():
            if apps_manifest and worker.succeeded and not errors:
                self.settings.setValue("apps/_meta/isDefaultLoaded", True)
            progress.close()
            progress.deleteLater()
            worker.deleteLater()
            self.add_app_thread = None
            self.app_progress = None
            if errors and not getattr(self, "_stopping", False):
                QMessageBox.warning(
                    None, "Application installation failed", "\n".join(errors)
                )

        worker.app_ready.connect(on_ready, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(on_failed, Qt.ConnectionType.QueuedConnection)
        worker.progress.connect(progress.setValue)
        worker.items.connect(progress.setMaximum)
        worker.label.connect(progress.setLabelText)
        worker.finished.connect(on_finished)
        progress.canceled.connect(worker.cancel)
        worker.start()
        progress.show()

    def stop(self):
        self._stopping = True
        worker = getattr(self, "add_app_thread", None)
        if worker is not None:
            worker.cancel()
            worker.wait()

    def add_app(self, manifest_url: str, manifest: AppManifest, icon_data=None):
        appid = app_id(manifest_url)

        try:
            manifest["appUrl"] = urljoin(manifest_url, manifest["appUrl"])
            manifest["iconUrl"] = (
                urljoin(manifest_url, manifest["iconUrl"])
                if "iconUrl" in manifest and manifest["iconUrl"]
                else ""
            )
            manifest["configUrl"] = urljoin(manifest_url, manifest["configUrl"])
        except KeyError:
            raise AddAppError(manifest_url)

        if icon_data is not None:
            if icon_data:
                (self.icon_write_dir / (appid + ".png")).write_bytes(icon_data)
        elif manifest["iconUrl"]:
            self.download_app_icon(appid, manifest["iconUrl"])

        self.settings.setValue(f"apps/{appid}", json.dumps(manifest))
        self.app_change.emit()
        logger.info(
            "Application %s (%s:%s) installed", manifest["appName"], appid, manifest_url
        )

    def mkdir(self, folder: str):
        assert folder
        assert "/" not in folder

        settings = QSettings()
        settings.beginGroup(f"apps/_folder/{folder}")
        settings.setValue("_dir", "true")
        self.app_change.emit()

    def add_app_to_folder(self, appid: str, folder: str = "", _emit=True):
        assert "/" not in folder
        settings = QSettings()
        settings.beginGroup(f"apps/_folder/{folder}")

        last_id = 0
        for key in settings.childKeys():
            if "_" in key:
                continue

            last_id = max(int(key), last_id)
            if appid == settings.value(key):
                settings.endGroup()
                return

        keys = [int(x) for x in settings.childKeys() if "_" not in x]
        last_id = max(keys) if keys else 0
        settings.endGroup()

        settings.setValue(f"apps/_folder/{folder}/{last_id + 1}", appid)

        if _emit:
            self.app_change.emit()

    def delete_app_from_folder(self, appid: str, folder: str):
        assert "/" not in folder
        settings = QSettings()
        settings.beginGroup(f"apps/_folder/{folder}")

        for key in settings.childKeys():
            if appid == settings.value(key):
                settings.remove(key)
                self.app_change.emit()
                return

    def remove_app(self, appid: str):
        settings = QSettings()
        settings.beginGroup("apps/_folder/")

        def recurse():
            for key in settings.childKeys():
                if settings.value(key) == appid:
                    settings.remove(key)

            for childGroup in settings.childGroups():
                settings.beginGroup(childGroup)
                recurse()
                settings.endGroup()

        recurse()

        self.settings.remove(f"apps/{appid}")

        icon_file = self.icon_write_dir / (appid + ".png")
        icon_file.unlink(True)

        self.app_change.emit()

    def rmdir(self, folder: str):
        assert folder
        assert "/" not in folder

        settings = QSettings()
        settings.beginGroup(f"apps/_folder/{folder}")

        for key in settings.childKeys():
            if "_" in key:
                continue

            try:
                int(key)
            except ValueError:
                continue

            self.add_app_to_folder(settings.value(key), "", _emit=False)

        settings.remove("")
        self.app_change.emit()

    def download_app_icon(self, appid: str, url: str):
        dest = self.icon_write_dir / (appid + ".png")
        req = requests.get(url, timeout=15)
        req.raise_for_status()
        with dest.open("wb") as fp:
            fp.write(req.content)

        logger.info("App icon %s wrote to %s", appid, str(dest))

    def icon(self, appid: str) -> Optional[QIcon]:
        fn = QStandardPaths.locate(
            QStandardPaths.StandardLocation.AppConfigLocation,
            "app_icons/" + appid + ".png",
        )
        if fn == "":
            return None

        return QIcon(QPixmap(fn))

    def all_apps(self) -> Iterator[Tuple[str, AppManifest]]:
        settings = QSettings()
        settings.beginGroup("apps")
        for appid in settings.childKeys():
            if appid.startswith("_"):
                continue

            manifest = json.loads(settings.value(appid))
            yield appid, manifest

        settings.endGroup()

    def list_app(self, root: str) -> Iterator[Tuple[str, Union[AppManifest, None]]]:
        settings = QSettings()

        settings.beginGroup(f"apps/_folder/" + root)
        for key in settings.childGroups():
            yield key, None
        for key in settings.childKeys():
            try:
                int(key)
            except ValueError:
                continue

            appid = settings.value(key)
            yield appid, self[appid]

    def __iter__(self):
        yield from self.all_apps()

    def __getitem__(self, item: str) -> AppManifest:
        assert not item.startswith("_")
        value = self.settings.value(f"apps/{item}")
        if value is None:
            raise KeyError(item)
        return json.loads(value)


class AddAppError(ValueError):
    def __init__(self, url):
        super().__init__(f"Unable to parse manifest of application at {url}")
