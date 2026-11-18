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
├── tests/test_session.py  # 23 条无窗口自测
└── web/
    ├── index.html     # 唯一入口：顶栏 + 导航 + #page-container + 页脚
    ├── css/app.css    # 暗色仪表盘主题
    ├── assets/vendor/ # three.js r128 + OrbitControls（本地，离线可用，MIT）
    ├── js/
    │   ├── bridge.js  # QWebChannel 封装：信号自动 JSON.parse，调用返回 Promise
    │   ├── util.js    # 单位格式化 + 分档配色 + 热力色带 + 画布适配
    │   ├── three3d.js # 3D 瀑布曲面 / 3D 信道柱状图
    │   ├── router.js  # 按 registry.json 加载片段（XHR，QWebEngine 不支持 file:// 的 fetch）
    │   ├── popout.js  # 双击任意面板 → 全窗口预览 + 自定义缩放 + 全屏 + 独立窗口
    │   ├── scanbar.js # 扫频进度条（2.4G/5G 频段地图 + 回卷游标）
    │   ├── i18n.js    # 中英切换（只翻外壳 + 结构化数据）
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
#    → 报告末尾 "checks": 21 项全 true，退出码 0（假固件与真板都验过）

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
     onInfo(payload) {},          // 开流/换扫描计划一次（含 plans，画频段条用）
    onSpectrum(payload) {},      // 每帧频谱（~20fps）
     onTables(payload) {},        // 每 1s 的表格式产出
     probe() { return {}; },      // 可选：给 --selftest 的报告里加一段
   };
   ```
   然后在 `index.html` 的 `<script>` 列表里加一行（这是唯一的 Python 之外的手工步骤）。
4. 没写 JS 也能跑：`raw` 页面会把任何模块的 `Output` 表格渲染出来。

## 扫频进度条（`web/js/scanbar.js`）

频谱页最上面那条横条就是「扫到哪了」：左边一段是 2.4G、右边一段是 5G，
每一小格 = 扫描计划里的一个 20MHz 窗，游标停在该窗的中心频点上；扫到最右端
**回卷**到最左端继续下一轮，底部细条是本轮已覆盖的窗数。

- **频段边界和窗位置都不是写死的**，来自 `info.plans[<module>]`（Python 侧
  `scan_requests()` 的 `[[中心频点, 带宽], ...]`，按扫描顺序）。前端按「相邻窗
  频率跳跃 > 3 倍窗宽」自动切段并命名（2.4G / 5G / #n），换实验不用改 JS。
- 横轴是**分段线性**的：2.4G 只有 80 MHz、5G 有 665 MHz，按真实比例画 2.4G 会
  变成一根看不见的线，所以每段宽度取 √跨度 归一（实测 2.4G ≈ 26%、5G ≈ 74%）。
- 游标用 `requestAnimationFrame` 插值（不是每帧硬跳）；回卷时用更慢的插值，
  能看见游标一路往左扫回去。已扫过的窗留浅蓝底、当前窗高亮。
- **假固件不模拟驻留时间**（一个窗口 3ms 就跑完），不加节流界面上的游标会闪成
  一片；`Runner._mock_pace()` 在每帧之间补 `dwell_ms/2`，节奏就与真机一致。
  实测：假固件 ≈ 8~10 窗/秒，真机 ≈ 4 窗/秒（串口解析 1024 点才是瓶颈，
  不是 100ms 驻留），一轮 49 窗约 12s。

## 双击弹出预览（`web/js/popout.js`）

**任何一个 `.panel` 双击就弹成全窗口预览**，右上角也有个 `⤢` 按钮（双击不显眼，
所以两个入口都给）。工具栏能做的事：

- `−` / `+`：**自定义缩放**，75% → 100% → 125% → 150% → 200% → 300%（12 档太密，
  按这 6 档走）；`重置` 回 100%；`独立窗口` 把这块面板丢到**另一个窗口**里
  （见下节）；`全屏` / `退出全屏` 见下；`✕`/`Esc` 关闭。
- 浮层是**同一文档内的 fixed 覆盖层 + 原地搬 DOM**，不是 `window.open`：
  canvas / WebGL 上下文跟着 DOM 走，搬进搬出都不丢内容，也不用给 Python 侧再加
  一个窗口和桥。
- 缩放是**按新尺寸重画**，不是把小图拉伸放大：面板宽度 = 倍率 × 100%，
  同时在面板上设 `--fit-scale: 倍率`，`U.fitCanvas()` 与 3D 容器高度
  （`#wf-3d` 的 `calc(300px * var(--fit-scale, 1))`）都乘这个倍率，
  然后派发一次 `resize` 让页面自己的钩子重算。
