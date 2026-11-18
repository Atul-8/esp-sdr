/* util.js —— 格式化 + 颜色 + 画布，前端各处共用。
 *
 * 两条规矩写死在这里，别在别处破：
 *   1. 功率码是**相对 dB 码（0..255 饱和）**，任何地方都不许标 dBm；
 *   2. 分档颜色只有一套（级别 → 颜色），2D/3D/表格全用 U.levelColor()。
 */
window.U = (function () {
  function fmtHz(hz) {
    const v = Number(hz) || 0;
    if (v >= 1e9) return (v / 1e9).toFixed(3) + " GHz";
    if (v >= 1e6) return (v / 1e6).toFixed(1) + " MHz";
    if (v >= 1e3) return (v / 1e3).toFixed(1) + " kHz";
    return v.toFixed(0) + " Hz";
  }

  function fmtMHz(hz) { return (Number(hz) / 1e6).toFixed(0); }
  function fmtPct(x, digits) { return (Number(x) * 100).toFixed(digits === undefined ? 1 : digits) + "%"; }
  function fmtDb(x) { const v = Number(x) || 0; return (v >= 0 ? "+" : "") + v.toFixed(0) + " dB"; }

  /** 分档 → CSS class（表格标签用） */
  function levelClass(level) {
    switch (level) {
      case "重度": return "tag--heavy";
      case "拥挤": return "tag--crowded";
      case "轻度": return "tag--light";
      case "无覆盖": return "tag--none";
      default: return "tag--idle";
    }
  }

  /** 分档 → 颜色（3D/2D 都用同一套，颜色含义别乱改） */
  function levelColor(level) {
    switch (level) {
      case "重度": return "#f87171";
      case "拥挤": return "#fbbf24";
      case "轻度": return "#4ade80";
      case "无覆盖": return "#475569";
      default: return "#38bdf8";
    }
  }

  /** 分档 → 当前语言显示名（数据里的档位值永远是中文，翻译只在显示层） */
  function levelText(level) {
    return window.I18N ? I18N.level(level) : level;
  }

  const HEAT_STOPS = [
    [0.00, [7, 10, 16]],
    [0.25, [16, 46, 108]],
    [0.50, [32, 168, 200]],
    [0.70, [124, 226, 120]],
    [0.85, [250, 204, 60]],
    [1.00, [248, 90, 90]],
  ];

  /** 归一化 0..1 → [r,g,b]（瀑布/3D 共用同一条色带） */
  function heatRGB(t) {
    const x = Math.max(0, Math.min(1, Number(t) || 0));
    for (let i = 1; i < HEAT_STOPS.length; i++) {
      if (x <= HEAT_STOPS[i][0]) {
        const [t0, c0] = HEAT_STOPS[i - 1];
        const [t1, c1] = HEAT_STOPS[i];
        const k = (x - t0) / (t1 - t0 || 1);
        return [0, 1, 2].map((j) => Math.round(c0[j] + (c1[j] - c0[j]) * k));
      }
    }
    return [248, 90, 90];
  }

  function heatT(t) { const c = heatRGB(t); return "rgb(" + c.join(",") + ")"; }

  /** 功率码 0..255 → 热力色（绝对映射，不拉伸） */
  function heat(code) { return heatT((Number(code) || 0) / 255); }

  /** 功率码 → 0xRRGGBB 数值（three.js 顶点色用） */
  function heatHexT(t) { const c = heatRGB(t); return (c[0] << 16) | (c[1] << 8) | c[2]; }
  function heatHex(code) { return heatHexT((Number(code) || 0) / 255); }

  /**
   * 自适应显示范围：用滑动平均跟住本帧的 min/max。
   *
   * 为什么需要：实板噪声底本身在 ±10~18 dB 内抖动，固定 0..255 映射会把
   * 20..70 的码全部压成深蓝，看着像"瀑布没东西"。这里只影响**显示**，
   * 判决仍然用 `modes/wifi_check.py` 里的相对抬升口径。
   */
  class AutoRange {
    constructor(minSpan, alpha) {
      this.minSpan = minSpan === undefined ? 12 : minSpan;
      this.alpha = alpha === undefined ? 0.3 : alpha;
      this.lo = null;
      this.hi = null;
    }

    update(values) {
      let lo = Infinity, hi = -Infinity;
      for (let i = 0; i < values.length; i++) {
        const v = values[i];
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
      if (!isFinite(lo)) return this;
      if (this.lo === null) { this.lo = lo; this.hi = hi; }
      else {
        this.lo += (lo - this.lo) * this.alpha;
        this.hi += (hi - this.hi) * this.alpha;
      }
      if (this.hi - this.lo < this.minSpan) {
        const mid = (this.lo + this.hi) / 2;
        this.lo = mid - this.minSpan / 2;
        this.hi = mid + this.minSpan / 2;
      }
      return this;
    }

    norm(v) { return Math.max(0, Math.min(1, (v - this.lo) / (this.hi - this.lo || 1))); }
    get ready() { return this.lo !== null; }
    get span() { return [Math.round(this.lo || 0), Math.round(this.hi || 0)]; }
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /** 高清屏画布：CSS 尺寸 → 物理像素，返回 ctx（已按 dpr 缩放，绘制坐标仍是 CSS px） */
  function fitCanvas(canvas, cssHeight) {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(320, Math.round(rect.width || canvas.clientWidth || 640));
    const height = Math.round(cssHeight || rect.height || 220);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.height = height + "px";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, width, height, dpr };
  }

  return {
    fmtHz, fmtMHz, fmtPct, fmtDb,
    levelClass, levelColor, levelText,
    heat, heatT, heatHex, heatHexT, heatRGB,
    AutoRange, el, fitCanvas,
  };
})();