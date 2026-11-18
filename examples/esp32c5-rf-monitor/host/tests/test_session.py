"""上位机逻辑层自测（不需要开窗口，也不需要真板）。

跑法（在 ``examples/esp32c5-rf-monitor`` 下）：

    python -m unittest discover -s host/tests -t . -v
"""
from __future__ import annotations

import json
import unittest

from host.session import HostConfig, HostSession, downsample_bins, list_ports, to_json


class ConfigTests(unittest.TestCase):
    def test_from_json_defaults(self):
        cfg = HostConfig.from_json("{}")
        self.assertEqual(cfg.port, "COM56")
        self.assertEqual(cfg.baudrate, 2_000_000)
        self.assertIsNone(cfg.gain_index)
        self.assertFalse(cfg.mock)

    def test_from_json_overrides(self):
        cfg = HostConfig.from_json(
            to_json({"port": "COM56", "mock": True, "scenario": "quiet", "bins_out": 128, "baudrate": 2_000_000})
        )
        self.assertTrue(cfg.mock)
        self.assertEqual(cfg.scenario, "quiet")
        self.assertEqual(cfg.bins_out, 128)

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            HostConfig.from_json(to_json({"port": "COM56", "nonsense": 1}))

    def test_unknown_scenario_rejected(self):
        with self.assertRaises(ValueError):
            HostConfig.from_json(to_json({"scenario": "no_such_scenario"}))

    def test_mock_drops_gain(self):
        cfg = HostConfig.from_json(to_json({"mock": True, "gain_index": 60}))
        cfg.validate()
        self.assertIsNone(cfg.gain_index)

    def test_empty_port_rejected_for_real_board(self):
        cfg = HostConfig.from_json(to_json({"mock": False, "port": ""}))
        with self.assertRaises(ValueError):
            cfg.validate()


class DownsampleTests(unittest.TestCase):
    def test_keeps_peak_of_each_bucket(self):
        bins = [0] * 8 + [200, 0, 0, 0] + [0] * 4
        out = downsample_bins(bins, 4)
        self.assertEqual(len(out), 4)
        self.assertEqual(max(out), 200)  # 窄带峰不能被平均掉

    def test_small_input_not_padded(self):
        out = downsample_bins([1, 2, 3], 8)
        self.assertEqual(len(out), 3)
        self.assertEqual(out, [1, 2, 3])

    def test_empty_input(self):
        self.assertEqual(downsample_bins([], 256), [])

    def test_never_exceeds_bin_count(self):
        bins = list(range(256))
        self.assertEqual(len(downsample_bins(bins, 1000)), 256)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.session = HostSession(bins_out=64)

    def tearDown(self):
        self.session.close()

    def test_open_mock_and_describe(self):
        info = self.session.open(HostConfig.from_json(to_json({"mock": True, "scenario": "wifi_busy"})))
        self.assertEqual(info["kind"], "info")
        self.assertTrue(info["mock"])
        self.assertEqual(info["identity"].split()[0], "C5SDR")
        self.assertTrue(info["profiles"])
        self.assertTrue(info["plugins"], "modes/ 里应当至少有一个功能模块")

    def test_step_produces_spectrum_json(self):
        self.session.open(HostConfig.from_json(to_json({"mock": True, "scenario": "wifi_busy"})))
        seen = []
        self.session.step(0.45, on_snapshot=lambda snap: seen.append(self.session.spectrum(snap, "wifi_check")))
        self.assertTrue(seen, "假固件应当产出快照")
        frame = seen[0]
        self.assertEqual(len(frame["bins"]), 64)
        self.assertEqual(frame["bins"].__class__, list)
        self.assertGreater(frame["freq_hi_hz"], frame["freq_lo_hz"])
        self.assertEqual(frame["center_hz"], (frame["freq_lo_hz"] + frame["freq_hi_hz"]) // 2)
        self.assertTrue(all(0 <= v <= 255 for v in frame["bins"]))
        json.dumps(frame)  # 必须能序列化成 JSON 字符串发给前端

    def test_tables_carry_wifi_payload(self):
        self.session.open(HostConfig.from_json(to_json({"mock": True, "scenario": "wifi_busy"})))
        self.session.step(2.0)
        tables = self.session.tables()
        self.assertEqual(tables["kind"], "tables")
        wifi = [p for p in tables["plugins"] if p["id"] == "wifi_check"]
        self.assertTrue(wifi, "应当有 wifi_check 模块")
        payload = wifi[0]["payload"]
        self.assertIsNotNone(payload)
        self.assertEqual(payload["kind"], "wifi_check")
        self.assertTrue(payload["channels"], "跑 2 秒后应当有信道样本")
        self.assertIn("avoided", payload)
        self.assertIn("scan_plan", payload)
        self.assertEqual(payload["scan_plan"]["bw_hz"], 20_000_000)
        self.assertEqual(payload["scan_plan"]["step_hz"], 15_000_000)
        # 分档必须是约定的四档之一
        allowed = {"重度", "拥挤", "轻度", "空闲", "无覆盖"}
        for channel in payload["channels"]:
            self.assertIn(channel["level"], allowed)

    def test_render_text_matches_cli_shape(self):
        self.session.open(HostConfig.from_json(to_json({"mock": True, "scenario": "quiet"})))
        self.session.step(1.2)
        text = self.session.render_text()
        self.assertIn("WiFi 信道体检", text)
        self.assertIn("建议", text)

    def test_list_ports_shape(self):
        ports = list_ports()
        self.assertIsInstance(ports, list)
        for item in ports:
            self.assertIn("device", item)


if __name__ == "__main__":
    unittest.main()