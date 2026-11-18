"""任务 2：周期性频谱瀑布图。

职责（只做两件事，不碰 ``core/``）：

1. **轮扫**：把 2.4G ISM / 5G 低段 / 5G 高段切成 ``20 MHz 窗 + 15 MHz 步进``的
   重叠窗口，一轮扫完三个频段（快照由调度器顺路入库，本模块不额外占扫描时间）。
2. **出图**：``produce()`` 把环形缓冲里的最近若干帧交给 ``output.heatmap``
   渲染成「时频热力图（PNG + HTML）」；历史全量渲染走 CLI（读 SQLite），见 ``app/waterfall.py``。

判据（见 ``tests/test_waterfall.py``）：

* ``microwave`` 场景：2430–2490 MHz 必须是一条**连续的宽带亮带**（微波炉漏能特征）；
* ``mixed`` 场景：2412 MHz 必须是**窄带 + 明暗交替**（WiFi 突发块）
  且与微波炉宽带在同一张图上肉眼可分；
* 产出物上必须带「周期快照，非连续实时」声明（HTML 与 PNG 标题都有）。

边界（写进产出物，不许美化）：

* **周期快照，非连续实时** —— 单射频分时扫描，帧与帧之间有接收空档；
* 功率码是**相对 dB 码（0..255）**，不是 dBm，色标只能说"相对强弱"；
* 每轮 44 个窗口 × 100 ms ≈ 5 s 一轮，时频图的时间分辨率就是这个量级。
"""
from __future__ import annotations

import os
from collections import deque
from typing import Any, Sequence

from core.registry import ModePlugin, Output, ScanRequest
from core.snapshot import Snapshot
from output.heatmap import BANDS, BANNER, Band, render_waterfall

#: 单个扫描窗带宽（用户裁决：5G 宽窗改成窄窗多次扫、允许重叠）
SWEEP_BW_HZ = 20_000_000
#: 中心频率步进（用户裁决：20 MHz 窗 / 15 MHz 步进）
SWEEP_STEP_HZ = 15_000_000
#: 单窗驻留时间（固件下限 100 ms）
DWELL_MS = 100
#: 每帧 bin 数（固件上限 512）
BINS = 512
#: 环形缓冲保留帧数（每帧 512 bin ≈ 4 KB，1200 帧 ≈ 5 MB ≈ 2 分钟数据）
RING_FRAMES = 1200
#: ``produce()`` 默认页时长（每页 5 分钟，一小时 12 页，可翻页看小时级历史）
PAGE_SECONDS = 300.0

DEFAULT_OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".data", "waterfall"
)