- 尺寸一变，2D 瀑布**保留历史**（把旧缓冲按新宽度重采样，`resizeWaterfall()`），
  不再像以前那样清空重来。
- 换页时 `Router.navigate()` 先 `Popout.close()`：浮层里那个面板属于上一页的 DOM，
  不搬回去会被 `innerHTML =` 一起清掉。

新实验页不用写任何代码就有这个功能——`Popout.attach(#page-container)` 是委托到
`.panel` 上的，按钮也是自动加的（`MutationObserver` 兜住运行时新插入的面板）。

### 弹出后高度自适应（别留一大片空白）

弹出来只是「变宽」不够，2D 瀑布那种固定 `150px` 高的画布放大后下面全是空白。
现在的做法是**两阶段量高度**：

1. 面板上 `--fit-grow` 归零 → 派发一次 `resize`，页面按倍率把画布重画成「自然高度」；
2. 260ms 后量 `浮层可用高度 − 面板实际高度`，把这段余量除以面板里标了
   `data-fit-grow="1"` 的个数，写进 `--fit-grow`，再派发一次 `resize`。

`U.fitCanvas()` 里的高度 = `cssHeight × fitScale + fitGrow()`，3D 容器高度 =
`calc(300px * var(--fit-scale, 1) + var(--fit-grow, 0px))`。窗口被拉大/全屏时
（Python 侧 `showFullScreen()` 也会触发真正的 `resize`）会重新走一遍这个流程；
自己派发的合成 `resize` 用一个 `suppress` 计数屏蔽，不会自我递归。

实测：普通窗口浮层 stage 865px / 面板 825px（95%），全屏 stage 1032 / 面板 993（96%）。

### 全屏为什么不走 `requestFullscreen()`

自动化（`element.click()`）和脚本触发的全屏**没有用户手势**，Chromium 直接拒，
表现就是「点了全屏没反应、布局跟普通一模一样」——真机第一次就是这么翻车的。
现在 `全屏` 按钮调 `Bridge.call("setFullscreen", {"on":..,"target":"main"|"popout"})`，
由 Python 侧 `win.showFullScreen()/showNormal()` 做，不需要手势；纯浏览器打开网页
（没有 QWebChannel）时才退回 DOM Fullscreen API，再不行退化成「铺满窗口」。

### 窗口外弹窗（`独立窗口`）

`Popout.openDocked()` → `window.open('index.html?solo=<页面:序号>')`，Python 侧
`ConsolePage.createWindow()` 接住并返回一个新页：

- 新窗口是**真正的窗口**（`QWebEngineView` 顶层窗口，可拖到别的显示器、可自己全屏）；
- 加载的还是同一套 `web/index.html`，只是带 `?solo=` 参数，网页自己
  `body.solo` 隐藏外壳、只把那一个面板铺满（隐藏「关闭」和「独立窗口」按钮）；
- **共用同一个 `Bridge`**：`QWebChannel` 是每个页面一个，但注册的是同一个
  `backend`，所以信号天然广播给所有窗口；晚连上的窗口靠 `runtime()` 里带回的
  最后一帧 `spectrum`/`tables` 立刻有画面（`HostWindow._cache_spectrum`）；
- 主窗口关掉时一起关掉（`HostWindow.shutdown()`）；浮层挂进 `#page-container`
  而不是 `body`，是为了让被搬走的面板仍然是页面 `root` 的后代
  （否则页面 resize 钩子 `root.querySelector("#spec-canvas")` 拿到 `null` 直接抛错，
  画面停止重绘——这个坑现场踩过）。

## 2D 瀑布的频率轴

`#wf-canvas` 底部有一条**简易频率轴**：轴线 + 5 个刻度（含两端）+ MHz 标签 +
单位，中间那个刻度标亮一点，代表本窗中心频点。两条边界要说清楚：

- 轴描述的是**最新一行**（当前窗）；扫频时上面旧行可能来自别的窗，所以轴不是
  「整张图的绝对横轴」，`wf2dAxis` 也只报最新一帧的 `lo/hi/center/ticks`。
- 刻度是按 `payload.freq_lo_hz..freq_hi_hz` 均分算的，不经 `modes/` 二次处理，
  界面与 `python -m app.live` 的横轴一致。

