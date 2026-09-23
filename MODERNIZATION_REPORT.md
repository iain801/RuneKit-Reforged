# RuneKit Modernization Report

This document describes all changes made to modernize RuneKit from the original [whs/runekit](https://github.com/whs/runekit) codebase.

## Summary

The upstream RuneKit was unmaintained since ~2021, pinned to Python 3.9 (EOL), PySide2/Qt5 (deprecated), and had outdated dependencies with known CVEs. This fork updates everything to run on current Linux/macOS with Python 3.11+ and PySide6/Qt6.

## Dependency Changes

| Package | Old Version | New Version | Notes |
|---|---|---|---|
| Python | ^3.9,<3.10 | >=3.11,<3.15 | 3.9 reached EOL Oct 2025 |
| PySide2 | ^5.15.2 | — | Removed (replaced by PySide6) |
| PySide6 | — | ^6.6.0 | Qt6 replacement |
| Pillow | ^8.3.1 | ^12.0.0 | Fixes CVE-2022-22817, CVE-2023-44271 and others |
| numpy | (implicit) | >=1.24.0 | Made explicit; old numpy had CVE-2021-33430 |
| opencv-python-headless | ^4.5.3.56 | ^4.9.0 | CPU image processing |
| pyopencl | — | ^2026.1.4 | Optional GPU matching dependency; installed by the launcher by default |
| requests | ^2.26.0 | ^2.31.0 | Fixes CVE-2023-32681 |
| click | ^8.0.1 | ^8.1.0 | Minor update |
| psutil | ^5.8.0 | ^5.9.0 | Minor update |
| xcffib | ^0.11.1 | ^1.5.0 | Updated for Python 3.11 |
| pyobjc-* | 7.3 | ^10.0 | Updated for Python 3.11/macOS 14 |
| black | ^20.8b1 | ^24.0 | Moved to dev dependencies |
| pyinstaller | ^4.4 | ^6.0 | macOS dev only |

## PySide2 → PySide6 Migration

27 Python files were modified. Key changes:

### Import Relocations

- `PySide2.QtWebEngineWidgets.QWebEngineProfile` → `PySide6.QtWebEngineCore.QWebEngineProfile`
- `PySide2.QtWebEngineWidgets.QWebEngineScript` → `PySide6.QtWebEngineCore.QWebEngineScript`
- `PySide2.QtWebEngineWidgets.QWebEngineSettings` → `PySide6.QtWebEngineCore.QWebEngineSettings`
- `PySide2.QtWebEngineWidgets.QWebEnginePage` → `PySide6.QtWebEngineCore.QWebEnginePage`
- `PySide2.QtGui.Qt` → `PySide6.QtCore.Qt`
- All other `PySide2.*` → `PySide6.*`

### API Breaking Changes

- **Removed `QtWebEngine.initialize()`** — Qt6 auto-initializes the web engine
- **`exec_()` → `exec()`** — Python 3 allows `exec` as method name (6 call sites)
- **`setMargin()` → `setContentsMargins()`** — Removed in Qt6 (`tooltip.py`)
- **`QRunnable` no longer accepts `parent` kwarg** — Removed from `api.py`
- **`QJsonValue` replaced with native Python `float`** — `setTaskbarProgress` in `api.py`

### Scoped Enum Migration (~80 changes)

Qt6 requires fully-qualified enum values. Examples:

- `Qt.Widget` → `Qt.WindowType.Widget`
- `Qt.WA_DeleteOnClose` → `Qt.WidgetAttribute.WA_DeleteOnClose`
- `Qt.DisplayRole` → `Qt.ItemDataRole.DisplayRole`
- `Qt.ItemIsSelectable` → `Qt.ItemFlag.ItemIsSelectable`
- `QWebEngineSettings.PlaybackRequiresUserGesture` → `QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture`
- `QWebEngineScript.MainWorld` → `QWebEngineScript.ScriptWorldId.MainWorld`
- `QWebEngineUrlScheme.SecureScheme` → `QWebEngineUrlScheme.Flag.SecureScheme`
- `QWebEngineUrlRequestJob.RequestFailed` → `QWebEngineUrlRequestJob.Error.RequestFailed`
- `QWebEnginePage.PermissionGrantedByUser` → `QWebEnginePage.PermissionPolicy.GrantedByUser`
- `QFont.SansSerif` → `QFont.StyleHint.SansSerif`
- `QImage.Format_ARGB32` → `QImage.Format.Format_ARGB32`
- `QSizePolicy.Fixed` → `QSizePolicy.Policy.Fixed`
- And many more across 15+ files

## Build System Changes

- **Makefile**: `pyside2-rcc` → `pyside6-rcc` for Qt resource compilation
- **Makefile**: Python AppImage version 3.9.7 → 3.11
- **deploy/runekit-appimage.sh**: `PYTHONHOME` path updated from `python3.9` to `python3.11`

## CI/CD Changes

- **build.yml / build-linux**: Ubuntu 18.04 → 24.04, Python 3.9 → 3.11, Poetry via pip
- **build.yml / build-mac**: macOS 11 → 14, Python 3.9 → 3.11
- **All workflows**: GitHub Actions updated (checkout@v4, setup-python@v5, cache@v4, softprops/action-gh-release@v2)
- **All workflows**: Branch references updated from `master` to `main`

## Known Issues

- Native Wayland game windows are unsupported; RuneKit selects Qt’s `xcb` backend for XWayland game windows.
- Mixed-scale and multi-monitor overlay layouts still need live testing.
- macOS support remains experimental and untested.
