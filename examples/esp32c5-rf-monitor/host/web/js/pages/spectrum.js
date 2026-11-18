/* pages/spectrum.js —— 实时频谱：2D 曲线 + 3D 瀑布 + 2D 瀑布。 */
window.Pages = window.Pages || {};

window.Pages.spectrum = (function () {
  const WF_ROWS = 180;      // 2D 瀑布保留的行数（每帧一行）
  const PEAK_DECAY = 0.35;  // 峰值保持每帧衰减（码）

  let root = null;
  let spec = null;          // {canvas, ctx, width, height}
  let wf2d = null;
  let waterfall3d = null;
  let peak = null;
  let lastPayload = null;
  let drawn = 0;
  let resizeTimer = null;

  function onEnter(host) {
    root = host;
    const specCanvas = host.querySelector("#spec-canvas");
    const wfCanvas = host.querySelector("#wf-canvas");
    spec = U.fitCanvas(specCanvas, 210);
    wf2d = U.fitCanvas(wfCanvas, 150);
    wf2d.ctx.fillStyle = "#070a10";
    wf2d.ctx.fillRect(0, 0, wf2d.width, wf2d.height);
    peak = null;
    lastPayload = null;

    const box = host.querySelector("#wf-3d");
    waterfall3d = new Three3D.Waterfall(box, { nFreq: 96, nTime: 110 });
    if (waterfall3d.ok) waterfall3d.resize();

    if (lastPayload) onSpectrum(lastPayload);
    redraw();

    // 窗口拉伸：节流后重算画布尺寸
    window.addEventListener("resize", onResize);
  }

  function onResize() {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (!root) return;
      spec = U.fitCanvas(root.querySelector("#spec-canvas"), 210);
      wf2d = U.fitCanvas(root.querySelector("#wf-canvas"), 150);
      if (waterfall3d && waterfall3d.ok) waterfall3d.resize();
      redraw();
    }, 180);
  }

  function onLeave() {
    window.removeEventListener("resize", onResize);
    if (waterfall3d) waterfall3d.dispose();
    waterfall3d = null;
    root = null;
  }

  function onSpectrum(payload) {
    if (!payload || !payload.bins) return;
    lastPayload = payload;
    drawn += 1;

    // ---- 峰值保持（按新帧长度重置） ----
    if (!peak || peak.length !== payload.bins.length) {
      peak = payload.bins.slice();
    } else {
      for (let i = 0; i < peak.length; i++) {
        peak[i] = Math.max(payload.bins[i], peak[i] - PEAK_DECAY);
      }
    }

    if (waterfall3d && waterfall3d.ok) waterfall3d.push(payload.bins);
    drawWaterfall2D(payload);
    drawSpectrum(payload);
  }

  function drawSpectrum(payload) {
    if (!spec) return;
    const { ctx, width, height } = spec;
    const bins = payload.bins;
    const padL = 46, padR = 8, padT = 10, padB = 22;
    const plotW = Math.max(10, width - padL - padR);
    const plotH = Math.max(10, height - padT - padB);

    let lo = 255, hi = 0;
    for (const v of bins) { if (v < lo) lo = v; if (v > hi) hi = v; }
    lo = Math.max(0, Math.min(lo, hi - 8) - 4);
    hi = Math.min(255, hi + 6);
    if (hi - lo < 12) hi = Math.min(255, lo + 12);
    const xOf = (i) => padL + (i / Math.max(1, bins.length - 1)) * plotW;
    const yOf = (v) => padT + plotH - ((v - lo) / (hi - lo)) * plotH;

    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#070a10";
    ctx.fillRect(0, 0, width, height);

    // 网格 + 纵轴刻度
    ctx.strokeStyle = "#16233a";
    ctx.fillStyle = "#5f7391";
    ctx.font = "10px Consolas, monospace";
    ctx.lineWidth = 1;
    for (let k = 0; k <= 4; k++) {
      const v = lo + ((hi - lo) * k) / 4;
      const y = yOf(v);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(width - padR, y); ctx.stroke();
      ctx.fillText(v.toFixed(0), 6, y + 3);
    }
    // 横轴刻度（频率）
    const fLo = payload.freq_lo_hz, fHi = payload.freq_hi_hz;
    for (let k = 0; k <= 4; k++) {
      const x = padL + (plotW * k) / 4;
      const f = fLo + ((fHi - fLo) * k) / 4;
      ctx.strokeStyle = "#16233a";
      ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, padT + plotH); ctx.stroke();
      ctx.fillStyle = "#5f7391";
      const label = (f / 1e6).toFixed(1);
      ctx.fillText(label, x - ctx.measureText(label).width / 2, height - 7);
    }
    ctx.fillStyle = "#5f7391";
    ctx.fillText("MHz", width - padR - 22, height - 7);

    // 峰值保持线
    if (peak) {
      ctx.strokeStyle = "#7c3aed";
      ctx.globalAlpha = 0.55;
      ctx.beginPath();
      peak.forEach((v, i) => (i ? ctx.lineTo(xOf(i), yOf(v)) : ctx.moveTo(xOf(i), yOf(v))));
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    // 当前帧：填充 + 描边
    const grad = ctx.createLinearGradient(0, padT, 0, padT + plotH);
    grad.addColorStop(0, "rgba(56,189,248,0.42)");
    grad.addColorStop(1, "rgba(56,189,248,0.02)");
    ctx.beginPath();
    ctx.moveTo(xOf(0), padT + plotH);
    bins.forEach((v, i) => ctx.lineTo(xOf(i), yOf(v)));
    ctx.lineTo(xOf(bins.length - 1), padT + plotH);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    bins.forEach((v, i) => (i ? ctx.lineTo(xOf(i), yOf(v)) : ctx.moveTo(xOf(i), yOf(v))));
    ctx.strokeStyle = "#38bdf8";
    ctx.lineWidth = 1.4;
    ctx.stroke();

    // 峰值点标记
    let maxIdx = 0;
    for (let i = 1; i < bins.length; i++) if (bins[i] > bins[maxIdx]) maxIdx = i;
    const px = xOf(maxIdx), py = yOf(bins[maxIdx]);
    ctx.fillStyle = "#fbbf24";
    ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI * 2); ctx.fill();
    const peakFreq = fLo + ((fHi - fLo) * maxIdx) / Math.max(1, bins.length - 1);
    const text = (peakFreq / 1e6).toFixed(2) + " MHz / 码 " + bins[maxIdx].toFixed(0);
    ctx.fillText(text, Math.min(px + 6, width - padR - ctx.measureText(text).width), Math.max(12, py - 8));

    // 角标：模块 / 增益 / 帧号
    ctx.fillStyle = "#8697b4";
    const tag = (payload.module ? payload.module + " · " : "") +
      "中心 " + (payload.center_hz / 1e6).toFixed(1) + " MHz · 带宽 " +
      (payload.bw_hz / 1e6).toFixed(0) + " MHz · " + payload.raw_bins + " bin → " + bins.length + " 点";
    ctx.fillText(tag, padL + 4, padT + 11);
  }

  function drawWaterfall2D(payload) {
    if (!wf2d) return;
    const { ctx, width, height } = wf2d;
    const rowH = Math.max(1, Math.floor(height / WF_ROWS));
    // 整体下移一行
    ctx.drawImage(ctx.canvas, 0, 0, width, height - rowH, 0, rowH, width, height - rowH);
    // 顶上写最新一行
    const bins = payload.bins;
    for (let x = 0; x < width; x++) {
      const idx = Math.min(bins.length - 1, Math.floor((x / width) * bins.length));
      ctx.fillStyle = U.heat(bins[idx]);
      ctx.fillRect(x, 0, 1, rowH);
    }
    ctx.fillStyle = "#8697b4";
    ctx.font = "10px Consolas, monospace";
    ctx.fillText("最新", 4, 11);
    ctx.fillText((payload.freq_lo_hz / 1e6).toFixed(0) + " MHz", 4, height - 4);
    const right = (payload.freq_hi_hz / 1e6).toFixed(0) + " MHz";
    ctx.fillText(right, width - ctx.measureText(right).width - 4, height - 4);
  }

  function redraw() {
    if (lastPayload) drawSpectrum(lastPayload);
  }

  return {
    onEnter,
    onLeave,
    onSpectrum,
    probe: () => ({ frames: drawn, three: three3dInfo() }),
  };

  function three3dInfo() {
    if (!waterfall3d || !waterfall3d.ok) return { ok: false };
    return { ok: true, pushFrames: waterfall3d.frames, revision: THREE.REVISION || "?" };
  }
})();