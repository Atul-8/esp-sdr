# todo.md — 唯一任务事实源

规则（来自 `docs/开发任务书.md`）：

1. 同一时刻只有一个 `[DOING]`；只干 `[DOING]` 那一条。
2. 每阶段结束是硬 STOP：先把「验收材料 + 验证命令 + 实测输出」交给人类，
   人类回「确认完成」才把 `[DOING]` 标成 `[DONE]` 并把下一条改成 `[DOING]`。
3. 功能分支不许改 `core/`；接口不够用，写进本文件「阻塞与需求」，等人裁决。
4. 分支名固定为 `feat/<阶段号>-<短名>`。

## 阶段表

| # | 分支 | 内容 | 状态 |
| --- | --- | --- | --- |
| 0 | `feat/0-foundation` | `core/` 地基 + mock/假固件 + 43 条自测 | `[REVIEW]` 待人类验收 |
| 1 | `feat/1-wifi-check` | WiFi 信道体检 | `[REVIEW]` 待人类验收（mock 判据 + 真机实测均已通过） |
| 2 | `feat/2-waterfall` | 频谱瀑布图（PNG + HTML 翻页） | `[REVIEW]` 待人类验收 |
| 3 | `feat/3-ble-risk` | BLE 风险与环境评估 | `[REVIEW]` 待人类验收（六档判词 + 仿真佐证 + 增益对照） |
| 4 | `feat/4-guard-58g` | 5.8G 频段守卫 | `[TODO]` |
| 5 | `feat/5-capture` | 可疑信号抓拍（含分类器） | `[TODO]` |
| T | `feat/host-gui` | 图形上位机（WebView，跨阶段工具，不占功能阶段） | `[REVIEW]` 待人类验收（含双击弹出预览 + 高度自适应 + 真全屏 + 独立窗口 + 2D 瀑布频率轴 + 周期瀑布图页） |

---

## [REVIEW] 0. 地基（feat/0-foundation）

交付物：

- `core/serial_link.py` `core/snapshot.py` `core/scheduler.py` `core/store.py`
  `core/registry.py` `core/classifier.py`（接口）
- `output/format.py`、`tests/`（mock 场景 + 假固件 + 自测）、`app/demo.py` `app/live.py`
- `docs/00-foundation.md`（验收材料）、`README.md`

验收标准 → 结果：

| 验收项 | 判据 | 结果 |
| --- | --- | --- |
| 自测全绿 | `python -m unittest discover -s tests -t .` 0 失败 | ✅ 43 passed |
| mock 全链路 | `python -m app.demo` 有统计表、有入库行数、无错误 | ✅ 30 片/60 帧/入库 60 行 |
| 权重生效 | `--weights 4:3:2:1` 占比 ≈ 40/30/20/10 | ✅ 40.0/30.0/20.0/10.0 |
| 协议解析 | 假固件跑真机同一条路径，状态 0、CRC 校验通过 | ✅ `app.live --mock` 错误 0 |
| 坏帧不静默 | CRC 错/截断帧抛 `SnapshotError` 并标记链路失效 | ✅ 有对应测试 |
| 模块隔离 | 一个模块抛异常不影响其它模块 | ✅ 有对应测试 |
| `modes/` 为空 | 阶段 0 不许提前实现功能 | ✅ `discover("modes")` 返回空且有测试守着 |
| 边界声明 | 产出物标明「周期快照、非连续实时」「功率码不是 dBm」 | ✅ README + 输出页脚 |

未覆盖（必须诚实标注）：**真机联调未做**（本机未接 ESP32-C5）；
`classify()` 仍抛 `NotImplementedError`（任务 5 负责）。

## [REVIEW] 1. WiFi 信道体检（feat/1-wifi-check）

- 只加 `modes/wifi_check.py` + `tests/test_wifi_check.py`（未动 `core/`）。
- 产出：2.4G ch1–13 / 5G ch36–165 表格（按相对抬升降序）、推荐信道、建议避开前 3、干扰源列表。

### 扫描计划：20MHz 窗 / 15MHz 步进重叠滑扫（本轮定稿）

