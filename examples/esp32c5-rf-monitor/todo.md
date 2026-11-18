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
| 1 | `feat/1-wifi-check` | WiFi 信道体检 | `[TODO]` |
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

## [TODO] 1. WiFi 信道体检（feat/1-wifi-check）

- 只加 `modes/wifi_check.py`（+ 可选 `output/wifi_*.py`，仅自己用）。
- 产出：2.4G/5G 各信道占用条形图、推荐信道、干扰源计数、时间序列。
- 判据：同一批 mock 快照下，推荐信道与 `wifi_busy` 场景注入的忙信道一致。

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

- `core/scheduler.py` 失败重试/退避：一次扫描失败把链路置 `failed` 后无退避空转
  （实测 1349405 次同类错误）。修它要动 `core/`，等人类裁决（任务 2 未依赖此修复）。
- 固定增益是否做自动标定（当前靠人工 `--gain-index 60`）：属应用层，等人类裁决。

## 约定

- 每条功能分支开工前：`git checkout -b feat/<n>-<name>`，动手前把本条改成 `[DOING]`。
- 收工：提交信息写清「做了什么 / 怎么验证 / 边界」，并在本文件补结果。