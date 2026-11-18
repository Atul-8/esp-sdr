# -*- coding: utf-8 -*-
"""英文界面「零中文」的正式断言：下行 payload 全过一遍，逐条查 CJK。

为什么要有这条测试：英文界面里的文案有两个来源——
  1. 网页里的静态词条（``web/js/i18n.js`` 的 ``en`` 词典）；
  2. 模块现算出来的判词/表格/体检结论（``modes/``、``app/audit.py`` 里是中文）。
第 2 类没法靠前端翻，只能在 ``host/l10n.py`` 沿着 Python→JS 的边界换成英文。
少写一条模板，中文就会漏进英文界面，而这种漏是肉眼在界面上一眼看不全的，
所以这里把 10 个场景的**所有**下行出口都扫一遍。

跑法：``cd host && python tests/test_l10n.py -v``
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import paths  # noqa: E402 - 本地上位机模块

paths.ensure_on_path()

import l10n  # noqa: E402
from session import HostConfig, HostSession, to_json  # noqa: E402

#: 10 个内置假场景（tests/mock_source.py 的全部场景）
SCENARIOS = (
    "quiet",
    "wifi_busy",
    "ble_crowded",
    "unknown_cw_clean",
    "unknown_cw",
    "video_58g",
    "microwave",
    "mixed",
    "wifi_suppress_ble",
    "hotel_scan",
)

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

#: 每个场景灌多少帧（0.5 s/帧）。凑够 12 s 就有判据样本，再多只是白等
STEPS = 12

#: 下行的所有出口（新增一个入口就要加到这里，否则它会绕过本测试）
OUTLETS = ("tables", "describe", "stats", "sweep_state", "gain_state", "audit")


def _walk(node: object, path: str, out: list[tuple[str, str]]) -> None:
    """递归收集所有含 CJK 的字符串（带 JSON 路径，报错时能直接定位）。"""
    if isinstance(node, str):
        if CJK.search(node):
            out.append((path, node))
    elif isinstance(node, dict):
        for key, val in node.items():
            _walk(val, f"{path}.{key}", out)
    elif isinstance(node, (list, tuple)):
        for val in node:
            _walk(val, f"{path}[]", out)


def _snapshot_payload(session: HostSession, lang: str) -> list[tuple[str, str]]:
    """跑一段假时间（够凑出判据样本），再把各下行出口全扫一遍。"""
    found: list[tuple[str, str]] = []
    session.open(
        HostConfig.from_json(
            to_json({"port": "MOCK", "mock": True, "scenario": "quiet", "lang": lang})
        )
    )
    try:
        for _ in range(STEPS):
            session.step(0.5, on_snapshot=lambda snap: None)
        for name in OUTLETS:
            _walk(getattr(session, name)(), name, found)
        text = session.render_text()
        if not text.strip():
            found.append(("render_text", "<<空>>"))
        for line in text.split("\n"):
            if CJK.search(line):
                found.append(("render_text", line.strip()[:160]))
    finally:
        session.close()
    return found


class EnglishHasNoChineseTest(unittest.TestCase):
    """英文模式：10 个场景 × 6 个出口 + render_text，一条中文都不许剩。"""

    def test_en_payload_has_no_cjk(self) -> None:
        offenders: list[str] = []
        for scen in SCENARIOS:
            session = HostSession(bins_out=256)
            try:
                session.open(
                    HostConfig.from_json(
                        to_json({"port": "MOCK", "mock": True, "scenario": scen, "lang": "en"})
                    )
                )
                for _ in range(STEPS):
                    session.step(0.5, on_snapshot=lambda snap: None)
                found: list[tuple[str, str]] = []
                for name in OUTLETS:
                    _walk(getattr(session, name)(), name, found)
                for line in session.render_text().split("\n"):
                    if CJK.search(line):
                        found.append(("render_text", line.strip()[:160]))
            finally:
                session.close()
            for path, text in found:
                offenders.append(f"{scen} {path}: {text}")
        self.assertEqual(
            offenders[:20],
            [],
            f"英文模式还有 {len(offenders)} 条中文下行文案（补 host/l10n.py 的 TABLE）",
        )

    def test_en_render_text_looks_english(self) -> None:
        """ASCII 页改成「从已本地化的表格拼英文」，得真拼出东西来。"""
        session = HostSession(bins_out=256)
        try:
            session.open(
                HostConfig.from_json(
                    to_json({"port": "MOCK", "mock": True, "scenario": "mixed", "lang": "en"})
                )
            )
            for _ in range(STEPS):
                session.step(0.5, on_snapshot=lambda snap: None)
            text = session.render_text()
        finally:
            session.close()
        self.assertGreater(len(text), 200)
        self.assertIn("MHz", text)
        self.assertIsNone(CJK.search(text), "render_text(en) 里还有中文")


class ChineseStillChineseTest(unittest.TestCase):
    """中文模式不能被误翻成英文（防止「为了英文把中文也改了」）。"""

    def test_zh_payload_keeps_chinese(self) -> None:
        found = _snapshot_payload(HostSession(bins_out=256), "zh")
        self.assertGreater(len(found), 20, "中文模式下行几乎没有中文了，可能是误配 l10n")

    def test_zh_render_text_keeps_chinese(self) -> None:
        session = HostSession(bins_out=256)
        try:
            session.open(
                HostConfig.from_json(
                    to_json({"port": "MOCK", "mock": True, "scenario": "mixed", "lang": "zh"})
                )
            )
            for _ in range(STEPS):
                session.step(0.5, on_snapshot=lambda snap: None)
            text = session.render_text()
        finally:
            session.close()
        self.assertIsNotNone(CJK.search(text), "render_text(zh) 全变英文了")


class TranslateUnitTest(unittest.TestCase):
    """``l10n`` 的三个纯函数：能翻、翻不到就原样返回、绝不抛异常。"""

    def test_skeleton_collapses_numbers(self) -> None:
        self.assertEqual(l10n.skeleton("带宽 12.5 MHz、占空 58%"), l10n.skeleton("带宽 3 MHz、占空 9%"))
        self.assertIn("{}", l10n.skeleton("带宽 12.5 MHz"))

    def test_known_string_translated(self) -> None:
        text = l10n.localize("重度")
        self.assertFalse(CJK.search(str(text)), f"「重度」没翻：{text}")

    def test_unknown_string_passes_through(self) -> None:
        """翻不到就原样返回——界面不能因为一条文案挂掉，也不能吞内容。"""
        weird = "这条文案一定不在词典里 42"
        self.assertEqual(l10n.localize(weird), weird)

    def test_placeholder_mismatch_passes_through(self) -> None:
        """模板占位数量与原文数字段不一致 → 原样返回（宁可留中文，也不要数字错位）。"""
        # 造一条「骨架对得上、但模板占位少一个」的词条：模板 1 个 {} 对上原文 2 个数字段
        key = "样例 {} {} 测试"
        l10n.TABLE[key] = "sample {} test"
        try:
            probe = "样例 1 2 测试"
            self.assertEqual(l10n.skeleton(probe), key)
            self.assertEqual(l10n.translate(probe), probe)
        finally:
            l10n.TABLE.pop(key, None)

    def test_non_string_untouched(self) -> None:
        self.assertEqual(l10n.localize(3.5), 3.5)
        self.assertEqual(l10n.localize(None), None)
        self.assertEqual(l10n.localize(True), True)

    def test_dict_keys_not_translated(self) -> None:
        """只翻值、不翻 key：前端是按 key 取数的，翻了就取不到。"""
        out = l10n.localize({"重度": "重度"})
        self.assertIn("重度", out)
        self.assertFalse(CJK.search(str(out["重度"])))


if __name__ == "__main__":
    unittest.main(verbosity=2)