```python
SCAN_BW_HZ   = 20_000_000   # 窗宽 = 采样率（20MS/s 实测满带宽，整窗可用）
USABLE_HALF_HZ = 10_000_000 # 窗内可用半宽
SCAN_STEP_HZ = 15_000_000   # 步进 < 窗宽 ⇒ 相邻窗重叠 5MHz，频段无空洞
BAND_24_HZ   = (2_402_000_000, 2_482_000_000)   # → 5 个窗：2412/2427/2442/2457/2472
BAND_5_HZ    = (5_170_000_000, 5_835_000_000)   # → 44 个窗：5180…5825，步进正好 15MHz
```

覆盖率按「信道 20MHz 内每个 MHz 是否真的收到能量（码 > 0）」求并集，跨窗累加：
`coverage < 60% ⇒ 分档「无覆盖」，不参与推荐、不计入干扰源`（防止宽窗边缘 0 码把信道
误判成空闲）。

### 判决口径（本轮改过一次，原因写在下面）

信道中位电平比「同频段最安静信道」高 `8/15/25 dB` ⇒ 轻度 / 拥挤 / 重度。

**为什么不用「高于噪声底的 bin 比例」**：实板实测同一帧内噪声底自身抖动 ±10~18 dB
（AGC 与量化台阶：`20*log10(Q15 power)` 只有 6 dB 一格），任何绝对门限都会把纯噪声判成
忙——实测 2.4G/5G 全部信道 100% 忙、推荐变成「无干净信道」。改成同频段相对抬升后，
安静环境全档位归零，有 AP 的信道高出 20~40 dB，结果可解释。

前提：**横向比信道必须固定增益**。硬件 AGC 会把每个信道拉到相近电平（实测各信道
`p50` 全在 62~77 之间），占用差异被吃掉。因此 `app/live.py` 增加 `--gain-index N`
（实测定稿 **60**：安静段 `p50≈28`、忙段 `p50≈68`，无饱和）。

### 验收标准 → 结果

| 验收项 | 判据 | 结果 |
| --- | --- | --- |
| 自测全绿 | `python -m unittest discover -s tests -t .` | ✅ **58 passed**（原 43 + 本轮 12 + 3 调整） |
| 零误报 | `quiet` 场景：全部「空闲」、干扰源 0、推荐 ch1 | ✅ 干扰源 `无`、推荐 ch1 |
| 塞高占用能被抓 | `wifi_busy`：ch1/ch6 判「拥挤」以上，推荐落在非重叠干净信道 | ✅ ch1/ch2/ch3 进「建议避开」，推荐 **ch11** |
| 宽带污染可分辨 | `microwave`：中段抬升、ch1 干净 | ✅ ch4–ch13 抬升，推荐 ch1 |
| BLE 不误判成 WiFi 负载 | `ble_crowded`：不产生 WiFi 干扰源 | ✅ 干扰源 `无`，推荐落在 1/6/11 |
| 表格按占用排序 | 输出「比最静」列严格降序 | ✅ 有测试守着 |
| 无覆盖不冒充空闲 | 覆盖 <60% ⇒ 「无覆盖」且不推荐 | ✅ 有测试守着 |
| 真机可跑 | `python -m app.live --port COM56 --duration 60 --gain-index 60` | ✅ 17880 帧 / 60.0s / **错误 0** |

### 真机实测（2026-11-18，COM56，`--gain-index 60`，60s / 17880 帧 / 错误 0）

| 排名 | 信道 | 比最静 | 分档 |
| --- | --- | --- | --- |
| 1 | ch36 (5.18G) | +41 dB | 重度 |
| 2 | ch40 | +40 dB | 重度 |
| 3 | ch44 | +34 dB | 重度 |
| 4 | ch48 | +28 dB | 重度 |
| 5 | ch153 (5.765G) | +26 dB | 重度 |
| 6 | ch1 (2.412G) | +22 dB | 拥挤 |
| 8–11 | ch8 / ch9 / ch2 | +19 dB | 拥挤 |
| 27 | ch100 (5.5G) | +0 dB | 空闲 |

