"""功能 3 验收：BLE 广播信道风险评估（不需要硬件，全部走 mock 合成器）。"""
from __future__ import annotations

import json
import unittest

from core.registry import Output, Registry, ScanRequest, discover
from core.scheduler import Scheduler
from core.snapshot import Snapshot
from modes.ble_risk import (
    ADV_CHANNELS,
    ADV_HALF_HZ,
    ADV_ORDER,
    BAND_HZ,
    BINS,
    BLE_DATA_FIRST_HZ,
    BLE_DATA_LAST_HZ,
    BLE_STEP_HZ,
    DEGENERATE_MAX_CODE,
    DWELL_MS,
    PRESENCE_DB,
    PROMINENCE_DB,
    RISK_BLE,
    RISK_MIXED,
    RISK_NONE,
    RISK_WIFI,
    SCAN_BW_HZ,
    SCAN_STEP_HZ,
    WIDEBAND_FRAC,
    BleRisk,
    data_slots,
    level_of,
    scan_plan,
    sliding_windows,
)
from tests.mock_source import MockSource, SpectrumSynthesizer

#: 场景跑一遍要几百帧合成，多个测试共用一份结果（合成器是纯函数式，结果稳定）
_REPORTS: dict[str, BleRisk] = {}


def run_scenario(scenario: str, passes: int = 8, frames: int = 3, seed: int = 11) -> BleRisk:
    """按模块自己声明的计划喂 N 轮 mock 快照，返回跑完的插件。"""
    if scenario in _REPORTS:
        return _REPORTS[scenario]
    synth = SpectrumSynthesizer(scenario, seed=seed)
    plugin = BleRisk()
    clock = 1_000.0
    for _ in range(passes):
        for request in plugin.scan_requests():
            for _frame in range(frames):
                clock += 0.1
                plugin.on_snapshot(
                    Snapshot(
                        ts=clock,
                        center_hz=request.freq_hz,
                        bw_hz=request.bw_hz,
                        bins_db=synth.codes(request.freq_hz, request.bw_hz, request.bins),
                        meta={"source": "mock", "scenario": scenario},
                    )
                )
    _REPORTS[scenario] = plugin
    return plugin


