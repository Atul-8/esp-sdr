# 测试上位机（PySide6 + WebView）

一个**桌面壳 + 网页界面**的测试上位机：Python 只做三件事（串口、调度、打包
JSON），界面全部在 `web/` 里的 HTML/CSS/JS。

**换实验 = 换网页**：新增一个实验只加 `web/pages/<id>/page.html` 和
`registry.json` 里一行，`app.py` **不用改**。

```
host/
├── app.py             # Qt 外壳：QWebEngineView + QWebChannel 桥 + 采集线程 + --selftest
├── session.py         # 纯 Python 逻辑层（无 Qt，可单独测）：配置校验 / 会话 / 频谱打包
├── start.bat          # Windows 双击启动（缺依赖自动装）
├── requirements.txt   # PySide6 + pyserial
├── tests/test_session.py  # 15 条无窗口自测
└── web/
    ├── index.html     # 唯一入口：顶栏 + 导航 + #page-container + 页脚
    ├── css/app.css    # 暗色仪表盘主题
    ├── assets/vendor/ # three.js r128 + OrbitControls（本地，离线可用，MIT）
    ├── js/
    │   ├── bridge.js  # QWebChannel 封装：信号自动 JSON.parse，调用返回 Promise
    │   ├── util.js    # 单位格式化 + 分档配色 + 热力色带 + 画布适配
    │   ├── three3d.js # 3D 瀑布曲面 / 3D 信道柱状图
    │   ├── router.js  # 按 registry.json 加载片段（XHR，QWebEngine 不支持 file:// 的 fetch）
    │   ├── app.js     # 外壳装配 + 事件路由 + 自检探针 window.probeReport()
    │   └── pages/     # 各实验自己的渲染逻辑
    └── pages/
        ├── registry.json      # 页面注册表（换实验只动这里）
        ├── spectrum/page.html # 实时频谱：2D 曲线 + 3D 瀑布 + 2D 瀑布
        ├── wifi/page.html     # WiFi 信道体检：卡片 + 排序表 + 3D 柱状图
        └── raw/page.html      # 通用兜底：任何模块的 Output 表格 + CLI 同款 ASCII
```

## 怎么跑

```sh
cd examples/esp32c5-rf-monitor

# 1) 真板（COM56 是这块板子的硬规则，见仓库根 AGENTS.md）
python host/app.py --port COM56 --gain-index 60

# 2) 没板也能看：假固件 + 内置场景
python host/app.py --mock --scenario wifi_busy

# 3) 无人工干预自检（跑 8s → 切三个页面截图 → 打印 JSON 报告 → 退出码 0/2）
python host/app.py --selftest --seconds 8 --shot out.png
python host/app.py --selftest --real --port COM56 --gain-index 60 --seconds 12

# 逻辑层自测（不开窗口）
python -m unittest discover -s host/tests -t . -v
```

Windows 上也可以双击 `host/start.bat`（缺依赖会自动 `pip install`）。

## 造一个新实验页（不碰 Python）

1. 建 `web/pages/<id>/page.html`（纯静态结构，`id` 自己起，只写要显示的骨架）。
2. 在 `web/pages/registry.json` 里加一行 `{"id": "<id>", "title": "显示名", "hint": "副标题"}`。
3. 需要实时数据就再写 `web/js/pages/<id>.js`：
   ```js
   window.Pages = window.Pages || {};
   window.Pages["<id>"] = {
     onEnter(root) {},            // 片段注入后调用，root 就是 #page-container
     onLeave() {},                // 切走时清理计时器/事件监听
     onSpectrum(payload) {},      // 每帧频谱（~20fps）
     onTables(payload) {},        // 每 1s 的表格式产出
     probe() { return {}; },      // 可选：给 --selftest 的报告里加一段
   };
   ```
   然后在 `index.html` 的 `<script>` 列表里加一行（这是唯一的 Python 之外的手工步骤）。
4. 没写 JS 也能跑：`raw` 页面会把任何模块的 `Output` 表格渲染出来。

## 数据契约（Python → 前端）

所有信号都传**一个 JSON 字符串**（QWebChannel 会把 `QVariantList` 降级成 JS
`object`，字符串最稳）：

| 信号 | 频率 | 内容 |
|---|---|---|
| `info` | 开流时一次 | `{kind:"info", identity, port, baudrate, mock, scenario, gain_index, profiles, plugins, notes}` |
| `spectrum` | ≤20fps | `{kind:"spectrum", ts, module, center_hz, bw_hz, freq_lo_hz, freq_hi_hz, raw_bins, bins:[0..255]}` |
| `tables` | 1s | `{kind:"tables", plugins:[{id, name, weight, table:{title, columns, rows}, payload, error}]}` |
| `stats` | 1s | `{kind:"stats", slices, snapshots, elapsed_s, source_errors, last_source_error}` |
| `state` / `log` | 事件 | 状态机与日志文本 |

JS → Python 只有四个入口：`listPorts()`、`scenarios()`、`renderText()`、
`start(configJson)` / `stop()`（都在 `bridge.js` 里封装成 Promise）。

## 边界（和 CLI 输出同一套口径，界面不许美化）

- **周期快照，非连续实时**：帧与帧之间有接收空档，瀑布是"离散时间片堆叠"。
- **功率码是相对 dB 码（0..255），不是 dBm**；纵轴标注写的就是"功率码"。
- **固定增益才有可比性**：跨信道比较前先钉 `--gain-index 60`；界面上 `硬件 AGC`
  和 `固定档 N` 分开显示，不混着比。
- **分类是启发式**：WiFi 体检只说"相对抬升多少 dB"，不说"这里一定是 WiFi"。
- 页面里的"推荐信道"直接取自 `modes/wifi_check.py` 的 `web_payload()`，前端
  **不做二次判定**，保证界面和 `python -m app.live` 的结论一致。

## 已经踩过的坑（都修了，别再踩）

| 现象 | 根因 | 修法 |
|---|---|---|
| 前端 `Bridge.ready()` 一直 false，`window.backend` 不存在 | `QWebChannel` 只交给了 `setWebChannel()`，Python 侧没留引用被 GC | `HostWindow.channel` 持强引用（`QWebChannel(self)`） |
| 页面白屏、网络面板无报错 | QWebEngine 的 `fetch()` 不支持 `file://` | 一律用 `XMLHttpRequest` 加载本地片段（`LocalContentCanAccessFileUrls=True`） |
| 画布全黑但帧数 > 0 | 页面切走后 `requestAnimationFrame` 停、画布尺寸 0 | `fitCanvas()` 只在 `onEnter` 里取容器尺寸；3D 组件在 `onLeave` 里 `dispose()` |
| 网页报错查不到 | 默认不打印前端 console | `ConsolePage.javaScriptConsoleMessage` 转发到 stdout（`[js:err] 文件:行 消息`） |