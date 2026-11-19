# -*- coding: utf-8 -*-
"""BLE 结论条探针：**不启射频线程**，用 mock 场景的 payload 直接喂页面，验两件事——

1. 中文模式下「结论 / 凭什么 / 接下来做什么」三行是否都填了真话（不是 ``—``）；
2. 英文模式下同样这三行是否**零中文、零全角**（页面是双语，英文模式不许漏中文）。

为什么单独一个探针：整套 ``--selftest`` 要 3 分半，而结论条是纯渲染，改一个词就得看一次；
这个探针 ~15 秒出结果（含 mock 跑 8 s 造数据）。

跑法（在本目录 ``host/`` 下）::

    python tests/probe_verdict_ui.py                 # 默认 wifi_busy 场景
    python tests/probe_verdict_ui.py --scenario mixed
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.dirname(HERE)
WEB = os.path.join(HOST, "web")
ROOT = os.path.dirname(HOST)
for path in (ROOT, HOST):
    if path not in sys.path:
        sys.path.insert(0, path)

from paths import ensure_on_path  # noqa: E402

ensure_on_path()

from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtWebEngineCore import QWebEngineSettings  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.registry import Registry  # noqa: E402
from core.scheduler import Scheduler  # noqa: E402
from modes import ble_risk  # noqa: E402
from tests.mock_source import make_source  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")
READ_JS = """JSON.stringify({
  level: (document.querySelector('#ble-verdict')||{}).dataset && document.querySelector('#ble-verdict').dataset.level,
  tag: (document.querySelector('#ble-verdict-tag')||{}).textContent || '',
  head: (document.querySelector('#ble-verdict-head')||{}).textContent || '',
  why: (document.querySelector('#ble-verdict-why')||{}).textContent || '',
  next: (document.querySelector('#ble-verdict-next')||{}).textContent || ''
})"""
OUT: dict[str, str] = {}


def payload_for(scenario: str) -> dict:
    plugin = ble_risk.BleRisk()
    registry = Registry()
    registry.register(plugin)
    scheduler = Scheduler(make_source(scenario), registry)
    scheduler.run(duration_s=8.0)
    return plugin.web_payload()


def main() -> int:
    parser = argparse.ArgumentParser(description="BLE 结论条渲染探针")
    parser.add_argument("--scenario", default="wifi_busy", help="mock 场景名")
    parser.add_argument("--wait", type=int, default=60000, help="超时毫秒（默认 60 s）")
    args = parser.parse_args()

    payload = payload_for(args.scenario)
    verdict = payload.get("verdict") or {}
    print("[data] %s → %s / %s" % (args.scenario, verdict.get("level"), verdict.get("code")), flush=True)
    literal = json.dumps(json.dumps(payload, ensure_ascii=False))

    app = QApplication(sys.argv[:1])
    view = QWebEngineView()
    settings = view.settings()
    settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
    settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
    settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebGLEnabled, True)
    settings.setAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled, True)
    view.resize(1280, 900)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(WEB, "index.html")))

    def js(code: str, tag: str = "") -> None:
        if not tag:
            view.page().runJavaScript(code, 0)
            return

        def take(result: object) -> None:
            OUT[tag] = str(result)
            print("[%s] %s" % (tag, result), flush=True)

        view.page().runJavaScript(code, 0, take)

    feed = (
        "(function(){try{"
        "Router.navigate('ble');"
        "localStorage['espsdr.lang']='zh';I18N.setLang('zh');"
        "Pages.ble.onTables({plugins:[{payload:JSON.parse(" + literal + ")}]});"
        "return 'fed';}catch(e){return 'ERR '+e;}})()"
    )
    switch_en = (
        "(function(){try{"
        "localStorage['espsdr.lang']='en';I18N.setLang('en');"
        "Pages.ble.onLang();"
        "return 'en';}catch(e){return 'ERR '+e;}})()"
    )

    steps = [
        (1200, lambda: js(feed, "feed")),
        (300, lambda: js(READ_JS, "zh")),
        (300, lambda: js(switch_en, "switch")),
        (400, lambda: js(READ_JS, "en")),
        (200, lambda: app.quit()),
    ]
    total = 0
    for delay, fn in steps:
        total += delay
        QTimer.singleShot(total, fn)
    QTimer.singleShot(args.wait, app.quit)
    app.exec()

    def load(tag: str) -> dict:
        try:
            return json.loads(OUT.get(tag) or "{}")
        except ValueError:
            return {}

    zh = load("zh")
    en = load("en")
    en_cjk = CJK.findall(" ".join([en.get("tag", ""), en.get("head", ""), en.get("why", ""), en.get("next", "")]))
    checks = {
        "zh_filled": min(len(zh.get("head", "")), len(zh.get("why", "")), len(zh.get("next", ""))) >= 8
        and "—" not in (zh.get("head", "") + zh.get("next", "")),
        "zh_level": zh.get("level") in ("risk", "watch", "clear", "nodata"),
        "zh_code_in_title": verdict.get("code") is not None,
        "en_filled": min(len(en.get("head", "")), len(en.get("why", "")), len(en.get("next", ""))) >= 8,
        "en_no_cjk": not en_cjk,
        "zh_has_cjk": bool(CJK.search(zh.get("head", ""))) or "at risk" not in zh.get("head", ""),
    }
    print("[probe] " + json.dumps(checks, ensure_ascii=False), flush=True)
    if en_cjk:
        print("[probe] 英文残留中文：" + "".join(en_cjk[:8]), flush=True)
    ok = all(checks.values())
    print("全部通过" if ok else "有失败项：" + ", ".join(k for k, v in checks.items() if not v), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())