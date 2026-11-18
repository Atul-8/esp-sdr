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
| 2 | `feat/2-waterfall` | 频谱瀑布图 | `[TODO]` |
| 3 | `feat/3-ble-risk` | BLE 风险与环境评估 | `[REVIEW]` 待人类验收 |
| 4 | `feat/4-guard-58g` | 5.8G 频段守卫 | `[TODO]` |
| 5 | `feat/5-capture` | 可疑信号抓拍（含分类器） | `[TODO]` |

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

## [TODO] 2. 频谱瀑布图（feat/2-waterfall）

- 只加 `modes/waterfall.py`（+ 自己的渲染实现）。
- 产出：时间-频率图（ASCII 起步，PNG 需先确认依赖）、时间窗查询（复用 `Store.iter_between`）。
- 判据：把 `video_58g` 场景渲出来，5740MHz 处必须是一条连续亮带。

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

**未验证 / 待人类确认**：

- 没在**安静房间**复核实板（房间是活的，清不了场）；安静侧只有 mock `quiet` 佐证。
- 本分支 `app/live.py` 还没有 `--gain-index`（属功能 1 分支），实板验证用了不提交的临时脚本。

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

- （无）

## 约定

- 每条功能分支开工前：`git checkout -b feat/<n>-<name>`，动手前把本条改成 `[DOING]`。
- 收工：提交信息写清「做了什么 / 怎么验证 / 边界」，并在本文件补结果。