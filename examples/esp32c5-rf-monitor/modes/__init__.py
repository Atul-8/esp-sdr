"""五个功能模块的家。

每个功能分支只新增/修改**自己那一个文件**：

    wifi_check.py   T1 WiFi 信道体检
    waterfall.py    T2 周期性瀑布图
    ble_risk.py     T3 BLE 广播信道风险评估
    guard_58g.py    T4 5.8G 频段守卫
    capture.py      T5 可疑信号抓拍存证

``core.registry.discover()`` 自动导入本包下所有模块并把 ``ModePlugin``
子类注册进去，不需要改任何共享文件 —— 这是五个分支合并零冲突的保证。
"""