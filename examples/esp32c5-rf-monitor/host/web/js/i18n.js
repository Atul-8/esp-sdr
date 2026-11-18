/*
 * i18n.js —— 中英文文案表 + 切换（不联网、不引第三方库）。
 *
 * 用法：
 *   · 静态文本：HTML 写 <span data-i18n="hdr.start">开始</span>，切换时自动改；
 *   · 动态文本：JS 里 T("wifi.card.rec24") 取文案，T("key", {n: 3}) 带占位符；
 *   · 页面注册表：registry.json 里给 title_en/hint_en，路由按当前语言取；
 *   · 语言变了会触发 window 上的 "langchange" 事件，页面自己重画动态部分。
 *
 * 约定：模块（modes/）自己产出的表格/ASCII 沿用模块语言（和 CLI 一致），
 * 不在这里翻——界面只翻「外壳 + 结构化数据」。
 */
window.I18N = (function () {
  const STORAGE_KEY = "espsdr.lang";
  const SUPPORTED = ["zh", "en"];

  const DICT = {
    zh: {
      "app.title": "esp-sdr 测试上位机",
      "app.subtitle": "ESP32-C5 射频监测 · 周期快照，非连续实时",
      "hdr.port": "串口",
      "hdr.gain": "增益档",
      "hdr.gainPlaceholder": "AGC",
      "hdr.mock": "假固件",
      "hdr.scenario": "场景",
      "hdr.start": "开始",
      "hdr.stop": "停止",
      "hdr.lang": "EN",
      "hdr.langTitle": "切到 English",
      "status.idle": "未连接",
      "status.starting": "连接中…",
      "status.running": "采集中",
      "status.error": "错误",

      "kv.firmware": "固件",
      "kv.transport": "传输",
      "kv.port": "串口",
      "kv.mode": "模式",
      "kv.gain": "增益",
      "kv.modules": "模块",
      "kv.slices": "时间片",
      "kv.snapshots": "快照",
      "kv.frames": "已推帧",
      "kv.rfSeconds": "射频秒",
      "kv.errors": "错误",
      "kv.lastError": "最后错误",
      "kv.gainAgc": "硬件 AGC",
      "kv.gainFixed": "固定档 {n}",
      "kv.mockMode": "假固件/{scenario}",
      "kv.realMode": "真机",

      "foot.next": "下一步：真机跑一遍同名场景（python host/app.py --port COM56 --gain-index 60）",
      "foot.nextMock": "现在看的是假固件数据；接板上真机见 host/README.md",

      "log.ready": "桥已就绪（window.backend 可用）",
      "log.noChannel": "QWebChannel 未连上：页面是不是被别的浏览器打开了？",
      "log.portsFailed": "读串口失败：{error}",
      "log.noPorts": "没读到任何串口，插好板子再点开始",
      "log.startFailed": "启动失败：{error}",
      "log.bridgeFailed": "桥调用失败：{error}",
      "log.stopFailed": "停止失败：{error}",
      "log.stopped": "已停止",

      "scanbar.title": "扫频进度（频段地图）",
      "scanbar.hint": "游标 = 正在读的窗；一段 = 一个频段，一小格 = 一个 20MHz 窗；扫到最右会回到最左继续下一轮",
      "scanbar.now": "当前 {center} MHz · 第 {at}/{total} 窗 · 第 {pass} 轮",
      "scanbar.noplan": "该模块没有声明扫描计划（scan_requests）— 画不出频段条",
      "scanbar.wait": "等待扫描计划…",
      "spectrum.title": "实时频谱（当前窗口）",
      "spectrum.hint": "横轴 = 频率（本窗口），纵轴 = 功率码（相对 dB 码，不是 dBm）；实线 = 当前帧，紫线 = 峰值保持",
      "spectrum.wf3d.title": "3D 瀑布",
      "spectrum.wf3d.hint": "越靠前越新；高度 = 功率码；鼠标左键旋转 / 滚轮缩放",
      "spectrum.wf2d.title": "2D 瀑布（最近 {rows} 帧）",
      "spectrum.wf2d.hint": "每帧一行，自上而下变旧；对比度按本帧动态范围自动拉伸",
      "spectrum.latest": "最新",
      "spectrum.mhz": "MHz",
      "spectrum.range": "码 {lo}~{hi}",
      "spectrum.tag": "{module} · 中心 {center} · 带宽 {bw} · {raw} bin → {out} 点",
      "spectrum.noThree": "three.js 没加载（检查 web/assets/vendor/three.min.js）",
      "spectrum.peak": "{freq} / 码 {code}",

      "wifi.card.rec24": "2.4G 推荐信道",
      "wifi.card.rec5": "5G 推荐信道",
      "wifi.card.avoid": "建议避开",
      "wifi.card.avoidNote": "相对抬升最高的 3 个",
      "wifi.card.interf": "有明显抬升的信道",
      "wifi.card.interfNote": "轻度以上；不代表一定是 WiFi",
      "wifi.card.wait": "等待样本",
      "wifi.card.none": "无",
      "wifi.card.noRec": "没有可推荐信道（覆盖率不足或全被占）",
      "wifi.card.excess": "比最静信道 {db} · {level}",
      "wifi.title": "信道体检（按相对抬升降序）",
      "wifi.sample": "{frames} 帧 / 覆盖 {span}s（窗 {bw}MHz / 步进 {step}MHz）",
      "wifi.col.rank": "#",
      "wifi.col.channel": "信道",
      "wifi.col.band": "频段",
      "wifi.col.center": "中心",
      "wifi.col.excess": "相对抬升",
      "wifi.col.bar": "占用条",
      "wifi.col.occupancy": "占空",
      "wifi.col.peak": "峰值码",
      "wifi.col.coverage": "覆盖",
      "wifi.col.level": "分档",
      "wifi.3d.title": "信道相对抬升（3D）",
      "wifi.3d.hint": "高度 = 比本频段最安静信道高多少 dB；颜色 = 分档；鼠标可旋转",
      "wifi.notes.title": "判读说明",
      "wifi.notes.levels": "分档：{levels}（相对同频段最安静信道）。",
      "wifi.notes.why": "为什么不比绝对门限：实板噪声底自身抖动 ±10~18 dB，绝对门限会把纯噪声判成忙。",
      "wifi.notes.scan": "扫描：{w24} 个 2.4G 窗 + {w5} 个 5G 窗，{bw}MHz 窗 / {step}MHz 步进重叠 {over}MHz；丢弃整窗 0 码的帧 {dropped} 帧。",
      "wifi.notes.bounds": "边界：分时快照，非连续实时；功率码是相对 dB 码，不是 dBm；跨增益不可比（先固定增益档再比）。",
      "wifi.waitData": "还没有数据：点顶部「开始」",

      "raw.title": "任意模块的产出（通用兜底页）",
      "raw.hint": "新实验只写了 produce() 也能在这里看表格；不用为每个实验改前端",
      "raw.asciiTitle": "CLI 同款 ASCII 输出",
      "raw.asciiHint": "和 python -m app.live 打印的一模一样，方便对照",
      "raw.asciiLang": "模块自带的文本沿用模块语言（与命令行一致），界面语言只翻外壳。",
      "raw.wait": "等待样本",
      "raw.notConnected": "（未连接）",
      "raw.empty": "该模块暂无表格产出（看下面的 ASCII 输出或还没开始扫）",
      "raw.noModules": "没有模块",
      "raw.weight": "权重 {w}",

      "level.重度": "重度",
      "level.拥挤": "拥挤",
      "level.轻度": "轻度",
      "level.空闲": "空闲",
      "level.无覆盖": "无覆盖",
    },
    en: {
      "app.title": "esp-sdr Test Host",
      "app.subtitle": "ESP32-C5 RF monitor · snapshot mode, not continuous",
      "hdr.port": "Port",
      "hdr.gain": "Gain idx",
      "hdr.gainPlaceholder": "AGC",
      "hdr.mock": "Fake FW",
      "hdr.scenario": "Scenario",
      "hdr.start": "Start",
      "hdr.stop": "Stop",
      "hdr.lang": "中文",
      "hdr.langTitle": "Switch to Chinese",
      "status.idle": "Idle",
      "status.starting": "Connecting…",
      "status.running": "Running",
      "status.error": "Error",

      "kv.firmware": "Firmware",
      "kv.transport": "Link",
      "kv.port": "Port",
      "kv.mode": "Mode",
      "kv.gain": "Gain",
      "kv.modules": "Modules",
      "kv.slices": "Slices",
      "kv.snapshots": "Snapshots",
      "kv.frames": "Frames out",
      "kv.rfSeconds": "RF seconds",
      "kv.errors": "Errors",
      "kv.lastError": "Last error",
      "kv.gainAgc": "HW AGC",
      "kv.gainFixed": "fixed index {n}",
      "kv.mockMode": "fake firmware/{scenario}",
      "kv.realMode": "real board",

      "foot.next": "Next: run the same scenario on the real board (python host/app.py --port COM56 --gain-index 60)",
      "foot.nextMock": "Showing fake-firmware data; see host/README.md for the real board",

      "log.ready": "Bridge ready (window.backend available)",
      "log.noChannel": "QWebChannel not connected — was this page opened in a normal browser?",
      "log.portsFailed": "Failed to list serial ports: {error}",
      "log.noPorts": "No serial port found — plug the board in and press Start",
      "log.startFailed": "Start failed: {error}",
      "log.bridgeFailed": "Bridge call failed: {error}",
      "log.stopFailed": "Stop failed: {error}",
      "log.stopped": "Stopped",

      "scanbar.title": "Scan progress (band map)",
      "scanbar.hint": "Cursor = window being read; one block = one band, one cell = one 20MHz window; at the right edge it wraps back to the left for the next pass",
      "scanbar.now": "now {center} MHz · window {at}/{total} · pass {pass}",
      "scanbar.noplan": "This module declares no scan plan (scan_requests) — cannot draw a band bar",
      "scanbar.wait": "waiting for scan plan…",
      "spectrum.title": "Live spectrum (current window)",
      "spectrum.hint": "X = frequency (this window), Y = power code (relative dB code, not dBm); solid = current frame, purple = peak hold",
      "spectrum.wf3d.title": "3D waterfall",
      "spectrum.wf3d.hint": "Nearest row is newest; height = power code; drag to rotate, wheel to zoom",
      "spectrum.wf2d.title": "2D waterfall (last {rows} frames)",
      "spectrum.wf2d.hint": "One row per frame, older downwards; contrast auto-stretched to this frame's range",
      "spectrum.latest": "new",
      "spectrum.mhz": "MHz",
      "spectrum.range": "code {lo}~{hi}",
      "spectrum.tag": "{module} · center {center} · bw {bw} · {raw} bin → {out} pts",
      "spectrum.noThree": "three.js not loaded (check web/assets/vendor/three.min.js)",
      "spectrum.peak": "{freq} / code {code}",

      "wifi.card.rec24": "Recommended 2.4G",
      "wifi.card.rec5": "Recommended 5G",
      "wifi.card.avoid": "Avoid",
      "wifi.card.avoidNote": "top-3 relative excess",
      "wifi.card.interf": "Channels with excess",
      "wifi.card.interfNote": "light and above; not necessarily WiFi",
      "wifi.card.wait": "waiting for samples",
      "wifi.card.none": "none",
      "wifi.card.noRec": "nothing recommendable (low coverage or all busy)",
      "wifi.card.excess": "{db} vs quietest · {level}",
      "wifi.title": "Channel survey (sorted by relative excess)",
      "wifi.sample": "{frames} frames / {span}s coverage (window {bw}MHz / step {step}MHz)",
      "wifi.col.rank": "#",
      "wifi.col.channel": "Ch",
      "wifi.col.band": "Band",
      "wifi.col.center": "Center",
      "wifi.col.excess": "Excess",
      "wifi.col.bar": "Bar",
      "wifi.col.occupancy": "Duty",
      "wifi.col.peak": "Peak code",
      "wifi.col.coverage": "Cover",
      "wifi.col.level": "Level",
      "wifi.3d.title": "Relative excess per channel (3D)",
      "wifi.3d.hint": "Height = dB above the quietest channel in that band; colour = level; drag to rotate",
      "wifi.notes.title": "How to read this",
      "wifi.notes.levels": "Levels: {levels} (relative to the quietest channel in the same band).",
      "wifi.notes.why": "Why no absolute threshold: on the real board the noise floor itself swings ±10–18 dB, so any absolute threshold marks pure noise as busy.",
      "wifi.notes.scan": "Scan: {w24} windows on 2.4G + {w5} on 5G, {bw}MHz window / {step}MHz step ({over}MHz overlap); dropped {dropped} all-zero frames.",
      "wifi.notes.bounds": "Boundaries: time-sliced snapshots, not continuous; power codes are relative dB codes, not dBm; not comparable across gain settings (fix the gain index first).",
      "wifi.waitData": "No data yet — press Start in the header",

      "raw.title": "Any module's output (generic fallback)",
      "raw.hint": "A new experiment that only implements produce() still renders here — no front-end change needed",
      "raw.asciiTitle": "CLI-identical ASCII output",
      "raw.asciiHint": "Byte-for-byte what python -m app.live prints, for cross-checking",
      "raw.asciiLang": "Module-generated text stays in the module's language (same as the CLI); the UI language only affects the shell.",
      "raw.wait": "waiting for samples",
      "raw.notConnected": "(not connected)",
      "raw.empty": "No table from this module yet (see the ASCII output below, or it has not scanned yet)",
      "raw.noModules": "no modules",
      "raw.weight": "weight {w}",

      "level.重度": "Heavy",
      "level.拥挤": "Crowded",
      "level.轻度": "Light",
      "level.空闲": "Idle",
      "level.无覆盖": "No cov.",
    },
  };

  let lang = detect();

  function detect() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved && SUPPORTED.indexOf(saved) >= 0) return saved;
    } catch (err) { /* 隐私模式拿不到 localStorage，忽略 */ }
    return "zh";
  }

  function t(key, params) {
    const table = DICT[lang] || DICT.zh;
    let text = table[key];
    if (text === undefined) text = DICT.zh[key];
    if (text === undefined) return key;
    if (!params) return text;
    return text.replace(/\{(\w+)\}/g, (all, name) =>
      params[name] === undefined ? all : String(params[name])
    );
  }

  function level(levelName) {
    return t("level." + levelName);
  }

  /** 把 root 子树里所有 [data-i18n] / [data-i18n-title] 填上当前语言 */
  function apply(root) {
    const scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll("[data-i18n]").forEach((node) => {
      const text = t(node.dataset.i18n);
      if (text !== undefined) node.textContent = text;
    });
    scope.querySelectorAll("[data-i18n-title]").forEach((node) => {
      node.title = t(node.dataset.i18nTitle);
    });
    scope.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
      node.placeholder = t(node.dataset.i18nPlaceholder);
    });
  }

  function setLang(next, opts) {
    if (SUPPORTED.indexOf(next) < 0) return lang;
    lang = next;
    try { localStorage.setItem(STORAGE_KEY, lang); } catch (err) { /* 忽略 */ }
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    const btn = document.querySelector("[data-lang-toggle]");
    if (btn) btn.title = t("hdr.langTitle");
    apply(document);
    if (!opts || opts.notify !== false) {
      window.dispatchEvent(new CustomEvent("langchange", { detail: { lang: lang } }));
    }
    return lang;
  }

  /** 页面注册表条目按当前语言取名（缺 *_en 时退回中文） */
  function entry(field, page) {
    if (lang === "en") {
      const key = field + "_en";
      if (page[key]) return page[key];
    }
    return page[field] || "";
  }

  return {
    t, apply, setLang, level, entry,
    lang: () => lang,
    supported: SUPPORTED.slice(),
    dict: DICT,
  };
})();

// 短别名：页面里写 T("wifi.col.rank") 比 I18N.t(...) 省事
window.T = window.I18N.t;