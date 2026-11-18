# AGENTS.md — 本机联调约束（人类设定，agent 必须遵守）

## 串口

| 用途 | 端口 | 说明 |
| --- | --- | --- |
| **ESP32-C5（本项目的板子）** | **COM56** | USB Serial/JTAG，VID:PID `303A:1001`，SER `38:44:BE:A5:0C:80` |

**硬规则**

1. 烧录、复位、串口读写，**只允许用 COM56**。
2. **不许碰其它串口**（如 COM59、COM10、COM11 等）：不打开、不发命令、
   不复位、不猜测用途。确实需要时先问人，得到明确答复再用。
3. 端口被占用（`Access is denied` / `PermissionError`）时，先报告谁占用，
   不要自己去找别的端口"凑合用"。
4. 打开 COM56 会复位板子：正在跑 `idf.py monitor` 时先退出再让脚本抢口。

## 固件

- 工程根 = 本仓库根（ESP-IDF 项目，`main/` 是固件，`examples/` 是电脑侧应用）。
- IDF 版本：**v6.1**（eim 安装，`export.sh` 后用 `idf.py`）。
- 目标芯片：`esp32c5`。

### ⛔ 编译：本机 Windows 一律不编译固件（2026-11-18 用户拍板）

同一工程 Windows 本机编译比 Linux 构建机明显慢，**固件编译一律在远程 Ubuntu 构建机**（ssh 别名 `idf-vm`，ESP-IDF v6.1.1）。
`testing/idf-c5.bat` 仅作历史参考，**禁止再用于 build**。本机只保留「烧录 + 实测」。

```sh
# ① 构建机编译（首次/全量走 stage 到本地盘：92 s；增量 10 s）
#    共享盘里做首次全量会挂死（实测 29 min 无产物），所以全量必须 stage
bash /c/.ai_global/meta/scripts/espidf-vm/vm-stage-build.sh E:/_00AE_PROJECR/esp32c5_sdr_app/repo

# ② 取回要烧的产物（整组同一次构建，别和主机旧 bin 混用）
cd /e/_00AE_PROJECR/esp32c5_sdr_app/repo && mkdir -p build/bootloader build/partition_table
for f in esp_sdr.bin bootloader/bootloader.bin partition_table/partition-table.bin flash_args; do
  scp -q idf-vm:/home/atul/work/repo/build/$f build/$f
done

# ③ 烧 COM56（不挂 monitor）
cd build && python -m esptool --chip esp32c5 -p COM56 -b 460800   --before default-reset --after watchdog-reset write-flash "@flash_args"

# ④ 烧完必须实测
cd ../examples/esp32c5-rf-monitor && python -m app.live --port COM56 --duration 5 --gain-index 60
```

实测记录（2026-11-18）：`vm-stage-build.sh` 全量 **92 s**、增量 **10 s** →
`esp_sdr.bin` 823,680 B / `bootloader.bin` 21,312 B / `partition-table.bin` 3,072 B；
esptool 烧录 `Wrote 823680 bytes ... Hash of data verified`；
复位后 `app.live` → `C5SDR 6 burst 16380`、30 片 / 1073 帧 / 5.1 s / 错误 0。

**本机踩过的坑（别重复踩）**

1. （历史）`export.sh` / `export.ps1` 全部失败：默认 venv 指向不存在的 `idf6.1_py3.15_env`；
   且 `idf_tools.py` 里 `if 'MSYSTEM' in os.environ: fatal(...)` 会拦住 Git Bash/MSYS。
   注意：`env -u MSYSTEM` 在 MSYS 下**不生效**，必须在 cmd 里 `set "MSYSTEM="`（脚本已做）。
2. 从 Git Bash 调 cmd 必须写 `cmd.exe //c`：写成 `/c` 会被 MSYS 路径转换吃掉，
   cmd 直接开成交互窗口。
3. 首次配置前必须 `git submodule update --init --recursive`，否则 `components/esp-dsp`
   是空目录 → `Failed to resolve component 'esp-dsp'`。
4. `idf.py flash` 会先重跑 `ninja all`，偶发 ldgen 崩溃
   （`ParseException: Expected 'In archive' ... lib*.a`）。这是 v6.1 增量 transient：
   `build` 重跑一次通常就过；若仍不行，直接绕开 ninja 烧已有产物：

   ```sh
   cd build && /d/Espressif/python_env/idf6.1_py3.12_env/Scripts/python.exe -m esptool \
     --chip esp32c5 -p COM56 -b 460800 --before default-reset --after hard-reset \
     write-flash "@flash_args"
   ```

5. 烧完可能停在 ROM 下载模式（串口只回 `waiting for download`，`SYNC` 无回显）。
   用 `--after watchdog-reset` 复位回应用态：

   ```sh
   cd build && /d/Espressif/python_env/idf6.1_py3.12_env/Scripts/python.exe -m esptool \
     --chip esp32c5 -p COM56 -b 460800 --before default-reset --after watchdog-reset read-mac
   ```

