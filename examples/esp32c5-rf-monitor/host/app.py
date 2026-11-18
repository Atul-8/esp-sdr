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
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

import paths  # noqa: E402 - 本地模块，负责定位分析层
from session import LANGS, WEB_DIR, HostConfig, HostSession, list_ports, to_json, tr  # noqa: E402

#: 分析层目录（core/ modes/ 所在）。找不到会在这里就抛明确的错误。
SRC_DIR = paths.ensure_on_path()

class ConsolePage(QWebEnginePage):
    """把前端 console 输出打到 Python 的 stdout：网页出错时不用开 DevTools 也能查。

    同时接住 ``window.open``：前端「独立窗口」按钮就是一次 ``window.open``，
    这里把它变成一个**真正的独立窗口**（可以拖到别的显示器），而不是被 Qt 丢掉。
    """

    #: 反向引用，由 :meth:`HostWindow.__init__` 填上
    owner: "HostWindow | None" = None

    def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802 - Qt 覆写
        name = getattr(level, "name", str(level))
        tag = {"InfoMessageLevel": "log", "WarningMessageLevel": "warn", "ErrorMessageLevel": "err"}.get(name, name)
        print(f"[js:{tag}] {source}:{line} {message}", flush=True)

    def createWindow(self, _window_type):  # noqa: N802 - Qt 覆写
        if self.owner is None:
            return None
        return self.owner.open_popout()


def _loads(payload: str) -> object:
    """把缓存的 JSON 字符串还原成对象（缓存为空时给 None）。"""
    if not payload:
        return None
    try:
        return json.loads(payload)
    except ValueError:
        return None