## 数据契约（Python → 前端）

所有信号都传**一个 JSON 字符串**（QWebChannel 会把 `QVariantList` 降级成 JS
`object`，字符串最稳）：

| 信号 | 频率 | 内容 |
|---|---|---|
| `info` | 开流时一次 | `{kind:"info", identity, port, baudrate, mock, scenario, gain_index, lang, profiles, plugins, plans:{id:[[center_hz,bw_hz]...]}, notes}` |
| `spectrum` | ≤20fps | `{kind:"spectrum", ts, module, center_hz, bw_hz, freq_lo_hz, freq_hi_hz, raw_bins, bins:[0..255]}` |
| `tables` | 1s | `{kind:"tables", plugins:[{id, name, weight, table:{title, columns, rows}, payload, error}]}` |
| `stats` | 1s | `{kind:"stats", slices, snapshots, elapsed_s, source_errors, last_source_error}` |
| `state` / `log` | 事件 | 状态机与日志文本 |

页面里 `probe()` 额外回给自检的键（不影响运行）：`frames`、`scanbar`、
`wf2d`（含 `paintedRows/distinctRows/topMean/bottomMean`）、`wf2dAxis`
（`{lo, hi, center, ticks}`，单位 MHz）、`three`。

自检另外收两路只有宿主才看得见的证据：`Popout.selftest()`（浮层开 / 1.5× 缩放 /
归位、`--fit-grow`、面板高度 vs 浮层高度）与 Python 侧 `popout_window`（独立窗口
里真有画布、窗口标题、截图）、`popout_fullscreen`（`win.isFullScreen()` 为真、
全屏下面板仍吃满高度）。这三项是「弹出预览 + 窗口外弹窗」的验收凭据。

JS → Python 的入口：`listPorts()`、`scenarios()`、`renderText()`、
`start(configJson)` / `stop()`、`setFullscreen({on,target})`、`uiDefaults()`、
`runtime()`、`uiReport(json)`（都在 `bridge.js` 里封装成 Promise）。

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
| 游标一秒刷完 90 多轮、覆盖数一直是 1 | 假固件不吃 `dwell_ms`，一个窗口 3ms 就跑完 | `Runner._mock_pace()` 补节奏（见上节） |
| 扫频进度条上没有游标 / 显示「等待扫描计划」 | `info` 是开流后才到，页面 `onEnter` 时还没有 `plans` | `app.js` 把 `info` 也 `Router.dispatch("Info", …)`，页面 `onInfo` 里补 `applyPlan()` |
| 弹出预览一把静态文案全翻回默认值（`#raw-text` 的 ASCII 被清成「未连接」） | `Popout` 里为了翻按钮标题调了 `I18N.apply(整个面板)`，把 `data-i18n` 占位的**动态内容**一起重写了 | 只 `I18N.apply(工具栏)` + 逐个设按钮 `title`，绝不 apply 面板 |
| 关闭浮层后面板还在浮层里 / 打不开第二次 | 浮层元素只是 `hidden`，探针按「元素存在」判断成没关 | 判据改成 `.popout:not([hidden])` 与 `overlay.hidden`；关闭时把面板 `insertBefore` 回原位 |
| 弹出/放大后画布高度一点没变，下面一大片空白 | 页面 resize 钩子用 `root.querySelector("#spec-canvas")`，面板被搬到 `body` 下的浮层后就不在 `root` 里了 → 拿到 `null` 抛 `TypeError`，整段重绘中断 | 浮层挂到 `#page-container` 内（`position: fixed`，视觉不变），`fitCanvas(null)` 也返回空对象不再抛错 |
| 点「全屏」没反应，布局跟普通窗口一模一样 | 合成点击没有用户手势，Chromium 拒 `requestFullscreen()`（`QtWebEngine` 的 `fullScreenRequested` 压根没触发） | 全屏走 `Bridge.setFullscreen` → Python `win.showFullScreen()`；DOM API 只作纯浏览器兜底 |
| 独立窗口里画面空白/不刷新 | 新窗口晚连上，`QWebChannel` 之前的信号全丢了 | `runtime()` 带回最后 `spectrum`/`tables`，`syncRuntime()` 直接回放；`HostWindow` 缓存两份 JSON |
| 网页报错查不到 | 默认不打印前端 console | `ConsolePage.javaScriptConsoleMessage` 转发到 stdout（`[js:err] 文件:行 消息`） |