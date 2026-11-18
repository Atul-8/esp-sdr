"""功能 1：WiFi 信道体检（2.4G / 5G 占用评估 + 推荐信道）。

只用 ``core`` 暴露的接口：
  * ``Snapshot.slice_db`` / ``noise_floor_db`` 做分带与判决
  * ``ScanRequest`` 声明驻留计划
  * ``Output`` 交付表格

判据（可自测，见 tests/test_wifi_check.py）：
  * ``wifi_busy``  : ch1 / ch6 / ch11 里忙的信道必须被判「拥挤」，推荐信道必须落在干净信道
  * ``quiet``      : 全部信道「空闲」，推荐 ch1，干扰源计数 0（零误报）
  * ``microwave``  : 2.4G 中段（ch6..ch11）被抬升，ch1 保持干净

边界：分时快照，不是连续实时；功率码是相对 dB 码，不是 dBm。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from core.registry import ModePlugin, Output, ScanRequest
from core.snapshot import Snapshot
from output.format import ascii_table, bar, fmt_hz, fmt_pct

# ---------------------------------------------------------------------------
# 频段定义（不依赖 tests/ 里的常量，功能模块自带）
# ---------------------------------------------------------------------------

#: 2.4G 信道 → 中心频率（MHz）
CHANNELS_24 = {ch: 2412 + 5 * (ch - 1) for ch in range(1, 14)}
#: 5G 常用 20 MHz 栅格信道 → 中心频率（MHz）
CHANNELS_5 = {ch: 5000 + 5 * ch for ch in list(range(36, 65, 4)) + list(range(100, 145, 4)) + list(range(149, 166, 4))}
#: 只有这三个信道互不重叠，推荐优先从中选
NON_OVERLAP_24 = (1, 6, 11)

#: 2.4G 一次 80 MHz 驻留即可覆盖 2402–2482 MHz
SCAN_24 = (2_442_000_000, 80_000_000)
#: 5G 逐段 80 MHz 覆盖：5150–5350 / 5470–5830
SCAN_5 = (
    (5_210_000_000, 80_000_000),
    (5_290_000_000, 80_000_000),
    (5_530_000_000, 80_000_000),
    (5_610_000_000, 80_000_000),
    (5_690_000_000, 80_000_000),
    (5_770_000_000, 80_000_000),
)

CHANNEL_WIDTH_HZ = 20_000_000
BUSY_EXCESS_DB = 6.0  # 高于噪声底多少算「有能量」
BUSY_FRAME_RATIO = 0.5  # 一帧里多少比例的 bin 有能量算「这帧信道忙」
DETECTED_OCCUPANCY = 0.30  # 信道占用超过它记一个「干扰源」

#: 占用度分档（busy_frame_ratio）
LEVELS = ((0.80, "重度"), (0.50, "拥挤"), (0.20, "轻度"), (0.0, "空闲"))
#: 只有这两档才允许被推荐
CLEAN_LEVELS = ("空闲", "轻度")


def level_of(busy_ratio: float) -> str:
    for threshold, label in LEVELS:
        if busy_ratio >= threshold:
            return label
    return "空闲"


@dataclass
class ChannelStat:
    """单个信道的累积统计（跨多帧）。"""

    channel: int
    center_hz: int
    band: str
    frames: int = 0
    busy_frames: int = 0
    occupancy_sum: float = 0.0
    excess_sum: float = 0.0
    peak_db: float = 0.0

    def feed(self, occupancy: float, mean_db: float, floor_db: float, peak_db: float) -> None:
        self.frames += 1
        self.occupancy_sum += occupancy
        self.excess_sum += max(0.0, mean_db - floor_db)
        if occupancy >= BUSY_FRAME_RATIO:
            self.busy_frames += 1
        self.peak_db = max(self.peak_db, peak_db)

    @property
    def busy_ratio(self) -> float:
        return self.busy_frames / self.frames if self.frames else 0.0

    @property
    def occupancy(self) -> float:
        """时间-频率二维占用度：平均每帧有能量的 bin 比例 × 有能量的帧比例。"""
        if not self.frames:
            return 0.0
        return (self.occupancy_sum / self.frames) * self.busy_ratio

    @property
    def excess_db(self) -> float:
        return self.excess_sum / self.frames if self.frames else 0.0

    @property
    def level(self) -> str:
        return level_of(self.busy_ratio)

    def score(self) -> float:
        """越大越糟：时间占用为主，平均抬升为辅。"""
        return self.occupancy * 0.75 + min(1.0, self.excess_db / 30.0) * 0.25


@dataclass
class WifiCheck(ModePlugin):
    """WiFi 信道体检。"""

    id: str = "wifi_check"
    name: str = "WiFi 信道体检"
    slice_weight: float = 40.0

    dwell_ms: int = 100
    bins: int = 1024
    include_5g: bool = True

    stats: dict[str, ChannelStat] = field(default_factory=dict)
    started_at: float = 0.0
    last_ts: float = 0.0
    slices: int = 0
    frames: int = 0

    # ---- 契约 -----------------------------------------------------------
    def scan_requests(self) -> list[ScanRequest]:
        requests = [ScanRequest(*SCAN_24, self.dwell_ms, self.bins)]
        if self.include_5g:
            requests.extend(ScanRequest(*window, self.dwell_ms, self.bins) for window in SCAN_5)
        return requests

    def on_snapshot(self, snap: Snapshot) -> None:
        if not self.started_at:
            self.started_at = snap.ts
        self.last_ts = max(self.last_ts, snap.ts)
        self.frames += 1
        floor = snap.noise_floor_db
        threshold = floor + BUSY_EXCESS_DB
        for channel, center_mhz in self._channels():
            center = center_mhz * 1_000_000
            half = CHANNEL_WIDTH_HZ / 2
            if not (snap.center_hz - snap.bw_hz / 2 <= center < snap.center_hz + snap.bw_hz / 2):
                continue
            bins = snap.slice_db(center - half, center + half)
            if not bins:
                continue
            occupancy = sum(1 for value in bins if value > threshold) / len(bins)
            stat = self.stats.setdefault(
                f"{channel}{'g' if center_mhz > 3000 else 'c'}",
                ChannelStat(channel=channel, center_hz=center, band="5G" if center_mhz > 3000 else "2.4G"),
            )
            stat.feed(occupancy, sum(bins) / len(bins), floor, max(bins))

    def produce(self) -> Output:
        rows = [
            [
                f"ch{stat.channel}",
                stat.band,
                fmt_hz(stat.center_hz),
                bar(stat.busy_ratio * 100, 100, 12),
                fmt_pct(stat.busy_ratio),
                f"{stat.peak_db:.0f}",
                stat.level,
            ]
            for stat in self._ordered()
        ]
        return Output(
            kind="table",
            title="WiFi 信道体检（占用 / 峰值 / 分档）",
            columns=["信道", "频段", "中心", "占用条", "帧占用", "峰值码", "分档"],
            rows=rows,
            notes=self.notes(),
            payload={"recommend_24": self.recommend_24(), "recommend_5": self.recommend_5(), "interferers": self.interferers()},
        )

    # ---- 判读 -----------------------------------------------------------
    def recommend_24(self) -> int | None:
        """2.4G 推荐信道：只在 1/6/11 里挑干净的那个；三个都被占就返回 None。"""
        return self._recommend(ch for ch in NON_OVERLAP_24 if self._stat(ch, "2.4G") is not None)

    def recommend_5(self) -> int | None:
        return self._recommend(stat.channel for stat in self._ordered() if stat.band == "5G")

    def _recommend(self, channels: Iterable[int]) -> int | None:
        candidates = [self.stats[self._key(ch)] for ch in channels if self._key(ch) in self.stats]
        clean = [stat for stat in candidates if stat.level in CLEAN_LEVELS]
        pool = clean or []
        if not pool:
            return None
        return min(pool, key=lambda stat: (round(stat.score(), 3), stat.channel)).channel

    def interferers(self) -> list[str]:
        return [f"ch{stat.channel}" for stat in self._ordered() if stat.busy_ratio >= DETECTED_OCCUPANCY]

    def channel_report(self) -> dict[str, Any]:
        return {
            "channels": [
                {
                    "channel": stat.channel,
                    "band": stat.band,
                    "busy_ratio": round(stat.busy_ratio, 4),
                    "occupancy": round(stat.occupancy, 4),
                    "peak_db": round(stat.peak_db, 1),
                    "level": stat.level,
                }
                for stat in self._ordered()
            ],
            "recommend_24": self.recommend_24(),
            "recommend_5": self.recommend_5(),
            "interferers": self.interferers(),
            "frames": self.frames,
            "span_s": round(self.span_s, 2),
        }

    def notes(self) -> list[str]:
        lines = [f"判据：bin 高于噪声底 {BUSY_EXCESS_DB:.0f} dB 记为有能量；一帧内 {BUSY_FRAME_RATIO:.0%} 以上有能量算该帧信道忙。"]
        rec_24, rec_5 = self.recommend_24(), self.recommend_5()
        if rec_24 is not None:
            lines.append(f"2.4G 建议：ch{rec_24}（只在 1/6/11 里选，避免重叠）。")
        else:
            lines.append("2.4G 建议：ch1/ch6/ch11 全部被占，没有可推荐的非重叠信道，建议改用 5G 或有线。")
        if rec_5 is not None:
            stat = self._stat(rec_5, "5G")
            lines.append(f"5G 建议：ch{rec_5}（帧占用 {fmt_pct(stat.busy_ratio) if stat else 'n/a'}）。")
        elif self.include_5g:
            lines.append("5G 建议：本次样本里没有干净信道。")
        found = self.interferers()
        lines.append(f"占用超过 {DETECTED_OCCUPANCY:.0%} 的信道：{', '.join(found) if found else '无'}。")
        lines.append(f"样本：{self.frames} 帧 / 覆盖 {self.span_s:.1f}s（分时扫描，帧间有接收空档）。")
        lines.append("边界：分时快照，非连续实时；功率码是相对 dB 码，不是 dBm，跨增益不可比。")
        return lines

    # ---- 内部 -----------------------------------------------------------
    def _channels(self) -> Iterable[tuple[int, int]]:
        for channel, center_mhz in CHANNELS_24.items():
            yield channel, center_mhz
        if self.include_5g:
            for channel, center_mhz in CHANNELS_5.items():
                yield channel, center_mhz

    @staticmethod
    def _key(channel: int, band: str = "") -> str:
        suffix = "g" if band == "5G" or channel > 13 else "c"
        return f"{channel}{suffix}"

    def _stat(self, channel: int, band: str) -> ChannelStat | None:
        return self.stats.get(self._key(channel, band))

    def _ordered(self) -> list[ChannelStat]:
        return sorted(self.stats.values(), key=lambda stat: (stat.band != "2.4G", stat.center_hz))

    @property
    def span_s(self) -> float:
        return max(0.0, self.last_ts - self.started_at) if self.started_at else 0.0

    def reset(self) -> None:
        self.stats.clear()
        self.started_at = 0.0
        self.last_ts = 0.0
        self.slices = 0
        self.frames = 0

    def summary(self) -> dict[str, Any]:
        data = super().summary()
        data.update({"frames": self.frames, "recommend_24": self.recommend_24(), "interferers": self.interferers()})
        return data


# ---------------------------------------------------------------------------
# 自带自测入口：python -m modes.wifi_check --scenario wifi_busy
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="WiFi 信道体检（mock 自测，无需硬件）")
    parser.add_argument("--scenario", default="wifi_busy")
    parser.add_argument("--slices", type=int, default=28)
    parser.add_argument("--no-5g", action="store_true")
    args = parser.parse_args(argv)

    from core.scheduler import Scheduler
    from tests.mock_source import MockSource

    plugin = WifiCheck(include_5g=not args.no_5g)
    scheduler = Scheduler(MockSource(scenario=args.scenario, realtime=False), _registry(plugin))
    scheduler.run(max_slices=args.slices)
    print(ascii_table(
        ["项目", "值"],
        [["场景", args.scenario], ["时间片", scheduler.stats.slices], ["帧数", plugin.frames],
         ["推荐信道", f"2.4G ch{plugin.recommend_24() or '无干净信道'} / 5G ch{plugin.recommend_5() or '无'}"],
         ["干扰源", ", ".join(plugin.interferers()) or "无"]],
    ))
    print(plugin.produce().render())
    return 0


def _registry(plugin: ModePlugin):
    from core.registry import Registry

    registry = Registry()
    registry.register(plugin)
    return registry


if __name__ == "__main__":
    raise SystemExit(main())