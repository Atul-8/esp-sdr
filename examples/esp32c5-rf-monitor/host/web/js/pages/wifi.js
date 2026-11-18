/* pages/wifi.js —— WiFi 信道体检：卡片 + 表格 + 3D 柱状图。
 *
 * 数据来自 modes/wifi_check.py 的 web_payload()（经 host/session.py 打包）。
 * 分档口径与 CLI 完全一致，前端不做二次判定。 */
window.Pages = window.Pages || {};

window.Pages.wifi = (function () {
  let root = null;
  let bars3d = null;
  let lastPayload = null;
  let resizeTimer = null;

  function onEnter(host) {
    root = host;
    const box = host.querySelector("#wifi-3d");
    bars3d = new Three3D.ChannelBars(box);
    if (bars3d.ok) bars3d.resize();
    if (lastPayload) render(lastPayload);
    window.addEventListener("resize", onResize);
  }

  function onResize() {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (bars3d && bars3d.ok) bars3d.resize(); }, 180);
  }

  function onLeave() {
    window.removeEventListener("resize", onResize);
    if (bars3d) bars3d.dispose();
    bars3d = null;
    root = null;
  }

  function pickPayload(tables) {
    if (!tables || !tables.plugins) return null;
    const item = tables.plugins.find((p) => p.payload && p.payload.kind === "wifi_check");
    return item ? item.payload : null;
  }

  function onTables(tables) {
    const payload = pickPayload(tables);
    if (!payload) return;
    lastPayload = payload;
    if (root) render(payload);
  }

  function render(payload) {
    const channels = payload.channels || [];
    const avoided = (payload.avoided && (payload.avoided["2.4G"] || []).concat(payload.avoided["5G"])) || [];

    // ---- 卡片 ----
    setCard("#rec24", payload.recommend_24 ? "ch" + payload.recommend_24 : "无", payload.recommend_24, "2.4G");
    setCard("#rec5", payload.recommend_5 ? "ch" + payload.recommend_5 : "无", payload.recommend_5, "5G");
    const avoidText = avoided.length ? avoided.map((c) => "ch" + c).join(" / ") : "无";
    root.querySelector("#avoid").textContent = avoidText;
    const interf = payload.interferers || [];
    root.querySelector("#interf").textContent = interf.length ? interf.join(" / ") : "无";

    const sample = root.querySelector("#wifi-sample");
    sample.textContent = (payload.frames || 0) + " 帧 / 覆盖 " + (payload.span_s || 0).toFixed(1) +
      "s（窗 " + (payload.scan_plan.bw_hz / 1e6).toFixed(0) + "MHz / 步进 " +
      (payload.scan_plan.step_hz / 1e6).toFixed(0) + "MHz）";

    // ---- 表格（按相对抬升降序；与 CLI 同序） ----
    const body = root.querySelector("#wifi-rows");
    body.innerHTML = "";
    if (!channels.length) {
      body.innerHTML = '<tr><td colspan="10" class="muted">等待样本</td></tr>';
      return;
    }
    const sorted = channels.slice().sort((a, b) => b.excess_db - a.excess_db || a.channel - b.channel);
    const worst = Math.max.apply(null, sorted.map((c) => Math.max(0, c.excess_db)).concat([1]));
    sorted.forEach((c, i) => {
      const tr = document.createElement("tr");
      const pct = Math.max(0, Math.min(1, c.excess_db / worst));
      tr.innerHTML =
        "<td>" + (i + 1) + "</td>" +
        "<td>ch" + c.channel + "</td>" +
        "<td>" + c.band + "</td>" +
        "<td>" + U.fmtHz(c.center_hz || channelCenter(c)) + "</td>" +
        "<td>" + U.fmtDb(c.excess_db) + "</td>" +
        '<td><span class="bar"><i style="width:' + (pct * 100).toFixed(0) + "%;background:" +
        U.levelColor(c.level) + '"></i></span></td>' +
        "<td>" + U.fmtPct(c.occupancy) + "</td>" +
        "<td>" + (c.peak_db || 0).toFixed(0) + "</td>" +
        "<td>" + U.fmtPct(c.coverage) + "</td>" +
        '<td><span class="tag ' + U.levelClass(c.level) + '">' + c.level + "</span></td>";
      body.appendChild(tr);
    });

    // ---- 3D 柱状图 ----
    if (bars3d && bars3d.ok) bars3d.setChannels(sorted.map((c) => ({
      channel: c.channel, band: c.band, excess_db: c.excess_db, level: c.level,
    })));

    // ---- 判读说明 ----
    const notes = root.querySelector("#wifi-notes");
    notes.innerHTML = "";
    const lines = [
      "分档：" + (payload.levels || []).map((l) => "≥" + l.min_db + " dB → " + l.label).join("，") +
        "（相对同频段最安静信道）。",
      "为什么不比绝对门限：实板噪声底自身抖动 ±10~18 dB，绝对门限会把纯噪声判成忙。",
      "扫描：" + (payload.scan_plan.windows_24) + " 个 2.4G 窗 + " + (payload.scan_plan.windows_5) +
        " 个 5G 窗，20MHz 窗 / 15MHz 步进重叠 5MHz；丢弃整窗 0 码的帧 " + (payload.dropped_frames || 0) + " 帧。",
      "边界：分时快照，非连续实时；功率码是相对 dB 码，不是 dBm；跨增益不可比（顶部固定增益档后再比）。",
    ];
    lines.forEach((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      notes.appendChild(li);
    });
  }

  function channelCenter(c) {
    if (c.band === "2.4G") return (2412 + 5 * (c.channel - 1)) * 1e6;
    return (5000 + 5 * c.channel) * 1e6;
  }

  function setCard(sel, text, channel, band) {
    const value = root.querySelector(sel);
    const note = root.querySelector(sel + "-note");
    value.textContent = text;
    if (!channel) {
      value.className = "card__value bad";
      note.textContent = "没有可推荐信道（覆盖率不足或全被占）";
      return;
    }
    // 推荐信道自身的档位决定颜色：空闲=绿，轻度=黄，拥挤及以上=红
    const target = lastPayload && lastPayload.channels
      ? lastPayload.channels.find((c) => c.channel === channel && c.band === band)
      : null;
    const level = target ? target.level : "";
    value.className = "card__value " + (level === "空闲" ? "good" : level === "轻度" ? "warn" : "bad");
    note.textContent = target
      ? "比最静信道 " + U.fmtDb(target.excess_db) + " · " + level
      : "等待样本";
  }

  return {
    onEnter,
    onLeave,
    onTables,
    probe: () => ({ bars3d: !!(bars3d && bars3d.ok), frames: bars3d ? bars3d.frames : 0 }),
  };
})();