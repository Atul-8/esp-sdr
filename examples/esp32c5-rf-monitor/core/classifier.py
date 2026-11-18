"""启发式信号分类器（仅任务 5「可疑信号抓拍」需要调用）。

阶段 0 只定义接口与特征结构，算法留给 feat/5-capture 实现。
边界（写进输出注释，不许美化）：这是**启发式**分类，只用带宽 / 持续
时间 / 占空比三个特征，不做调制识别、不做设备级归因。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

#: 已知类别。``unknown`` 是任务 5 的抓拍触发条件。
KNOWN_LABELS = (
    "wifi",
    "ble",
    "microwave",
    "analog_video",
    "unknown",
)


@dataclass(frozen=True)
class SignalFeatures:
    """一个候选发射源的三个（可选再加两个）启发式特征。"""

    bw_hz: float
    duration_s: float
    duty_cycle: float
    peak_over_noise_db: float = 0.0
    hop_rate_hz: float | None = None
    centre_hz: float | None = None

    def as_dict(self) -> dict[str, float | None]:
        return {
            "bw_hz": self.bw_hz,
            "duration_s": self.duration_s,
            "duty_cycle": self.duty_cycle,
            "peak_over_noise_db": self.peak_over_noise_db,
            "hop_rate_hz": self.hop_rate_hz,
            "centre_hz": self.centre_hz,
        }


@dataclass(frozen=True)
class Classification:
    label: str
    confidence: float
    reason: str
    notes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.label not in KNOWN_LABELS:
            raise ValueError(f"未知类别：{self.label}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence 必须在 0..1")


def classify(features: SignalFeatures) -> Classification:
    """启发式分类入口。

    计划中的判定骨架（任务 5 实现，这里是占位）：

    * 带宽 ≥ 5 MHz 且占空比高      → ``wifi``
    * 带宽 ≈ 1–2 MHz、脉冲窄而密   → ``ble``
    * 宽带抬升噪声（≥ 10 MHz 连续）→ ``microwave``
    * 6–8 MHz、长时间持续、窄带     → ``analog_video``
    * 其余                          → ``unknown``（触发抓拍）
    """
    raise NotImplementedError(
        "classify() 由 feat/5-capture 分支实现；阶段 0 只固定接口与特征结构"
    )


__all__ = ["KNOWN_LABELS", "Classification", "SignalFeatures", "classify"]