推荐：2.4G **ch11**（+3 dB），5G **ch100**（+0 dB）；建议避开 2.4G ch1/ch8/ch9、5G ch36/ch40/ch44。
覆盖列全部 100%，没有出现假「空闲」（上一轮 5G ±20MHz 之外假空闲的问题已消失）。

### 遗留

- 5G 一次全扫需要 44 个窗（≈11s/轮），单模块独占时 60s 只跑 4~5 轮，样本仍偏少；
  后续可在 `todo.md` 定权重要求，或按需 `include_5g=False` 只扫 2.4G（5 个窗 ≈1.3s/轮）。
- 「相对抬升」只在同一次固定增益扫描内可比；换增益/换天线后历史数据不可直接比较。
- 仍未做形式化人工确认（STOP 门禁），等人回「确认完成」再开阶段 2。

## [REVIEW] 2. 频谱瀑布图（feat/2-waterfall）

- 只加 `modes/waterfall.py` + 自己的渲染（`output/png.py` `output/heatmap.py`）+ `app/waterfall.py`。
- 产出：时频热力图（PNG 774×376 每页 + `index.html` 翻页 + HTML 页内导航），
  横轴频率（等宽、各自刻度）、纵轴时间（**一行 = 一轮轮扫 ≈ 4.4 s**），页时长 300 s。
- 判据：mock 跑 10 分钟出图，**能肉眼分辨 WiFi 突发块与微波炉宽带噪声** → 见下表实测。

验收材料（`docs/02-waterfall.md` 有完整版）：

```bash
python -m unittest discover -s tests -t .        # Ran 103 tests / OK（含 feat/host-gui 合并；纯本分支 82）
python -m app.waterfall --minutes 10 --scenario microwave --db .data/wf/microwave.db --out .data/wf/microwave --ascii
python -m app.waterfall --minutes 10 --scenario mixed     --db .data/wf/mixed.db     --out .data/wf/mixed     --ascii
python -m app.waterfall --render-only --db .data/wf/mixed.db --out .data/wf/mixed   # 只重渲染
```

实测输出（本机 Python 3.12.10，mock 虚拟时间）：

| 验收项 | 判据 | 结果 |
| --- | --- | --- |
| 自测全绿 | `unittest` 0 失败 | ✅ 103 passed（新增 `test_png` 10 / `test_heatmap` 15 / `test_waterfall` 20） |
| 轮扫覆盖 | 三频段首尾压边、步进 ≤ 15 MHz、相邻窗重叠 | ✅ 2.4G 6 窗 / 5G LOW 13 / 5G HIGH 25，共 **44 窗一轮** |
| 快照顺路入库 | 10 分钟 mock 入库帧数 == 生成帧数 | ✅ 5984 / 5984，`source_errors 0` |
| 分页不丢帧 | 10 分钟 → 2 页，页帧数之和 == 总数 | ✅ 3036 + 2948 = 5984 |
| PNG 合法且保真 | 尺寸/颜色类型正确，**像素与矩阵逐行一致** | ✅ 774×376 / RGB8；逐行比对测试通过 |
| 微波炉宽带 | 连续抬升 ≥ 40 MHz | ✅ 2430–2483.5 连续（ASCII `====`） |
| WiFi 突发块 | 窄（< 30 MHz）+ 时间轴有暗行 | ✅ 2432–2442，min 22 / max 137 |
| 无假信号 | `quiet` 不出现连续亮带 | ✅ `runs_above(p50+8)` 为空 |
| 边界声明 | 「周期快照，非连续实时」+「相对码不是 dBm」 | ✅ PNG 页眉 / HTML banner / 页脚 / CLI 四处 |
| **上位机真板联调**（合入 `feat/host-gui` 后） | 自检 29 项全绿 + 瀑布页真在滚动 | ✅ `--selftest --real --port COM56 --gain-index 60 --seconds 40` → 29/29、`rows 5 / sweeps 5 / painted 100%`、`share {waterfall 0.5, wifi_check 0.5}`、实测一轮 `12.4 s` |
| 单射频公平配比 | 两个模块各拿一半射频 | ✅ `slice_weight` 都改 1.0；改前实测 share `{waterfall 0.016, wifi_check 0.984}`（瀑布一轮 5 分钟） |