## 应用侧

- 业务代码在 `examples/esp32c5-rf-monitor/`，只跑在电脑上。
- 自测：`cd examples/esp32c5-rf-monitor && python -m unittest discover -s tests -t .`
- 真机（WiFi 信道体检，横向比信道必须固定增益）：
  `python -m app.live --port COM56 --duration 60 --gain-index 60`
- 真机（随便跑跑 / 只看链路）：`python -m app.live --port COM56 --duration 10`
- 假固件（不碰硬件）：`python -m app.live --mock --duration 5`

## 真机实测记录（2026-11-18，COM56 实板）

- 固件自报：`C5SDR 6 burst 16380`，24 个剖面（4/8/10/20/40/80 MS/s × 256/512/1024/2048 bin），
  传输 `USB`，caps 含 `SPEC SPECN SPECCAPS SPECSTAT DCT UARTBAUD RXLIMITS GAIN HWAGC IQ8`。
- `python -m app.live --port COM56 --duration 10` → 3262 帧 / 10.0s / 0 错误，
  wifi_check 表格正常产出（2.4G ch5–ch10 重度、ch1–ch3 空闲）。
- **80 MS/s 下 5G 实际可用带宽只有约 ±20MHz**：5.53GHz 窗口内非零 bin 仅覆盖
  中心约 38MHz，窗口边缘 bin 恒为 0 码。因此 80MHz 宽窗扫描时，落在 ±20MHz 之外的
  信道会读到 0% 占用 → 假「空闲」。宽窗归属信道前必须先做覆盖掩码判断。
- 刚调谐的头几帧增益未稳定，可能出现整段 0 码（噪声底读到 0），占用判决会失真。
- 一次扫描失败会把链路置为 `failed`，调度器随后无退避地空转（实测出现过 1349405 次
  同类错误）——这是应用侧健壮性缺口，已记入待修清单。

## 增益与判决口径（2026-11-18 补，实测得出）

- **横向比较信道必须固定增益**：`GAIN MANUAL <index>`（`GAIN?` 回 `GAIN MANUAL 60 0 81 1`，
  index 是 PHY 表索引，不是 dB）。硬件 AGC（`GAIN HARDWARE`）会把每个信道都拉到相近
  电平（实测各信道 `p50` 全落 62~77），信道间差异被吃掉。
- **实测定稿 index=60**：安静 5G 段 `p50≈28 / p95≈62`、忙 2.4G 段 `p50≈68`，全程无饱和；
  index=50/70/80 与 60 不成单调关系（PHY 表分段校准），所以别"按 dB 直觉"调，要实测。
- **判决不能用"高于噪声底的 bin 比例"**：实板同一帧内噪声底自身抖动 ±10~18 dB
  （功率码 `20*log10(Q15)` 只有 6 dB 一格），任何绝对门限都会把纯噪声判成忙
  （实测 2.4G/5G 全部 100% 忙）。现在判「信道中位电平比本频段最安静信道高多少 dB」：
  `8/15/25 dB ⇒ 轻度/拥挤/重度`，安静环境全归零，有 AP 的信道 +20~+40 dB。
- 20 MS/s 窗（±10MHz）整窗都是有效通带；80 MS/s 窗边缘约 60% 是 0 码，不能用来判占用。

## 5.8G 守卫口径（2026-11-18 补，实测得出）

- **频段上限 5895 MHz**：C5 调谐上限就是 5895，任务书写的 5900 不可达；守卫实现 5700–5895。
- **窗中心 ±250 kHz 与本机直流泄漏重合，必须挖掉**：5740 / 5768 / 5450 MHz 三个频点、
  每个窗正中心都有一根 **3 格宽、高出底噪 33~43 码**的尖峰，形状完全一致 → 是接收机
  直流/LO 泄漏，不是环境信号。不挖会得到「每个窗中心都有一条持续窄带」的假报警
  （实测 13 窗 → 13 条）。
- **窗两侧各 0.5 MHz 也要挖**：20 MHz 窗靠边的滚降本身高出均值 10~27 码。
- **0.1 MHz 级持续凸起 = 本振/时钟杂散**（实测 5708 / 5776 MHz，3 格宽、两侧一格就掉回
  底噪、占空比 88~97%）→ 判「单频点」，与图传（「窄带」，≥1 MHz）分开报，不许混类。
- **没有真 5.8G 发射器时，正样本靠回放注入自证**：`testing/guard_replay.py` 把合成信号
  加到真板录的 snapshot 上，能复现 4 MHz/96% 占空比/持续 9.4 s 的告警；
  灵敏度下限 = `ALERT_DB` 10 码（+8 码按设计漏报），弱信号下带宽会低估。

## 边界

- `core/` 是五个功能共享的地基，功能分支只读；需要改先在此文件登记理由并经人确认。
- 输出里不许写 dBm：功率码是相对 dB 码，没有绝对校准。
- 分时快照，不是连续实时流；不要用"实时"字样描述产物。