"""上位机逻辑层（**不依赖 Qt**）：连链路、跑调度、把快照/产出转成前端要的 JSON。

分层（对齐参考工程 ``MagicRobot_V0.1/host`` 的做法）：

    host/app.py       Qt 外壳：QWebEngineView + QWebChannel，只做窗口与桥
    host/session.py   ← 本文件：无界面逻辑，可用 unittest 直接测
    host/web/         前端（HTML/CSS/JS + three.js 3D 引擎）

``app.py`` 里的 ``Runner`` 线程只做两件事：反复调 :meth:`HostSession.step` 跑时间片、
把 :meth:`HostSession.spectrum` / :meth:`HostSession.tables` 的 JSON 丢给前端。
所有能出错、能算错的东西都在本文件，方便不开窗口也能验证。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import paths  # 本地模块：定位分析层（core/ modes/ output/ tests/）
from gain import MODES as GAIN_MODES, GainAwareSource, GainControl, parse_gain_line  # noqa: E402

#: 分析层目录（<proj>/repo/examples/esp32c5-rf-monitor 或 $ESPSDR_SRC）
SRC_DIR = str(paths.ensure_on_path())

from core.registry import Output, Registry, discover  # noqa: E402 - 必须先 ensure_on_path
from core.scheduler import Scheduler, SchedulerStats  # noqa: E402
from core.serial_link import EspSdrLink  # noqa: E402
from core.snapshot import Snapshot  # noqa: E402
from tests.fake_firmware import FakeFirmware  # noqa: E402
from tests.mock_modes import ProbePlugin  # noqa: E402
from tests.mock_source import SCENARIOS  # noqa: E402
from app import bind_capture  # noqa: E402
from app.capture_link import (  # noqa: E402
    CaptureAwareSource,
    iter_capture_plugins,
    profile_for,
    synthetic_capture,
)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(SRC_DIR, ".data")
WEB_DIR = os.path.join(HERE, "web")

# ---------------------------------------------------------------------------
# 界面文案（中/英）。前端自己有一份完整的 UI 词典；这里只翻 Python 侧会发给
# 界面的那几条（链路说明、启停消息、错误），模块自带的表格/ASCII 沿用模块语言。
# ---------------------------------------------------------------------------
MESSAGES: dict[str, dict[str, str]] = {
    "zh": {
        "probe_only": "modes/ 还没有功能模块 → 只用「链路探针」验证真机链路",
        "read_link_failed": "读链路信息失败：{error}",
        "already_running": "已经在跑了，先停一下",
        "no_run": "当前没有在跑",
        "started": "已开始：{what}",
        "stopped": "已停止",
        "not_connected": "（未连接）",
        "no_output": "（没有模块产出）",
        "rf_error": "[射频错误] {message}",
        "auto_stop": "连续 {n} 个时间片失败，已自动停机。最后一次：{message}",
        "pack_failed": "打包频谱失败：{error}",
        "thread_error": "[线程异常] {error}",
        "already_running_log": "[打开失败] {message}",
        "gain_no_session": "还没开始采集：点「开始」后再调增益",
    },
    "en": {
        "probe_only": "No functional module in modes/ yet — using the link probe to verify the board",
        "read_link_failed": "Failed to read link info: {error}",
        "already_running": "Already running — stop it first",
        "no_run": "Nothing is running",
        "started": "Started: {what}",
        "stopped": "Stopped",
        "not_connected": "(not connected)",
        "no_output": "(no module output)",
        "rf_error": "[RF error] {message}",
        "auto_stop": "{n} consecutive slices failed — stopped automatically. Last: {message}",
        "pack_failed": "Failed to pack spectrum: {error}",
        "thread_error": "[Thread error] {error}",
        "already_running_log": "[Open failed] {message}",
        "gain_no_session": "Not collecting yet — press Start before changing gain",
    },
}

#: 支持的语言（前端下拉/按钮也只给这两个）
LANGS = ("zh", "en")


def tr(lang: str | None, key: str, **kwargs: Any) -> str:
    """取一条 UI 文案；缺语言/缺 key 时退回中文（绝不抛，界面不能因为文案挂掉）。"""
    table = MESSAGES.get((lang or "zh").lower()) or MESSAGES["zh"]
    template = table.get(key) or MESSAGES["zh"].get(key) or key
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        return template

#: 默认下行点数：前端画图够用，又不至于把 QWebChannel 灌满（1024 → 256 取桶内最大值）
DEFAULT_BINS_OUT = 256


def build_registry(lang: str = "zh") -> tuple[Registry, list[str]]:
    """扫描 ``modes/`` 包，拿到所有功能模块；一个都没有时退化成一个链路探针。"""
    registry = discover("modes")
    notes = list(registry.errors)
    if len(registry) == 0:
        registry.register(ProbePlugin(name="链路探针" if lang == "zh" else "Link probe", slice_weight=1.0))
        notes.append(tr(lang, "probe_only"))
    return registry, notes


def list_ports() -> list[dict[str, str]]:
    """本机串口清单（给前端下拉框用）。打不开的口也会列出来，但不主动去开。"""
    try:
        from serial.tools import list_ports as lp
    except Exception:  # pragma: no cover - pyserial 必装
        return []
    out = []
    for info in lp.comports():
        out.append(
            {
                "device": info.device,
                "description": info.description or "",
                "hwid": info.hwid or "",
            }
        )
    return out


def downsample_bins(bins: Iterable[float], out_n: int = DEFAULT_BINS_OUT) -> list[float]:
    """把 N 个 bin 压成 ``out_n`` 个桶，**取桶内最大值**。

    取最大值而不是平均：窄带信号（BLE / CW）只有几个 bin 宽，平均会被噪声底稀释，
    峰值保留才能在图上看出来。代价是噪声底看起来比实际高一点——图注里写清楚了。
    """
    values = list(bins)
    if not values:
        return []
    out_n = max(1, min(int(out_n), len(values)))
    per = len(values) / out_n
    out: list[float] = []
    for i in range(out_n):
        lo = int(i * per)
        hi = max(lo + 1, int((i + 1) * per))
        chunk = values[lo:hi]
        out.append(max(chunk) if chunk else 0.0)
    return out


#: 允许前端下发的配置字段 → (类型, 是否可空)。漏了字段直接报错，避免"传了但没生效"。
CONFIG_FIELDS: dict[str, tuple[type, bool]] = {
    "port": (str, False),
    "baudrate": (int, False),
    "gain_index": (int, True),
    "gain_mode": (str, True),
    "gain_target": (float, True),
    "mock": (bool, False),
    "scenario": (str, False),
    "bins_out": (int, False),
    "store_path": (str, False),
    "lang": (str, False),
}


@dataclass
class HostConfig:
    """前端「开始」按钮传下来的一整包参数。"""

    port: str = "COM56"
    baudrate: int = 2_000_000
    gain_index: int | None = None
    #: 增益模式：HARDWARE(固件 AGC) / MANUAL(固定档) / AUTO(主机闭环)；空 = 有档位就 MANUAL
    gain_mode: str | None = None
    #: AUTO 模式的目标底噪参考码（功率码，不是 dBm）
    gain_target: float | None = None
    mock: bool = False
    scenario: str = "mixed"
    bins_out: int = DEFAULT_BINS_OUT
    store_path: str = ""  # 空 = 不入库
    lang: str = "zh"  # 界面语言（zh/en），只影响 Python 侧发给界面的那几条文案

    @classmethod
    def from_json(cls, text: str) -> "HostConfig":
        raw = json.loads(text) if text else {}
        cfg = cls()
        for key, value in raw.items():
            if key not in CONFIG_FIELDS:
                raise ValueError(f"未知参数：{key}")
            kind, nullable = CONFIG_FIELDS[key]
            if value is None or value == "":
                if nullable or kind is str:
                    setattr(cfg, key, None if nullable else "")
                    continue
                raise ValueError(f"参数 {key} 不能为空")
            if kind is bool:
                setattr(cfg, key, bool(value))
            elif kind is int:
                setattr(cfg, key, int(value))
            elif kind is float:
                setattr(cfg, key, float(value))
            else:
                setattr(cfg, key, str(value))
        cfg.lang = (cfg.lang or "zh").lower()
        if cfg.lang not in LANGS:
            raise ValueError(f"未知语言：{cfg.lang}（可选 {'/'.join(LANGS)}）")
        if cfg.gain_mode and cfg.gain_mode.upper() not in GAIN_MODES:
            raise ValueError(f"未知增益模式：{cfg.gain_mode}（可选 {'/'.join(GAIN_MODES)}）")
        if cfg.gain_mode:
            cfg.gain_mode = cfg.gain_mode.upper()
        if cfg.scenario not in SCENARIOS:
            raise ValueError(f"未知场景：{cfg.scenario}（可选 {', '.join(sorted(SCENARIOS))}）")
        return cfg

    def validate(self) -> None:
        if not self.mock and not self.port:
            raise ValueError("真机模式必须给串口")
        if self.gain_index is not None and self.mock:
            self.gain_index = None
        if self.baudrate <= 0:
            raise ValueError("波特率必须为正")


@dataclass
class HostSession:
    """一次「打开链路 + 跑调度」的会话；由 Qt 线程驱动，也可纯脚本驱动。"""

    bins_out: int = DEFAULT_BINS_OUT
    frames_in: int = 0
    frames_out: int = 0
    started_at: float = 0.0
    registry: Registry = field(init=False)
    notes: list[str] = field(init=False, default_factory=list)
    link: EspSdrLink | None = field(init=False, default=None)
    scheduler: Scheduler | None = field(init=False, default=None)
    store: Any = field(init=False, default=None)
    config: HostConfig | None = field(init=False, default=None)
    last_error: str = field(init=False, default="")
    #: 增益调控（GUI 线程写意图 / 射频线程落地 + 闭环；见 host/gain.py）
    gain: GainControl = field(init=False, default_factory=GainControl)
    #: 每个快照的回调（GUI 用它推频谱；``step()`` 每次可换）
    on_snapshot_cb: Any = field(init=False, default=None, repr=False)
    #: 支持抓档的模块 + 落盘目录 + 最近抓档日志（任务 5）
    capture_plugins: list[Any] = field(init=False, default_factory=list)
    capture_dir: str = field(init=False, default="")
    capture_log: list[str] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.registry, self.notes = build_registry()

    @property
    def lang(self) -> str:
        return (self.config.lang if self.config else "zh") or "zh"

    # ---- 启停 ------------------------------------------------------------
    def open(self, config: HostConfig) -> dict[str, Any]:
        """打开链路（真机 or 假固件）并建好调度器。返回信息块给前端顶部显示。"""
        config.validate()
        self.config = config
        # 语言可能在 open() 时才确定（前端点「开始」才下发），这里重建一次说明文案
        if (config.lang or "zh") != "zh" or not self.notes:
            self.registry, self.notes = build_registry(config.lang)
        factory = None
        if config.mock:
            scenario = config.scenario
            factory = lambda port, baud, timeout=5.0: FakeFirmware(  # noqa: E731
                port, baud, timeout, scenario=scenario
            )
        else:
            factory = None
        self.link = EspSdrLink(config.port, config.baudrate, factory=factory)
        self.link.open()
        mode = config.gain_mode or ("MANUAL" if config.gain_index is not None else "HARDWARE")
        self.gain = GainControl(
            mode=mode,
            index=config.gain_index if config.gain_index is not None else None,
            target=config.gain_target if config.gain_target is not None else 4.0,
            mock=config.mock,
        )
        # 初始档位**建控制器时**就落地：真链路等第一次 scan 才设的话，前几帧都是 AGC 值
        if not config.mock:
            # 先问固件要真实的档位范围（实板 0..81，兜底写死 90 会给出无效档）
            try:
                self.gain.set_limits(parse_gain_line(self.link.command("GAIN?", strict=False)))
            except Exception:  # noqa: BLE001 - 老固件不认 GAIN? 就用兜底范围
                pass
            self.gain.apply(self.link)
        self.store = None
        if config.store_path:
            from core.store import Store

            self.store = Store(config.store_path)
        self.last_run_stats = None
        # 抓档（任务 5）：把「真抓」函数交给有 capture_fn 槽的模块，再包一层 Source
        # 让抓档只在两次扫描之间落地（跟增益同一条纪律，理由见 app/capture_link.py）。
        source: Any = GainAwareSource(self.link, self.gain)
        self.capture_plugins = iter_capture_plugins(self.registry)
        self.capture_dir = ""
        if self.capture_plugins:
            capture_fn: Any
            if config.mock:
                # mock 下按场景挑假抓档长相：CW 场景给纯单音、图传/微波炉给平谱、
                # 间歇场景给半段载波 —— 这样存档的 IQ 文件本身带着场景特征，能回放验证。
                profile = profile_for(config.scenario)
                capture_fn = lambda f, n, r, b: synthetic_capture(f, n, r, b, profile=profile)  # noqa: E731
            else:
                capture_fn = self._link_capture
            base_dir = os.path.dirname(config.store_path) if config.store_path else DEFAULT_DIR
            self.capture_dir = os.path.join(base_dir or DEFAULT_DIR, "capture")
            bind_capture(self.registry, capture_fn, out_dir=self.capture_dir)
            source = CaptureAwareSource(source, self.capture_plugins, capture_fn, logger=self._log_capture)
        self.scheduler = Scheduler(
            # 包一层：增益变更只在两次扫描之间落地（扫描中途插 GAIN 会把串口读缓冲打乱）
            source,
            self.registry,
            store=self.store,
            on_snapshot=self._dispatch_snapshot,
        )
        self.frames_in = 0
        self.frames_out = 0
        self.started_at = time.time()
        self.last_error = ""
        return self.describe()

    def close(self) -> None:
        if self.link is not None:
            try:
                if not (self.config and self.config.mock):
                    self.link.set_gain("HARDWARE")
            except Exception:  # noqa: BLE001 - 关链路时不掩盖原错误
                pass
            try:
                self.link.close()
            except Exception:  # noqa: BLE001
                pass
        if self.store is not None:
            try:
                self.store.close()
            except Exception:  # noqa: BLE001
                pass
        self.link = None
        self.scheduler = None
        self.store = None

    # ---- 抓档 ------------------------------------------------------------
    def _link_capture(self, freq_hz: int, samples: int, rate_code: int, bits: int) -> Any:
        """真机抓档：把模块给的 ``(freq, samples, rate, bits)`` 转成 ``link.capture_iq`` 的参数顺序。"""
        if self.link is None:
            raise RuntimeError("链路已关闭")
        return self.link.capture_iq(samples=samples, rate_code=rate_code, bits=bits, freq_hz=freq_hz)

    def _log_capture(self, message: str) -> None:
        self.capture_log.append(message)
        del self.capture_log[: max(0, len(self.capture_log) - 40)]

    # ---- 增益 ------------------------------------------------------------
    def gain_state(self) -> dict[str, Any]:
        return self.gain.state()

    def gain_control(self, payload: str) -> dict[str, Any]:
        """顶栏增益控件 → 控制器。**只写意图**：真正的串口命令在两次扫描之间发。"""
        try:
            req = json.loads(payload) if payload else {}
        except Exception as exc:  # noqa: BLE001 - 前端传坏了不能把窗口带崩
            return {"kind": "gain", "ok": False, "note": f"参数不是 JSON：{exc}"}
        if not isinstance(req, dict):
            return {"kind": "gain", "ok": False, "note": "参数必须是对象"}
        action = str(req.get("action") or "").lower()
        try:
            if action == "mode":
                return self.gain.request_mode(str(req.get("mode") or ""))
            if action == "step":
                return self.gain.request_step(int(req.get("delta") or 0))
            if action == "index":
                return self.gain.request_index(int(req.get("index") or 0))
            if action == "target":
                return self.gain.request_target(float(req.get("target") or 0))
        except (TypeError, ValueError) as exc:
            return {"kind": "gain", "ok": False, "note": f"参数不合法：{exc}"}
        return self.gain.state(message=f"未知操作：{action or '（空）'}")

    # ---- 一键体检（只读当前状态） ----------------------------------------
    def audit(self) -> dict[str, Any]:
        """把「活跃发射源清单 + 已存证抓拍」汇成一句结论给界面。

        与 ``python -m app.audit`` 用同一套判据（``app.audit.summary``），
        区别是：界面版**只读当前会话已经扫到的数据**，不重新跑一分钟、不新抓档。
        所以 ``done`` 永远是 False —— 界面不许把它当成「完整一轮体检报告」。
        """
        from app.audit import summary

        try:
            payload = summary(self.registry)
        except Exception as exc:  # noqa: BLE001 - 体检炸了不能让界面崩
            return {"kind": "anti_peep_audit", "headline": "体检失败", "why": f"{type(exc).__name__}: {exc}", "sources": [], "captures": [], "boundaries": [], "ok": False}
        payload["ok"] = True
        payload["dir"] = self.capture_dir
        return payload

    # ---- 驱动 ------------------------------------------------------------
    def _dispatch_snapshot(self, snap: Snapshot) -> None:
        callback = self.on_snapshot_cb
        if callback is not None:
            callback(snap)

    def step(self, seconds: float = 0.35, on_snapshot=None) -> SchedulerStats | None:
        """跑一小段时间片。GUI 反复调用它，任意时刻都能在 ``seconds`` 内停下来。"""
        if self.scheduler is None:
            return None
        self.on_snapshot_cb = on_snapshot
        stats = self.scheduler.run(duration_s=seconds)
        self.last_run_stats = stats  # 收尾统计（停线程后 stats() 还能拿到 share）
        return stats

    # ---- 数据打包 --------------------------------------------------------
    def spectrum(self, snap: Snapshot, module_id: str = "") -> dict[str, Any]:
        """一帧频谱 → 前端 JSON（降采样后的点数由 ``bins_out`` 决定）。"""
        self.frames_out += 1
        bins = downsample_bins(snap.bins_db, self.bins_out)
        half = snap.bw_hz / 2
        meta = dict(snap.meta or {})
        return {
            "kind": "spectrum",
            "module": module_id or str(meta.get("module", "")),
            "seq": int(meta.get("seq", self.frames_out)),
            "ts": round(snap.ts, 3),
            "center_hz": int(snap.center_hz),
            "bw_hz": int(snap.bw_hz),
            "freq_lo_hz": int(round(snap.center_hz - half)),
            "freq_hi_hz": int(round(snap.center_hz + half)),
            "bin_hz": round(snap.bw_hz / len(bins), 1) if bins else 0.0,
            "raw_bins": snap.bin_count,
            "max_detector": bool(meta.get("max_detector", False)),
            "gain_code": meta.get("gain_code"),
            "bins": [round(v, 1) for v in bins],
        }

    def tables(self) -> dict[str, Any]:
        """所有模块的产出物 → 前端 JSON；模块自带 ``web_payload()`` 时一并带上。"""
        plugins = []
        for plugin in self.registry.plugins:
            item: dict[str, Any] = {
                "id": plugin.id,
                "name": plugin.name,
                "weight": plugin.slice_weight,
                "table": None,
                "payload": None,
                "error": "",
            }
            try:
                output = plugin.produce()
                item["table"] = self.table_json(output)
                extra = getattr(plugin, "web_payload", None)
                if callable(extra):
                    item["payload"] = extra()
            except Exception as exc:  # noqa: BLE001 - 单个模块炸了不能拖垮整个界面
                item["error"] = f"{type(exc).__name__}: {exc}"
            plugins.append(item)
        return {"kind": "tables", "ts": round(time.time(), 3), "plugins": plugins}

    @staticmethod
    def table_json(output: Output | None) -> dict[str, Any] | None:
        if output is None:
            return None
        return {
            "kind": output.kind,
            "title": output.title,
            "columns": [str(c) for c in output.columns],
            "rows": [[_cell(c) for c in row] for row in output.rows],
            "notes": [str(n) for n in output.notes],
            "files": [str(f) for f in output.files],
        }

    def stats(self, run_stats: SchedulerStats | None = None) -> dict[str, Any]:
        stats = run_stats if run_stats is not None else (self.scheduler.stats if self.scheduler else None)
        if stats is None:
            # 线程停了 scheduler 就拆掉了；留档让"停之后"的查询（自检收尾、late window）还有数
            stats = self.last_run_stats
        if stats is None:
            return {"kind": "stats", "plugins": [], "slices": 0, "snapshots": 0, "elapsed_s": 0.0}
        self.last_run_stats = stats
        return {
            "kind": "stats",
            "uptime_s": round(time.time() - self.started_at, 1),
            # 增益读数跟着每秒的统计一起推：前端不用额外轮询，也不会算出「改了但没生效」
            "gain": self.gain.state(),
            **stats.as_dict(),
        }

    def _plugin_requests(self) -> list[tuple[Any, list[Any]]]:
        """(插件, 扫描请求列表)；没实现 scan_requests 的插件给空表。"""
        pairs: list[tuple[Any, list[Any]]] = []
        for plugin in self.registry.plugins:
            try:
                requests = list(plugin.scan_requests()) if hasattr(plugin, "scan_requests") else []
            except Exception:  # noqa: BLE001 - 计划拿不到就画不出频段条，但不该拖垮界面
                requests = []
            pairs.append((plugin, requests))
        return pairs

    def describe(self) -> dict[str, Any]:
        """链路自报 + 模块清单 + 扫描计划数目（前端顶部那一排）。"""
        link = self.link
        info: dict[str, Any] = {
            "kind": "info",
            "port": self.config.port if self.config else "",
            "baudrate": self.config.baudrate if self.config else 0,
            "mock": bool(self.config and self.config.mock),
            "scenario": self.config.scenario if self.config else "",
            "gain_index": self.config.gain_index if self.config else None,
            "gain": self.gain.state(),
            "lang": self.lang,
            "identity": "",
            "transport": "",
            "caps": [],
            "profiles": [],
            "notes": list(self.notes),
            "plugins": [
                {
                    "id": p.id,
                    "name": p.name,
                    "weight": p.slice_weight,
                    "requests": len(requests),
                }
                for p, requests in self._plugin_requests()
            ],
            # 扫描计划（每窗的中心/带宽，按扫描顺序）—— 前端据此画频段条与游标，
            # 不在这里写死 2.4G/5G 的边界，换实验时前端零改动。
            "plans": {
                p.id: [[int(req.freq_hz), int(req.bw_hz)] for req in requests]
                for p, requests in self._plugin_requests()
            },
        }
        if link is not None:
            try:
                info["identity"] = link.identity
                info["transport"] = link.transport or ""
                info["caps"] = list(link.caps)
                info["profiles"] = [
                    {"sample_rate_hz": fs, "bins": bins, "dwell_ms": dwell}
                    for fs, _code, bins, _start, dwell, _cont in link.profiles()
                ]
            except Exception as exc:  # noqa: BLE001
                info["notes"].append(
                    tr(self.lang, "read_link_failed", error=f"{type(exc).__name__}: {exc}")
                )
        return info

    def render_text(self) -> str:
        """把当前产出渲染成 CLI 同款 ASCII（「原始输出」页用它做对照）。"""
        blocks = []
        for plugin in self.registry.plugins:
            try:
                blocks.append(plugin.produce().render())
            except Exception as exc:  # noqa: BLE001
                blocks.append(f"[error] {plugin.id}: {type(exc).__name__}: {exc}")
        return "\n\n".join(blocks) if blocks else "（没有模块产出）"


def _cell(value: Any) -> Any:
    """表格单元格转成 JSON 安全值（numpy/Decimal 之类不会进到这里，但防一手）。"""
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def to_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))