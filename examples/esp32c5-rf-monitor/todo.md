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
| 3 | `feat/3-ble-risk` | BLE 风险与环境评估 | `[TODO]` |
| 4 | `feat/4-guard-58g` | 5.8G 频段守卫 | `[TODO]` |
| 5 | `feat/5-capture` | 可疑信号抓拍（含分类器） | `[TODO]` |

> 本分支（`feat/2-waterfall`）从阶段 0（`1f36a4e`）切出，是独立 worktree：
> 阶段 1 的成果在 `feat/1-wifi-check`、上位机在 `feat/host-gui`，所以本文件里它们仍显示 `[TODO]`。

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
python -m unittest discover -s tests -t .        # Ran 82 tests / OK
python -m app.waterfall --minutes 10 --scenario microwave --db .data/wf/microwave.db --out .data/wf/microwave --ascii
python -m app.waterfall --minutes 10 --scenario mixed     --db .data/wf/mixed.db     --out .data/wf/mixed     --ascii
python -m app.waterfall --render-only --db .data/wf/mixed.db --out .data/wf/mixed   # 只重渲染
```

实测输出（本机 Python 3.12.10，mock 虚拟时间）：

| 验收项 | 判据 | 结果 |
| --- | --- | --- |
| 自测全绿 | `unittest` 0 失败 | ✅ 82 passed（新增 `test_png` 10 / `test_heatmap` 15 / `test_waterfall` 14） |
| 轮扫覆盖 | 三频段首尾压边、步进 ≤ 15 MHz、相邻窗重叠 | ✅ 2.4G 6 窗 / 5G LOW 13 / 5G HIGH 25，共 **44 窗一轮** |
| 快照顺路入库 | 10 分钟 mock 入库帧数 == 生成帧数 | ✅ 5984 / 5984，`source_errors 0` |
| 分页不丢帧 | 10 分钟 → 2 页，页帧数之和 == 总数 | ✅ 3036 + 2948 = 5984 |
| PNG 合法且保真 | 尺寸/颜色类型正确，**像素与矩阵逐行一致** | ✅ 774×376 / RGB8；逐行比对测试通过 |
| 微波炉宽带 | 连续抬升 ≥ 40 MHz | ✅ 2430–2483.5 连续（ASCII `====`） |
| WiFi 突发块 | 窄（< 30 MHz）+ 时间轴有暗行 | ✅ 2432–2442，min 22 / max 137 |
| 无假信号 | `quiet` 不出现连续亮带 | ✅ `runs_above(p50+8)` 为空 |
| 边界声明 | 「周期快照，非连续实时」+「相对码不是 dBm」 | ✅ PNG 页眉 / HTML banner / 页脚 / CLI 四处 |

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
4. **上位机还没有瀑布图页**：`host/` 在 `feat/host-gui` 分支，本分支从阶段 0 切出无 `host/`。

## [TODO] 3. BLE 风险与环境评估（feat/3-ble-risk）

- 只加 `modes/ble_risk.py`。
- 产出：BLE 广播信道（2402/2426/2480）活动度、拥挤度评分、与 WiFi 的共存提示。
- 判据：`ble_crowded` 场景评分必须高于 `quiet`；`wifi_suppress_ble` 场景必须给出「被压制」判读。

## [TODO] 4. 5.8G 频段守卫（feat/4-guard-58g）

- 只加 `modes/guard_58g.py`。
- 产出：5150–5850MHz 巡检、持续占用报警、事件入库（`store.add_event`）。
- 判据：`video_58g` 场景必须触发报警；`quiet` 场景必须不报警（误报率受控）。

## [TODO] 5. 可疑信号抓拍（feat/5-capture）

- 只加 `modes/capture.py` **和** `core/classifier.py` 的实现（任务书授权本阶段动 core 的只有这一处，需人类二次确认）。
- 产出：按启发式分类（窄带连续/宽带突发/跳频样）触发 `CAP16`/`CAP20` 抓档，
  落盘原始 I/Q + 元数据 + 分类结论。
- 判据：`unknown_cw` 场景触发抓档且分类为「窄带持续」；`quiet` 场景不抓。

## 阻塞与需求

- **应用侧健壮性缺口**（真机实测暴露，属 `core/scheduler.py`）：一次扫描失败会把链路
  置为 `failed`，调度器随后无退避地空转重试（实测一次卡死刷出 1349405 次同类错误）。
  修它要动 `core/`，按规则需人类裁决：是否允许在功能分支修 `core/scheduler.py`
  的失败重试/退避逻辑（或允许加"连续 N 次失败即停止本轮"的保护）。
- ~~5G 宽窗归属口径~~：已由人类裁决「可以窄、多次移动扫描、允许重叠」→ 本轮落实为
  20MHz 窗 / 15MHz 步进重叠滑扫 + 覆盖率掩码，**已解除**。
- **固定增益档位的产品化口径**：当前靠人工给 `--gain-index 60`。要不要做自动标定
  （逐档试扫、挑不饱和的最高档）需要人类定，属于应用层（不碰 `core/`）。

## 约定

- 每条功能分支开工前：`git checkout -b feat/<n>-<name>`，动手前把本条改成 `[DOING]`。
- 收工：提交信息写清「做了什么 / 怎么验证 / 边界」，并在本文件补结果。