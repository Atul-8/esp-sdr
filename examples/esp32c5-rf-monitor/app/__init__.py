"""应用入口（demo = mock 全链路，live = 真机/假固件链路）。"""
from __future__ import annotations

from typing import Any


def bind_store(registry: Any, store: Any) -> list[str]:
    """把 ``store`` 交给「自己声明了 ``store`` 属性」的功能模块，返回绑定的模块 id。

    为什么要这一层：功能模块（``modes/``）不 import ``core.store``，只在自己的实例上
    留一个 ``store`` 槽位；由 app 层注入，模块就能 ``store.add_event(...)`` 落告警/事件。
    这样 ``modes/`` 与存储层解耦，也方便单测塞假 store。
    """
    bound: list[str] = []
    if store is None:
        return bound
    for plugin in getattr(registry, "plugins", []):
        if hasattr(plugin, "store"):
            try:
                if getattr(plugin, "store", None) is None:
                    plugin.store = store
                    bound.append(plugin.id)
            except Exception:  # noqa: BLE001 - 只读属性的模块跳过即可
                continue
    return bound


__all__ = ["bind_store"]