产物（`examples/esp32c5-rf-monitor/.data/wf/`，不入库）：

```text
microwave/waterfall-p1.png  774×376  124,975 B     mixed/waterfall-p1.png  774×376  131,932 B
microwave/index.html        2.3 KB                 mixed/index.html        2.3 KB
microwave/waterfall-p1.html 2.9 KB                 mixed/waterfall-p1.html 2.9 KB
```

未覆盖（诚实标注）：

1. **没上真机跑瀑布图** —— 本分支只做宿主侧，`app.waterfall` 目前只有 mock 源；
   真机数据同样进 `Store`，接口一致但**未实测**。
2. **虚拟时间**：10 分钟数据 40~50 s 生成，时间轴是等效时间不是墙钟（产出物已注明）。
3. **mock 逐 bin 抖动**：`SpectrumSynthesizer._is_on()` 按 bin 掷骰子，图上噪点感是 mock 特性。
4. **上位机页已落地（已合并进 `feat/host-gui`）**：`host/web/pages/waterfall/`（注册表一行 + 片段 + `js/pages/waterfall.js`），
   数据走本模块的 `web_payload()`（一行 = 一轮、一格一字节 base64、0 码 = 未扫到），
   页上还能直接「生成 PNG + HTML」。真板 40 s 自检已验。
5. **环形缓冲按「窗」存**（同窗连帧最大保持合并）：默认 1200 窗 ≈ 27 轮 ≈ 5 分钟；
   按帧存时 1200 帧连一轮都装不下（真板一次 `scan()` 回 30+ 帧），页面只画得出一行半。
6. **`row_period_s` 只用扫满的轮次估**：残轮会把周期算小（实测 1.8 s < 射频 4.4 s）；
   凑不齐两整轮返回 0，前端退回射频时间。小时级历史仍走 `app/waterfall.py` 读 SQLite。

## [REVIEW] 3. BLE 风险与环境评估（feat/3-ble-risk）

- 只加 `modes/ble_risk.py` + `tests/test_ble_risk.py` + `docs/03-ble-risk.md`（未动 `core/`、未动其它模块）。
- 产出：BLE 广播信道（2402/2426/2480）活动度、拥挤度评分、与 WiFi 的共存提示。
- 判据：`ble_crowded` 场景评分必须高于 `quiet`；`wifi_suppress_ble` 场景必须给出「被压制」判读。

**已完成（数字都是实跑）**：

- [x] 扫描计划 8 窗（3 广播信道中心对准 + 数据区 5 窗重叠滑扫），`data_slots()` 37 个数据信道栅格。
- [x] 判据三原则：电平取 **p80**（脉冲不铺满 2 MHz）、窄带占比 ≥ 50%（防 WiFi 斜坡摊肩误判）、
      肩部取左右 max（防不对称斜坡误判）。
- [x] 宽带占用率改在**时间均值频谱**上算（单帧噪声抖动会让 37% 纯噪声 bin 越线）。
- [x] WiFi 共存用双半宽（测电平 ±9 MHz / 判重叠 ±10 MHz），「在用」判据改成占用比例 ≥50%。
- [x] mock 8 场景：quiet 4.9 → crowded **82.7**（密度型）→ suppress **23.8**（压制型）→ mixed 42.8 →
      microwave 31.7 → wifi_busy 21.2 → unknown_cw 19.3 → video_58g 4.8。
- [x] 实板 COM56 固定增益 60：45 s / 372 时间片 / 13262 帧 / 源错误 0，
      判「WiFi 压制型（48.5）」，宽带占用 85%（房间本身 2.4G 很忙，见 `docs/03-ble-risk.md` §6）。
- [x] `python -m unittest discover -s tests -t .` → **76 条 OK**（43 地基 + 33 本功能）。
- [x] 顺手修地基里两条「`modes/` 必须为空」的断言（与功能分支共存冲突，功能 1 分支同款修法）。

**本轮（人类要求「用仿真佐证输出逻辑完备」）**：

