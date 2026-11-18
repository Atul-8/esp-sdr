"""下行本地化：把 Python 侧（模块判词 / 表格 / 状态）的中文在上行到界面前翻成英文。

为什么不在前端字典里翻（``host/web/js/i18n.js`` 已经有完整 UI 词典）：

* 模块（``modes/``）产出的判词、表格列名、建议文本**运行时才拼出来**，前端只能收到
  成品字符串；前端拿不到「这是哪条模板」的语义，硬翻就得在前端再写一遍正则。
* 模块的 ASCII/终端输出必须保持中文（``app.live`` / ``docs/`` 里的实测记录），所以在
  ``modes/`` 里改成 key-based i18n 会把命令行输出也带偏；本地化只发生在「发给界面」
  这一层，不影响任何模块自身产出。

规则：

* 只翻 ``str`` **值**，不碰 key、不碰数字/布尔/None（表格数据原样）。
* 数字先抽出来（``{}`` 占位），翻完再按顺序填回 —— 判词模板里全是实测数字，
  逐条枚举会爆炸。
* 查不到就原样返回（**绝不抛**，界面不能因为一条文案挂掉）；覆盖率由
  ``tests/test_l10n.py`` 盯着：英文模式下整份下行 payload 必须零 CJK。
"""
from __future__ import annotations

import re
from typing import Any

#: CJK 统一表意文字（够用：项目里不会出现假名/韩文）
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

#: 非 ASCII 段（异常文案兜底时整段剔掉，剩下的数字/设备回显才是有效信息）
def _ascii_only(text: str) -> str:
    """剔掉非 ASCII 字符（异常文案兜底用：剩下的数字/设备回显才算信息）。"""
    return "".join(ch if ch.isascii() else " " for ch in text)

#: 数字段（**带符号**）：``-2.0`` 整段算一个 ``{}``，回填时符号跟着回来。
#: 少了这个 ``-?``，像「开关幅度 -2.0 码」这种负数的骨架就与模板对不上 → 英文界面漏中文。
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def has_cjk(text: str) -> bool:
    """是不是含中文（表格里 ``ch1`` / ``2.6/帧`` 只看后半段）。"""
    return bool(CJK_RE.search(text))


def skeleton(text: str) -> str:
    """把数字段换成 ``{}``，得到查表用的骨架。"""
    return NUM_RE.sub("{}", text)


