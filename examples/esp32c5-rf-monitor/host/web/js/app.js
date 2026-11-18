/*
 * app.js —— 外壳装配：导航 + 桥 + 顶栏控制 + 事件路由 + 自检探针。
 *
 * 依赖方向：app.js → router/bridge/util/three3d → pages/*（单向，页面不反调外壳）。
 */
(function () {
  const state = {
    framesSeen: 0,
    tablesSeen: 0,
    info: null,
    stats: null,
    running: false,
    lastSpectrumAt: 0,
  };

  const MAX_LOG = 220;

  document.addEventListener("DOMContentLoaded", init);

  async function init() {
    // ---- 桥事件 → 页面分发 ----
    Bridge.on("spectrum", (payload) => {
      state.framesSeen += 1;
      state.lastSpectrumAt = Date.now();
      Router.dispatch("Spectrum", payload);
    });
    Bridge.on("tables", (payload) => {
      state.tablesSeen += 1;
      Router.dispatch("Tables", payload);
    });
    Bridge.on("stats", onStats);
    Bridge.on("info", onInfo);
    Bridge.on("state", onState);
    Bridge.on("log", (payload) => log(payload));

    // ---- 导航 + 首屏 ----
    await Router.init();
    await Router.navigate("spectrum");

    // ---- 桥就绪后拉一次本机串口/场景清单 ----
    Bridge.init(async () => {
      await loadPorts();
      await loadScenarios();
      bindControls();
      log({ text: "桥已就绪（window.backend 可用）" });
    });

    if (!Bridge.ready()) {
      setTimeout(() => {
        if (!Bridge.ready()) log({ level: "err", text: "QWebChannel 未连上：页面是不是被别的浏览器打开了？" });
      }, 1500);
    }
  }

  // ---- 顶栏 -------------------------------------------------------------
  async function loadPorts() {
    const select = document.getElementById("port");
    let ports = [];
    try { ports = await Bridge.call("listPorts"); } catch (err) { log({ level: "err", text: "读串口失败：" + err }); }
    select.innerHTML = "";
    const preset = ["COM56", "COM59"].filter((p) => !ports.some((x) => x.device === p));
    ports.forEach((info) => {
      const opt = document.createElement("option");
      opt.value = info.device;
      opt.textContent = info.device + (info.description ? " · " + info.description : "");
      select.appendChild(opt);
    });
    if (!ports.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "（没找到串口）";
      select.appendChild(opt);
    }
    const prefer = ports.find((p) => p.device === "COM56") || ports[0];
    if (prefer) select.value = prefer.device;
    if (preset.length && !ports.length) log({ level: "err", text: "没读到任何串口，插好板子再点开始" });
  }

  async function loadScenarios() {
    const select = document.getElementById("scenario");
    let list = [];
    try { list = await Bridge.call("scenarios"); } catch (err) { /* 忽略 */ }
    select.innerHTML = "";
    (list.length ? list : ["mixed", "quiet", "wifi_busy"]).forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    });
    if (list.indexOf("wifi_busy") >= 0) select.value = "wifi_busy";
    select.disabled = !document.getElementById("mock").checked;
    document.getElementById("mock").addEventListener("change", (e) => {
      select.disabled = !e.target.checked;
    });
  }

  function bindControls() {
    document.getElementById("btn-start").addEventListener("click", startRun);
    document.getElementById("btn-stop").addEventListener("click", stopRun);
  }

  async function startRun() {
    const mock = document.getElementById("mock").checked;
    const gainRaw = document.getElementById("gain").value;
    const config = {
      port: document.getElementById("port").value,
      baudrate: 2000000,
      gain_index: mock || gainRaw === "" ? null : parseInt(gainRaw, 10),
      mock: mock,
      scenario: document.getElementById("scenario").value,
      bins_out: 256,
    };
    setStatus("starting", "连接中…");
    setRunning(true);
    try {
      const result = await Bridge.call("start", JSON.stringify(config));
      if (!result || !result.ok) {
        setStatus("error", (result && result.message) || "启动失败");
        setRunning(false);
        log({ level: "err", text: "启动失败：" + (result && result.message) });
      } else {
        setStatus("running", "采集中");
        log({ text: result.message });
      }
    } catch (err) {
      setStatus("error", "桥调用失败");
      setRunning(false);
      log({ level: "err", text: "桥调用失败：" + err });
    }
  }

  async function stopRun() {
    try {
      const result = await Bridge.call("stop");
      log({ text: (result && result.message) || "已停止" });
    } catch (err) {
      log({ level: "err", text: "停止失败：" + err });
    }
    setStatus("idle", "已停止");
    setRunning(false);
  }

  function setRunning(running) {
    state.running = running;
    document.getElementById("btn-start").disabled = running;
    document.getElementById("btn-stop").disabled = !running;
  }

  function setStatus(stateName, text) {
    const el = document.getElementById("status");
    el.dataset.state = stateName;
    el.textContent = text;
  }

  // ---- 事件 -------------------------------------------------------------
  function onInfo(info) {
    state.info = info;
    const kv = document.getElementById("kv");
    kv.innerHTML = "";
    const pairs = [
      ["固件", info.identity || "—"],
      ["传输", info.transport || "—"],
      ["串口", (info.port || "—") + " @ " + (info.baudrate || 0)],
      ["模式", info.mock ? "假固件/" + info.scenario : "真机"],
      ["增益", info.gain_index === null || info.gain_index === undefined ? "硬件 AGC" : "固定档 " + info.gain_index],
      ["模块", (info.plugins || []).map((p) => p.id + "(" + p.requests + "窗)").join(" ") || "—"],
    ];
    pairs.forEach(([k, v]) => {
      const span = document.createElement("span");
      span.innerHTML = k + " <b>" + escapeHtml(String(v)) + "</b>";
      kv.appendChild(span);
    });
    (info.notes || []).forEach((n) => log({ text: n }));
  }

  function onStats(stats) {
    state.stats = stats;
    const kv = document.getElementById("kv");
    const info = state.info;
    if (!info) return;
    kv.innerHTML = "";
    const pairs = [
      ["固件", info.identity || "—"],
      ["串口", (info.port || "—") + (info.mock ? " · 假固件/" + info.scenario : "")],
      ["增益", info.gain_index === null || info.gain_index === undefined ? "硬件 AGC" : "固定档 " + info.gain_index],
      ["时间片", stats.slices],
      ["快照", stats.snapshots],
      ["已推帧", state.framesSeen],
      ["射频秒", (stats.elapsed_s || 0).toFixed(1)],
      ["错误", stats.source_errors],
    ];
    pairs.forEach(([k, v]) => {
      const span = document.createElement("span");
      span.innerHTML = k + " <b>" + escapeHtml(String(v)) + "</b>";
      kv.appendChild(span);
    });
    if (stats.last_source_error) {
      const span = document.createElement("span");
      span.className = "err";
      span.textContent = "最后错误：" + stats.last_source_error;
      kv.appendChild(span);
    }
    if (!state.running) {
      setStatus("idle", "已停止");
    }
  }

  function onState(msg) {
    if (!msg) return;
    if (msg.state === "running") {
      setStatus("running", "采集中");
      setRunning(true);
    } else if (msg.state === "stopped") {
      setStatus("idle", "已停止");
      setRunning(false);
    } else if (msg.state === "error") {
      setStatus("error", msg.message || "错误");
      setRunning(false);
    } else if (msg.state === "ui-loaded") {
      log({ text: "前端页面加载完成" });
    } else if (msg.state === "ui-load-failed") {
      setStatus("error", "页面加载失败");
    }
  }

  function log(entry) {
    if (!entry) return;
    const text = typeof entry === "string" ? entry : entry.text || JSON.stringify(entry);
    const box = document.getElementById("log");
    const line = document.createElement("div");
    if (entry.level === "err") line.className = "err";
    const now = new Date();
    const stamp = now.toTimeString().slice(0, 8);
    line.textContent = "[" + stamp + "] " + text;
    box.appendChild(line);
    while (box.childNodes.length > MAX_LOG) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
  }

  function escapeHtml(text) {
    return text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // ---- 自检探针（host/app.py --selftest 会读它） -------------------------
  function nonBlackRatio(canvas) {
    try {
      const w = Math.min(320, canvas.width || 0);
      const h = Math.min(200, canvas.height || 0);
      if (!w || !h) return 0;
      const tmp = document.createElement("canvas");
      tmp.width = w;
      tmp.height = h;
      const ctx = tmp.getContext("2d");
      ctx.drawImage(canvas, 0, 0, w, h);
      const data = ctx.getImageData(0, 0, w, h).data;
      let lit = 0;
      for (let i = 0; i < data.length; i += 4) {
        if (data[i] + data[i + 1] + data[i + 2] > 90) lit += 1;
      }
      return lit / (w * h);
    } catch (err) {
      return 0;
    }
  }

  function webglInfo() {
    try {
      const c = document.createElement("canvas");
      const gl = c.getContext("webgl2") || c.getContext("webgl");
      if (!gl) return { ok: false };
      return { ok: true, version: gl.getParameter(gl.VERSION), renderer: gl.getParameter(gl.RENDERER) };
    } catch (err) {
      return { ok: false, error: String(err) };
    }
  }

  window.probeReport = function probeReport() {
    const canvases = Array.from(document.querySelectorAll("canvas")).map((c) => ({
      id: c.id || "(3d)",
      w: c.width,
      h: c.height,
      nonBlack: Number(nonBlackRatio(c).toFixed(4)),
    }));
    const page = Router.current;
    return {
      ready: Bridge.ready(),
      page: page ? page.id : null,
      three: typeof THREE !== "undefined" ? THREE.REVISION || "?" : null,
      webgl: webglInfo(),
      framesSeen: state.framesSeen,
      tablesSeen: state.tablesSeen,
      running: state.running,
      canvases: canvases,
      pageProbe: page && page.mod && page.mod.probe ? page.mod.probe() : null,
    };
  };

  // 让页面模块也能记日志
  window.AppLog = log;
})();