class ScanPlanTest(unittest.TestCase):
    def test_adv_windows_are_centered_on_adv_channels(self):
        """三个广播信道必须各有一个**中心对准**的窗：偏了就没有干净的肩部可比。"""
        centers = scan_plan()
        for channel in ADV_ORDER:
            self.assertIn(ADV_CHANNELS[channel], centers, f"BLE {channel} 没有对准的扫描窗")

    def test_plan_covers_whole_data_band(self):
        """37 个数据信道必须被某个窗**完整**覆盖（±1 MHz 全在窗内）。"""
        centers = scan_plan()
        for slot in data_slots():
            covered = any(
                abs(slot - center) + 1_000_000 <= SCAN_BW_HZ // 2 for center in centers
            )
            self.assertTrue(covered, f"{slot/1e6:.0f} MHz 没被完整覆盖")

    def test_windows_overlap(self):
        """步进必须小于窗宽（重叠滑扫），否则两窗之间有盲区。"""
        self.assertLess(SCAN_STEP_HZ, SCAN_BW_HZ)
        windows = sliding_windows(BLE_DATA_FIRST_HZ, BLE_DATA_LAST_HZ)
        for left, right in zip(windows, windows[1:]):
            self.assertLessEqual(right - left, 2 * (SCAN_BW_HZ // 2))

    def test_plan_stays_inside_ism_band(self):
        centers = scan_plan()
        self.assertEqual(centers, sorted(centers))
        self.assertEqual(len(centers), len(set(centers)), "重复的窗没有任何收益")
        for center in centers:
            self.assertGreaterEqual(center - SCAN_BW_HZ // 2, BAND_HZ[0] - SCAN_BW_HZ // 2)
            self.assertLessEqual(center + SCAN_BW_HZ // 2, BAND_HZ[1] + SCAN_BW_HZ // 2)

    def test_scan_requests_respect_firmware_limits(self):
        plugin = BleRisk()
        requests = plugin.scan_requests()
        self.assertEqual(len(requests), len(scan_plan()))
        for request in requests:
            self.assertIsInstance(request, ScanRequest)
            self.assertEqual(request.bw_hz, SCAN_BW_HZ)
            self.assertTrue(100 <= request.dwell_ms <= 60_000)
            self.assertIn(request.bins, (256, 512, 1024, 2048, 4096, 8192))
            self.assertEqual(request.bins, BINS)
            self.assertEqual(request.dwell_ms, DWELL_MS)

    def test_ble_channel_table_matches_spec(self):
        """BLE 40 通道：37/38/39 = 2402/2426/2480，数据信道 2404~2478 步进 2 MHz。"""
        self.assertEqual(ADV_CHANNELS, {37: 2_402_000_000, 38: 2_426_000_000, 39: 2_480_000_000})
        slots = data_slots()
        self.assertEqual(len(slots), 37, "数据信道 0..36 共 37 个")
        self.assertEqual(slots[0], BLE_DATA_FIRST_HZ)
        self.assertEqual(slots[-1], BLE_DATA_LAST_HZ)
        # 步进 2 MHz，但在广播信道频点上断开（2426 是 adv 38 的频率，不算数据信道）
        self.assertTrue(all(b - a in (BLE_STEP_HZ, 2 * BLE_STEP_HZ) for a, b in zip(slots, slots[1:])))
        for center in ADV_CHANNELS.values():
            self.assertNotIn(center, slots, "广播信道不该算进数据信道")


class ScenarioVerdictTest(unittest.TestCase):
    def setUp(self):
        self.quiet = run_scenario("quiet")
        self.crowded = run_scenario("ble_crowded")
        self.suppressed = run_scenario("wifi_suppress_ble")

    def test_quiet_has_no_risk(self):
        self.assertEqual(self.quiet.risk_type(), RISK_NONE)
        self.assertLess(self.quiet.score(), 15.0, "纯噪声底不该被判成拥挤")
        self.assertEqual(self.quiet.adv_activity, 0.0)

    def test_ble_crowded_verdict_is_density(self):
        self.assertEqual(self.crowded.risk_type(), RISK_BLE)
        self.assertGreater(self.crowded.adv_activity, 0.5)
        self.assertLess(self.crowded.wideband, WIDEBAND_FRAC, "这个场景里 WiFi 是空闲的")

    def test_ble_crowded_scores_much_higher_than_quiet(self):
        """验收硬条件：蓝牙拥挤场景的评分必须显著高于安静场景。"""
        self.assertGreater(self.crowded.score(), self.quiet.score() + 30.0)

    def test_wifi_suppress_verdict_is_suppressed(self):
        """验收硬条件：WiFi 压制场景必须给出「WiFi 压制型」判词。"""
        self.assertEqual(self.suppressed.risk_type(), RISK_WIFI)
        self.assertGreaterEqual(self.suppressed.wideband, WIDEBAND_FRAC)
        self.assertLess(self.suppressed.adv_activity, 0.10, "被压制时广播活动应该分辨不出来")

    def test_suppressed_scenario_records_masked_frames(self):
        """「压制」不是猜的：要有"有能量但抬不出窄带凸起"的帧做证据。"""
        self.assertGreater(self.suppressed.masked_frames, 0)
        self.assertEqual(self.suppressed.masked_frames, sum(s.masked for s in self.suppressed.adv.values()))

    def test_crowded_hits_are_narrowband(self):
        """拥挤场景的命中必须真的窄带（突出度够 + 占抬升一半以上）。"""
        for channel in ADV_ORDER:
            stat = self.crowded.adv[channel]
            if stat.coverage < 0.6:
                continue
            self.assertGreater(stat.hits, 0, f"BLE {channel} 应该有命中")
            self.assertGreaterEqual(stat.prominence_db, PROMINENCE_DB)
            rise = stat.level_db - self.crowded.ref_db
            self.assertGreaterEqual(stat.prominence_db, 0.5 * rise)

    def test_adv_levels_are_ordered_by_signal_strength(self):
        """合成器里 38 dB 最高（70）> 37（68）> 39（66），电平排序应一致。"""
        levels = {channel: self.crowded.adv[channel].level_db for channel in ADV_ORDER}
        self.assertGreater(levels[38], levels[37])
        self.assertGreater(levels[37], levels[39])

    def test_level_labels(self):
        self.assertEqual(level_of(95.0), "重度")
        self.assertEqual(level_of(55.0), "拥挤")
        self.assertEqual(level_of(20.0), "轻度")
        self.assertEqual(level_of(0.0), "空闲")


class FrameHandlingTest(unittest.TestCase):
    def test_degenerate_frames_are_dropped(self):
        plugin = BleRisk()
        silent = tuple([0] * 1024)
        plugin.on_snapshot(Snapshot(ts=1.0, center_hz=2_402_000_000, bw_hz=20_000_000, bins_db=silent))
        self.assertEqual(plugin.dropped_frames, 1)
        self.assertEqual(plugin.frames, 0)
        self.assertEqual(plugin.risk_type(), "无数据")

    def test_ref_is_per_frame_not_absolute(self):
        """参考电平取本帧 p10：整块抬高 40 码后判决不应变化。"""
        synth = SpectrumSynthesizer("ble_crowded", seed=3)
        base = BleRisk()
        shifted = BleRisk()
        offset = 40
        for _ in range(6):
            for request in base.scan_requests():
                codes = synth.codes(request.freq_hz, request.bw_hz, request.bins)
                base.on_snapshot(Snapshot(ts=1.0, center_hz=request.freq_hz, bw_hz=request.bw_hz, bins_db=codes))
                lifted = tuple(min(255, value + offset) for value in codes)
                shifted.on_snapshot(Snapshot(ts=1.0, center_hz=request.freq_hz, bw_hz=request.bw_hz, bins_db=lifted))
        self.assertAlmostEqual(shifted.ref_db - base.ref_db, offset, delta=queue_tolerance())
        self.assertEqual(shifted.risk_type(), base.risk_type())
        self.assertAlmostEqual(shifted.score(), base.score(), delta=12.0)

    def test_coverage_reaches_full_after_one_pass(self):
        plugin = run_scenario("quiet")
        for channel in ADV_ORDER:
            self.assertAlmostEqual(plugin.adv[channel].coverage, 1.0, places=3)
        self.assertAlmostEqual(plugin.slot_coverage, 1.0, places=3)
        self.assertEqual(set(plugin._slot_covered), set(data_slots()))

    def test_out_of_reach_window_does_not_feed_adv(self):
        """离广播信道太远的窗不参与它的判决（取不到肩部，强行算会造出假凸起）。"""
        plugin = BleRisk()
        synth = SpectrumSynthesizer("ble_crowded", seed=5)
        far = 2_480_000_000
        codes = synth.codes(far, 20_000_000, 1024)
        plugin.on_snapshot(Snapshot(ts=1.0, center_hz=far, bw_hz=20_000_000, bins_db=codes))
        self.assertEqual(plugin.adv[37].frames, 0)
        self.assertEqual(plugin.adv[39].frames, 1)


class PayloadTest(unittest.TestCase):
    def setUp(self):
        self.plugin = run_scenario("ble_crowded")
        self.payload = self.plugin.web_payload()

    def test_produce_table_shape(self):
        output = self.plugin.produce()
        self.assertIsInstance(output, Output)
        self.assertEqual(output.kind, "table")
        self.assertEqual(len(output.columns), 9)
        self.assertEqual(len(output.rows), 4, "3 个广播信道 + 1 行数据区")
        self.assertIn("风险", output.title)
        self.assertGreaterEqual(len(output.notes), 4)
        self.assertEqual(output.payload["kind"], "ble_risk")

    def test_payload_fields(self):
        self.assertEqual(self.payload["kind"], "ble_risk")
        self.assertEqual(len(self.payload["adv"]), 3)
        self.assertEqual([item["channel"] for item in self.payload["adv"]], list(ADV_ORDER))
        for item in self.payload["adv"]:
            for key in ("activity", "level_db", "excess_db", "prominence_db", "peak_db",
                        "coverage", "frames", "hits", "masked", "level"):
                self.assertIn(key, item)
        self.assertEqual(self.payload["scan_plan"]["bw_hz"], SCAN_BW_HZ)
        self.assertEqual(self.payload["scan_plan"]["windows"], len(scan_plan()))
        self.assertEqual(self.payload["data_band"]["slots"], 37)
        self.assertIn("risk_type", self.payload)
        self.assertIn("score", self.payload)

    def test_payload_is_json_serializable(self):
        text = json.dumps(self.payload, ensure_ascii=False, sort_keys=True)
        self.assertGreater(len(text), 200)
        self.assertIn("risk_type", text)
        self.assertIn("蓝牙", text)

    def test_notes_state_the_boundary(self):
        """边界必须写在产出里：只评概率、不做设备级归因。"""
        notes = "\n".join(self.plugin.notes())
        self.assertIn("不做设备级归因", notes)
        self.assertIn("dBm", notes)
        self.assertIn("分时", notes)

    def test_summary_for_scheduler(self):
        summary = self.plugin.summary()
        self.assertEqual(summary["id"], "ble_risk")
        self.assertEqual(summary["slice_weight"], 1.0)
        self.assertIn("risk_type", summary)
        self.assertGreater(summary["frames"], 0)


class CoexistTest(unittest.TestCase):
    def setUp(self):
        self.plugin = run_scenario("wifi_suppress_ble")

    def test_overlap_marks_full_cover(self):
        pairs = {(item["wifi_channel"], item["ble_channel"]): item for item in self.plugin.overlap()}
        self.assertTrue(any(item["full"] for item in pairs.values()), "应该有被 WiFi 完全覆盖的广播信道")
        for (wifi_ch, ble_ch), item in pairs.items():
            self.assertLessEqual(item["fraction"], 1.0)
            if item["full"]:
                self.assertGreaterEqual(item["overlap_mhz"], 1.9)

    def test_wifi_busy_channels_are_reported(self):
        busy = self.plugin.wifi_busy()
        self.assertTrue(busy, "这个场景里 2.4G 全被 WiFi 占住")
        for channel in busy:
            self.assertIn(channel, range(1, 14))

    def test_hint_suggests_moving_the_router(self):
        hints = "\n".join(self.plugin.hints())
        self.assertIn("路由器", hints)
        self.assertIn("5G", hints)

    def test_crowded_hint_suggests_fewer_devices(self):
        crowded = run_scenario("ble_crowded")
        hints = "\n".join(crowded.hints())
        self.assertIn("广播间隔", hints)


class PluginContractTest(unittest.TestCase):
    def test_registry_discovers_ble_risk(self):
        registry = Registry()
        discover("modes", registry)
        self.assertIn("ble_risk", [plugin.id for plugin in registry.plugins])

    def test_end_to_end_with_scheduler(self):
        """注册 → 调度 → 收快照 → 产出的整条链路（和 app.demo 走同一条路）。"""
        plugin = BleRisk()
        registry = Registry()
        registry.register(plugin)
        scheduler = Scheduler(MockSource(scenario="ble_crowded", realtime=False), registry)
        scheduler.run(max_slices=24)
        self.assertGreater(plugin.frames, 0)
        self.assertEqual(plugin.risk_type(), RISK_BLE)
        output = plugin.produce()
        self.assertEqual(len(output.rows), 4)

    def test_reset_clears_everything(self):
        # 注意：这里必须用**独立**实例，不能动上面按场景缓存的共享实例
        plugin = BleRisk()
        synth = SpectrumSynthesizer("ble_crowded", seed=4)
        for _ in range(3):
            for request in plugin.scan_requests():
                plugin.on_snapshot(
                    Snapshot(ts=1.0, center_hz=request.freq_hz, bw_hz=request.bw_hz,
                             bins_db=synth.codes(request.freq_hz, request.bw_hz, request.bins))
                )
        self.assertGreater(plugin.frames, 0)
        plugin.reset()
        self.assertEqual(plugin.frames, 0)
        self.assertEqual(plugin.score(), 0.0)
        self.assertEqual(plugin.risk_type(), "无数据")
        self.assertEqual(plugin.masked_frames, 0)
        self.assertEqual(len(plugin._slot_covered), 0)
        self.assertFalse(plugin.wifi_busy())
        self.assertEqual(plugin.span_s, 0.0)
        for stat in plugin.adv.values():
            self.assertEqual(stat.frames, 0)

    def test_slice_weight_is_not_starving_others(self):
        """单射频分时：权重悬殊会把别的模块饿死（META-0087），这里必须对半分。"""
        self.assertEqual(BleRisk().slice_weight, 1.0)

    def test_span_and_frames(self):
        plugin = BleRisk()
        synth = SpectrumSynthesizer("quiet", seed=2)
        codes = synth.codes(2_402_000_000, 20_000_000, 1024)
        plugin.on_snapshot(Snapshot(ts=100.0, center_hz=2_402_000_000, bw_hz=20_000_000, bins_db=codes))
        plugin.on_snapshot(Snapshot(ts=112.5, center_hz=2_402_000_000, bw_hz=20_000_000, bins_db=codes))
        self.assertAlmostEqual(plugin.span_s, 12.5, places=3)
        self.assertEqual(plugin.frames, 2)


class MixedScenarioTest(unittest.TestCase):
    def test_mixed_reports_something_and_stays_json_safe(self):
        plugin = run_scenario("mixed", passes=4)
        self.assertIn(plugin.risk_type(), (RISK_NONE, RISK_WIFI, RISK_BLE, RISK_MIXED))
        json.dumps(plugin.ble_report(), ensure_ascii=False)


def queue_tolerance() -> float:
    """整块平移后 p10/p80 的取整误差上限（码是整数，±2 码内算一致）。"""
    return 3.0


if __name__ == "__main__":
    unittest.main()