def configure_settings(view: QWebEngineView) -> None:
    """主窗口和每个独立窗口用同一套设置（本地文件可读、不联网、开 WebGL）。"""
    settings = view.settings()
    settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
    settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
    settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
    settings.setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, True)
    settings.setAttribute(QWebEngineSettings.WebGLEnabled, True)
    settings.setAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled, True)


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
    gain = Signal(str)
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
    def audit(self) -> str:  # noqa: N802 - JS 侧用同名
        """一键体检：把当前会话已扫到的数据汇成「活跃发射源清单 + 结论」。

        **只读**：不重新跑扫描、不新抓档。界面上的措辞必须保留四条边界
        （分时快照 / 相对码 / 启发式 / 「未发现」≠「安全」）。
        """
        session = self._window.session
        if session is None:
            return to_json({"kind": "anti_peep_audit", "ok": False, "headline": "还没有会话", "why": "先点「开始」", "sources": [], "captures": [], "boundaries": []})
        return to_json(session.audit())

    @Slot(result=str)
    def scenarios(self) -> str:
        from tests.mock_source import SCENARIOS

        return to_json(sorted(SCENARIOS))

    @Slot(result=str)
    def renderWaterfall(self) -> str:  # noqa: N802 - JS 侧用驼峰
        """瀑布图页的「生成 PNG + HTML」按钮：强制重渲一次（绕过节流）。

        注意 ``@Slot`` 的参数表必须和方法签名一致：写成 ``@Slot(str, result=str)``
        时 Qt 会按「一个 str 入参」注册，网页只传回调就会被吞掉，前端拿到的是空值
        （自检里实测过：Promise 直接 resolve 成 ``[]``，Python 侧根本没被调用）。
        """
        return to_json(self._window.render_waterfall())

    @Slot(str, result=str)
    def openFile(self, path: str) -> str:  # noqa: N802 - JS 侧用驼峰
        """用系统默认程序打开产物（只允许 ``.data/`` 下的东西，防越权）。"""
        return to_json(self._window.open_report(path))

    @Slot(result=str)
    def gainState(self) -> str:  # noqa: N802 - JS 侧用驼峰
        """当前增益状态（模式/档位/底噪读数）。前端首屏和 `runtime()` 都会用。"""
        return to_json(self._window.gain_state())

    @Slot(str, result=str)
    def gainControl(self, payload: str) -> str:  # noqa: N802 - JS 侧用驼峰
        """顶栏增益控件：``{action:"mode"|"step"|"index"|"target", ...}``。

        只写「意图」，真正的 `GAIN` 命令由射频线程在**两次扫描之间**发出去
        （扫描中途插命令会把串口读缓冲打乱，实测 ``SPEC 启动回复异常``）。
        """
        return to_json(self._window.gain_control(payload))

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

    @Slot(str, result=str)
    def setFullscreen(self, payload: str) -> str:  # noqa: N802 - JS 侧用驼峰
        """网页里的「全屏」按钮。

        ``element.click()`` 是合成事件、没有用户手势，``requestFullscreen()`` 会被
        Chromium 直接拒（现场量的就是「点了没反应/跟普通布局一模一样」）。所以真正
        的全屏由 Python 侧调窗口 API 完成：手上有窗口，不需要手势。
        """
        return to_json(self._window.set_fullscreen(payload))

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

    def __init__(
        self,
        session: HostSession,
        bridge: Bridge,
        lang: str = "zh",
        box: dict[str, object] | None = None,
    ) -> None:
        super().__init__()
        self.session = session
        self.bridge = bridge
        self.lang = lang
        #: 最近一次 stats 快照的落脚点（**普通 dict，由窗口持有**）。
        #: 不要把它存在 Runner 自己身上：主线程去读一个还在跑的 QThread 实例属性，
        #: PySide6 会走原生属性查找 → 直接栈溢出崩进程（实测无任何 Python 回溯）。
        self.box: dict[str, object] = box if box is not None else {}
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
                    snapshot = session.stats(stats)
                    self.box["stats"] = snapshot  # 收尾时 scheduler 已拆，自检靠这份留档
                    bridge.stats.emit(to_json(snapshot))
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
        #: 增益模式默认值（HARDWARE/MANUAL/AUTO）；界面里随时可切
        self.default_gain_mode = "MANUAL"
        #: AUTO 模式目标底噪参考码
        self.default_gain_target = 4.0
        self.view = view
        self.bridge = Bridge(self)
        self.session: HostSession | None = None
        self.runner: Runner | None = None
        #: 采集线程写、主线程读的普通 dict（放 stats 留档；见 Runner.box 的注释）
        self.stats_box: dict[str, object] = {}
        self.ui_report = ""
        #: 最近一次 stats 快照（采集线程写；收尾自检要用 share 判定射频时间配比）
        self.last_stats: dict[str, object] = {}
        self.page_loaded = False
        #: 最后一帧频谱 / 最后一次表格：独立窗口是「晚连上」的，靠这个立刻有内容
        self.last_spectrum = ""
        self.last_tables = ""
        #: 独立窗口（window.open 来的）：(view, channel) —— channel 必须留引用，否则被 GC
        self.popouts: list[tuple[QWebEngineView, QWebChannel]] = []
        self.bridge.spectrum.connect(self._cache_spectrum)
        self.bridge.tables.connect(self._cache_tables)
        #: **必须持有 QWebChannel 的 Python 引用**：只交给 setWebChannel 时对象可能被 GC，
        #: 表现是前端 new QWebChannel 回调永不触发（Bridge 一直 ready=false）。
        self.channel: QWebChannel | None = None

    def _cache_spectrum(self, payload: str) -> None:
        self.last_spectrum = payload

    def _cache_tables(self, payload: str) -> None:
        self.last_tables = payload

    def gain_state(self) -> dict[str, object]:
        session = self.session
        if session is None:
            # 还没开始采集：给一份「将要用的」状态，顶栏不会空着
            return {
                "kind": "gain",
                "mode": self.default_gain_mode,
                "index": self.default_gain if self.default_gain is not None else 60,
                "effective": None,
                "floor_code": None,
                "target": self.default_gain_target,
                "tolerance": 1.5,
                "step": 2,
                "min_index": 0,
                "max_index": 90,
                "samples": 0,
                "adjusts": 0,
                "history": [],
                "note": "未开始采集（点了「开始」后这里会显示实时档位与底噪）",
                "mock": False,
                "ok": True,
            }
        return session.gain_state()

    def gain_control(self, payload: str) -> dict[str, object]:
        session = self.session
        if session is None:
            return {"kind": "gain", "ok": False, "note": tr(self.lang, "gain_no_session")}
        state = session.gain_control(payload)
        # 立刻回推一次：界面按下去就有反馈，不用等下一个 stats 周期
        self.bridge.gain.emit(to_json(state))
        return state

    def runtime(self) -> dict[str, object]:
        session = self.session
        runner = self.runner
        return {
            "gain": self.gain_state(),
            "running": bool(runner is not None and runner.isRunning()),
            "info": session.describe() if session is not None else None,
            "stats": session.stats() if session is not None else None,
            # 晚连上的窗口（独立小窗口）要靠这两个才有画面
            "spectrum": _loads(self.last_spectrum),
            "tables": _loads(self.last_tables),
        }

    def ui_defaults(self) -> dict[str, object]:
        return {
            "lang": self.lang,
            "port": self.default_port,
            "gain_index": self.default_gain,
            "gain_mode": self.default_gain_mode,
            "gain_target": self.default_gain_target,
        }

    # ---- 瀑布图出图（网页按钮 → Python） ---------------------------------
    def render_waterfall(self) -> dict[str, object]:
        """强制渲染最新时频热力图，返回产物路径给网页。

        为什么放在 GUI 线程里跑：一次渲染量的是环形缓冲（最多 1200 帧），
        实测 27 轮扫描约 0.5 s，冷启动秒级；为它单开线程不值得，
        代价是点按钮瞬间界面卡一下（按钮文案已换「生成中…」）。
        """
        session = self.session
        if session is None:
            return {"ok": False, "error": tr(self.lang, "not_connected")}
        plugin = None
        for item in session.registry.plugins:
            if getattr(item, "id", "") == "waterfall":
                plugin = item
                break
        if plugin is None:
            return {"ok": False, "error": "modes/ 里没有 waterfall 模块"}
        try:
            output = plugin.produce(force=True)
        except Exception as exc:  # noqa: BLE001 - 界面上要看到原因
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        payload = output.payload or {}
        files = [str(item) for item in (output.files or [])]
        index = str(payload.get("index") or "")
        png = next((f for f in files if f.lower().endswith(".png")), "")
        html = index if index else next((f for f in files if f.lower().endswith(".html")), "")
        return {
            "ok": True,
            "pages": int(payload.get("pages") or 0),
            "index": index,
            "png": os.path.basename(png) if png else "",
            "html": os.path.basename(html) if html else "",
        }

    def open_report(self, path: str) -> dict[str, object]:
        """用系统浏览器打开产物（只接受 ``.data/`` 之内的路径）。"""
        target = str(path or "").strip()
        if not target:
            return {"ok": False, "error": "空路径"}
        root = os.path.realpath(os.path.join(WEB_DIR, os.pardir, ".data"))
        real = os.path.realpath(target)
        if os.path.commonpath([root, real]) != root:
            return {"ok": False, "error": f"只允许打开 .data/ 下的产物：{target}"}
        if not os.path.isfile(real):
            return {"ok": False, "error": f"文件不存在：{target}"}
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(real)):
            return {"ok": False, "error": "系统没有可打开该文件的程序"}
        return {"ok": True, "path": real}

    # ---- 页面 -------------------------------------------------------------
    def _wire_page(self, page: QWebEnginePage) -> None:
        """页面接线：接住 window.open，以及网页里的 requestFullscreen()。

        不接 ``fullScreenRequested`` 的话 Qt 会**拒绝**全屏请求，前端就只能退化成
        「铺满窗口」—— 用户看到的现象就是「全屏跟普通布局一模一样」。
        """
        if isinstance(page, ConsolePage):
            page.owner = self
        page.fullScreenRequested.connect(self._on_fullscreen)

    def _on_fullscreen(self, request) -> None:
        view = request.page().view() if hasattr(request, "page") else None
        win = (view.window() if view is not None else self.view.window())
        request.accept()
        if request.toggleOn():
            win.showFullScreen()
        else:
            win.showNormal()
        print(f"[ui] 全屏 {'开' if request.toggleOn() else '关'}", flush=True)

    def load(self) -> None:
        page = self.view.page()
        self._wire_page(page)
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

    def set_fullscreen(self, payload: str) -> dict[str, object]:
        """payload = {"on": bool, "target": "main" | "popout"}（老式 true/false 也认）"""
        try:
            data = json.loads(payload) if payload else {}
        except ValueError:
            data = {}
        if isinstance(data, bool):
            wanted, target = data, "main"
        else:
            wanted = bool((data or {}).get("on"))
            target = str((data or {}).get("target") or "main")
        win = self.view.window()
        if target == "popout":
            live = self.live_popouts()
            if live:
                win = live[-1].window()
        if wanted:
            win.showFullScreen()
        else:
            win.showNormal()
        return {"fullscreen": bool(win.isFullScreen()), "target": target, "on": wanted}

    # ---- 独立窗口（窗口外弹窗）--------------------------------------------
    def open_popout(self) -> QWebEnginePage:
        """接住前端的 ``window.open``：开一个真正的窗口，共用同一个 Bridge。

        新窗口加载的还是 ``web/index.html``，只是带 ``?solo=<页面:序号>``，
        由网页自己把它变成「只显示那一个面板」。数据不用另外接：QWebChannel
        把同一个 :class:`Bridge` 暴露给每个窗口，信号天然广播。
        """
        view = QWebEngineView()
        page = ConsolePage(view)
        view.setPage(page)
        self._wire_page(page)
        channel = QWebChannel(page)
        channel.registerObject("backend", self.bridge)
        page.setWebChannel(channel)
        configure_settings(view)
        view.setWindowTitle("esp-sdr · 面板窗口")
        view.resize(1024, 680)
        view.show()
        self.popouts.append((view, channel))
        view.destroyed.connect(lambda *_: self._forget_popout(view))
        print("[ui] 独立窗口已打开", flush=True)
        return page

    def _forget_popout(self, view: QWebEngineView) -> None:
        self.popouts = [(v, c) for (v, c) in self.popouts if v is not view]

    def live_popouts(self) -> list[QWebEngineView]:
        return [v for (v, _c) in self.popouts if v.isVisible()]

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
        self.runner = Runner(session, self.bridge, config.lang, box=self.stats_box)
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
        for view in list(self.live_popouts()):
            view.close()