#: 骨架 → 英文模板（``{}`` 数量必须与中文骨架一致）
TABLE: dict[str, str] = {
    # ---- modes/ble_risk.py：判据 / 判词 / 边界 ---------------------------------
    "判据③：「抬升」先减掉本机噪声自己的抬升零点 rise_floor={} 码（纯噪声下 p{}−p{} 本来就有 {}~{} dB，"
    "不减就会把「完全安静」判成「轻微干扰」）。判据④：宽带占用高还要看**时间上开不开**——开关幅度 {} 码 "
    "≥ {} 才算信号，否则只是前端固有形状（判「宽带常开/形状疑似」）。判据⑤：没到门限但明确高于零点的帧"
    "算「浅抬升」，占 ≥ {}% 就自成一档（当前 {}%），不与「完全安静」混同。":
        "Criterion ③: first subtract this front end's own rise floor rise_floor={} codes (pure noise already "
        "spreads p{}−p{} by {}~{} dB, so without it a completely quiet band would read as slight interference). "
        "Criterion ④: a high wideband occupancy must also switch on and off in time — a switching swing of {} "
        "codes ≥ {} counts as a signal, otherwise it is just the front end's fixed shape (reported as "
        "\"wideband always on / shape-like\"). Criterion ⑤: frames above the zero point but below the gate are "
        "\"shallow rise\"; at ≥ {}% of frames they get their own bucket (now {}%) instead of being mixed with "
        "\"completely quiet\".",

    "宽带占用率 {}%（≥{}%，按时间均值频谱算）→ BLE 广播信道整体被埋在宽带能量下，窄带凸起分辨不出来。"
    "宽带源不一定是 WiFi：微波炉、无线摄像头、{}G 图传的镜像、邻近 LTE 也会铺满。建议：先逐个关掉可疑宽带设备复测"
    "（这台的判词只说明「宽带占主导」，不指名设备）；确认是路由器就把信道挪开或换到 {}G；"
    "仅靠 BLE 侧调参没用（抬不起头的是 BLE 广播本身）。":
        "Wideband occupancy {}% (≥{}%, measured on the time-averaged spectrum) → the BLE advertising channels "
        "are buried under wideband energy and narrowband bumps cannot be resolved. The wideband source is not "
        "necessarily WiFi: microwave ovens, wireless cameras, the image of a {}G video link or nearby LTE fill "
        "the band as well. Suggestion: switch off suspicious wideband devices one at a time and re-measure (this "
        "verdict only says \"wideband dominates\"; it does not name a device); if it turns out to be a router, "
        "move the channel away or go to {}G; re-tuning the BLE side alone will not help (what cannot rise above "
        "the floor is the BLE advertising itself).",

    "前端底噪参考码 {}（≥{}）⇒ 增益偏高，宽带占用率 {}% 会随增益变化（实板同环境实测：增益 {}/{}/{} → "
    "宽带占用 {}%/{}%/{}%，判词从「复杂噪音」一路滑到「WiFi 压制型」）。拿宽带占用下结论前，先降两档增益复测："
    "占用率不随增益走才是环境，随增益走就是前端动态范围。":
        "Front-end noise-floor reference {} (≥{}) ⇒ the gain is high and the {}% wideband occupancy moves with "
        "gain (measured on a real board in one room: gain {}/{}/{} → occupancy {}%/{}%/{}%, and the verdict "
        "slides from \"complex noise\" all the way to \"WiFi dominated\"). Before drawing any conclusion from "
        "wideband occupancy, drop two gain steps and re-measure: occupancy that does not follow gain is the "
        "environment; occupancy that follows gain is front-end dynamic range.",

    "判据①：本通道 p{} 电平 > 本帧 p{} + {} dB ⇒ 这次「有信号」；判据②：比肩部（±{}~{}MHz）高 ≥ {} dB 且占"
    "这次抬升 ≥ {}% ⇒ 这次是「窄带凸起」（像 BLE）（宽带源把能量摊到肩部，通道只比肩部高一点，占比低）。"
    "两条都成立才算广播活动命中。":
        "Criterion ①: this channel's p{} level > this frame's p{} + {} dB ⇒ signal present this time; "
        "criterion ②: at least {} dB above the shoulders ({}~{}MHz) and ≥ {}% of this rise ⇒ this is a "
        "\"narrowband bump\" (like BLE) (a wideband source spreads energy into the shoulders, so the channel is "
        "only slightly above them and the share is low). Both must hold for an advertising-activity hit.",

    "共存冲突（{} MHz WiFi 完全覆盖 BLE 广播信道）：ch{}↔BLE {}、ch{}↔BLE {}、ch{}↔BLE {}、ch{}↔BLE {}、"
    "ch{}↔BLE {}。BLE 广播会丢包重传、WiFi 也会被 BLE 脉冲打断，两边都掉性能。":
        "Co-channel conflict (a {} MHz WiFi channel fully covers the BLE advertising channels): ch{}↔BLE {}, "
        "ch{}↔BLE {}, ch{}↔BLE {}, ch{}↔BLE {}, ch{}↔BLE {}. BLE advertising retransmits and WiFi gets "
        "interrupted by BLE pulses — both sides lose performance.",

    "共存冲突（{} MHz WiFi 完全覆盖 BLE 广播信道）：ch{}↔BLE {}、ch{}↔BLE {}。BLE 广播会丢包重传、"
    "WiFi 也会被 BLE 脉冲打断，两边都掉性能。":
        "Co-channel conflict (a {} MHz WiFi channel fully covers the BLE advertising channels): ch{}↔BLE {}, "
        "ch{}↔BLE {}. BLE advertising retransmits and WiFi gets interrupted by BLE pulses — both sides lose "
        "performance.",

    "窗内抬起格占比 ≥{}% 视为环境饱和（满负荷 WiFi/微波炉），不做逐段判定（本轮 {} 窗）；帧按 ≥{} s 抽帧，"
    "缓冲覆盖不足 {} s 时不判（本轮 {} 次）——真板一次扫描能吐 {}+ 帧，不抽帧的话「{} 帧」其实只覆盖 {} s。":
        "A ≥{}% share of raised cells in the window counts as environmental saturation (saturated "
        "WiFi/microwave), so no segment-by-segment verdict is made ({} windows this round); frames are sampled "
        "at ≥{} s and nothing is judged when the buffer covers less than {} s ({} times this round) — a real "
        "board emits {}+ frames per scan, so without sampling \"{} frames\" would really cover only {} s.",

    "广播信道活动占空 {}%、宽带占用仅 {}% → 是蓝牙设备的广播密度问题。建议：减少同时广播的 BLE 设备，"
    "或把广播间隔（adv interval）加长；注意数据信道区栅格占用 {}/帧，说明还有连接态流量在动。":
        "Advertising-channel activity duty {}% with only {}% wideband occupancy → this is a Bluetooth "
        "advertising-density problem. Suggestion: reduce how many BLE devices advertise at the same time, or "
        "lengthen the advertising interval; note the data-channel region at {}/frame of grid occupancy, which "
        "means connection traffic is moving too.",

    "窄带持续源（≤{} kHz 且占空 ≥{}%）当**本机杂散**处理，报清单但不抓拍（本轮 {} 根：真板本振/时钟是真的会"
    "一直响）——想找窄带窃听器得靠人工靠近对比，不在自动判定范围。":
        "Narrowband persistent sources (≤{} kHz with duty ≥{}%) are treated as **local spurs**: listed but not "
        "captured ({} this round — a real board's LO/clock really does ring forever). Hunting a narrowband "
        "listening device needs a human walking up to it for comparison, which is outside automatic judgment.",

    "多类证据同时成立（浅抬升 {}%、宽带占用 {}%、数据区 {} 栅格/帧）→ 这是**多源混合**的复杂噪音，"
    "不指名任何一类设备；要定位只能逐个关设备复测，或把分析带宽收窄逐段看。":
        "Several kinds of evidence hold at once (shallow rise {}%, wideband occupancy {}%, data region {} "
        "grids/frame) → this is **multi-source mixed** complex noise and no device class is named; localising "
        "it means switching devices off one by one, or narrowing the analysis band and looking segment by "
        "segment.",

    "边界：**只评概率，不做设备级归因**（看出「这里在动、动得像不像 BLE」，看不出「几台什么设备」）；"
    "功率码是相对 dB 码不是 dBm；单射频分时扫描，帧间有接收空档。":
        "Limits: **probability only, no device-level attribution** (you can see \"something is moving here and "
        "whether it looks like BLE\", not \"how many devices of what kind\"); power codes are relative dB codes, "
        "not dBm; the one radio scans in time slices, so there are reception gaps between frames.",

    "扫描：{} 个广播信道各对准一窗 + 数据区 {} 窗重叠滑扫（{}MHz 窗 / {}MHz 步进 / {}ms 驻留）；"
    "样本 {} 帧、丢弃整窗 {} 码 {} 帧。":
        "Scan: {} advertising channels each get one aligned window plus a {} window overlapping sweep of the "
        "data region ({}MHz window / {}MHz step / {}ms dwell); {} frames sampled, {} frames whose whole "
        "window was {} codes dropped.",

    "判据：信道中位电平比同频段最安静信道高 {}/{}/{} dB ⇒ 轻度/拥挤/重度（实板噪声底自身抖动 ±{}~{} dB，"
    "绝对门限会全判忙，所以用同频段相对抬升）。":
        "Criterion: a channel's median level is {}/{}/{} dB above the quietest channel in the same band ⇒ "
        "mild/crowded/heavy (a real board's noise floor jitters by ±{}~{} dB, so absolute thresholds would call "
        "everything busy — hence the relative rise within the band).",

    "边界：分时快照，非连续实时；功率码是相对 dB 码，不是 dBm；跨增益不可比，真机请固定增益扫描"
    "（app.live --gain-index N）后看相对抬升。":
        "Limits: time-sliced snapshots, not continuous real time; power codes are relative dB codes, not dBm; "
        "values are not comparable across gains — on real hardware scan at a fixed gain "
        "(app.live --gain-index N) and then read the relative rise.",

    "底噪参考取逐帧 p{}（拿 p{} 会把「占窗 {}% 的宽带」当成整体底噪抬升而看不见）；若整窗一起抬升（连 p{} 都抬）"
    "= 增益/底噪漂移，不判成信号。":
        "The noise-floor reference is the per-frame p{} (using p{} would take a wideband signal covering {}% of "
        "the window as an overall floor rise and make it invisible); if the whole window rises together (even "
        "p{}) it is gain/floor drift, not a signal.",

    "触发条件：启发式分类落 unknown（触发时候选形如 窄带持续/持续凸起/分不清的宽带/间歇出现），"
    "且抬升 ≥{} 码、占空 ≥{}%、连续 ≥{} 帧。":
        "Trigger: the heuristic classification lands on unknown (at trigger time the candidate looks like "
        "narrowband-persistent / persistent bump / indistinguishable wideband / intermittent), with a rise "
        "≥{} codes, duty ≥{}% and ≥{} consecutive frames.",

    "宽带占用 {}%（≥{}%）⇒ 「在用 WiFi 信道」列表与「共存冲突」已经没有分辨力（整段都被铺满，每个邻道都会亮），"
    "这种情况只看宽带占用率本身。":
        "Wideband occupancy {}% (≥{}%) ⇒ the \"WiFi channels in use\" list and the \"co-channel conflicts\" "
        "have no resolving power any more (the whole band is filled, so every neighbour lights up); in that "
        "case only the occupancy figure itself is meaningful.",

    "判据：抬升 ≥{} 码、占空比 ≥{}%、连续 ≥{} 帧、段宽 {} kHz–{} MHz；占空比 ≥{}% 记「持续」，"
    "否则记「规律跳频」。":
        "Criterion: rise ≥{} codes, duty ≥{}%, ≥{} consecutive frames, segment width {} kHz–{} MHz; duty "
        "≥{}% is recorded as \"persistent\", otherwise \"regular hopping\".",

    "每次抓档的 sidecar JSON 带前后各 {} s 的本模块均值谱（下采样到 {} 格）；"
    "store 没注入时如实写「没有可用上下文」。":
        "Each capture's sidecar JSON carries this module's mean spectrum for {} s before and after "
        "(downsampled to {} grid cells); when no store is injected it honestly writes \"no usable context\".",

    "不判两段：窗中心 ±{} kHz（接收机直流/LO 泄漏，实板每个窗正中都有一根 {} 格尖峰）与窗两侧各 {} MHz（滚降）。":
        "Two regions are never judged: the window centre ±{} kHz (receiver DC/LO leakage — on a real board "
        "every window has a {} cell spike right in the middle) and {} MHz on each side of the window "
        "(roll-off).",

    "不判两段：窗中心 ±{} kHz（接收机直流/LO 泄漏）与窗两侧各 {} MHz（滚降）。":
        "Two regions are never judged: the window centre ±{} kHz (receiver DC/LO leakage) and {} MHz on each "
        "side of the window (roll-off).",

    "重叠窗会把同一个源在多窗里各看到一次 → 按频点 {} MHz 内聚类合并（代价：相距 {} MHz 内的两个独立源"
    "会并成一条）。":
        "Overlapping windows see the same source once per window → merged by clustering within {} MHz "
        "(the cost: two independent sources closer than {} MHz become one line).",

    "扫描：{}MHz 窗 / {}MHz 步进重叠滑扫，整窗 {} 码的帧丢弃（{} 帧）；信道覆盖 <{}% 记「无覆盖」。":
        "Scan: {}MHz window / {}MHz step overlapping sweep; frames whose whole window is {} codes are dropped "
        "({} frames); channel coverage <{}% is recorded as \"no coverage\".",

    # 单独出现的风险类型名（抓档表单元格、卡片大标题、前端 U.riskClass 的键）
    "宽带常开/形状疑似": "wideband always-on / shape-like",
    "轻微干扰": "slight interference",

    "风险：**宽带常开/形状疑似**（评分 {}/{}，宽带占用 {}%，广播活动 {}%，数据区 {} 栅格/帧）。":
        "Risk: **wideband always-on / shape-like** (score {}/{}, wideband occupancy {}%, advertising activity "
        "{}%, data region {} grids/frame).",
    "风险：**轻微干扰**（评分 {}/{}，宽带占用 {}%，广播活动 {}%，数据区 {} 栅格/帧）。":
        "Risk: **slight interference** (score {}/{}, wideband occupancy {}%, advertising activity {}%, data "
        "region {} grids/frame).",

    "风险：**复杂噪音（多源混合）**（评分 {}/{}，宽带占用 {}%，广播活动 {}%，数据区 {} 栅格/帧）。":
        "Risk: **complex noise (multi-source mixed)** (score {}/{}, wideband occupancy {}%, advertising "
        "activity {}%, data region {} grids/frame).",
    "风险：**WiFi 压制型**（评分 {}/{}，宽带占用 {}%，广播活动 {}%，数据区 {} 栅格/帧）。":
        "Risk: **WiFi dominated** (score {}/{}, wideband occupancy {}%, advertising activity {}%, data region "
        "{} grids/frame).",
    "风险：**蓝牙设备密度型**（评分 {}/{}，宽带占用 {}%，广播活动 {}%，数据区 {} 栅格/帧）。":
        "Risk: **Bluetooth density** (score {}/{}, wideband occupancy {}%, advertising activity {}%, data "
        "region {} grids/frame).",
    "风险：**无明显风险**（评分 {}/{}，宽带占用 {}%，广播活动 {}%，数据区 {} 栅格/帧）。":
        "Risk: **no obvious risk** (score {}/{}, wideband occupancy {}%, advertising activity {}%, data region "
        "{} grids/frame).",
    "风险：**混合型**（评分 {}/{}，宽带占用 {}%，广播活动 {}%，数据区 {} 栅格/帧）。":
        "Risk: **mixed** (score {}/{}, wideband occupancy {}%, advertising activity {}%, data region {} "
        "grids/frame).",

    # RISK_FLAT 专有 hints：宽带占用高、但时间上几乎不开关 → 只下「形状疑似」，不下 WiFi 结论
    "宽带占用 {}%（≥{}%），但这些频点在时间上几乎不开关（开关幅度 {} 码，判据 ≥{}）→ 分不清是"
    "「常开宽带源」还是「前端滤波器的固定起伏（形状）」，所以不下 WiFi 结论。想定性：换一档增益再测一次"
    "（形状随增益走、占用不随），或做一次对照空窗（把可疑设备全关掉）比两次的宽带占用率。":
        "Wideband occupancy is {}% (≥{}%), but these frequencies barely switch on and off in time (switching "
        "swing {} codes, gate ≥{}) — so this cannot be told apart from an always-on wideband source or the "
        "front end's own fixed ripple (shape). No WiFi verdict is drawn. To settle it: re-measure at a "
        "different gain (shape follows gain, occupancy does not), or compare two runs with the suspect device "
        "switched off (control window).",

    "比 {} kHz 还窄的段记「单频点」（实板环境里就是本振/时钟杂散，不是摄像头；照实报但类别分开，"
    "别误读成图传）。":
        "Segments narrower than {} kHz are recorded as \"single frequency\" (in a real environment these are "
        "LO/clock spurs, not cameras; reported as measured but kept in a separate category so they are not "
        "misread as a video link).",

    "分类是启发式的（只看带宽/持续/占空比），不做调制识别、不做设备级归因；"
    "「分不清的宽带」是明确不给结论，不是漏判。":
        "The classification is heuristic (bandwidth/persistence/duty only): no modulation recognition and no "
        "device-level attribution; \"indistinguishable wideband\" is an explicit refusal to conclude, not a "
        "missed detection.",

    "贴着窗边的段不判（宽度被窗切掉、不可信；≥{} MHz 的宽带例外，因为它本来就顶满整窗），"
    "本轮丢弃过 {} 段。":
        "Segments hugging the window edge are not judged (their width is cut off and untrustworthy; wideband "
        "≥{} MHz is the exception because it fills the whole window anyway) — {} segments were dropped this "
        "round.",

    "周期快照，非连续实时 —— 单射频分时扫描，帧与帧之间存在接收空档；图上的行是「扫到过」的功率码，"
    "不是连续采样。":
        "Periodic snapshots, not continuous real time — the one radio scans in time slices, so there are "
        "reception gaps between frames; the rows on the chart are power codes that were \"scanned at some "
        "point\", not continuous samples.",

    "宽带占用 {}% 且广播信道活动 {}% → 两类风险同时存在：先把 WiFi 挪开（宽带源），再评估蓝牙密度。":
        "Wideband occupancy {}% together with advertising-channel activity {}% → two risks at once: move the "
        "WiFi away first (the wideband source), then assess Bluetooth density.",

    "窄段（<{} MHz）要求抬升 ≥{} 码：噪声纹波能造出 +{} 码的假凸起，本模块另有 {} 段因此被挡。":
        "Narrow segments (<{} MHz) must rise by ≥{} codes: noise ripple can fake a +{} code bump, and {} "
        "segments were blocked by this rule.",

    "频段上限 {} MHz 就是芯片调谐上限，{}–{} MHz 这一段本机测不到（不是没扫，是硬件到不了）。":
        "The upper band limit of {} MHz is the chip's tuning limit; {}–{} MHz cannot be measured here (not "
        "unscanned — the hardware cannot reach it).",

    "同一个频点（±{} MHz）抓过一次后 {} s 内不重复抓；整轮最多 {} 次（防止抓档把时间片吃光）。":
        "One frequency (±{} MHz) is not captured twice within {} s; at most {} captures per round (so capture "
        "cannot eat all the time slices).",

    "功率码是相对 dB 码（{}..{}），不是 dBm；「抬升 {} 码」不等于绝对功率 {} dB。":
        "Power codes are relative dB codes ({}..{}), not dBm; \"a rise of {} codes\" is not an absolute "
        "power of {} dB.",

    "增益会改占用率读数：跨会话比较前先固定档位（见 docs/{}-ble-risk.md §{}）。":
        "Gain changes the occupancy reading: fix the step before comparing across sessions (see "
        "docs/{}-ble-risk.md §{}).",

    "! 注意：{}G 没有真正空闲的信道，ch{} 只是相对最轻的，建议优先上 {}G 或有线。":
        "! Note: {}G has no truly free channel; ch{} is only the least busy — prefer {}G or wired.",

    "带宽仅 {} kHz 的窄带，占空 {}% → 不像已知的 WiFi/BLE/微波炉/图传":
        "Only {} kHz wide, duty {}% → does not match known WiFi/BLE/microwave/video-link signatures",

    "功率码/IQ 都是相对量（{}..{} / 有符号整数），不是 dBm，没做过绝对校准。":
        "Power codes/IQ are both relative ({}..{} / signed integers), not dBm; no absolute calibration was "
        "done.",

    "当前在发 {} 条 / 累计 {} 条；丢弃全 {} 码帧 {} 帧（不参与判决）。":
        "Active now {} / {} in total; {} frames whose bins were all {} codes dropped (not used in the "
        "verdict).",

    "缓冲 {} 帧 / 累计 {} 帧，{} 个窗 × {} ms ≈ {} s 一轮":
        "Buffer {} frames / {} in total, {} windows × {} ms ≈ {} s per round",

    "广播信道没有可分辨的窄带活动，宽带占用也低 → 本次样本里没有 BLE 密度风险。":
        "No resolvable narrowband activity on the advertising channels and low wideband occupancy → no BLE "
        "density risk in this sample.",

    "{}G 没有测量到在用的 WiFi 信道（宽带占比都低于 {}%，或样本不足）。":
        "{}G: no WiFi channel in use was measured (all wideband shares are below {}%, or there are too few "
        "samples).",

    "跨窗跳频不追：只判同一 {} MHz 窗内两个频点交替（相关系数 ≤{}）。":
        "Cross-window hopping is not chased: only two alternating frequencies inside the same {} MHz window "
        "are judged (correlation ≤{}).",

    "周期快照，非连续实时：一轮 {} 窗，本次统计跨度 {} s / {} 帧。":
        "Periodic snapshots, not continuous real time: {} windows per round, this sample spans {} s / {} "
        "frames.",

    "周期快照，非连续实时：一轮 {} 窗，抓档时射频停扫（单射频分时）。":
        "Periodic snapshots, not continuous real time: {} windows per round; the RF stops scanning while "
        "capturing (single radio, time-sliced).",

    "BLE 广播信道风险评估 — 风险：宽带常开/形状疑似（评分 {}/{}）":
        "BLE advertising-channel risk assessment — risk: wideband always-on / shape-like (score {}/{})",
    "BLE 广播信道风险评估 — 风险：轻微干扰（评分 {}/{}）":
        "BLE advertising-channel risk assessment — risk: slight interference (score {}/{})",
    "BLE 广播信道风险评估 — 风险：复杂噪音（多源混合）（评分 {}/{}）":
        "BLE advertising-channel risk assessment — risk: complex noise (multi-source mixed) (score {}/{})",
    "BLE 广播信道风险评估 — 风险：WiFi 压制型（评分 {}/{}）":
        "BLE advertising-channel risk assessment — risk: WiFi dominated (score {}/{})",
    "BLE 广播信道风险评估 — 风险：蓝牙设备密度型（评分 {}/{}）":
        "BLE advertising-channel risk assessment — risk: Bluetooth density (score {}/{})",
    "BLE 广播信道风险评估 — 风险：无明显风险（评分 {}/{}）":
        "BLE advertising-channel risk assessment — risk: no obvious risk (score {}/{})",
    "BLE 广播信道风险评估 — 风险：混合型（评分 {}/{}）":
        "BLE advertising-channel risk assessment — risk: mixed (score {}/{})",

    "功率码是相对 dB 码（{}..{}），不是 dBm；色标只表示相对强弱。":
        "Power codes are relative dB codes ({}..{}), not dBm; the colour scale only shows relative strength.",

    "{}G 建议：ch{}（非重叠信道；比最静信道 +{} dB，拥挤）。":
        "{}G suggestion: ch{} (non-overlapping; +{} dB above the quietest channel, crowded).",
    "{}G 建议：ch{}（非重叠信道；比最静信道 +{} dB，空闲）。":
        "{}G suggestion: ch{} (non-overlapping; +{} dB above the quietest channel, idle).",
    "{}G 建议：ch{}（比最静信道 +{} dB，空闲）。":
        "{}G suggestion: ch{} (+{} dB above the quietest channel, idle).",

    "带宽 {} MHz、占空 {}% → 不在任何已知类型的特征区间":
        "{} MHz wide, duty {}% → outside the feature range of any known type",
    "带宽 {} MHz、占空 {}% → 间歇出现，不像已知类型":
        "{} MHz wide, duty {}% → intermittent, unlike any known type",

    "{}G 建议避开（占用偏高）：ch{}, ch{}, ch{}。":
        "{}G: avoid these (occupancy too high): ch{}, ch{}, ch{}.",
    "{}G 建议避开（占用偏高）：ch{}, ch{}。": "{}G: avoid these (occupancy too high): ch{}, ch{}.",
    "{}G 建议避开（占用偏高）：ch{}。": "{}G: avoid this (occupancy too high): ch{}.",

    "样本：{} 帧 / 覆盖 {}s（分时扫描，帧间有接收空档）。":
        "Sample: {} frames / covers {}s (time-sliced scan, reception gaps between frames).",

    "{}G 频段守卫（{}–{} MHz，告警 {} 条）": "{}G band guard ({}–{} MHz, {} alerts)",

    "可疑信号抓拍（{}s 内未抓到可疑信号）":
        "Suspicious-signal capture (nothing suspicious captured within {}s)",
    "可疑信号抓拍（{} 次）": "Suspicious-signal capture ({} times)",

    "有明显抬升（轻度以上）的信道：ch{}。":
        "Channels with a clear rise (mild or worse): ch{}.",
    "有明显抬升（轻度以上）的信道：ch{}, ch{}, ch{}, ch{}。":
        "Channels with a clear rise (mild or worse): ch{}, ch{}, ch{}, ch{}.",
    "有明显抬升（轻度以上）的信道：ch{}, ch{}, ch{}, ch{}, ch{}。":
        "Channels with a clear rise (mild or worse): ch{}, ch{}, ch{}, ch{}, ch{}.",
    "有明显抬升（轻度以上）的信道：ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}。":
        "Channels with a clear rise (mild or worse): ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}.",
    "有明显抬升（轻度以上）的信道：ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}。":
        "Channels with a clear rise (mild or worse): ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, "
        "ch{}.",
    "有明显抬升（轻度以上）的信道：ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}。":
        "Channels with a clear rise (mild or worse): ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, "
        "ch{}, ch{}.",
    "有明显抬升（轻度以上）的信道：ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}。":
        "Channels with a clear rise (mild or worse): ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, ch{}, "
        "ch{}, ch{}, ch{}.",
    "有明显抬升（轻度以上）的信道：无。": "Channels with a clear rise (mild or worse): none.",

    "抓档参数：CAP{}（{} 位 I/Q）、码 {} = {} MS/s、{} 样本 ≈ {} ms；固件一次突发上限就是 IQ_WORDS"
    "（C{} = {}），不是连续录制。":
        "Capture parameters: CAP{} ({} bit I/Q), code {} = {} MS/s, {} samples ≈ {} ms; one firmware burst "
        "is capped at IQ_WORDS (C{} = {}) — this is not continuous recording.",

    "数据区 {}–{}": "Data region {}–{}",

    # ---- app/audit.py：一键体检 -------------------------------------------------
    "「未发现可疑抓拍」≠「房间里没有摄像头」：只看 {}G/{}G 的功率特征，看不到离线存储型设备、"
    "也看不到非 ISM 频段。":
        "\"No suspicious capture found\" ≠ \"there is no camera in the room\": only {}G/{}G power "
        "signatures are examined — devices with offline storage and non-ISM bands stay invisible.",
    "分时扫描的快照，不是连续实时监测：同一时刻只有一个频段在被扫，抓档期间射频停扫。":
        "Time-sliced snapshots, not continuous live monitoring: only one band is scanned at a time, and the "
        "RF stops while capturing.",
    "分类是启发式的（只看带宽/持续/占空比），不做调制识别、不做设备级归因。":
        "The classification is heuristic (bandwidth/persistence/duty only): no modulation recognition and no "
        "device-level attribution.",
    "功率码是相对 dB 码，不是 dBm；跨增益不可比 —— 体检期间请固定增益（--gain-index）。":
        "Power codes are relative dB codes, not dBm, and are not comparable across gains — keep the gain "
        "fixed during a check (--gain-index).",
    "发现可疑信号并已抓档": "Suspicious signal found and captured",
    "有需要复核的持续信号": "Persistent signals need review",
    "本轮未抓到可疑信号": "No suspicious signal captured this round",
    "该窗无覆盖": "no coverage in this window",
    "覆盖 {}%": "Coverage {}%",
    "抬升 {} 码 / 占空 {}%": "Rise {} codes / duty {}%",
    "相对抬升 {} dB 码 / 占用 {}%": "Relative rise {} dB codes / occupancy {}%",
    "评分 {}/{}（重度） / 宽带占用 {}% / 参考底噪 {} 码":
        "Score {}/{} (heavy) / wideband occupancy {}% / floor reference {} codes",
    "评分 {}/{}（拥挤） / 宽带占用 {}% / 参考底噪 {} 码":
        "Score {}/{} (crowded) / wideband occupancy {}% / floor reference {} codes",
    "评分 {}/{}（轻度） / 宽带占用 {}% / 参考底噪 {} 码":
        "Score {}/{} (mild) / wideband occupancy {}% / floor reference {} codes",
    "评分 {}/{}（空闲） / 宽带占用 {}% / 参考底噪 {} 码":
        "Score {}/{} (idle) / wideband occupancy {}% / floor reference {} codes",
    "{}G 守卫": "{}G guard",
    "BLE 风险评估": "BLE risk",
    "可疑抓拍": "Suspicious captures",
    "WiFi 信道": "WiFi channels",
    "关注": "Watch",
    "可疑": "Suspicious",
    "需复核": "Needs review",
    "低": "Low",
    "未知": "Unknown",
    "{} 个分不清类型的源已存证（{} MHz），请人工复核 IQ 文件；这不是「确认是摄像头」。":
        "{} unclear source captured ({} MHz) — review the IQ file manually; this is not \"confirmed a "
        "camera\".",
    "{} 个分不清类型的源已存证（{} MHz, {} MHz），请人工复核 IQ 文件；这不是「确认是摄像头」。":
        "{} unclear sources captured ({} MHz, {} MHz) — review the IQ files manually; this is not "
        "\"confirmed a camera\".",
    "{} 个分不清类型的源已存证（{} MHz, {} MHz, {} MHz），请人工复核 IQ 文件；"
    "这不是「确认是摄像头」。":
        "{} unclear sources captured ({} MHz, {} MHz, {} MHz) — review the IQ files manually; this is not "
        "\"confirmed a camera\".",
    "只代表这段时间、这几个频段、这种灵敏度下没看到异常；不等于房间里没有摄像头。":
        "This only means nothing abnormal was seen in this time span, these bands and at this sensitivity; "
        "it does not mean there is no camera in the room.",
    "{} 条窄带持续源（见清单），类型判读为 {}G 图传样；分类器没抓到可分型证据。":
        "{} narrowband persistent source(s) (see the list), judged to look like a {}G video link; the "
        "classifier found no classifiable evidence.",

    # ---- 表格列名 / 单元格枚举 ------------------------------------------------
    "{}/帧": "{}/frame",
    "WiFi 信道体检（按相对抬升降序）": "WiFi channel check-up (sorted by relative rise, descending)",
    "WiFi 信道体检": "WiFi channel check-up",
    "BLE 广播信道风险评估": "BLE advertising-channel risk assessment",
    "5.8G 频段守卫": "5.8G band guard",
    "{}G 频段守卫": "{}G band guard",
    "周期性频谱瀑布图（时频热力图）": "Periodic spectrum waterfall (time-frequency heatmap)",
    "周期性频谱瀑布图": "Periodic spectrum waterfall",
    "可疑信号抓拍": "Suspicious-signal capture",
    "复杂噪音（多源混合）": "complex noise (multi-source mixed)",
    "WiFi 压制型": "WiFi dominated",
    "蓝牙设备密度型": "Bluetooth density",
    "无明显风险": "no obvious risk",
    "混合型": "mixed",
    "时间均值频谱": "Time-averaged spectrum",
    "窄带栅格占用": "Narrowband grid occupancy",
    "频点 MHz": "Frequency MHz",
    "带宽 MHz": "Bandwidth MHz",
    "活动占空": "Activity duty",
    "窄带突出": "Narrowband prominence",
    "峰值抬升": "Peak rise",
    "持续 s": "Duration s",
    "首次出现": "First seen",
    "时长 s": "Length s",
    "持续窄带": "Persistent narrow",
    "持续宽带": "Persistent wide",
    "占空条": "Duty bar",
    "占用条": "Occupancy bar",
    "比底噪": "vs floor",
    "比最静": "vs quietest",
    "峰值码": "Peak code",
    "占空比": "Duty cycle",
    "色标码": "Colour code",
    "采样率": "Sample rate",
    "通道": "Channel",
    "信道": "Channel",
    "中心": "Centre",
    "覆盖": "Coverage",
    "判定": "Verdict",
    "空闲": "Idle",
    "编号": "ID",
    "状态": "State",
    "类型": "Type",
    "帧数": "Frames",
    "排名": "Rank",
    "频段": "Band",
    "占空": "Duty",
    "分档": "Bucket",
    "重度": "Heavy",
    "拥挤": "Crowded",
    "轻度": "Mild",
    "在发": "Active",
    "频点": "Frequency",
    "样本": "Samples",
    "位宽": "Bit depth",
    "分类": "Class",
    "置信": "Confidence",
    "时间": "Time",
    "文件": "File",
    "页": "Page",

    # ---- host/gain.py / host/app.py：状态行 ----------------------------------
    "未知增益模式：{}": "Unknown gain mode: {}",
    "未知操作：{}": "Unknown action: {}",
    "（空）": "(empty)",
    "固件硬件 AGC：由固件自己挑档（测「有没有信号」用它）":
        "Firmware hardware AGC: the firmware picks the step itself (use it to check \"is there a signal\")",
    "自动闭环：目标底噪 {} 码，步进 {}": "Auto loop: target noise floor {} codes, step {}",
    "手动固定档位 {}": "Manual fixed step {}",
    "手动固定档位 {}（已退出手动之外的模式）": "Manual fixed step {} (left the other modes)",
    "已经到档位边界": "Already at the gain limit",
    "设增益失败：{}": "Failed to set gain: {}",
    "自动闭环已到位：底噪 {} 码 ≈ 目标 {}": "Auto loop settled: floor {} codes ≈ target {}",
    "已到档位边界（{}），底噪 {} 码 ≠ 目标 {}": "At the gain limit ({}), floor {} codes ≠ target {}",
    "自动闭环：底噪 {} → 目标 {}，档位调到 {}": "Auto loop: floor {} → target {}, step moved to {}",
    "未开始采集（点了「开始」后这里会显示实时档位与底噪）":
        "Not collecting yet (press Start and the live gain step and noise floor appear here)",
}


