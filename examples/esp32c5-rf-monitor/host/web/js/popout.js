/*
 * popout.js —— 任意面板双击弹出预览（铺满窗口 / 自定义缩放）。
 *
 * 用起来：**双击任意 `.panel`**（或点面板右上角的 ⤢），面板会搬到全窗口的浮层里；
 * 工具栏可以 −/+ 缩放（75%…300%）、重置、全屏、关闭；Esc 也关。
 *
 * 为什么不是 window.open：QWebEngine 里开新窗口要 Python 侧再接
 * QWebEnginePage.createWindow，而且跨窗口传数据还得再搭一层桥。这里用**同一文档内
 * 的浮层 + 原地搬 DOM**，好处是：
 *   · canvas / WebGL 的上下文跟着 DOM 走，搬进搬出都不丢内容，不用重建；
 *   · 页面自己的 resize 钩子就能重画（缩放时把 --fit-scale 换成新的倍率，
 *     再派发一次 window resize），大图是**按新尺寸重新渲染**的，不是拿小图拉伸放大。
 *
 * 缩放的实现：
 *   · 面板宽度 = 倍率 × 100%（浮层可滚动），
 *   · 面板上设 `--fit-scale: 倍率`，`U.fitCanvas()` 与 3D 容器高度都乘这个倍率，
 *     所以横纵等比放大，坐标轴文字不会被拉变形。
 */