- [x] 判词从 4 档扩到 **6 档 + 2 过渡档**：`无明显风险` / `轻微干扰` / `复杂噪音（多源混合）` /
      `蓝牙设备密度型` / `WiFi 压制型` / `宽带常开/形状疑似`（+ `混合型`、`无数据`）。
      核心是**先判「分不分得清」再判「是哪一类」**，分不清就明说分不清，不许冒认 WiFi。
- [x] 新增 4 条判据：`rise_floor`（本机噪声自己的抬升零点，实测标定；样本池含数据区 37 个栅格）、
      `contrast_db`（抬升频点在时间上的开关幅度，p80；≥8 码才算信号）、
      `shallow_ratio`（浅抬升占比，自成一档，需 ≥60 帧样本）、`slots_persistent`（常亮栅格 ⇒ CW 指纹）。
- [x] **仿真佐证脚本** `tests/sim_ble_verdict.py`：用合成功率造 6 档输入，
      五档现实输入 → **5 个互不相同的判词**（完全安静 / 浅干扰 / 复杂噪音 / 静态形状 / 开关型宽带），
      病态输入（整段一起动）落到兜底「宽带常开/形状疑似」。跑法：
      `python tests/sim_ble_verdict.py --scenarios`。
- [x] 断言固化为 `DiscriminationTest`（7 条）→ `tests/test_ble_risk.py` 41 条，
      全量 `discover` **145 条 OK**。
- [x] 实板增益对照实验（同房间同脚本，只改增益）：
      增益 20/40/60 → 宽带占用 **27% / 61% / 85%**，判词「复杂噪音 29.0」→「WiFi 压制型 40.7」→「WiFi 压制型 47.3」。
      ⇒ 高增益下的宽带占用主要是**前端动态范围**，不能当环境真值；新增 `headroom_warning` 自动提示。
- [x] 前端形状取证（24 帧 / 每桶 32 bin）：2442 MHz 窗是平滑「碗形」（两端高 14~27 码、中间 0 码），
      时间抖动恒定 39–45 码、不随电平变化 ⇒ 是前端固有起伏，不是「13 个 WiFi 信道全在用」。

**未验证 / 待人类确认**：

- 没在**安静房间**复核实板（房间是活的，清不了场）；安静侧靠 mock `quiet`（4.9 / 0% / 0%）+
  仿真六档守。
- 本分支 `app/live.py` 还没有 `--gain-index`（属功能 1 分支），实板验证用了不提交的临时脚本。
- **没有自动增益标定**：宽带占用随增益走这件事目前只出告警，不自动降增益复测（需人类定操作口径）。

## [TODO] 4. 5.8G 频段守卫（feat/4-guard-58g）

- 只加 `modes/guard_58g.py`。
- 产出：5150–5850MHz 巡检、持续占用报警、事件入库（`store.add_event`）。
- 判据：`video_58g` 场景必须触发报警；`quiet` 场景必须不报警（误报率受控）。

## [TODO] 5. 可疑信号抓拍（feat/5-capture）

- 只加 `modes/capture.py` **和** `core/classifier.py` 的实现（任务书授权本阶段动 core 的只有这一处，需人类二次确认）。
- 产出：按启发式分类（窄带连续/宽带突发/跳频样）触发 `CAP16`/`CAP20` 抓档，
  落盘原始 I/Q + 元数据 + 分类结论。
- 判据：`unknown_cw` 场景触发抓档且分类为「窄带持续」；`quiet` 场景不抓。

## [REVIEW] T. 图形上位机 / WebView（feat/host-gui）

人类需求原文：「做一个测试上位机，参考 `MagicRobot_V0.1/host` 用 webview 3D 引擎，
以后换别的实验只需要换网页」。

