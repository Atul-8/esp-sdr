/*
 * scanbar.js —— 扫频进度条（频段地图 + 滚动游标）。
 *
 * 用途：实时看「现在读到哪了」。左边一条横条按**真实频率范围**切成若干段
 * （本工程就是 2.4G 2402–2482 MHz 和 5G 5170–5835 MHz），每一小格 = 扫描计划里的
 * 一个窗；游标停在该窗的中心频点上，一轮扫完从尾回到头，继续下一轮。
 *
 * 关键约定：
 *   · 段的边界**不是写死的**，而是从 `info.plans[module]`（Python 侧各插件的
 *     scan_requests()）推出来的：按扫描顺序走，遇到 > 3 倍窗宽的频率跳跃就另起一段。
 *     换实验只要插件声明了 scan_requests()，这里不用改一行。
 *   · 横轴是「分段线性」的：2.4G 只有 80 MHz、5G 有 665 MHz，按真实比例画 2.4G 会
 *     变成一根看不见的线，所以每段宽度取 √跨度 归一（2.4G 约 26%，5G 约 74%）。
 *   · 游标用 requestAnimationFrame 插值移动（不是每帧硬跳），一轮结束回到起点时会
 *     从左到右一路滑回去，看得出「又从头扫」。
 */
window.ScanBar = (function () {
  const HEIGHT = 66;       // 整块高度（CSS px）
  const TRACK_TOP = 6;
  const TRACK_H = 30;
  const LERP = 0.25;
  const LERP_WRAP = 0.15;  // 回卷时稍慢一点，能看清游标往左扫回去

  class ScanBar {
    constructor(host) {
      this.host = host;
      this.canvas = document.createElement("canvas");
      this.canvas.id = "scanbar-canvas";  // 给自检探针一个可读名字
      this.canvas.className = "scanbar__canvas";
      host.innerHTML = "";
      host.appendChild(this.canvas);
      this.fit = U.fitCanvas(this.canvas, HEIGHT);
      this.module = null;
      this.plan = [];        // [{center, bw}]
      this.groups = [];      // [{lo, hi, name, items:[{center, x0, x1}]}]
      this.index = -1;
      this.passes = 1;
      this.visited = new Set();
      this.x = 0;
      this.target = 0;
      this.wrapping = false;
      this.raf = 0;
      this.lastCenter = null;
      this.trail = [];
      this.resize();
    }

    // ---- 计划 -------------------------------------------------------------
    /** windows: [[center_hz, bw_hz], ...]（扫描顺序，来自 Python 的 scan_requests()） */
    setPlan(module, windows) {
      const key = module + ":" + (windows ? windows.length : 0) + ":" + (windows && windows.length ? windows[0][0] : 0);
      if (this.planKey === key) return;
      this.planKey = key;
      this.module = module;
      this.plan = (windows || []).map((w) => ({ center: w[0], bw: w[1] }));
      this.rebuild();
      this.index = -1;
      this.visited.clear();
      this.passes = 1;
      this.x = 0;
      this.target = 0;
      this.draw();
    }

    rebuild() {
      const groups = [];
      const gapLimit = (this.plan[0] ? this.plan[0].bw : 20e6) * 3;
      let cur = null;
      this.plan.forEach((item) => {
        const lo = item.center - item.bw / 2;
        const hi = item.center + item.bw / 2;
        if (!cur || lo - cur.hi > gapLimit) {
          cur = { lo: lo, hi: hi, centers: [] };
          groups.push(cur);
        }
        cur.hi = Math.max(cur.hi, hi);
        cur.centers.push(item.center);
      });
      groups.forEach((g, i) => {
        const spanMHz = (g.hi - g.lo) / 1e6;
        g.name = spanMHz < 200 && g.lo > 2.3e9 && g.lo < 2.6e9 ? "2.4G" : g.lo > 5e9 ? "5G" : "#" + (i + 1);
        g.color = g.lo > 5e9 ? "#2b2350" : "#123b4d";
        g.edge = g.lo > 5e9 ? "#6d5bd0" : "#2f7fa8";
      });
      this.groups = groups;
      this.layout();
    }

    /** 分段线性布局：每段宽度 ∝ √跨度（保底 18%） */
    layout() {
      const width = this.fit.width;
      const weights = this.groups.map((g) => Math.sqrt(Math.max(1, g.hi - g.lo)));
      const total = weights.reduce((a, b) => a + b, 0) || 1;
      let x = 0;
      this.groups.forEach((g, i) => {
        const raw = (width * weights[i]) / total;
        g.x0 = x;
        g.x1 = i === this.groups.length - 1 ? width : x + raw;
        x = g.x1;
        // 每格（窗）在段内的位置
        const span = Math.max(1, g.hi - g.lo);
        g.items = [];
        this.plan.forEach((p, planIndex) => {
          if (p.center - p.bw / 2 < g.lo - 1 || p.center + p.bw / 2 > g.hi + 1) return;
          const cx = g.x0 + ((p.center - g.lo) / span) * (g.x1 - g.x0);
          const half = ((p.bw / 2) / span) * (g.x1 - g.x0);
          g.items.push({ center: p.center, index: planIndex, x0: cx - half, x1: cx + half, cx: cx });
        });
      });
      // 尺寸变了：按当前窗重算坐标；还没开始扫就把游标放在第一格
      this.curWindow = this.findItem(this.index);
      this.target = this.curWindow ? this.curWindow.cx : (this.groups[0] && this.groups[0].items[0] ? this.groups[0].items[0].cx : 0);
      if (this.index < 0) this.x = this.target;
    }

    // ---- 数据 -------------------------------------------------------------
    /** payload 来自 spectrum 信号（有 center_hz / bw_hz / module） */
    update(payload) {
      if (!payload) return;
      this.curWidth = payload.bw_hz;
      if (!this.plan.length) {
        this.lastCenter = payload.center_hz;
        this.draw();
        return;
      }
      const found = this.locate(payload.center_hz);
      if (!found) {
        this.lastCenter = payload.center_hz;
        this.draw();
        return;
      }
      const moved = found.index !== this.index;
      if (moved) {
        // 只有"跳回大半张图"才算换轮：20MHz 窗 / 15MHz 步进是**重叠**的，
        // 相邻窗互有覆盖，最近匹配会在相邻格之间前后抖，按"变小就算换轮"会被抖动
        // 反复清空 visited（实测 8s 只记到 1 个窗）
        const wrapped = this.index >= 0 && found.index < this.index && this.index - found.index > this.plan.length / 2;
        if (wrapped) {
          this.passes += 1;          // 一轮扫完，回到起点
          this.visited.clear();
          this.wrapping = true;
        } else {
          this.wrapping = false;
        }
        this.index = found.index;
        this.visited.add(found.index);
        this.target = found.item.cx;
      }
      this.curWindow = found.item;
      this.lastCenter = payload.center_hz;
      this.trail.push(found.index);
      if (this.trail.length > 200) this.trail.shift();
      this.start();
    }

    locate(centerHz) {
      let best = null;
      this.groups.forEach((g) => {
        g.items.forEach((item) => {
          const d = Math.abs(item.center - centerHz);
          if (!best || d < best.d) best = { d: d, item: item };
        });
      });
      if (!best || best.d > (this.curWidth || 20e6)) return null;
      return { index: best.item.index, item: best.item };
    }

    findItem(index) {
      let found = null;
      this.groups.forEach((g) => {
        g.items.forEach((item) => { if (item.index === index) found = item; });
      });
      return found;
    }

    // ---- 动画 -------------------------------------------------------------
    start() {
      if (this.raf) return;
      const step = () => {
        const k = this.wrapping ? LERP_WRAP : LERP;
        this.x += (this.target - this.x) * k;
        if (Math.abs(this.target - this.x) < 0.4) {
          this.x = this.target;
          this.raf = 0;
          this.wrapping = false;
          this.draw();
          return;
        }
        this.draw();
        this.raf = requestAnimationFrame(step);
      };
      this.raf = requestAnimationFrame(step);
    }

    resize() {
      this.fit = U.fitCanvas(this.canvas, HEIGHT);
      this.layout();
      this.draw();
    }

    // ---- 绘制 -------------------------------------------------------------
    draw() {
      const { ctx, width } = this.fit;
      ctx.clearRect(0, 0, width, HEIGHT);

      // 轨道底
      ctx.fillStyle = "#0b1220";
      roundRect(ctx, 0, TRACK_TOP, width, TRACK_H, 4);
      ctx.fill();
      ctx.strokeStyle = "#1c2a41";
      ctx.lineWidth = 1;
      ctx.stroke();

      if (!this.groups.length) {
        ctx.fillStyle = "#5f7391";
        ctx.font = "11px Consolas, monospace";
        ctx.fillText(T("scanbar.noplan"), 8, TRACK_TOP + 19);
        return;
      }

      ctx.font = "10px Consolas, monospace";
      this.groups.forEach((g) => {
        // 段底色
        ctx.fillStyle = g.color;
        roundRect(ctx, g.x0 + 1, TRACK_TOP + 1, Math.max(2, g.x1 - g.x0 - 2), TRACK_H - 2, 3);
        ctx.fill();
        ctx.strokeStyle = g.edge;
        ctx.globalAlpha = 0.7;
        ctx.stroke();
        ctx.globalAlpha = 1;

        // 已扫过的窗
        g.items.forEach((item) => {
          if (!this.visited.has(item.index)) return;
          ctx.fillStyle = "rgba(56,189,248,0.20)";
          ctx.fillRect(item.x0, TRACK_TOP + 3, Math.max(1, item.x1 - item.x0), TRACK_H - 6);
        });

        // 窗刻度（每 4 格一条，避免 44 格糊成一片）
        ctx.strokeStyle = "rgba(134,151,180,0.35)";
        g.items.forEach((item, i) => {
          if (i % 4 !== 0) return;
          ctx.beginPath();
          ctx.moveTo(Math.round(item.cx) + 0.5, TRACK_TOP + TRACK_H - 9);
          ctx.lineTo(Math.round(item.cx) + 0.5, TRACK_TOP + TRACK_H - 3);
          ctx.stroke();
        });

        // 段名 + 频率范围
        ctx.fillStyle = "#c8d6ef";
        const name = g.name;
        ctx.fillText(name, g.x0 + 6, TRACK_TOP + 13);
        const range = (g.lo / 1e6).toFixed(0) + "–" + (g.hi / 1e6).toFixed(0) + " " + T("spectrum.mhz");
        ctx.fillStyle = "#8697b4";
        ctx.fillText(range, g.x0 + 6, TRACK_TOP + TRACK_H + 12);
      });

      // 当前窗高亮
      if (this.curWindow && this.index >= 0) {
        ctx.fillStyle = "rgba(56,189,248,0.42)";
        const w = Math.max(2, this.curWindow.x1 - this.curWindow.x0);
        ctx.fillRect(this.curWindow.x0, TRACK_TOP + 2, w, TRACK_H - 4);
      }

      // 游标：一条竖线 + 顶部小三角
      const x = Math.round(this.x) + 0.5;
      ctx.strokeStyle = "#fbbf24";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x, TRACK_TOP - 4);
      ctx.lineTo(x, TRACK_TOP + TRACK_H + 1);
      ctx.stroke();
      ctx.fillStyle = "#fbbf24";
      ctx.beginPath();
      ctx.moveTo(x - 4, TRACK_TOP - 6);
      ctx.lineTo(x + 4, TRACK_TOP - 6);
      ctx.lineTo(x, TRACK_TOP - 1);
      ctx.closePath();
      ctx.fill();

      // 右上角：当前频点 + 轮次 + 本轮进度
      if (this.lastCenter) {
        const total = this.plan.length;
        const text = T("scanbar.now", {
          center: (this.lastCenter / 1e6).toFixed(0),
          at: Math.max(1, this.index + 1),
          total: total,
          pass: this.passes,
        });
        ctx.fillStyle = "#8697b4";
        ctx.fillText(text, Math.max(4, width - ctx.measureText(text).width - 2), TRACK_TOP + 13);
      }

      // 底部：本轮覆盖率细条
      const done = this.visited.size;
      const total = Math.max(1, this.plan.length);
      ctx.fillStyle = "#16233a";
      ctx.fillRect(0, HEIGHT - 8, width, 4);
      ctx.fillStyle = "#38bdf8";
      ctx.fillRect(0, HEIGHT - 8, (width * done) / total, 4);
    }

    dispose() {
      if (this.raf) cancelAnimationFrame(this.raf);
      this.raf = 0;
      this.host.innerHTML = "";
    }

    probe() {
      const g = this.groups[0];
      return {
        module: this.module,
        groups: this.groups.map((x) => ({ name: x.name, lo: x.lo, hi: x.hi, windows: x.items.length })),
        windows: this.plan.length,
        index: this.index,
        passes: this.passes,
        visited: this.visited.size,
        trail: this.trail.slice(-24),
        x: Math.round(this.x),
        targetX: Math.round(this.target),
        width: g ? Math.round(this.fit.width) : 0,
      };
    }
  }

  function roundRect(ctx, x, y, w, h, r) {
    if (typeof ctx.roundRect === "function") {
      ctx.beginPath();
      ctx.roundRect(x, y, w, h, r);
      return;
    }
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  /** 给 --selftest 用：喂一串频点，验证「到终点回卷」的判定。 */
  ScanBar.selftest = function () {
    const host = document.createElement("div");
    host.style.width = "600px";
    const bar = new ScanBar(host);
    bar.setPlan("_selftest", [[100e6, 20e6], [120e6, 20e6], [140e6, 20e6], [160e6, 20e6]]);
    const feed = (hz) => bar.update({ center_hz: hz, bw_hz: 20e6 });
    feed(100e6); feed(120e6); feed(140e6); feed(160e6);
    const atEnd = bar.probe();
    feed(100e6);
    const wrapped = bar.probe();
    bar.dispose();
    return {
      plan: atEnd.windows,
      indexAtEnd: atEnd.index,
      visitedAtEnd: atEnd.visited,
      passesAfterWrap: wrapped.passes,
      visitedAfterWrap: wrapped.visited,
      xAfterWrap: wrapped.targetX,
      groups: atEnd.groups.length,
    };
  };

  return ScanBar;
})();