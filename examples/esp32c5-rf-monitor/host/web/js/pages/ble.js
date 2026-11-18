/* pages/ble.js —— BLE 广播信道风险评估页：卡片 + 证据链 + 广播信道表 + 3D + 建议。
 *
 * 数据来自 modes/ble_risk.py 的 web_payload()（kind == "ble_risk"，经 host/session.py 打包）。
 * 判据门槛全部从 payload.thresholds 里取 —— 前端不写死任何数字：
 * 改判据只改模块，网页跟着变（上一版把 8 dB / 50% 写进 JS，改判据就两边不一致）。
 *
 * 界面规矩：判词、level 档位、hints 正文都是模块产出的**中文原文**，界面只翻外壳与结构化字段；
 * 不做整块 I18N.apply()，也不把模块判词翻译成英文（会跟 CLI 对不上）。
 */
window.Pages = window.Pages || {};

window.Pages.ble = (function () {
  let root = null;
  let bars3d = null;
  let lastPayload = null;
  let resizeTimer = null;

  const RISK_STYLE = {
    "无明显风险": "ok",
    "轻微干扰": "light",
    "蓝牙设备密度型": "heavy",
    "WiFi 压制型": "heavy",
    "混合型": "heavy",
    "复杂噪音（多源混合）": "flat",
    "宽带常开/形状疑似": "flat",
    "无数据": "flat",
  };

  function onEnter(host) {
    root = host;
    bars3d = new Three3D.ChannelBars(host.querySelector("#ble-3d"));
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
    const item = tables.plugins.find((p) => p.payload && p.payload.kind === "ble_risk")
      || tables.plugins.find((p) => p.payload && p.payload.risk_type);
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
    const th = payload.thresholds || {};
    const ev = payload.evidence || {};
    const adv = (payload.adv || []).slice();
    const data = payload.data_band || {};
    const plan = payload.scan_plan || {};

    // ---- 前端余量告警（底噪被增益抬起 ⇒ 占用率不可比） ----
    const warn = root.querySelector("#ble-warn");
    const text = payload.headroom_warning || "";
    warn.hidden = !text;
    warn.textContent = text ? T("ble.warn.title") + "：" + text : "";

    // ---- 卡片 ----
    const risk = root.querySelector("#risk-type");
    risk.textContent = payload.risk_type || "—";
    risk.dataset.risk = RISK_STYLE[payload.risk_type] || "flat";
    root.querySelector("#risk-note").textContent = T("ble.card.score", {
      score: (payload.score || 0).toFixed(1),
      level: U.levelText(payload.level || "空闲"),
    });

    root.querySelector("#wideband").textContent = U.fmtPct(payload.wideband || 0, 0);
    root.querySelector("#wideband-note").textContent = T("ble.card.widebandNote", {
      db: (th.wideband_excess_db !== undefined ? th.wideband_excess_db : 10).toFixed(0),
      n: (payload.wideband_method || {}).windows || 0,
    });

    const contrast = payload.contrast_db;
    root.querySelector("#contrast").textContent =
      (contrast === undefined || contrast === null ? "—" : Number(contrast).toFixed(1) + " 码");
    root.querySelector("#contrast-note").textContent = T("ble.card.contrastNote", {
      thr: (th.contrast_db !== undefined ? th.contrast_db : 8).toFixed(0),
    });

    root.querySelector("#adv-activity").textContent = U.fmtPct(payload.adv_activity || 0, 0);
    root.querySelector("#adv-note").textContent = T("ble.card.advNote");
    root.querySelector("#data-band").textContent = (data.active_per_frame || 0).toFixed(1) + " / " + (data.slots || 0);
    root.querySelector("#data-note").textContent = T("ble.card.dataNote", {
      active: (data.active_per_frame || 0).toFixed(1),
      slots: data.slots || 0,
      cov: U.fmtPct(data.coverage || 0, 0),
    });

    root.querySelector("#ble-sample").textContent = T("ble.sample", {
      frames: payload.frames || 0,
      span: (payload.span_s || 0).toFixed(1),
      dropped: payload.dropped_frames || 0,
      windows: plan.windows || 0,
      bw: ((plan.bw_hz || 0) / 1e6).toFixed(0),
      step: ((plan.step_hz || 0) / 1e6).toFixed(0),
    });

    // ---- 证据链：判词就是按这张表自上而下走的 ----
    const evBody = root.querySelector("#ev-rows");
    evBody.innerHTML = "";
    const minFrames = th.min_adv_frames !== undefined ? th.min_adv_frames : 60;
    const rows = [
      [T("ble.ev.adv"), U.fmtPct(ev.adv_activity || 0, 0), (ev.adv_activity || 0) >= 0.10],
      [T("ble.ev.shallow"), U.fmtPct(ev.shallow || 0, 0) + " / " + Math.round(ev.adv_frames || 0) + " 帧", (ev.shallow || 0) >= (th.light_ratio !== undefined ? th.light_ratio : 0.1) && (ev.adv_frames || 0) >= minFrames],
      [T("ble.ev.wide"), U.fmtPct(ev.wideband || 0, 0), (ev.wideband || 0) >= (th.wideband_frac !== undefined ? th.wideband_frac : 0.5)],
      [T("ble.ev.contrast", { thr: (th.contrast_db !== undefined ? th.contrast_db : 8).toFixed(0) }), Number(ev.contrast_db || 0).toFixed(1) + " 码", (ev.contrast_db || 0) >= (th.contrast_db !== undefined ? th.contrast_db : 8)],
      [T("ble.ev.flat"), String(Math.round(ev.slots_persistent || 0)), (ev.slots_persistent || 0) >= 1],
      [T("ble.ev.rise"), Number(ev.rise_floor || 0).toFixed(1) + " 码", null],
    ];
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      const mark = row[2] === null ? "—" : row[2] ? T("ble.ev.yes") : T("ble.ev.no");
      const cls = row[2] === null ? "tag tag--idle" : row[2] ? "tag tag--heavy" : "tag tag--none";
      tr.innerHTML =
        "<td>" + esc(row[0]) + "</td>" +
        "<td>" + esc(row[1]) + "</td>" +
        '<td><span class="' + cls + '">' + esc(mark) + "</span></td>";
      evBody.appendChild(tr);
    });

    // ---- 广播信道表（按活动降序；与 CLI 同序） ----
    const body = root.querySelector("#ble-rows");
    body.innerHTML = "";
    if (!adv.length) {
      body.innerHTML = '<tr><td colspan="9" class="muted">' + esc(T("ble.waitData")) + "</td></tr>";
    } else {
      const sorted = adv.slice().sort((a, b) => (b.activity || 0) - (a.activity || 0) || a.channel - b.channel);
      sorted.forEach((c) => {
        const tr = document.createElement("tr");
        const pct = Math.max(0, Math.min(1, c.activity || 0));
        tr.innerHTML =
          "<td>BLE " + c.channel + "</td>" +
          "<td>" + U.fmtHz(c.center_hz) + "</td>" +
          "<td>" + U.fmtPct(c.coverage || 0, 0) + "</td>" +
          "<td>" + U.fmtPct(c.activity || 0, 0) + "</td>" +
          '<td><span class="bar"><i style="width:' + (pct * 100).toFixed(0) + "%;background:" +
          U.levelColor(c.level) + '"></i></span></td>' +
          "<td>" + U.fmtDb(c.excess_db) + "</td>" +
          "<td>" + (c.prominence_db || 0).toFixed(0) + " dB</td>" +
          "<td>" + (c.peak_db || 0).toFixed(0) + "</td>" +
          '<td><span class="tag ' + U.levelClass(c.level) + '">' + esc(U.levelText(c.level)) + "</span></td>";
        body.appendChild(tr);
      });
    }

    // ---- 3D：三个广播信道的相对抬升 ----
    if (bars3d && bars3d.ok) {
      bars3d.setChannels(adv.map((c) => ({
        channel: c.channel,
        band: "2.4G",
        excess_db: c.excess_db,
        level: c.level,
      })));
    }

    // ---- 2.4G 在用信道 ----
    const wifi = root.querySelector("#ble-wifi");
    wifi.innerHTML = "";
    const busy = payload.wifi_busy || [];
    const channels = payload.wifi_channels || [];
    if (!busy.length) {
      wifi.innerHTML = '<li class="muted">' + esc(T("ble.wifi.none")) + "</li>";
    } else {
      busy.forEach((ch) => {
        const info = channels.find((c) => c.channel === ch) || {};
        const li = document.createElement("li");
        const parts = [
          "ch" + ch,
          "占空 " + U.fmtPct(info.fill || 0, 0),
          "比底噪 " + U.fmtDb(info.excess_db || 0),
          "覆盖 " + (info.covered_mhz === undefined ? "—" : info.covered_mhz) + "/18 MHz",
        ];
        // 20 MHz WiFi 完全盖住某个广播信道 = 共存硬冲突，单独标出来
        const hits = (payload.overlap || []).filter(
          (o) => o.wifi_channel === ch && o.wifi_busy && (o.fraction || 0) >= 0.9
        );
        if (hits.length) parts.push("硬冲突 BLE " + hits.map((o) => o.ble_channel).join("/"));
        li.textContent = parts.join(" · ");
        wifi.appendChild(li);
      });
    }

    // ---- 建议 + 说明（模块产出的中文原文，界面不翻） ----
    fillList(root.querySelector("#ble-hints"), payload.hints || []);
    const notes = (payload.notes || []).slice();
    fillList(root.querySelector("#ble-notes"), notes.length ? notes : [T("ble.sub")]);
  }

  function fillList(box, items) {
    box.innerHTML = "";
    if (!items.length) {
      box.innerHTML = '<li class="muted">' + esc(T("ble.card.wait")) + "</li>";
      return;
    }
    items.forEach((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      box.appendChild(li);
    });
  }

  function esc(text) {
    return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  return {
    onEnter,
    onLeave,
    onTables,
    onLang,
    probe: () => ({
      bars3d: !!(bars3d && bars3d.ok),
      frames: bars3d ? bars3d.frames : 0,
      risk: lastPayload ? lastPayload.risk_type : null,
      contrast: lastPayload ? lastPayload.contrast_db : null,
    }),
  };
})();