def window_centers(
    lo_hz: int,
    hi_hz: int,
    bw_hz: int = SWEEP_BW_HZ,
    step_hz: int = SWEEP_STEP_HZ,
) -> list[int]:
    """覆盖 ``[lo_hz, hi_hz]`` 的中心频点序列（首尾都要盖到，相邻窗必须重叠）。

    首窗左沿压频段下沿、末窗右沿压频段上沿，中间把剩余跨度**均分**
    （每段 ≤ ``step_hz``）。均分而不是"按步进走到底再补一窗"，
    是为了避免最后一跳超过步进（那样会留下 >15 MHz 的空档）。
    """
    if hi_hz <= lo_hz:
        raise ValueError("频段上沿必须大于下沿")
    if bw_hz <= 0 or step_hz <= 0:
        raise ValueError("窗宽与步进必须为正")
    half = bw_hz // 2
    start = lo_hz + half
    stop = hi_hz - half
    if stop <= start:  # 频段比窗还窄：一个窗居中盖住
        return [(lo_hz + hi_hz) // 2]
    span = stop - start
    count = max(1, -(-span // step_hz))  # 向上取整
    centers: list[int] = []
    for index in range(count + 1):
        center = start + span * index // count
        if not centers or center > centers[-1]:
            centers.append(center)
    if centers[-1] != stop:
        centers[-1] = stop
    return centers


def sweep_plan(bands: Sequence[Band] = BANDS) -> list[tuple[Band, list[int]]]:
    """一轮轮扫的完整计划：每个频段 + 它的窗口中心序列。"""
    return [(band, window_centers(band.lo_hz, band.hi_hz)) for band in bands]


def sweep_seconds(bands: Sequence[Band] = BANDS, dwell_ms: int = DWELL_MS) -> float:
    """跑完一轮（三个频段所有窗）要多久，单位秒。"""
    return sum(len(centers) for _band, centers in sweep_plan(bands)) * dwell_ms / 1000.0


class WaterfallMonitor(ModePlugin):
    """周期性全频段轮扫 + 时频热力图产出。"""

    id = "waterfall"
    name = "周期性频谱瀑布图"
    slice_weight = 0.8

    def __init__(
        self,
        out_dir: str | None = None,
        page_seconds: float = PAGE_SECONDS,
        ring_frames: int = RING_FRAMES,
    ) -> None:
        self.out_dir = out_dir or DEFAULT_OUT
        self.page_seconds = page_seconds
        self._ring: deque[Snapshot] = deque(maxlen=max(8, ring_frames))
        self.frames = 0
        self.sweeps = 0
        self.per_band_frames: dict[str, int] = {band.id: 0 for band in BANDS}
        self.first_ts: float | None = None
        self.last_ts: float | None = None
        self._windows = 0
        for _band, centers in sweep_plan():
            self._windows += len(centers)

    # ---- 契约 ------------------------------------------------------------
    def scan_requests(self) -> list[ScanRequest]:
        requests: list[ScanRequest] = []
        for band, centers in sweep_plan():
            for center in centers:
                requests.append(
                    ScanRequest(
                        freq_hz=int(center),
                        bw_hz=SWEEP_BW_HZ,
                        dwell_ms=DWELL_MS,
                        bins=BINS,
                    )
                )
        self.sweeps += 1
        return requests

    def on_snapshot(self, snap: Snapshot) -> None:
        self._ring.append(snap)
        self.frames += 1
        if self.first_ts is None:
            self.first_ts = snap.ts
        self.last_ts = snap.ts
        for band in BANDS:
            if band.lo_hz <= snap.center_hz <= band.hi_hz:
                self.per_band_frames[band.id] += 1
                break

    def produce(self) -> Output:
        notes = [
            BANNER,
            f"缓冲 {len(self._ring)} 帧 / 累计 {self.frames} 帧，"
            f"{self._windows} 个窗 × {DWELL_MS} ms ≈ {sweep_seconds():.1f} s 一轮",
            "功率码是相对 dB 码（0..255），不是 dBm；色标只表示相对强弱。",
        ]
        if not self._ring:
            return Output(
                kind="figure",
                title="周期性频谱瀑布图",
                notes=notes + ["还没有快照：先跑一轮扫描（app/waterfall.py --minutes N）"],
                payload={"bands": [], "frames": 0, "pages": 0},
            )
        result = render_waterfall(
            list(self._ring),
            self.out_dir,
            page_seconds=self.page_seconds,
        )
        rows = [
            [
                page.index,
                page.duration_s,
                page.frames,
                f"{page.code_range[0]}–{page.code_range[1]}",
                os.path.basename(page.png),
            ]
            for page in result.pages
        ]
        return Output(
            kind="figure",
            title="周期性频谱瀑布图（时频热力图）",
            columns=["页", "时长 s", "帧数", "色标码", "PNG"],
            rows=rows,
            files=list(result.files),
            notes=notes,
            payload={
                "bands": self.band_summary(),
                "frames": result.frames,
                "pages": result.page_count,
                "span_s": round(result.span[1] - result.span[0], 1),
                "index": result.index_html,
                "files": result.files,
            },
        )

    # ---- 供上位机 / 报告用 ------------------------------------------------
    def band_summary(self) -> list[dict[str, Any]]:
        """每个频段的覆盖情况（给上位机画图 / 给报告做表）。"""
        out: list[dict[str, Any]] = []
        for band, centers in sweep_plan():
            out.append(
                {
                    "id": band.id,
                    "label": band.label,
                    "lo_mhz": band.lo_hz / 1e6,
                    "hi_mhz": band.hi_hz / 1e6,
                    "windows": len(centers),
                    "frames": self.per_band_frames.get(band.id, 0),
                }
            )
        return out

    def summary(self) -> dict[str, Any]:
        base = super().summary()
        base.update(
            {
                "frames": self.frames,
                "sweeps": self.sweeps,
                "span_s": round((self.last_ts or 0) - (self.first_ts or 0), 2),
            }
        )
        return base

    def reset(self) -> None:
        self._ring.clear()
        self.frames = 0
        self.sweeps = 0
        self.per_band_frames = {band.id: 0 for band in BANDS}
        self.first_ts = None
        self.last_ts = None


__all__ = [
    "BINS",
    "DWELL_MS",
    "PAGE_SECONDS",
    "RING_FRAMES",
    "SWEEP_BW_HZ",
    "SWEEP_STEP_HZ",
    "WaterfallMonitor",
    "sweep_plan",
    "sweep_seconds",
    "window_centers",
]