window.Popout = (function () {
  const ZOOMS = [0.75, 1, 1.25, 1.5, 2, 3];
  const OVERLAY_CLASS = "popout";

  let overlay = null;
  let stage = null;
  let panel = null;
  let home = null;        // {parent, next} 原位置，关闭时搬回去
  let zoom = 1;
  let resizeTimer = null;
  let bar = null;

  // ---- 装饰：每个面板右上角一个 ⤢ ------------------------------------------
  function decorate(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll(".panel").forEach((node) => {
      if (node.querySelector(":scope > .panel__zoom")) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "panel__zoom";
      btn.textContent = "⤢";
      btn.dataset.i18nTitle = "popout.dblHint";
      btn.title = T("popout.dblHint");
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        toggle(node);
      });
      node.appendChild(btn);
    });
  }

  let observer = null;
  let decorateTimer = null;

  function watch(root) {
    if (!root || observer) return;
    observer = new MutationObserver((records) => {
      const grew = records.some((rec) => rec.addedNodes && rec.addedNodes.length);
      if (!grew || decorateTimer) return;
      decorateTimer = setTimeout(() => {
        decorateTimer = null;
        decorate(root);
      }, 500);
    });
    observer.observe(root, { childList: true, subtree: true });
  }

  function attach(root) {
    if (!root || root.__popoutBound) {
      decorate(root);
      return;
    }
    root.__popoutBound = true;
    root.addEventListener("dblclick", (ev) => {
      // 表格里双击选中文字是常事，别一选就弹
      const sel = window.getSelection && String(window.getSelection());
      const panelNode = ev.target.closest && ev.target.closest(".panel");
      if (!panelNode || !root.contains(panelNode)) return;
      if (sel && sel.length > 1) return;
      ev.preventDefault();
      toggle(panelNode);
    });
    decorate(root);
    watch(root);
  }

  // ---- 浮层 ---------------------------------------------------------------
  function build() {
    overlay = document.createElement("div");
    overlay.className = OVERLAY_CLASS;
    overlay.innerHTML =
      '<div class="popout__bar">' +
      '  <span class="popout__title"></span>' +
      '  <span class="popout__hint" data-i18n="popout.hint">双击任意面板可放大预览；Esc 关闭</span>' +
      '  <span class="popout__tools">' +
      '    <span class="popout__label" data-i18n="popout.zoom">缩放</span>' +
      '    <button type="button" class="btn btn--mini" data-act="out" title="-">−</button>' +
      '    <span class="popout__val">100%</span>' +
      '    <button type="button" class="btn btn--mini" data-act="in" title="+">+</button>' +
      '    <button type="button" class="btn btn--mini" data-act="reset" data-i18n="popout.reset">重置</button>' +
      '    <button type="button" class="btn btn--mini" data-act="full" data-i18n="popout.full">全屏</button>' +
      '    <button type="button" class="btn btn--mini btn--ghost" data-act="close" data-i18n="popout.close">关闭</button>' +
      '  </span>' +
      "</div>" +
      '<div class="popout__stage"></div>';
    document.body.appendChild(overlay);
    stage = overlay.querySelector(".popout__stage");
    bar = overlay.querySelector(".popout__bar");
    overlay.addEventListener("click", (ev) => {
      const act = ev.target.dataset && ev.target.dataset.act;
      if (act) {
        ev.preventDefault();
        if (act === "in") step(1);
        else if (act === "out") step(-1);
        else if (act === "reset") setZoom(1);
        else if (act === "full") fullscreen();
        else if (act === "close") close();
      }
    });
    window.addEventListener("keydown", onKey);
    window.addEventListener("langchange", relabel);
    I18N.apply(bar);  // 只翻工具栏，别碰搬进来的面板（里面有动态内容）
  }

  function open(node) {
    if (!node || panel === node) return;
    if (panel) close();
    if (!overlay) build();
    panel = node;
    home = { parent: node.parentNode, next: node.nextSibling };
    stage.appendChild(node);
    node.classList.add("panel--popped");
    overlay.querySelector(".popout__title").textContent = titleOf(node);
    overlay.hidden = false;
    setZoom(zoom, true);
    refit();
  }

  function close() {
    if (!overlay || !panel) return;
    panel.classList.remove("panel--popped");
    panel.style.width = "";
    panel.style.removeProperty("--fit-scale");
    if (home && home.parent) home.parent.insertBefore(panel, home.next || null);
    overlay.hidden = true;
    const was = panel;
    panel = null;
    home = null;
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    refit();
    if (was) was.scrollTop = 0;
  }

  function toggle(node) {
    if (panel === node) close();
    else open(node);
  }

  function titleOf(node) {
    const t = node.querySelector(".panel__title");
    return t ? t.textContent.trim() : "panel";
  }

  // ---- 缩放 ---------------------------------------------------------------
  function setZoom(next, quiet) {
    zoom = Math.min(ZOOMS[ZOOMS.length - 1], Math.max(ZOOMS[0], next));
    if (panel) {
      panel.style.width = zoom * 100 + "%";
      panel.style.setProperty("--fit-scale", String(zoom));
    }
    if (overlay) {
      const val = overlay.querySelector(".popout__val");
      if (val) val.textContent = Math.round(zoom * 100) + "%";
    }
    if (!quiet) refit();
  }

  function step(dir) {
    let i = 0;
    let best = Infinity;
    ZOOMS.forEach((z, k) => {
      const d = Math.abs(z - zoom);
      if (d < best) { best = d; i = k; }
    });
    setZoom(ZOOMS[Math.min(ZOOMS.length - 1, Math.max(0, i + dir))]);
  }

  /** 尺寸变了：让页面自己的 resize 钩子重画（画布按新尺寸重新渲染，不是拉伸） */
  function refit() {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      resizeTimer = null;
      window.dispatchEvent(new Event("resize"));
    }, 120);
  }

  function fullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
      return;
    }
    const root = document.documentElement;
    const req = root.requestFullscreen && root.requestFullscreen();
    if (req && req.catch) {
      // QWebEngine 默认忽略全屏请求（要宿主 accept）；失败就退回「铺满窗口」
      req.catch(() => {
        overlay.classList.toggle("popout--max");
        refit();
      });
    }
  }

  function onKey(ev) {
    if (!panel) return;
    if (ev.key === "Escape") {
      ev.preventDefault();
      close();
    }
  }

  function relabel() {
    document.querySelectorAll(".panel__zoom").forEach((b) => {
      b.title = T("popout.dblHint");
    });
    if (!overlay) return;
    I18N.apply(bar);
    if (panel) overlay.querySelector(".popout__title").textContent = titleOf(panel);
  }

  /** 给 --selftest 用：开 → 放大 → 关，把三段的实测值记到 window.__popout。 */
  function selftest() {
    const out = { steps: [] };
    window.__popout = out;
    const host = document.getElementById("page-container");
    const target = host && host.querySelector(".panel");
    if (!target) {
      out.error = "no panel";
      out.done = true;
      return out;
    }
    const canvasOf = (node) => node.querySelector("canvas");
    open(target);
    const c0 = canvasOf(target);
    out.open = {
      overlay: !!document.querySelector(".popout"),
      opened: target.classList.contains("panel--popped"),
      inOverlay: target.parentElement === stage,
      width: c0 ? Math.round(c0.getBoundingClientRect().width) : Math.round(target.getBoundingClientRect().width),
      zoom: zoom,
    };
    setZoom(1.5);
    setTimeout(() => {
      const c1 = canvasOf(target);
      out.zoom = {
        zoom: zoom,
        width: c1 ? Math.round(c1.getBoundingClientRect().width) : Math.round(target.getBoundingClientRect().width),
        fitScale: target.style.getPropertyValue("--fit-scale"),
      };
      close();
      setTimeout(() => {
        out.close = {
          overlay: !!(overlay && !overlay.hidden),  // 关闭后元素还在，但必须 hidden
          hidden: overlay ? overlay.hidden : true,
          backHome: !target.classList.contains("panel--popped") && host.contains(target),
        };
        out.done = true;
      }, 500);
    }, 700);
    return out;
  }

  return {
    attach,
    open,
    close,
    toggle,
    setZoom,
    selftest,
    get current() { return panel; },
    get zoom() { return zoom; },
  };
})();