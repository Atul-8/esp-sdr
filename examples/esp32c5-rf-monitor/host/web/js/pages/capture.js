/* pages/capture.js —— 可疑信号抓拍页：卡片 + 抓档清单 + 过滤计数 + 杂散清单 + 门槛表。
 *
 * 数据来自 modes/capture.py 的 web_payload()（kind == "capture"，经 host/session.py 打包）。
 *
 * 三条界面纪律（跟 ble 页一致）：
 *   1. 门槛一律从 payload.thresholds 取，前端不写死任何数字 —— 改判据只改模块；
 *   2. 模块产出的中文判词（reason / notes）原样显示，不翻英文（跟 CLI 对得上）；
 *   3. 「已存证」不等于「确认是摄像头」：这条边界永远显示在页面顶部，不能藏。
 */
window.Pages = window.Pages || {};

window.Pages.capture = (function () {
  let root = null;
  let lastPayload = null;

  //: 门槛字段 → 词典后缀（只用于两列表头；值永远来自 payload.thresholds）
  const TH_LABELS = {
    alert_db: "alert",
    narrow_alert_db: "narrow",
    narrow_max_hz: "narrowMax",
    busy_fill: "busy",
    min_span_s: "minSpan",
    frame_min_gap_s: "frameGap",
    occ_excess_db: "occ",
    hop_fill: "hopFill",
    steady_fill: "steadyFill",
    min_frames: "minFrames",
    min_width_hz: "minWidth",
    max_width_hz: "maxWidth",
    retrigger_s: "retrigger",
    max_captures: "maxCaptures",
    capture_samples: "samples",
    capture_rate_code: "rate",
  };

  function fmtHz(hz) {
    if (!hz) return "—";
    return (hz / 1e6).toFixed(hz % 1e6 === 0 ? 0 : 3) + " MHz";
  }

  function fmtSamples(n) {
    return Number(n || 0).toLocaleString("en-US");
  }

  function onEnter(host) {
    root = host;
    const run = host.querySelector("#cap-audit-run");
    if (run && !run.dataset.bound) {
      run.dataset.bound = "1";
      run.addEventListener("click", runAudit);
    }
    if (lastPayload) render(lastPayload);
    // 进页就跑一次（只读、便宜）；按钮用于「数据变了再刷一次」
    runAudit();
  }

  function onLeave() {
    root = null;
  }

  /* 一键体检：Bridge.audit 只读当前会话，不新抓档（正式一分钟报告跑 python -m app.audit）。 */
  function runAudit() {
    if (!root) return;
    const btn = root.querySelector("#cap-audit-run");
    btn.disabled = true;
    Bridge.call("audit")
      .then((data) => {
        // Bridge.call 已经把 JSON 解成对象了；只有老路径才给字符串
        if (typeof data === "string") {
          try { data = JSON.parse(data || "{}"); } catch (err) { data = { ok: false, headline: T("cap.parseFailed"), why: String(err) }; }
        }
        renderAudit(data || {});
      })
      .catch((err) => renderAudit({ ok: false, headline: T("cap.audit.fail"), why: String(err) }))
      .then(() => { btn.disabled = false; });
  }

  function renderAudit(data) {
    if (!root) return;
    const panel = root.querySelector("#cap-audit-panel");
    if (panel) panel.dataset.has = data && data.ok ? "1" : "0";
    root.querySelector("#cap-audit-headline").textContent = data.headline || "—";
    root.querySelector("#cap-audit-why").textContent = data.why || "";

    const body = root.querySelector("#cap-audit-rows");
    body.innerHTML = "";
    const rows = data.sources || [];
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="5" class="muted">' + esc(T("cap.audit.empty")) + "</td></tr>";
    } else {
      rows.forEach((row) => {
        const tr = document.createElement("tr");
        const cls = U.levelClass(row.level);
        tr.innerHTML =
          "<td>" + esc(row.source || "") + "</td>" +
          "<td>" + esc(row.kind || "") + "</td>" +
          "<td>" + esc(row.freq_mhz ? Number(row.freq_mhz).toFixed(1) + " MHz" : "-") + "</td>" +
          '<td><span class="tag ' + cls + '">' + esc(row.level || "") + "</span></td>" +
          '<td class="muted">' + esc(row.detail || "") + "</td>";
        body.appendChild(tr);
      });
    }

    const box = root.querySelector("#cap-audit-boundary");
    box.innerHTML = "";
    const items = data.boundaries || [];
    if (!items.length) {
      box.innerHTML = '<li class="muted">' + esc(T("cap.card.wait")) + "</li>";
    } else {
      items.forEach((text) => {
        const li = document.createElement("li");
        li.textContent = text;
        box.appendChild(li);
      });
    }
  }

  function pickPayload(tables) {
    if (!tables || !tables.plugins) return null;
    const item = tables.plugins.find((p) => p.payload && p.payload.kind === "capture");
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
    const scan = payload.scan || {};
    const band = payload.band || {};
    const captures = payload.captures || [];

    // ---- 顶部边界：这句话必须一直在 ----
    const warn = root.querySelector("#cap-warn");
    warn.hidden = false;
    warn.textContent = T("cap.warn", {
      lo: ((band.lo_hz || 0) / 1e6).toFixed(0),
      hi: ((band.hi_hz || 0) / 1e6).toFixed(0),
    });

    // ---- 卡片 ----
    root.querySelector("#cap-records").textContent = String(captures.length);
    root.querySelector("#cap-records-note").textContent = T("cap.card.recordsNote", {
      max: Math.round(th.max_captures || 20),
      dir: payload.dir ? String(payload.dir).split(/[\\/]/).pop() : T("cap.card.noDir"),
    });

    root.querySelector("#cap-triggers").textContent = String(payload.triggers || 0);
    root.querySelector("#cap-triggers-note").textContent = T("cap.card.triggersNote", {
      analyses: payload.analyses || 0,
    });

    root.querySelector("#cap-pending").textContent = String(payload.pending || 0);
    root.querySelector("#cap-pending-note").textContent = T("cap.card.pendingNote");

    const span = Number(payload.span_s || 0);
    root.querySelector("#cap-span").textContent = span.toFixed(1) + " s";
    root.querySelector("#cap-span-note").textContent = T("cap.card.spanNote", {
      need: Number(th.min_span_s || 3).toFixed(0),
      gap: Number(th.frame_min_gap_s || 0.2).toFixed(1),
    });

    root.querySelector("#cap-analyses").textContent = String(payload.analyses || 0);
    root.querySelector("#cap-analyses-note").textContent = T("cap.card.analysesNote", {
      min: Number(th.analyze_min_interval_s || 0.9).toFixed(1),
      frames: payload.frames || 0,
      dropped: payload.dropped_frames || 0,
    });

    root.querySelector("#cap-sample").textContent = T("cap.sample", {
      windows: scan.windows || 0,
      bw: ((scan.bw_hz || 0) / 1e6).toFixed(0),
      step: ((scan.step_hz || 0) / 1e6).toFixed(0),
      frames: payload.frames || 0,
      span: span.toFixed(1),
    });

    // ---- 抓档清单 ----
    const body = root.querySelector("#cap-rows");
    body.innerHTML = "";
    if (!captures.length) {
      body.innerHTML = '<tr><td colspan="10" class="muted">' + esc(T("cap.waitData")) + "</td></tr>";
    } else {
      captures.forEach((c) => {
        const tr = document.createElement("tr");
        const conf = Number(c.confidence || 0);
        tr.innerHTML =
          "<td>" + esc(c.id || "") + "</td>" +
          "<td>" + esc(fmtHz(c.freq_hz)) + "</td>" +
          "<td>" + esc(fmtSamples(c.samples)) + "</td>" +
          "<td>" + esc("CAP" + (c.bits === 20 ? "20" : "16") + " · " +
            ((c.sample_rate_hz || 0) / 1e6).toFixed(0) + " MS/s") + "</td>" +
          "<td>" + esc(Number(c.duration_us || 0).toFixed(0)) + " µs</td>" +
          '<td><span class="tag ' + (c.label === "unknown" ? "tag--heavy" : "tag--idle") + '">' +
            esc(c.label || "") + "</span></td>" +
          "<td>" + esc(conf.toFixed(2)) + "</td>" +
          "<td>" + esc(String(c.peak === undefined ? "—" : c.peak)) + "</td>" +
          '<td class="muted">' + esc(c.reason || "") + "</td>" +
          "<td>" + esc((c.path || "").split(/[\\/]/).pop() || "—") + "</td>";
        body.appendChild(tr);
      });
    }

    // ---- 过滤计数表 ----
    const filters = [
      ["skipped_repeat", "cap.filter.repeat", "cap.filter.repeatWhy"],
      ["skipped_budget", "cap.filter.budget", "cap.filter.budgetWhy"],
      ["skipped_edge", "cap.filter.edge", "cap.filter.edgeWhy"],
      ["skipped_weak", "cap.filter.weak", "cap.filter.weakWhy"],
      ["skipped_busy", "cap.filter.busy", "cap.filter.busyWhy"],
      ["skipped_short", "cap.filter.short", "cap.filter.shortWhy"],
      ["skipped_spur", "cap.filter.spur", "cap.filter.spurWhy"],
    ];
    const fbody = root.querySelector("#cap-filter-rows");
    fbody.innerHTML = "";
    filters.forEach(([key, label, why]) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + esc(T(label)) + "</td>" +
        "<td>" + esc(String(payload[key] || 0)) + "</td>" +
        '<td class="muted">' + esc(T(why, gateVars(th))) + "</td>";
      fbody.appendChild(tr);
    });

    // ---- 杂散清单 ----
    const sbody = root.querySelector("#cap-spur-rows");
    sbody.innerHTML = "";
    const spurs = payload.spurs || [];
    if (!spurs.length) {
      sbody.innerHTML = '<tr><td colspan="5" class="muted">' + esc(T("cap.spur.none")) + "</td></tr>";
    } else {
      spurs.forEach((s) => {
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" + esc(Number(s.freq_mhz || 0).toFixed(1)) + " MHz</td>" +
          "<td>" + esc(Number(s.width_khz || 0).toFixed(0)) + " kHz</td>" +
          "<td>" + esc(((Number(s.fill || 0)) * 100).toFixed(0)) + " %</td>" +
          "<td>" + esc(T("cap.filter.codes", { n: Number(s.peak_excess_db || 0).toFixed(0) })) + "</td>" +
          "<td>" + esc(Number(s.duration_s || 0).toFixed(1)) + " s</td>";
        sbody.appendChild(tr);
      });
    }

    // ---- 门槛表（值全部来自模块） ----
    const tbody = root.querySelector("#cap-th-rows");
    tbody.innerHTML = "";
    Object.keys(TH_LABELS).forEach((key) => {
      if (th[key] === undefined) return;
      const value = th[key];
      let shown;
      if (key.endsWith("_hz")) shown = fmtHz(value);
      else if (key.endsWith("_s")) shown = Number(value).toFixed(value < 10 ? 2 : 0) + " s";
      else shown = String(value);
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + esc(T("cap.th." + TH_LABELS[key])) + '<span class="muted"> · ' +
        esc(T("cap.why." + key)) + "</span></td>" +
        "<td>" + esc(shown) + "</td>";
      tbody.appendChild(tr);
    });

    // ---- notes（模块原文） ----
    const notes = root.querySelector("#cap-notes");
    notes.innerHTML = "";
    const items = (payload.notes || []).slice();
    (payload.errors || []).forEach((e) => items.push("[" + T("cap.errors") + "] " + e));
    if (!items.length) {
      notes.innerHTML = '<li class="muted">' + esc(T("cap.card.wait")) + "</li>";
    } else {
      items.forEach((text) => {
        const li = document.createElement("li");
        li.textContent = text;
        notes.appendChild(li);
      });
    }
  }

  function gateVars(th) {
    return {
      fr: Number(th.min_frames || 8).toFixed(0),
      fill: ((Number(th.steady_fill || 0.75)) * 100).toFixed(0),
      busy: ((Number(th.busy_fill || 0.35)) * 100).toFixed(0),
      span: Number(th.min_span_s || 3).toFixed(0),
      retrig: Number(th.retrigger_s || 120).toFixed(0),
      max: Math.round(th.max_captures || 20),
      alert: Number(th.alert_db || 20).toFixed(0),
      narrow: Number(th.narrow_alert_db || 30).toFixed(0),
      narrowMax: ((Number(th.narrow_max_hz || 1e6)) / 1e6).toFixed(0),
      spur: "0.5",
    };
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
      audit: !!(root && root.querySelector("#cap-audit-headline") && root.querySelector("#cap-audit-headline").textContent !== "—"),
      rows: lastPayload ? (lastPayload.captures || []).length : 0,
      spurs: lastPayload ? (lastPayload.spurs || []).length : 0,
      notes: lastPayload ? (lastPayload.notes || []).length : 0,
      triggers: lastPayload ? lastPayload.triggers || 0 : 0,
      span: lastPayload ? lastPayload.span_s || 0 : 0,
      kind: lastPayload ? lastPayload.kind : null,
    }),
  };
})();