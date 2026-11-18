"""esp-sdr 测试上位机（PySide6 + QWebEngine + QWebChannel）。

参照 ``MagicRobot_V0.1/host`` 的做法，只做三件事：

1. ``QWebEngineView`` 加载 ``host/web/index.html``（UI 全在网页里）
2. ``QWebChannel`` 把 :class:`Bridge` 暴露成 JS 的 ``window.backend``
3. ``Runner`` 线程反复调 :meth:`session.HostSession.step` 跑射频时间片

**换实验 = 换网页**：网页按 ``web/pages/registry.json`` 注册页面，加一个实验只要
新增 ``web/pages/<id>/page.html`` + 一行注册，不用改 Python。

约定（踩过的坑，别改回去）：

* 信号一律传 **JSON 字符串**：``QVariantList`` 经 QWebChannel 会退化成 JS
  ``object``，前端拿不到数组（参考工程 app.py 注释里的同一条）。
* 前端发来的参数也用 **JSON 字符串**，Python 侧统一在
  :meth:`session.HostConfig.from_json` 里解析，避免 Qt 类型转换的玄学。
* 帧率上限 ~20 fps：实板 300 帧/s，按帧推给 QWebChannel 会灌满 IPC 卡死界面。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QObject, QThread, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

import paths  # noqa: E402 - 本地模块，负责定位分析层
from session import LANGS, WEB_DIR, HostConfig, HostSession, list_ports, to_json, tr  # noqa: E402

#: 分析层目录（core/ modes/ 所在）。找不到会在这里就抛明确的错误。
SRC_DIR = paths.ensure_on_path()

class ConsolePage(QWebEnginePage):
    """把前端 console 输出打到 Python 的 stdout：网页出错时不用开 DevTools 也能查。"""

    def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802 - Qt 覆写
        name = getattr(level, "name", str(level))
        tag = {"InfoMessageLevel": "log", "WarningMessageLevel": "warn", "ErrorMessageLevel": "err"}.get(name, name)
        print(f"[js:{tag}] {source}:{line} {message}", flush=True)


#: 频谱推送间隔（秒）。实板 300 帧/s，全推会灌满 QWebChannel IPC。
SPECTRUM_MIN_INTERVAL = 0.05
#: 表格/统计推送间隔（秒）
TABLE_INTERVAL = 1.0
#: 单个时间片长度（秒）：GUI 里 stop 的响应时间上限
STEP_SECONDS = 0.35
#: 连续失败多少个时间片就自动停机（core 目前没有退避，应用层兜底）
MAX_CONSECUTIVE_ERRORS = 5
MOCK_FRAMES_PER_WINDOW = 2  # 假固件每个窗口吐 2 帧（tests/fake_firmware.py 的约定）


class Bridge(QObject):
    """暴露给前端 JS 的 ``window.backend``。"""

    # Python → JS（全部是 JSON 字符串）
    info = Signal(str)
    spectrum = Signal(str)
    tables = Signal(str)
    stats = Signal(str)
    state = Signal(str)
    log = Signal(str)

    def __init__(self, window: "HostWindow") -> None:
        super().__init__()
        self._window = window

    # ---- JS 主动查询 ------------------------------------------------------
    @Slot(result=str)
    def listPorts(self) -> str:  # noqa: N802 - JS 侧用驼峰
        return to_json(list_ports())

    @Slot(result=str)
    def uiDefaults(self) -> str:  # noqa: N802 - JS 侧用驼峰
        """界面初始值（语言/串口/增益）：让 ``--lang en`` 这类命令行参数也作用到网页。"""
        return to_json(self._window.ui_defaults())

    @Slot(result=str)
    def runtime(self) -> str:
        """当前运行态快照：给**晚连上**的前端补一次状态。

        QWebChannel 连上之前的信号会丢；``--mock`` / ``--autostart`` 早在页面
        加载完之前就把采集跑起来了，前端不补这一下会一直显示「未连接」。
        """
        return to_json(self._window.runtime())

    @Slot(result=str)
    def scenarios(self) -> str:
        from tests.mock_source import SCENARIOS

        return to_json(sorted(SCENARIOS))

    @Slot(result=str)
    def renderText(self) -> str:  # noqa: N802
        session = self._window.session
        if session is None:
            return tr(self._window.lang, "not_connected")
        return session.render_text()

    # ---- 控制 -------------------------------------------------------------
    @Slot(str, result=str)
    def start(self, config_json: str) -> str:  # noqa: N802
        return to_json(self._window.start_run(config_json))

    @Slot(result=str)
    def stop(self) -> str:
        return to_json(self._window.stop_run())

    # ---- 前端回报（自证用） ------------------------------------------------
    @Slot(str)
    def uiReport(self, payload: str) -> None:  # noqa: N802
        """JS 上报界面自检结果（WebGL/three.js/画布非空比例），打到 stdout。"""
        self._window.ui_report = payload
        print(f"[ui] {payload}", flush=True)


class Runner(QThread):
    """射频线程：反复跑时间片，把快照/表格节流后推给前端。"""

    def __init__(self, session: HostSession, bridge: Bridge, lang: str = "zh") -> None:
        super().__init__()
        self.session = session
        self.bridge = bridge
        self.lang = lang
        self._stop = False
        self.last_error = ""

    def request_stop(self) -> None:
        self._stop = True

    def _mock_pace(self) -> float:
        """假固件不模拟驻留时间（一个窗口 3ms 就跑完），整条扫描计划会瞬间刷完、
        游标闪成一片。这里按「真机每窗 100ms / 每窗 2 帧」给帧之间补一个步长，
        界面节奏就和真机一致（约 10 窗/秒）。真机返回 0（节奏由串口自然决定）。"""
        config = self.session.config
        if not config or not config.mock:
            return 0.0
        dwell_ms = 100
        for plugin in self.session.registry.plugins:
            try:
                requests = list(plugin.scan_requests()) if hasattr(plugin, "scan_requests") else []
            except Exception:  # noqa: BLE001 - 折算用的常量，拿不到就用默认值
                requests = []
            if requests:
                dwell_ms = max(10, int(requests[0].dwell_ms))
                break
        return (dwell_ms / 1000.0) / MOCK_FRAMES_PER_WINDOW

    def run(self) -> None:  # noqa: C901 - 单线程主循环，读下来比拆散更清楚
        session, bridge = self.session, self.bridge
        last_spectrum = 0.0
        last_slow = 0.0
        consecutive_errors = 0
        seen_errors = 0

        pace = self._mock_pace()

        def on_snapshot(snap) -> None:
            nonlocal last_spectrum
            now = time.time()
            if now - last_spectrum < SPECTRUM_MIN_INTERVAL:
                return
            last_spectrum = now
            try:
                bridge.spectrum.emit(to_json(session.spectrum(snap)))
            except Exception as exc:  # noqa: BLE001 - 一帧打包失败不该停掉整个会话
                bridge.log.emit(tr(self.lang, "pack_failed", error=f"{type(exc).__name__}: {exc}"))
            if pace:
                time.sleep(pace)

        try:
            while not self._stop:
                stats = session.step(STEP_SECONDS, on_snapshot=on_snapshot)
                now = time.time()
                if stats is not None and now - last_slow >= TABLE_INTERVAL:
                    last_slow = now
                    bridge.stats.emit(to_json(session.stats(stats)))
                    bridge.tables.emit(to_json(session.tables()))
                if stats is not None:
                    if stats.source_errors > seen_errors:
                        seen_errors = stats.source_errors
                        consecutive_errors += 1
                        message = stats.last_source_error or "scan failed"
                        if message != self.last_error:
                            self.last_error = message
                            bridge.log.emit(tr(self.lang, "rf_error", message=message))
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            bridge.log.emit(
                                tr(self.lang, "auto_stop", n=consecutive_errors, message=message)
                            )
                            break
                    else:
                        consecutive_errors = 0
        except Exception as exc:  # noqa: BLE001
            bridge.log.emit(tr(self.lang, "thread_error", error=f"{type(exc).__name__}: {exc}"))
        finally:
            try:
                session.close()
            finally:
                bridge.state.emit(to_json({"state": "stopped", "ts": round(time.time(), 3)}))


class HostWindow(QObject):
    """窗口 + 会话 + 线程的生命周期管理（也负责 ``--selftest`` 的自检流程）。"""

    def __init__(self, view: QWebEngineView) -> None:
        super().__init__()
        #: 当前界面语言（由最近一次 start_run / 命令行决定）
        self.lang = "zh"
        self.default_port = "COM56"
        self.default_gain: int | None = None
        self.view = view
        self.bridge = Bridge(self)
        self.session: HostSession | None = None
        self.runner: Runner | None = None
        self.ui_report = ""
        self.page_loaded = False
        #: **必须持有 QWebChannel 的 Python 引用**：只交给 setWebChannel 时对象可能被 GC，
        #: 表现是前端 new QWebChannel 回调永不触发（Bridge 一直 ready=false）。
        self.channel: QWebChannel | None = None

    def runtime(self) -> dict[str, object]:
        session = self.session
        runner = self.runner
        return {
            "running": bool(runner is not None and runner.isRunning()),
            "info": session.describe() if session is not None else None,
            "stats": session.stats() if session is not None else None,
        }

    def ui_defaults(self) -> dict[str, object]:
        return {"lang": self.lang, "port": self.default_port, "gain_index": self.default_gain}

    # ---- 页面 -------------------------------------------------------------
    def load(self) -> None:
        self.channel = QWebChannel(self)
        self.channel.registerObject("backend", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self.view.loadFinished.connect(self._on_load_finished)
        self.view.load(QUrl.fromLocalFile(os.path.join(WEB_DIR, "index.html")))

    def _on_load_finished(self, ok: bool) -> None:
        self.page_loaded = bool(ok)
        self.bridge.state.emit(to_json({"state": "ui-loaded" if ok else "ui-load-failed"}))
        if not ok:
            print("[ui] 页面加载失败", flush=True)

    # ---- 会话 -------------------------------------------------------------
    def start_run(self, config_json: str) -> dict[str, object]:
        if self.runner is not None and self.runner.isRunning():
            return {"ok": False, "message": tr(self.lang, "already_running")}
        try:
            config = HostConfig.from_json(config_json)
            self.lang = config.lang
            session = HostSession(bins_out=config.bins_out)
            info = session.open(config)
        except Exception as exc:  # noqa: BLE001 - 打不开端口是最常见的用户错误
            message = f"{type(exc).__name__}: {exc}"
            self.bridge.log.emit(tr(self.lang, "already_running_log", message=message))
            self.bridge.state.emit(to_json({"state": "error", "message": message}))
            return {"ok": False, "message": message}
        self.session = session
        self.bridge.info.emit(to_json(info))
        self.bridge.state.emit(to_json({"state": "running", "ts": round(time.time(), 3)}))
        self.runner = Runner(session, self.bridge, config.lang)
        self.runner.start()
        message = tr(config.lang, "started", what=info.get("identity") or config.port)
        return {"ok": True, "message": message, "info": info}

    def stop_run(self) -> dict[str, object]:
        if self.runner is None or not self.runner.isRunning():
            return {"ok": False, "message": tr(self.lang, "no_run")}
        self.runner.request_stop()
        self.runner.wait(4000)
        self.bridge.tables.emit(to_json(self.session.tables()) if self.session else "{}")
        return {"ok": True, "message": tr(self.lang, "stopped")}

    def shutdown(self) -> None:
        if self.runner is not None and self.runner.isRunning():
            self.runner.request_stop()
            self.runner.wait(4000)


def build_app(argv: list[str]) -> tuple[QApplication, QWebEngineView, HostWindow, argparse.Namespace]:
    parser = argparse.ArgumentParser(description="esp-sdr 测试上位机（WebView 版）")
    parser.add_argument("--port", default="COM56", help="默认串口（界面里还能改）")
    parser.add_argument("--gain-index", type=int, default=60, help="默认固定增益档位（真机）")
    parser.add_argument("--mock", action="store_true", help="启动即用假固件自跑")
    parser.add_argument("--scenario", default="wifi_busy", help="--mock 时的场景")
    parser.add_argument("--autostart", action="store_true", help="页面加载完成后自动开始")
    parser.add_argument("--selftest", action="store_true", help="自检：自动跑几秒 → 抓图 → 打印界面自检报告 → 退出")
    parser.add_argument("--real", action="store_true", help="--selftest 时对着真板跑（默认假固件）")
    parser.add_argument("--seconds", type=float, default=6.0, help="--selftest 跑多久")
    parser.add_argument("--shot", default="", help="--selftest 截图保存路径")
    parser.add_argument("--window-size", default="1440x900", help="窗口尺寸 WxH")
    parser.add_argument("--lang", default="zh", choices=list(LANGS), help="界面默认语言（网页里还能切，切换后记在 localStorage）")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("esp-sdr 测试上位机")
    width, height = (int(v) for v in args.window_size.lower().split("x", 1))
    view = QWebEngineView()
    view.setPage(ConsolePage(view))
    view.resize(width, height)
    settings = view.settings()
    # 本地页面要能读本地 css/js（three.js vendor 就在本地），不需要联网
    settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
    settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
    settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebGLEnabled, True)
    settings.setAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled, True)
    view.setWindowTitle("esp-sdr 测试上位机 · ESP32-C5 射频监测")
    view.show()

    window = HostWindow(view)
    window.lang = args.lang
    window.default_port = args.port
    window.default_gain = args.gain_index
    window.load()
    return app, view, window, args


def run_selftest(app: QApplication, view: QWebEngineView, window: HostWindow, args: argparse.Namespace) -> int:
    """自动跑一段：证明链路 + 桥 + 三个页面都真的活着，打印证据并退出。

    验收点（三条齐全才算 OK）：
      1. 频谱页：收到帧 > 0 且 2D/3D 画布都不是纯黑；
      2. WiFi 页：表格行数 > 5、推荐信道有值、说明行 >= 3；
      3. 原始页：至少一个模块面板 + ASCII 文本非空。
    """
    report: dict[str, object] = {"ok": False, "pages": {}}
    steps: list[str] = []
    started: dict[str, float] = {}

    def fail(message: str) -> None:
        report["ok"] = False
        report["error"] = message
        steps.append(f"FAIL {message}")
        print("[selftest] " + json.dumps(report, ensure_ascii=False), flush=True)
        app.quit()

    def shot(tag: str) -> dict[str, object]:
        base = args.shot or os.path.join(os.path.dirname(os.path.abspath(__file__)), "selftest.png")
        root, ext = os.path.splitext(base)
        path = base if tag == "spectrum" else f"{root}-{tag}{ext or '.png'}"
        image = view.grab()
        saved = image.save(path)
        return {"path": path, "saved": bool(saved), "w": image.width(), "h": image.height()}

    # 每个页面的 DOM 探针：只读，不改状态
    PAGE_JS = {
        "wifi": """JSON.stringify({
            rows: document.querySelectorAll('#wifi-rows tr').length,
            rec24: (document.querySelector('#rec24')||{}).textContent || '',
            rec24note: (document.querySelector('#rec24-note')||{}).textContent || '',
            notes: document.querySelectorAll('#wifi-notes li').length,
            sample: (document.querySelector('#wifi-sample')||{}).textContent || '',
            probe: (window.Pages.wifi.probe ? Pages.wifi.probe() : null)
        })""",
        "raw": """JSON.stringify({
            panels: document.querySelectorAll('#raw-tables .panel').length,
            tables: document.querySelectorAll('#raw-tables table').length,
            textLen: (((document.querySelector('#raw-text')||{}).textContent) || '').length
        })""",
    }

    def finish() -> None:
        pages = report["pages"]
        spectrum = pages.get("spectrum", {}) if isinstance(pages, dict) else {}
        wifi = pages.get("wifi", {}) if isinstance(pages, dict) else {}
        raw = pages.get("raw", {}) if isinstance(pages, dict) else {}
        canvases = spectrum.get("canvases", []) if isinstance(spectrum, dict) else []
        drawn = [c for c in canvases if float(c.get("nonBlack", 0)) > 0.01]
        frames = int(spectrum.get("framesSeen", 0) or 0)
        wf = next((c for c in canvases if c.get("id") == "wf-canvas"), {})
        i18n = report.get("i18n") or {}
        nav = i18n.get("navLabels") or []
        wf_probe = i18n.get("wf2d") or {}
        bar = ((spectrum.get("pageProbe") or {}).get("scanbar") or {}) if isinstance(spectrum, dict) else {}
        bar_groups = bar.get("groups") or []
        bar_wrap = spectrum.get("scanbarSelfTest") or {} if isinstance(spectrum, dict) else {}
        report["canvases_drawn"] = len(drawn)
        report["frames_seen"] = frames
        checks = {
            "spectrum_frames": frames > 0,
            "canvas_drawn": len(drawn) > 0,
            # 2D 瀑布（坏过的那块）：画布要有内容 + 缓冲里「一帧一行」真的在累积，
            # 且最新几行比未填充的老行亮（= 旧行确实在下移，不是原地覆盖/拉伸）
            "waterfall_2d": (
                float(wf.get("nonBlack", 0)) > 0.05
                and int(wf_probe.get("frames", 0) or 0) > 10
                and int(wf_probe.get("distinctRows", 0) or 0) >= int(wf_probe.get("frames", 0) or 0) * 0.9
                and int(wf_probe.get("topMean", 0) or 0) > int(wf_probe.get("bottomMean", 0) or 0) + 100
                and not wf_probe.get("imagesTooMany")
            ),
            # 扫频进度条：至少切出两个频段（2.4G / 5G），游标在轨道内、已走过多个窗
            "scanbar_bands": len(bar_groups) >= 2 and int(bar.get("windows", 0) or 0) > 10,
            "scanbar_progress": (
                int(bar.get("visited", 0) or 0) >= 3
                and int(bar.get("index", -1)) >= 0
                and 0 <= int(bar.get("x", -1)) <= int(bar.get("width", 0) or 0)
            ),
            # 到终点回卷：喂到最后一格再喂第一格，轮次 +1、本轮覆盖清零
            "scanbar_wrap": int(bar_wrap.get("passesAfterWrap", 0) or 0) == 2
            and int(bar_wrap.get("visitedAfterWrap", 0) or 0) == 1
            and int(bar_wrap.get("indexAtEnd", -1)) == 3,
            # 语言切换：中文 → English，导航与状态栏都要跟着变
            "i18n_switch": i18n.get("lang") == "en" and any("Live spectrum" in str(x) for x in nav),
            # 外壳文案（含扫频进度条）真的翻了英文，level 数据值仍走显示层映射
            "i18n_shell": (
                len(i18n.get("shell") or []) == 4
                and "Scan progress" in str((i18n.get("shell") or [""])[0])
                and "window 6/49" in str((i18n.get("shell") or ["", ""])[1])
                and str((i18n.get("shell") or ["", "", "", ""])[3]) == "Heavy"
            ),
            "wifi_rows": int(wifi.get("rows", 0) or 0) > 5,
            "wifi_recommend": bool(str(wifi.get("rec24", "")).startswith("ch")),
            "wifi_notes": int(wifi.get("notes", 0) or 0) >= 3,
            "raw_panels": int(raw.get("panels", 0) or 0) >= 1,
            "raw_text": int(raw.get("textLen", 0) or 0) > 40,
        }
        report["checks"] = checks
        ok = all(checks.values())
        report["ok"] = ok
        steps.append("OK" if ok else "FAIL " + ",".join(k for k, v in checks.items() if not v))
        print("[selftest] " + json.dumps(report, ensure_ascii=False), flush=True)
        app.quit()

    def stage(page_id: str) -> None:
        """切到某个页面 → 等它画好 → 截图 + DOM 探针。"""
        view.page().runJavaScript(f"Router.navigate('{page_id}')")

        def probe() -> None:
            code = PAGE_JS.get(page_id)
            if page_id == "spectrum":
                code = (
                    "JSON.stringify(Object.assign(window.probeReport ? window.probeReport() : {missing:true},"
                    " {scanbarSelfTest: (window.ScanBar ? ScanBar.selftest() : null)}))"
                )
            view.page().runJavaScript(code, 0, lambda result: collect(page_id, result))

        def collect(page_id: str, result: object) -> None:
            try:
                payload = json.loads(str(result)) if result else {}
            except Exception:  # noqa: BLE001
                payload = {"parse_error": str(result)}
            if isinstance(payload, dict):
                payload["shot"] = shot(page_id)
            report["pages"][page_id] = payload
            nxt = ORDER[ORDER.index(page_id) + 1 :]
            if page_id == "spectrum":
                # 频谱页验完顺手验一次中英切换，然后保持英文继续验后面的页
                QTimer.singleShot(300, lambda: switch_lang(nxt[0]))
            elif nxt:
                QTimer.singleShot(400, lambda: stage(nxt[0]))
            else:
                finish()

        QTimer.singleShot(1800, probe)

    def switch_lang(next_id: str) -> None:
        view.page().runJavaScript("I18N.setLang('en')")

        def probe_lang() -> None:
            view.page().runJavaScript(
                "JSON.stringify(Object.assign(window.probeReport ? window.probeReport() : {},"
                ' {shell: [T("scanbar.title"), T("scanbar.now", {center: 5180, at: 6, total: 49, pass: 2}),'
                ' T("spectrum.title"), T("level.重度")]}))',
                0,
                collect_lang,
            )

        def collect_lang(result: object) -> None:
            try:
                payload = json.loads(str(result)) if result else {}
            except Exception:  # noqa: BLE001
                payload = {}
            i18n = {k: payload.get(k) for k in ("lang", "statusText", "navLabels", "shell")}
            i18n["wf2d"] = (payload.get("pageProbe") or {}).get("wf2d")
            i18n["shot"] = shot("en-spectrum")
            report["i18n"] = i18n
            steps.append(f"语言切换：{i18n.get('lang')} / {i18n.get('navLabels')}")
            QTimer.singleShot(300, lambda: stage(next_id))

        QTimer.singleShot(700, probe_lang)

    ORDER = ["spectrum", "wifi", "raw"]

    def start_after_load() -> None:
        if not window.page_loaded:
            if time.time() - started["load"] > 20:
                return fail("页面 20s 未加载完成")
            QTimer.singleShot(200, start_after_load)
            return
        steps.append("页面已加载")
        result = window.start_run(
            to_json(
                {
                    "port": args.port,
                    # 自检默认用假固件（无板也能验）；加 --real 就对着真板跑
                    "mock": (bool(args.mock) or not args.real) if args.selftest else bool(args.mock),
                    "scenario": args.scenario,
                    "gain_index": args.gain_index,
                    "bins_out": 256,
                    "lang": args.lang,
                }
            )
        )
        if not result.get("ok"):
            return fail(f"启动失败：{result.get('message')}")
        steps.append(f"启动成功：{result.get('message')}")
        QTimer.singleShot(int(args.seconds * 1000), lambda: stage("spectrum"))

    started["load"] = time.time()
    QTimer.singleShot(200, start_after_load)
    QTimer.singleShot(int(args.seconds * 1000) + 40000, lambda: fail("自检超时"))
    app.exec()
    print("[selftest] steps: " + " | ".join(steps), flush=True)
    window.shutdown()
    return 0 if report.get("ok") else 2


def main(argv: list[str] | None = None) -> int:
    args_holder: dict[str, object] = {}

    app, view, window, args = build_app(list(argv if argv is not None else sys.argv[1:]))
    args_holder["args"] = args

    if args.selftest:
        return run_selftest(app, view, window, args)

    if args.autostart or args.mock:
        def autostart() -> None:
            if not window.page_loaded:
                QTimer.singleShot(300, autostart)
                return
            window.start_run(
                to_json(
                    {
                        "port": args.port,
                        "mock": bool(args.mock),
                        "scenario": args.scenario,
                        "gain_index": args.gain_index,
                        "bins_out": 256,
                        "lang": args.lang,
                    }
                )
            )

        QTimer.singleShot(300, autostart)

    app.aboutToQuit.connect(window.shutdown)
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())