def build_app(argv: list[str]) -> tuple[QApplication, QWebEngineView, HostWindow, argparse.Namespace]:
    parser = argparse.ArgumentParser(description="esp-sdr 测试上位机（WebView 版）")
    parser.add_argument("--port", default="COM56", help="默认串口（界面里还能改）")
    parser.add_argument("--gain-index", type=int, default=60, help="默认固定增益档位（真机）")
    parser.add_argument(
        "--gain-mode",
        default="MANUAL",
        choices=["HARDWARE", "MANUAL", "AUTO"],
        help="默认增益模式：HARDWARE=固件 AGC / MANUAL=固定档位 / AUTO=主机闭环（把底噪压到 --gain-target）",
    )
    parser.add_argument("--gain-target", type=float, default=4.0, help="--gain-mode AUTO 的目标底噪参考码")
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
    configure_settings(view)
    view.setWindowTitle("esp-sdr 测试上位机 · ESP32-C5 射频监测")
    view.show()

    window = HostWindow(view)
    window.lang = args.lang
    window.default_port = args.port
    window.default_gain = args.gain_index
    window.default_gain_mode = args.gain_mode
    window.default_gain_target = args.gain_target
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
            probe: (window.Pages.wifi.probe ? Pages.wifi.probe() : null),
            popoutAny: (function () {
                const p = document.querySelectorAll('#page-container .panel')[0];
                if (!p || !window.Popout) return { ok: false };
                Popout.open(p);
                const opened = !!document.querySelector('.popout:not([hidden])') && p.parentElement.className === 'popout__stage';
                const themed = getComputedStyle(p.querySelector('.panel__zoom') || p).display !== 'none';
                Popout.close();
                return { ok: opened, themed: themed, back: !p.classList.contains('panel--popped') };
            })()
        })""",
        "ble": """JSON.stringify({
            rows: document.querySelectorAll('#ble-rows tr').length,
            risk: ((document.querySelector('#risk-type')||{}).textContent||''),
            riskNote: ((document.querySelector('#risk-note')||{}).textContent||''),
            evidence: document.querySelectorAll('#ev-rows tr').length,
            hints: document.querySelectorAll('#ble-hints li').length,
            notes: document.querySelectorAll('#ble-notes li').length,
            warn: ((document.querySelector('#ble-warn')||{}).textContent||'').length,
            probe: (window.Pages.ble.probe ? Pages.ble.probe() : null)
        })""",
        "waterfall": """JSON.stringify({
            probe: (window.Pages.waterfall && Pages.waterfall.probe ? Pages.waterfall.probe() : null),
            cards: document.querySelectorAll('#page-container .card').length,
            banner: (((document.querySelector('#wf-banner')||{}).textContent) || '').length,
            files: (((document.querySelector('#wf-files')||{}).textContent) || '').length,
            sweep: (((document.querySelector('#wf-sweep-s')||{}).textContent) || ''),
            sweepNote: (((document.querySelector('#wf-sweep-note')||{}).textContent) || '')
        })""",
        "capture": """JSON.stringify({
            rows: document.querySelectorAll('#cap-rows tr').length,
            filters: document.querySelectorAll('#cap-filter-rows tr').length,
            spurs: document.querySelectorAll('#cap-spur-rows tr').length,
            thresholds: document.querySelectorAll('#cap-th-rows tr').length,
            notes: document.querySelectorAll('#cap-notes li').length,
            warn: (((document.querySelector('#cap-warn')||{}).textContent) || '').length,
            records: (((document.querySelector('#cap-records')||{}).textContent) || ''),
            audit: (((document.querySelector('#cap-audit-headline')||{}).textContent) || ''),
            auditRows: document.querySelectorAll('#cap-audit-rows tr').length,
            auditBoundary: document.querySelectorAll('#cap-audit-boundary li').length,
            probe: (window.Pages.capture ? Pages.capture.probe() : null)
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
        ble_page = pages.get("ble", {}) if isinstance(pages, dict) else {}
        cap_page = pages.get("capture", {}) if isinstance(pages, dict) else {}
        wf_page = pages.get("waterfall", {}) if isinstance(pages, dict) else {}
        wf_page_probe = (wf_page.get("probe") or {}) if isinstance(wf_page, dict) else {}
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
        page_probe = (spectrum.get("pageProbe") or {}) if isinstance(spectrum, dict) else {}
        axis = page_probe.get("wf2dAxis") or {}
        popout = report.get("popout") or {}
        pp_open = popout.get("open") or {}
        pp_zoom = popout.get("zoom") or {}
        pp_close = popout.get("close") or {}
        pp_win = report.get("popout_window") or {}
        share: dict[str, object] = {}
        try:
            # 注意：finish() 是模块级函数里的闭包，只能拿 window.*，用 self.* 会 NameError
            # （在 Qt 回调里抛异常是 C 级栈溢出、连 Python 回溯都没有，排查成本极高）
            cached = window.stats_box.get("stats") or {}
            share = dict(cached.get("share") or {})
            if not share and window.session is not None:
                share = dict((window.session.stats() or {}).get("share") or {})
        except Exception:  # noqa: BLE001 - 自检收尾不该因统计失败而崩
            share = {}
        report["share"] = share


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
                # 与 waterfall 平分射频后 8s 内只走过 2 个窗（独占时 >3）；
                # 这里守的是"游标在轨道内真的在动"，不是"动得快"
                int(bar.get("visited", 0) or 0) >= 2
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
            # 2D 瀑布频率轴：5 个刻度、升序、中点是本窗中心频点
            "wf2d_axis": (
                len(axis.get("ticks") or []) == 5
                and list(axis.get("ticks") or []) == sorted(axis.get("ticks") or [])
                # 中点 = 本窗中心频点，且落在两端之间
                and int(axis.get("lo", 0)) < int(axis.get("center", 0)) < int(axis.get("hi", 0))
            ),
            # 双击弹出：浮层出现、面板搬进浮层；放大到 150% 后画布真的更宽；关闭后回到页面
            "popout_open": bool(pp_open.get("inOverlay")) and float(pp_open.get("width", 0) or 0) > 0,
            "popout_zoom": (
                float(pp_zoom.get("zoom", 0) or 0) == 1.5
                and float(pp_zoom.get("width", 0) or 0) > float(pp_open.get("width", 0) or 0) * 1.35
            ),
            "popout_restore": bool(pp_close.get("backHome")) and pp_close.get("overlay") is False,
            # 全屏/放大后高度自适应：面板高度贴着浮层高度，画布比原来高（不是留一大片空白）
            "popout_fill_height": (
                bool(pp_zoom.get("fitGrow"))
                and float(pp_zoom.get("height", 0) or 0) > float(pp_open.get("height", 0) or 0) * 1.3
                and float(pp_zoom.get("panelH", 0) or 0) >= float(pp_zoom.get("stageH", 0) or 0) * 0.9
            ),
            # 全屏：窗口真进了全屏，且面板把整屏高度吃满（不是缩在中间）
            "popout_fullscreen": bool((report.get("popout_fullscreen") or {}).get("check")),
            # 窗口外弹窗：新的独立窗口里只有那个面板，且画布真的按窗口尺寸渲染
            "popout_window": (
                bool(pp_win.get("solo"))
                and bool(pp_win.get("panel"))
                and int((pp_win.get("shot") or {}).get("saved") is True)
                and any(
                    float(c.get("h", 0) or 0) > 200 and float(c.get("w", 0) or 0) > 300
                    for c in (pp_win.get("canvases") or [])
                )
            ),
            # 换页也能弹：通用逻辑不依赖频谱页；⤢ 按钮存在
            "popout_any_page": bool((wifi.get("popoutAny") or {}).get("ok"))
            and bool((wifi.get("popoutAny") or {}).get("back"))
            and bool((wifi.get("popoutAny") or {}).get("themed")),
            "wifi_rows": int(wifi.get("rows", 0) or 0) > 5,
            "wifi_recommend": bool(str(wifi.get("rec24", "")).startswith("ch")),
            "wifi_notes": int(wifi.get("notes", 0) or 0) >= 3,
            # BLE 风险页：判词/证据链/广播信道/建议 都要真有东西
            "ble_rows": int(ble_page.get("rows", 0) or 0) >= 3,
            "ble_risk": len(str(ble_page.get("risk", ""))) > 1,
            "ble_evidence": int(ble_page.get("evidence", 0) or 0) >= 6,
            "ble_hints": int(ble_page.get("hints", 0) or 0) >= 1,
            "ble_notes": int(ble_page.get("notes", 0) or 0) >= 3,
            "ble_bars3d": bool((ble_page.get("probe") or {}).get("bars3d")),
            # 抓拍页：门槛表真的从模块下发、过滤计数真的在渲染、边界声明不空
            "capture_page": (
                int(cap_page.get("rows", 0) or 0) >= 1
                and int(cap_page.get("filters", 0) or 0) >= 7
                and int(cap_page.get("thresholds", 0) or 0) >= 10
                and int(cap_page.get("notes", 0) or 0) >= 3
                and int(cap_page.get("warn", 0) or 0) > 30
                and str((cap_page.get("probe") or {}).get("kind", "")) == "capture"
                and float((cap_page.get("probe") or {}).get("span", 0) or 0) > 0
            ),
            # 一键体检接口：Bridge.audit → app.audit.summary → 表格 + 四条边界
            "capture_audit": int(cap_page.get("auditRows", 0) or 0) >= 1
            and int(cap_page.get("auditBoundary", 0) or 0) == 4
            and len(str(cap_page.get("audit", ""))) > 4,
            # 抓档链路真的接上了（模块拿到 capture_fn + 存档目录）
            "capture_wired": bool(window.session is not None and window.session.capture_plugins)
            and bool(str(window.session.capture_dir)),
            # 增益控件：手动→升档→自动 一路走通，且顶栏读数是 Python 回推的
            "gain_manual": str((report.get("gain") or {}).get("index0", "")) != "",
            "gain_step": bool(report.get("gain"))
            and int((report.get("gain") or {}).get("stepped", {}).get("index", -1))
            == int((report.get("gain") or {}).get("index0", -9)) + 1,
            "gain_auto": str(((report.get("gain") or {}).get("auto") or {}).get("mode", "")) == "AUTO",
            "gain_on_button": list((report.get("gain") or {}).get("on") or []) == ["AUTO"],
            "gain_readback": "底噪" in str((report.get("gain") or {}).get("read", "")),
            "raw_panels": int(raw.get("panels", 0) or 0) >= 1,
            "raw_text": int(raw.get("textLen", 0) or 0) > 40,
            # 瀑布图页：页面自报的画布/卡片/声明都到位，且热力图像素真画上了
            "waterfall_rows": int(wf_page_probe.get("rows", 0) or 0) >= 1,
            "waterfall_painted": float(wf_page_probe.get("painted", 0) or 0) > 1.0,
            "waterfall_cards": int(wf_page.get("cards", 0) or 0) >= 4,
            "waterfall_banner": int(wf_page.get("banner", 0) or 0) > 20,
            "waterfall_notes": int(wf_page_probe.get("notes", 0) or 0) >= 3,
            "waterfall_sweep": str(wf_page.get("sweep", "")).endswith(" s"),
            # 单射频分时：瀑布图必须真拿到射频时间，不能被别的模块饿死。
            # 门槛按 META-0087 的五模块公平分摊算：5 个模块各 weight 1.0 → 各 ~20%，
            # 所以「没被饿死」= ≥15%（写死 0.3 是四模块时代的遗留，2026-11 抓拍上线后假红）。
            "waterfall_share": float(share.get("waterfall", 0) or 0) >= 0.15
            and float(share.get("capture", 0) or 0) >= 0.15,
            "waterfall_render": bool((report.get("waterfall_render") or {}).get("open"))
            and len(str((report.get("waterfall_render") or {}).get("files", ""))) > 10,
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
                # 频谱页验完顺手验「双击弹出预览」，再验中英切换，然后保持英文继续验后面的页
                QTimer.singleShot(300, lambda: popout_test(lambda: popout_window_test(lambda: popout_fullscreen_test(lambda: switch_lang(nxt[0])))))
            elif page_id == "waterfall":
                # 瀑布图页：点一次「生成 PNG + HTML」，验 JS→Bridge→produce(force) 整条链路
                waterfall_render_test(lambda: stage(nxt[0]))
            elif page_id == "ble":
                # BLE 页探完顺手动一遍顶栏增益控件：JS → Bridge → HostSession → 状态回推
                QTimer.singleShot(400, lambda: gain_test(lambda: stage(nxt[0])))
            elif nxt:
                QTimer.singleShot(400, lambda: stage(nxt[0]))
            else:
                finish()

        QTimer.singleShot(1800, probe)

    def gain_test(then) -> None:
        """顶栏增益控件实操：切手动 → 升一档 → 切自动，读回状态与显示。

        验的是「按钮真能改到射频参数」这条链，不是好看：
        `#gain-read` 由 Python 的 `stats.gain` 驱动，它有数就说明
        Slot → HostSession.gain_control → 回推 → 顶栏渲染 全都通了。
        """
        view.page().runJavaScript(
            "(async function () {"
            " window.__gain = {};"
            " var call = async function (o) { return Bridge.call('gainControl', JSON.stringify(o)); };"
            " try {"
            "   var before = Gain.probe() || {};"
            "   window.__gain.manual = await call({action: 'mode', mode: 'MANUAL'});"
            "   window.__gain.index0 = window.__gain.manual.index;"
            "   window.__gain.stepped = await call({action: 'step', delta: 1});"
            "   window.__gain.auto = await call({action: 'mode', mode: 'AUTO'});"
            "   window.__gain.target = await call({action: 'target', target: 5});"
            "   window.__gain.before = {mode: before.mode, index: before.index};"
            " } catch (e) { window.__gain.err = String(e); }"
            "})()"
        )

        def read_back() -> None:
            view.page().runJavaScript(
                "JSON.stringify(Object.assign({}, window.__gain || {}, {"
                "  read: ((document.querySelector('#gain-read')||{}).textContent||''),"
                "  note: ((document.querySelector('#gain-note')||{}).textContent||''),"
                "  on: Array.from(document.querySelectorAll('[data-gain-mode]'))"
                "        .filter(function (b) { return b.classList.contains('seg__btn--on'); })"
                "        .map(function (b) { return b.dataset.gainMode; }),"
                "  live: Gain.probe()"
                "}))",
                0,
                collect_gain,
            )

        def collect_gain(result: object) -> None:
            try:
                payload = json.loads(str(result)) if result else {}
            except Exception:  # noqa: BLE001
                payload = {"parse_error": str(result)}
            payload["shot"] = shot("gain")
            report["gain"] = payload
            steps.append(
                "增益控件：" + json.dumps(
                    {k: payload.get(k) for k in ("index0", "read", "on")}, ensure_ascii=False
                )
            )
            then()

        QTimer.singleShot(1000, read_back)

    def waterfall_render_test(then) -> None:
        """点瀑布图页的「生成 PNG + HTML」，等生成完后读回状态行。

        为什么放 4.5 s 后读：渲染是同步跑的（QThread 里没占，直接在主线程），
        一次 buffered 全量渲染约 0.1～1 s；固定等 4.5 s 是为了避开慢机器抖动，
        免得把「还没回包」误判为失败。
        """

        def read_back() -> None:
            view.page().runJavaScript(
                "JSON.stringify({files: ((document.querySelector('#wf-files')||{}).textContent||''),"
                " open: !((document.querySelector('#wf-open')||{}).disabled)})",
                0,
                collect_render,
            )

        def collect_render(result: object) -> None:
            try:
                payload = json.loads(str(result)) if result else {}
            except Exception:  # noqa: BLE001
                payload = {"parse_error": str(result)}
            report["waterfall_render"] = payload
            steps.append(f"瀑布图出图：{json.dumps(payload, ensure_ascii=False)}")
            then()

        view.page().runJavaScript(
            "(function(){const b=document.querySelector('#wf-render'); if(b){b.click(); return true;} return false;})()"
        )
        QTimer.singleShot(4500, read_back)

    def popout_test(then) -> None:
        """双击面板 → 浮层 → 放大 150% 重画 → 关闭复位。结果由 Popout.selftest() 写进 window.__popout。"""

        def read_out() -> None:
            view.page().runJavaScript(
                "JSON.stringify(window.__popout || {})", 0, lambda r: collect_popout(r, then)
            )

        view.page().runJavaScript("Popout.selftest()")
        QTimer.singleShot(1600, read_out)

    def collect_popout(result: object, then) -> None:
        try:
            payload = json.loads(str(result)) if result else {}
        except Exception:  # noqa: BLE001
            payload = {"parse_error": str(result)}
        payload["shot"] = shot("spectrum-popout")
        report["popout"] = payload
        steps.append(f"弹出预览：{json.dumps({k: payload.get(k) for k in ('open', 'zoom', 'close')}, ensure_ascii=False)}")
        QTimer.singleShot(200, then)

    def popout_window_test(then) -> None:
        """点「独立窗口」按钮 → Python 侧 createWindow → 新窗口真的渲染出画布。"""
        view.page().runJavaScript("Popout.openDocked()")

        def read_out() -> None:
            live = window.live_popouts()
            if not live:
                report["popout_window"] = {"ok": False, "windows": len(window.popouts)}
                steps.append("独立窗口：没开出来")
                QTimer.singleShot(200, then)
                return
            child = live[-1]

            def collect_child(result: object) -> None:
                try:
                    payload = json.loads(str(result)) if result else {}
                except Exception:  # noqa: BLE001
                    payload = {"parse_error": str(result)}
                image = child.grab()
                path = (args.shot or "selftest.png")
                root, ext = os.path.splitext(path)
                shot_path = f"{root}-window{ext or '.png'}"
                payload["shot"] = {"path": shot_path, "saved": bool(image.save(shot_path))}
                payload["windows"] = len(live)
                payload["title"] = child.windowTitle()
                report["popout_window"] = payload
                steps.append(f"独立窗口：{json.dumps(payload, ensure_ascii=False)}")
                QTimer.singleShot(200, then)

            child.page().runJavaScript(
                "JSON.stringify({"
                " solo: document.body.classList.contains('solo'),"
                " panel: !!Popout.current,"
                " key: Popout.current ? Popout.current.dataset.panelKey : '',"
                " canvases: Array.from(document.querySelectorAll('canvas')).map(c => ({id: c.id || '(3d)', w: c.width, h: c.height})),"
                " frames: (window.probeReport ? probeReport().framesSeen : -1)"
                "})",
                0,
                collect_child,
            )

        QTimer.singleShot(2200, read_out)

    def popout_fullscreen_test(then) -> None:
        """点「全屏」→ 真的进全屏，且面板把整屏高度用满（不是照原样缩在中间）。"""
        view.page().runJavaScript(
            "(function () {"
            " var g = document.querySelector('#page-container [data-fit-grow=\"1\"]');"
            " var p = g && g.closest('.panel');"
            " if (!p) return 'no panel';"
            " Popout.open(p);"
            " return 'ok';"
            "})()"
        )

        def click_full() -> None:
            view.page().runJavaScript("document.querySelector('.popout [data-act=\"full\"]').click()")

        def read_out() -> None:
            win = window.view.window()

            def collect(result: object) -> None:
                try:
                    payload = json.loads(str(result)) if result else {}
                except Exception:  # noqa: BLE001
                    payload = {"parse_error": str(result)}
                payload["full"] = bool(win.isFullScreen())
                payload["screenH"] = int(window.view.screen().geometry().height())
                payload["check"] = bool(win.isFullScreen()) and (
                    float(payload.get("panelH", 0) or 0) >= float(payload.get("stageH", 0) or 0) * 0.85
                ) and float(payload.get("stageH", 0) or 0) >= payload["screenH"] * 0.8
                report["popout_fullscreen"] = payload
                steps.append(f"全屏：{json.dumps(payload, ensure_ascii=False)}")
                # 收尾：再点一次「退出全屏」+ 关浮层，后面还要截图
                view.page().runJavaScript("document.querySelector('.popout [data-act=\"full\"]').click()")
                QTimer.singleShot(900, leave)

            def leave() -> None:
                view.page().runJavaScript("if (window.Popout) Popout.close();")
                QTimer.singleShot(600, then)

            view.page().runJavaScript(
                "(function () {"
                " var st = document.querySelector('.popout__stage');"
                " var pn = document.querySelector('.panel--popped');"
                " var css = pn ? parseFloat(getComputedStyle(pn).getPropertyValue('--fit-grow')) || 0 : 0;"
                " return JSON.stringify({"
                "   stageH: st ? Math.round(st.getBoundingClientRect().height) : 0,"
                "   panelH: pn ? Math.round(pn.getBoundingClientRect().height) : 0,"
                "   fitGrow: Math.round(css),"
                "   innerH: window.innerHeight,"
                "   label: (document.querySelector('.popout [data-act=\"full\"]') || {}).textContent || '',"
                "   bridge: !!(window.Bridge && Bridge.ready())"
                " });"
                "})()",
                0,
                collect,
            )

        QTimer.singleShot(400, click_full)
        QTimer.singleShot(1800, read_out)

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

    ORDER = ["spectrum", "waterfall", "wifi", "ble", "capture", "raw"]

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
                    # 自检也走同一套模式选择：AUTO 会被自检顺手验一遍
                    "gain_mode": args.gain_mode,
                    "gain_target": args.gain_target,
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
                        "gain_mode": args.gain_mode,
                        "gain_target": args.gain_target,
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