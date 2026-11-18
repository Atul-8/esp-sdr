/* pages/wifi.js —— WiFi 信道体检：卡片 + 表格 + 3D 柱状图。
 *
 * 数据来自 modes/wifi_check.py 的 web_payload()（经 host/session.py 打包）。
 * 分档口径与 CLI 完全一致，前端不做二次判定；这里只负责**显示**和**翻译**。
 */
window.Pages = window.Pages || {};

window.Pages.wifi = (function () {
  let root = null;
  let bars3d = null;
  let lastPayload = null;
  let resizeTimer = null;

  function onEnter(host) {
    root = host;
    bars3d = new Three3D.ChannelBars(host.querySelector("#wifi-3d"));
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

  function onLang() {
    if (root && lastPayload) render(lastPayload);
  }

  function render(payload) {
    if (!root) return;
    const channels = payload.channels || [];
    const avoided = (payload.avoided && (payload.avoided["2.4G"] || []).concat(payload.avoided["5G"])) || [];

    // ---- 卡片 ----
    setCard("#rec24", payload.recommend_24, "2.4G");
    setCard("#rec5", payload.recommend_5, "5G");
    root.querySelector("#avoid").textContent = avoided.length ? avoided.map((c) => "ch" + c).join(" / ") : T("wifi.card.none");
    const interf = payload.interferers || [];
    root.querySelector("#interf").textContent = interf.length ? interf.join(" / ") : T("wifi.card.none");

    const plan = payload.scan_plan || {};
    root.querySelector("#wifi-sample").textContent = T("wifi.sample", {
      frames: payload.frames || 0,
      span: (payload.span_s || 0).toFixed(1),
      bw: ((plan.bw_hz || 0) / 1e6).toFixed(0),
      step: ((plan.step_hz || 0) / 1e6).toFixed(0),
    });

    // ---- 表格（按相对抬升降序；与 CLI 同序） ----
    const body = root.querySelector("#wifi-rows");
    body.innerHTML = "";
    if (!channels.length) {
      body.innerHTML = '<tr><td colspan="10" class="muted">' + esc(T("wifi.waitData")) + "</td></tr>";
    } else {
      const sorted = channels.slice().sort((a, b) => b.excess_db - a.excess_db || a.channel - b.channel);
      const worst = Math.max.apply(null, sorted.map((c) => Math.max(0, c.excess_db)).concat([1]));
      sorted.forEach((c, i) => {
        const tr = document.createElement("tr");
        const pct = Math.max(0, Math.min(1, c.excess_db / worst));
        tr.innerHTML =
          "<td>" + (i + 1) + "</td>" +
          "<td>ch" + c.channel + "</td>" +
          "<td>" + esc(c.band) + "</td>" +
          "<td>" + U.fmtHz(c.center_hz || channelCenter(c)) + "</td>" +
          "<td>" + U.fmtDb(c.excess_db) + "</td>" +
          '<td><span class="bar"><i style="width:' + (pct * 100).toFixed(0) + "%;background:" +
          U.levelColor(c.level) + '"></i></span></td>' +
          "<td>" + U.fmtPct(c.occupancy) + "</td>" +
          "<td>" + (c.peak_db || 0).toFixed(0) + "</td>" +
          "<td>" + U.fmtPct(c.coverage) + "</td>" +
          '<td><span class="tag ' + U.levelClass(c.level) + '">' + esc(U.levelText(c.level)) + "</span></td>";
        body.appendChild(tr);
      });
    }

    // ---- 3D 柱状图 ----
    if (bars3d && bars3d.ok) {
      bars3d.setChannels(channels.map((c) => ({
        channel: c.channel, band: c.band, excess_db: c.excess_db, level: c.level,
      })));
    }

    // ---- 判读说明 ----
    const notes = root.querySelector("#wifi-notes");
    notes.innerHTML = "";
    const levels = (payload.levels || [])
      .map((l) => "≥" + l.min_db + " dB → " + U.levelText(l.label))
      .join("，");
    const over = (((plan.bw_hz || 0) - (plan.step_hz || 0)) / 1e6).toFixed(0);
    [
      T("wifi.notes.levels", { levels: levels }),
      T("wifi.notes.why"),
      T("wifi.notes.scan", {
        w24: plan.windows_24 || 0,
        w5: plan.windows_5 || 0,
        bw: ((plan.bw_hz || 0) / 1e6).toFixed(0),
        step: ((plan.step_hz || 0) / 1e6).toFixed(0),
        over: over,
        dropped: payload.dropped_frames || 0,
      }),
      T("wifi.notes.bounds"),
    ].forEach((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      notes.appendChild(li);
    });
  }

  function channelCenter(c) {
    if (c.band === "2.4G") return (2412 + 5 * (c.channel - 1)) * 1e6;
    return (5000 + 5 * c.channel) * 1e6;
  }

  function setCard(sel, channel, band) {
    const value = root.querySelector(sel);
    const note = root.querySelector(sel + "-note");
    const target = channel && lastPayload && lastPayload.channels
      ? lastPayload.channels.find((c) => c.channel === channel && c.band === band)
      : null;
    if (!channel) {
      value.textContent = T("wifi.card.none");
      value.className = "card__value bad";
      note.textContent = T("wifi.card.noRec");
      return;
    }
    value.textContent = "ch" + channel;
    const lk = target ? U.levelKey(target.level) : "unknown";
    value.className = "card__value " + (lk === "idle" ? "good" : lk === "light" ? "warn" : "bad");
    note.textContent = target
      ? T("wifi.card.excess", { db: U.fmtDb(target.excess_db), level: U.levelText(target.level) })
      : T("wifi.card.wait");
  }

  function esc(text) {
    return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  return {
    onEnter,
    onLeave,
    onTables,
    onLang,
    probe: () => ({ bars3d: !!(bars3d && bars3d.ok), frames: bars3d ? bars3d.frames : 0 }),
  };
})();