#: 「列表长度会变」的句子：骨架匹配天生干不了这类活——占位数量随列表长度变，
#: 而模板是固定的。所以单独用正则：**只换句子外壳，列表原样（数字不参与翻译）**。
#: 踩过：真板上「有明显抬升的信道」是 6 个，词典里只备了 1/4/5/9/10/11/12 个，
#: 差这 2 个就整句漏中文——英文界面里一眼看不出是「少了个长度」。
#: 异常文案里常混着固件回显和数字，骨架对不上，改按「已知中文片段」换；
#: 片段之外剩下的 ASCII 尾巴（数字、``SPEC``/``FSM`` 之类）原样保留。
ERROR_PHRASES: list[tuple[str, str]] = [
    ("连接已标记失效，请重新打开", "the link was marked dead, reopen the port"),
    ("串口读取超时（无数据）", "serial read timeout (no data)"),
    ("无法取得串口租约（SYNC 未回显）", "could not take the serial lease (no SYNC echo)"),
    ("FSM STOP 未收到 FSMEND（在途帧没排空）",
     "FSM STOP got no FSMEND (in-flight frames were not drained)"),
    ("固件未通告频谱能力（CAPS 无 SPECCAPS），无法扫描",
     "the firmware announced no spectrum capability (CAPS has no SPECCAPS), cannot scan"),
    ("缺少 pyserial：pip install pyserial", "pyserial is missing: pip install pyserial"),
    ("连接未打开", "the port is not open"),
    ("连接已标记失效", "the link was marked dead"),
    ("帧同步丢失", "frame sync lost"),
    ("频谱帧解析失败", "spectrum frame could not be parsed"),
    ("SPEC 未产出任何帧", "SPEC produced no frame"),
    ("SPECEND 记录异常", "SPECEND record looks wrong"),
    ("SPEC 启动回复异常", "unexpected SPEC start reply"),
    ("SPEC 剖面与请求不符", "SPEC profile does not match the request"),
    ("SPECINFO? 回复异常", "unexpected SPECINFO? reply"),
    ("FSMPLAN 回读不符", "FSMPLAN read-back mismatch"),
    ("FSMPLANEND 缺失", "FSMPLANEND missing"),
    ("FSM START 回复异常", "unexpected FSM START reply"),
    ("频点超出固件范围", "frequency outside the firmware range"),
    ("固件的便携快照 stride 必须为 1，实际", "portable snapshot stride must be 1, got"),
    ("固件剖面点数异常", "firmware profile bin count looks wrong"),
    ("二进制读取超时", "binary read timeout"),
    ("CAP20 载荷不足", "CAP20 payload too short"),
    ("I/Q 载荷 CRC32 不匹配", "I/Q payload CRC32 mismatch"),
    ("扫频计划不能为空", "the sweep plan cannot be empty"),
    ("（需重新连接）", "(reconnect needed)"),
    ("帧长度不符", "frame length mismatch"),
    ("CRC32 不匹配", "CRC32 mismatch"),
    ("帧太短", "frame too short"),
    ("magic 不匹配", "magic mismatch"),
    ("不支持的 FFT 点数", "unsupported FFT size"),
    ("统计记录 CRC 不匹配", "stat record CRC mismatch"),
    ("统计记录格式错误", "stat record layout is wrong"),
    ("不支持的采样位宽", "unsupported sample width"),
    ("dwell_ms 超出固件范围", "dwell_ms outside the firmware range"),
]


