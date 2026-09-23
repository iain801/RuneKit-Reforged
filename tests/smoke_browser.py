"""Live browser/capture smoke test; requires an open RuneScape X11 window."""

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["QT_QPA_PLATFORM"] = "xcb"
config = tempfile.TemporaryDirectory(prefix="runekit-smoke-")
os.environ["XDG_CONFIG_HOME"] = config.name
os.environ["XDG_CACHE_HOME"] = config.name

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtTest import QTest
from shiboken6 import isValid
import runekit._resources
from runekit import browser
from runekit.app.app import App
from runekit.game.x11.manager import X11GameManager

browser.init()
qt = QApplication(["runekit-browser-smoke"])
qt.setQuitOnLastWindowClosed(False)
manager = X11GameManager()
apps = []
try:
    instances = manager.get_instances()
    if not instances:
        raise RuntimeError("Start RuneScape before running this test")
    game = instances[0]
    pixels = game.grab_game()
    print(
        "GAME_CAPTURE",
        pixels.shape,
        "RGB_STD",
        float(pixels[:, :, :3].std()),
        flush=True,
    )
    manifest = dict(
        appName="RuneKit smoke test",
        appUrl="about:blank",
        configUrl="https://runekit.invalid/appconfig.json",
        permissions="pixel,overlay,gamestate",
        defaultWidth=320,
        defaultHeight=180,
        minWidth=100,
        minHeight=100,
        maxWidth=800,
        maxHeight=600,
    )
    host = SimpleNamespace(app_store=SimpleNamespace(icon=lambda _: None))
    game_app = App(host, "smoke", manifest, game)
    apps.append(game_app)
    window = game_app.get_window()
    window.show()
    html = """<html><body>RuneKit compatibility test<script>
    const timer=setInterval(async()=>{
      if(!window.alt1 || !alt1.rsLinked)return;
      clearInterval(timer);
      try {
        alt1.overLayRect(0xffff0000,10,10,10,10,100,10);
        const result={sync:alt1.capture(0,0,8,8).length, async:(await alt1.captureAsync(0,0,8,8)).length};
        const many=await alt1.captureMultiAsync({a:{x:0,y:0,width:8,height:8},b:{x:10,y:10,width:4,height:4}});
        result.multi=[many.a.length,many.b.length];
        const bound=alt1.bindRegion(0,0,64,64);
        const needle=alt1.bindGetRegion(bound,0,0,8,8);
        const matches=JSON.parse(alt1.bindFindSubImg(bound,needle,8,0,0,64,64));
        if(!matches.some(p=>p.x===0 && p.y===0))throw new Error('Native search missed its source pixels');
        window.review=result;
      }catch(e){window.review={error:String(e)}}
    },100);
    </script></body></html>"""
    for cycle in range(2):
        window.browser.setHtml(html, QUrl("https://runekit.invalid/"))
        results = []
        for _ in range(60):
            QTest.qWait(100)
            window.browser.page().runJavaScript(
                "JSON.stringify(window.review)",
                lambda out: results.append(out) if out else None,
            )
            QTest.qWait(10)
            if results:
                break
        assert results, "Browser capture timed out"
        actual = json.loads(results[-1])
        assert actual == {"sync": 256, "async": 256, "multi": [256, 64]}, actual
        print("BROWSER_CYCLE", cycle + 1, actual, flush=True)
    window.browser.page().runJavaScript(
        "Promise.all(Array.from({length:20},()=>alt1.captureAsync(0,0,128,128)))"
    )
    QTest.qWait(1)
    window.close()
    QTest.qWait(100)
    assert not isValid(window), "App window did not close"
    print("BROWSER_CLOSE_OK", flush=True)
finally:
    for app in apps:
        app.close()
    QTest.qWait(50)
    manager.stop()
    QTest.qWait(50)
    config.cleanup()
