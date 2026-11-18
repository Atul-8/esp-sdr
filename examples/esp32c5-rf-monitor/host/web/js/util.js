/* util.js —— 格式化 + 颜色，前端各处共用。功率码是相对 dB 码，这里一律不加 dBm。 */

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

  /** 分档 → CSS class */
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

  /** 功率码 0..255 → 热力色（低=深蓝，高=红） */
  function heat(code) {
    const t = Math.max(0, Math.min(1, (Number(code) || 0) / 255));
    const stops = [
      [0.00, [7, 10, 16]],
      [0.25, [16, 46, 108]],
      [0.50, [32, 168, 200]],
      [0.70, [124, 226, 120]],
      [0.85, [250, 204, 60]],
      [1.00, [248, 90, 90]],
    ];
    for (let i = 1; i < stops.length; i++) {
      if (t <= stops[i][0]) {
        const [t0, c0] = stops[i - 1];
        const [t1, c1] = stops[i];
        const k = (t - t0) / (t1 - t0 || 1);
        const c = c0.map((v, j) => Math.round(v + (c1[j] - v) * k));
        return "rgb(" + c.join(",") + ")";
      }
    }
    return "rgb(248,90,90)";
  }

  /** 功率码 → 0xRRGGBB 数值（给 three.js 顶点色用） */
  function heatHex(code) {
    const css = heat(code);
    const m = css.match(/\d+/g).map(Number);
    return (m[0] << 16) | (m[1] << 8) | m[2];
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /** 高清屏画布：CSS 尺寸 → 物理像素，返回 ctx（已按 dpr 缩放） */
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
    return { ctx, width, height };
  }

  return { fmtHz, fmtMHz, fmtPct, fmtDb, levelClass, levelColor, heat, heatHex, el, fitCanvas };
})();