LIST_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(?P<band>-?[0-9.]+G) 在用的 WiFi 信道：(?P<list>.+)$"),
     "WiFi channels in use on {band}: {list}"),
    (re.compile(r"^(?P<band>-?[0-9.]+G) 建议避开（占用偏高）：(?P<list>.+)。$"),
     "{band}: avoid these (occupancy too high): {list}."),
    (re.compile(r"^有明显抬升（轻度以上）的信道：(?P<list>.+)。$"),
     "Channels with a clear rise (mild or worse): {list}."),
]


def _translate_list(text: str) -> str | None:
    """列表句：只换外壳，列表内容（``ch1, ch6`` 之类）原样带回。

    用**命名组**而不是反向引用：反向引用要写反斜杠，跨工具传递时极易被吃掉
    （本项目踩过两次），命名组一个转义都不用。
    """
    for pattern, template in LIST_PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        fields = {name: ("none" if value == "无" else value) for name, value in match.groupdict().items()}
        return template.format(**fields)
    return None



def translate_error(text: str) -> str:
    """英文模式下的异常文案：**保证不残留中文**（数字与设备回显尽量保住）。

    异常信息走的是另一条路：正文里常混着固件回显（``ERR fsm_args``）和数字，
    骨架匹配基本对不上，所以先用 :data:`ERROR_PHRASES` 按片段换，再把剩下来的
    非 ASCII 字符剔掉。剔掉的部分不是丢了——终端日志里还是原文。
    """
    if not text or not has_cjk(text):
        return text
    full = translate(text)
    if not has_cjk(full):
        return full
    out = text
    for zh, en in ERROR_PHRASES:
        if zh in out:
            out = out.replace(zh, " " + en + " ")
    tail = re.sub(r"\s{2,}", " ", _ascii_only(out)).strip(" :;,、")
    return f"error: {tail}" if tail else "error (details in the terminal log)"


def translate(text: str) -> str:
    """中文 → 英文；翻不了就原样返回（调用方用 :func:`has_cjk` 兜底检查）。"""
    if not text or not has_cjk(text):
        return text
    direct = TABLE.get(text)
    if direct is not None:
        return direct
    key = skeleton(text)
    template = TABLE.get(key)
    if template is None:
        listed = _translate_list(text)
        return text if listed is None else listed
    numbers = NUM_RE.findall(text)
    if template.count("{}") != len(numbers):
        return text  # 模板与骨架占位不一致：宁可原样返回，也不给错位数字
    for value in numbers:
        template = template.replace("{}", value, 1)
    return template


def localize(node: Any) -> Any:
    """递归翻一份下行 payload：只翻 ``str`` **值**，key 与其它类型原样返回。"""
    if isinstance(node, str):
        return translate(node)
    if isinstance(node, dict):
        return {key: localize(value) for key, value in node.items()}
    if isinstance(node, (list, tuple)):
        return [localize(value) for value in node]
    return node