上位机**可以脱离仓库独立运行**：项目根 `E:\_00AE_PROJECR\esp32c5_sdr_app\host\` 是工作副本，
靠 `host/paths.py` 定位分析层（`ESPSDR_SRC` 环境变量 > `host/src` 自包含副本 > 向上 4 层找
`repo/examples/esp32c5-rf-monitor`）。`ESPSDR_SRC` 一旦设了就是权威值，指错立刻抛错不静默回退。

交付物：

- `host/app.py`：`QWebEngineView` + `QWebChannel` 桥（所有 Python→JS 信号都是 JSON 字符串）
  + `QThread` 采集线程（频谱 20fps / 表格 1s / 单片 0.35s）
- `host/session.py`：纯 Python 逻辑层（无 Qt）：`HostConfig` 校验、`HostSession`
  `open/step/spectrum/tables/render_text`、`downsample_bins`（按桶取最大值，保窄带峰）
- `host/web/`：`index.html` + 路由 + 四个页面（实时频谱 / 周期瀑布图 / WiFi 体检 / 原始输出）+
  本地 three.js r128（离线可用）
- 周期瀑布图页（`web/pages/waterfall/` + `js/pages/waterfall.js`）：三频段并排的时频热力图，
  一行 = 一轮完整轮扫、一格一字节、0 码画最暗色；鼠标读频率/功率码；按钮走
  `Bridge.renderWaterfall()` → `produce(force=True)` 在 `.data/waterfall/` 出 PNG + HTML 翻页，
  `Bridge.openFile()` 只在 `.data/` 内允许打开（越界直接拒）。
  `--selftest` 新增 `waterfall_rows/painted/cards/banner/notes/sweep/render/share` 等（共 **29 项 checks**）。
- `host/paths.py`：定位分析层（`ESPSDR_SRC` 权威值 > `host/src` > 向上 4 层）
- `host/web/js/i18n.js` + `--lang zh|en`：外壳文案中英切换（模块自产表格沿用模块语言）
- `host/web/js/scanbar.js`：扫频进度条（频段地图 + 回卷游标，数据来自 `info.plans`）
- `host/web/js/popout.js`：**双击任意面板弹出预览**（铺满窗口 / 75%…300% 自定义缩放 /
  真全屏 / **独立窗口**（`window.open` → `ConsolePage.createWindow`，可拖到别的显示器、
  小窗口自己全屏）/ Esc 关闭，原地搬 DOM 不重建 canvas，缩放按新尺寸重画）
- **弹出后高度自适应**：`--fit-grow` 两阶段量高度（余量 ÷ 标了 `data-fit-grow="1"`
  的画布个数），`U.fitCanvas()` 高度 = `cssHeight × fitScale + fitGrow()`，
  3D 容器 = `calc(300px * var(--fit-scale,1) + var(--fit-grow,0px))`
- `host/web/js/pages/spectrum.js`：2D 瀑布底部**简易频率轴**（轴线 + 5 刻度 + MHz + 中心标亮）
- `host/tests/test_session.py`：23 条无窗口自测
- `host/README.md`：跑法、数据契约、**新增实验页的四步**、边界、踩坑表

验收标准 → 结果：

| 验收项 | 判据 | 结果 |
| --- | --- | --- |
| 逻辑层自测 | `cd host && python -m unittest discover -s tests -t tests` 0 失败 | ✅ 23 passed |
| 无人工自检（假固件） | `python app.py --selftest --seconds 8 --shot out.png` 退出码 0 | ✅ 退出码 0，**29/29 checks** |
| 频谱页真的在画 | 收到帧 > 0 且 2D/3D 画布非黑比例 > 1% | ✅ 188 帧；spec 24.7% / 3D 10.1% / 2D 瀑布 11.6% |
| **2D 瀑布真的在滚** | 缓冲行数 240；`paintedRows ≥ 10`、`distinctRows ≥ 0.9×frames`、顶部均值 > 底部 + 100 | ✅ `paintedRows 35 / frames 35 / distinctRows 32 / topMean 291 / bottomMean 33` |
| **扫频进度条：分段** | 从 `info.plans` 自动切段，至少 2 段、总窗数 > 10 | ✅ `2.4G`（2402–2482，5 窗）+ `5G`（5170–5835，44 窗）= 49 窗 |
| **扫频进度条：游标** | 游标在轨道内、已走过 ≥ 3 个窗 | ✅ `index 44 / visited 18 / x 1043 ≤ 1208`；像素实测琥珀色游标列 `1172 → 1673 → 343（回卷）→ 938` |
| **扫频进度条：回卷** | 喂到最后一窗再喂第一窗：轮次 +1、本轮覆盖清零 | ✅ `passesAfterWrap 2 / visitedAfterWrap 1 / indexAtEnd 3` |
| **中英切换** | 切 `en` 后导航、状态栏、扫频条文案都变；`level` 数据值仍走显示层翻译 | ✅ `navLabels` 含 `Live spectrum`；`shell = ["Scan progress (band map)", "now 5180 MHz · window 6/49 · pass 2", "Heavy"]` |
| **2D 瀑布有频率轴** | 5 个刻度、升序、`lo < 中线 < hi` | ✅ 探针 `wf2dAxis {lo 5710, hi 5730, center 5720, ticks [5710,5715,5720,5725,5730]}`；像素带实测 H-30 处整行 2114 px 轴线 + 5 处标签笔画 |
| **双击弹出预览（开）** | 浮层出现、面板搬进浮层、画布宽度 > 0 | ✅ `open {overlay true, inOverlay true, width 1391}` |
| **双击弹出预览（缩放）** | 150% 后画布宽度 > 1.35× 原宽 | ✅ `zoom {1.5, width 2099} = 1.51×`，`--fit-scale 1.5` |
| **双击弹出预览（关）** | 关闭后浮层不可见、面板回到页面原位 | ✅ `close {overlay false, hidden true, backHome true}` |
| **换页也能弹** | WiFi 页不写代码也能弹（通用逻辑 + ⤢ 按钮） | ✅ `popoutAny {ok true, themed true, back true}` |
| **弹出后高度自适应** | 面板高度 ≥ 浮层高度 90%，画布比 100% 时高 1.3 倍以上 | ✅ `stageH 865 / panelH 825`（95%），画布 `210 → 776`，`--fit-grow 461px` |
| **全屏是真的全屏** | `win.isFullScreen()` 为真，且全屏下面板仍吃满高度 | ✅ `full true`；`stageH 1032 / panelH 993`（96%），`--fit-grow 629`，按钮变「退出全屏」 |
| **窗口外弹窗** | 新开一个独立窗口，里面只有那块面板、画布按窗口尺寸渲染 | ✅ `popout_window {solo true, panel true, key spectrum:1, windows 1}`；`spec-canvas 1463×858`；截图存活 |
| **独立窗口自己全屏** | 在小窗口里点全屏 → 小窗口全屏、主窗口不动 | ✅ 探针：`child_full true / main_full false`，`innerW 1707 × innerH 1067`，面板 1008 高，退出后 `false` |
| WiFi 页真的在判 | 表格行 > 5、推荐信道有值、说明 ≥ 3 条 | ✅ 38 行；推荐 ch11 +17dB 拥挤；4 条说明 |
| 原始页真的在输出 | 至少 1 个模块面板 + ASCII 文本 > 40 字符 | ✅ 1 面板 / 1 表 / 4364 字符 |
| 换实验不改 Python | 新页面只加 `web/pages/<id>/page.html` + `registry.json` 一行 | ✅ 三页共用同一个 `app.py`，`router.js` 不认具体页名 |
| 独立目录 | 上位机放项目根 `host/`，不依赖 `repo/` 的相对位置 | ✅ `paths.find_source()` 解析到 `…
epo\examples\esp32c5-rf-monitor`；`ESPSDR_SRC=/tmp` 正确抛错 |
| 真板可跑 | `--selftest --real --port COM56` 退出码 0 | ✅ 40 s → **29/29 checks 全绿**（598 频谱帧）；瀑布页 `rows 5 / sweeps 5 / painted 100%`、`share {waterfall 0.5, wifi_check 0.5}`、实测一轮 `12.4 s`；弹出 `open 1391 → 2099 → 关闭复位`；全屏 `1032/993`；独立窗口 `spec-canvas 1463×858` |
| 边界一致 | 界面上「功率码不是 dBm」「周期快照」「固定增益才可比」 | ✅ 纵轴/提示/说明页脚都写了 |
| **单射频配比公平** | 两个模块各拿一半射频（`share` 各 ≈0.5） | ✅ 两边 `slice_weight=1.0`；改前实测 `{waterfall 0.016, wifi_check 0.984}`（瀑布一轮被拉成 5 分钟） |
| **进度条只跟一个计划** | 多模块同跑时不因换计划把 `visited` 清零 | ✅ `planModule` 固定；真板 40 s 探针 `visited 15 / passes 1` |
| **重叠窗不误判换轮** | 「索引变小」加幅度门槛（跳回大半张图才算换轮） | ✅ `scanbar.js` 用 `index - found > plan.length/2` |

未覆盖（必须诚实标注）：

- **只在 Windows + Qt 6.12 上验过**；Linux/macOS 没测。
- 「独立窗口」是**同一进程**里的第二个 `QWebEngineView`（共用同一个 `Bridge`），
  不是新进程；主窗口退出时一起关闭，不能单独留一个小窗口活着。
- 独立窗口的 `?solo=<页面:序号>` 里的序号是**面板在页面里的出现顺序**，页面动态
  插拔面板后序号会变——只影响「已开着的旧窗口」重载，重新打开就好。
- 全屏靠 Python 侧 `showFullScreen()`（合成点击没有用户手势，DOM Fullscreen 会被拒）；
  只有纯浏览器打开网页时才会退化成 `popout--max`（铺满窗口）。
- 3D 视图**没有自动对比度/色标校准**，只是同一色带映射；不做定量判读。
- 页面刷新率受采集线程节流（20fps），**不是**固件原始帧率。
- 扫频进度条的**段名**（2.4G / 5G）是前端按频率猜的纯装饰标签，判据全部来自
  `info.plans`；游标位置只反映「正在读哪个窗」，不表示覆盖质量。
- 弹出预览的**「全屏」按钮**走浏览器 Fullscreen API，QWebEngine 默认不 accept，
  失败时退回「铺满窗口」（同一层浮层），行为一致但不会真的占满显示器。
- 缩放上限 300%：再大只是滚动，不会提升信息量（画布是按容器尺寸重渲染的，不是放大位图）。
- 真机实测扫速 ≈ **4 窗/秒**（瓶颈是串口解析 1024 点，不是 100ms 驻留），一轮 49 窗约 12s；
  假固件补节奏后 ≈ 8–10 窗/秒。

遗留（可选优化）：

- `wifi` 页每次表格刷新都重建 38 行 DOM（1 Hz，实测无卡顿）；若以后行数上千要改 diff 渲染。
- 3D 柱状图每次刷新重建 mesh（≤38 个），切页时未复用；暂时够用。

## 阻塞与需求

- **应用侧健壮性缺口**（真机实测暴露，属 `core/scheduler.py`）：一次扫描失败会把链路
  置为 `failed`，调度器随后无退避地空转重试（实测一次卡死刷出 1349405 次同类错误）。
  修它要动 `core/`，按规则需人类裁决：是否允许在功能分支修 `core/scheduler.py`
  的失败重试/退避逻辑（或允许加"连续 N 次失败即停止本轮"的保护）。
- ~~5G 宽窗归属口径~~：已由人类裁决「可以窄、多次移动扫描、允许重叠」→ 本轮落实为
  20MHz 窗 / 15MHz 步进重叠滑扫 + 覆盖率掩码，**已解除**。
> 补充（任务 2 视角）：瀑布图不依赖上述两项修复，mock 与真机（`app.live`）都能出图。

- **上位机瀑布图页嵌不嵌**：已按人类「烧录了就做上位机给我」需求在 `feat/host-gui` 落地（`web/pages/waterfall/`），
  本源实现 `modes/waterfall.py:web_payload()`；不需要再等裁决。

- **固定增益档位的产品化口径**：当前靠人工给 `--gain-index 60`。要不要做自动标定
  （逐档试扫、挑不饱和的最高档）需要人类定，属于应用层（不碰 `core/`）。

## 约定

- 每条功能分支开工前：`git checkout -b feat/<n>-<name>`，动手前把本条改成 `[DOING]`。
- 收工：提交信息写清「做了什么 / 怎么验证 / 边界」，并在本文件补结果。