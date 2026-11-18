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
 * 为什么浮层挂在 `#page-container` 里（而不是 `document.body`）：面板被搬进浮层后，
 * 页面模块的 `root.querySelector("#spec-canvas")` 还得能找到它 —— 一搬出 root，
 * resize 钩子就拿到 null 直接抛错，画面停止重绘（这个坑现场踩过）。浮层是
 * `position: fixed`，挂在哪儿视觉上都一样。
 *
 *   · 面板宽度 = 倍率 × 100%（浮层可滚动），
 *   · 面板上设 `--fit-scale: 倍率`，`U.fitCanvas()` 与 3D 容器高度都乘这个倍率，
 *     所以横纵等比放大，坐标轴文字不会被拉变形。
 *
 * **高度自适应**（弹出/全屏后别留一大片空白）：
 *   · 量一次「面板按倍率渲染完还差多少高度到浮层底」，把这段余量除以会长的画布个数，
 *     写到面板的 `--fit-grow`；标了 `data-fit-grow="1"` 的画布/容器吃掉这段余量。
 *   · 两阶段：先归零 `--fit-grow` 派发 resize（页面按倍率重画）→ 200ms 后量高度 →
 *     写新余量再派发一次 resize。这样不会自己跟自己打架（量的是稳定后的布局）。
 *
 * **窗口外弹窗**（`openDocked()`）：`window.open('index.html?solo=<页面:序号>')`，
 * Python 侧 `ConsolePage.createWindow` 接住、开一个真正的独立窗口（可以拖到别的
 * 显示器），新窗口加载同一套页面 + **同一个 Bridge**，只显示那一个面板。
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
  let growTimer = null;
  let bar = null;
  let solo = false;          // 本窗口就是「独立小窗口」：关掉就没有内容了，不给关
  let fsOn = false;          // 当前是不是（由 Python 侧窗口 API 做的）全屏
  let suppress = 0;          // 自己派发合成 resize 时别自我递归

  // ---- 装饰：每个面板右上角一个 ⤢ ------------------------------------------
  function decorate(root) {
    if (!root || !root.querySelectorAll) return;
    const pageId = (window.Router && Router.current && Router.current.id) || "page";
    root.querySelectorAll(".panel").forEach((node, i) => {
      if (!node.dataset.panelKey) node.dataset.panelKey = pageId + ":" + i;
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
      '    <button type="button" class="btn btn--mini" data-act="window" data-i18n="popout.window">独立窗口</button>' +
      '    <button type="button" class="btn btn--mini btn--ghost" data-act="close" data-i18n="popout.close">关闭</button>' +
      '  </span>' +
      "</div>" +
      '<div class="popout__stage"></div>';
    // 挂到页面容器里：搬走的面板仍然是页面 root 的后代，页面钩子照常能找到画布
    (document.getElementById("page-container") || document.body).appendChild(overlay);
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
        else if (act === "window") openDocked();
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
    // 切页会把 #page-container 的 innerHTML 换掉，浮层可能已经不在文档里了
    if (!overlay || !overlay.isConnected) build();
    panel = node;
    home = { parent: node.parentNode, next: node.nextSibling };
    stage.appendChild(node);
    node.classList.add("panel--popped");
    overlay.querySelector(".popout__title").textContent = titleOf(node);
    overlay.hidden = false;
    applyChrome();
    setZoom(zoom, true);
    refit();
  }

  function close() {
    if (!overlay || !overlay.isConnected) {
      // 浮层跟着上一页一起被清掉了：只需要把状态复位，面板本身已经不存在
      overlay = null;
      stage = null;
      bar = null;
      panel = null;
      home = null;
      return;
    }
    if (!panel) return;
    if (fsOn) fullscreen(false);
    panel.classList.remove("panel--popped");
    panel.style.width = "";
    panel.style.removeProperty("--fit-scale");
    panel.style.removeProperty("--fit-grow");
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

  /**
   * 尺寸变了：让页面自己的 resize 钩子重画（画布按新尺寸重新渲染，不是拉伸），
   * 然后第二阶段把余量高度分给会长的画布 —— 全屏/放大后不该还留一大块空白。
   */
  function refit() {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      resizeTimer = null;
      setGrow(0);
      dispatchResize();
      if (!panel) return;
      if (growTimer) clearTimeout(growTimer);
      // 等页面把画布按新倍率重画完（各页的 resize 钩子是防抖的），再量高度
      growTimer = setTimeout(applyGrow, 260);
    }, 120);
  }

  /** 派发一次合成 resize：自己派发的不要反过来触发 refit（会无限递归） */
  function dispatchResize() {
    suppress += 1;
    try {
      window.dispatchEvent(new Event("resize"));
    } finally {
      suppress = 0;
    }
  }

  // 窗口真的被拉大/拉小（包括 Python 侧把窗口设为全屏）：余量高度要重算
  window.addEventListener("resize", () => {
    if (suppress) return;
    if (panel) refit();
  });

  function setGrow(px) {
    if (!panel) return;
    if (px > 0) panel.style.setProperty("--fit-grow", Math.round(px) + "px");
    else panel.style.setProperty("--fit-grow", "0px");
  }

  /** 把浮层里剩下的高度分给标了 data-fit-grow="1" 的画布/容器 */
  function applyGrow() {
    growTimer = null;
    if (!panel || !stage) return;
    const growers = panel.querySelectorAll('[data-fit-grow="1"]').length;
    if (!growers) return;
    const avail = stage.clientHeight - 24;              // stage 上下 padding
    const used = panel.getBoundingClientRect().height;
    const extra = Math.max(0, avail - used);
    const per = extra / growers;
    if (per < 2) return;                                 // 已经塞满，别来回抖
    setGrow(per);
    dispatchResize();
  }

  /**
   * 全屏。
   *
   * 合成点击（element.click() / 自动化）没有**用户手势**，Chromium 会直接拒
   * `requestFullscreen()` —— 于是「点全屏没反应，布局跟普通一模一样」。所以这里
   * 优先走 Python：`setFullscreen` 由宿主调 `showFullScreen()`，不需要手势。
   * 没有 Bridge（纯浏览器打开网页）时才退回 DOM API，再不行就退化成铺满窗口。
   */
  function fullscreen(force) {
    if (!overlay) return;
    const want = typeof force === "boolean" ? force : !fsOn;
    if (window.Bridge && Bridge.ready && Bridge.ready()) {
      Bridge.call("setFullscreen", JSON.stringify({ on: want, target: solo ? "popout" : "main" }))
        .then((res) => {
          fsOn = !!(res && res.fullscreen);
          markFull();
          refit();
        })
        .catch(() => fallbackFullscreen(want));
      return;
    }
    fallbackFullscreen(want);
  }

  /** 纯浏览器（没有 Bridge）兜底：DOM 全屏，被拒就铺满窗口 */
  function fallbackFullscreen(want) {
    if (want) {
      if (overlay && overlay.requestFullscreen) {
        overlay.requestFullscreen().catch(() => overlay.classList.add("popout--max"));
      } else if (overlay) {
        overlay.classList.add("popout--max");
      }
      fsOn = true;
    } else {
      if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(() => {});
      if (overlay) overlay.classList.remove("popout--max");
      fsOn = false;
    }
    markFull();
    refit();
  }

  function markFull() {
    if (!overlay) return;
    const btn = overlay.querySelector('[data-act="full"]');
    if (btn) btn.textContent = T(fsOn ? "popout.exitFull" : "popout.full");
  }

  /** 独立小窗口：隐藏「关闭」（关掉就只剩空白）与「独立窗口」（已经独立了） */
  function applyChrome() {
    if (!overlay) return;
    overlay.classList.toggle("popout--solo", solo);
    const closeBtn = overlay.querySelector('[data-act="close"]');
    const dockBtn = overlay.querySelector('[data-act="window"]');
    const hint = overlay.querySelector(".popout__hint");
    if (closeBtn) closeBtn.hidden = solo;
    if (dockBtn) dockBtn.hidden = solo;
    if (hint) hint.hidden = solo;
  }

  function setSolo(on) {
    solo = !!on;
    document.body.classList.toggle("solo", solo);
    applyChrome();
  }

  /** 开一个**真正的独立窗口**（Python 侧 createWindow 接住，可拖到别的显示器） */
  function openDocked(key) {
    const node = key && typeof key === "string" ? document.querySelector('[data-panel-key="' + key + '"]') : panel;
    const grower = document.querySelector('#page-container [data-fit-grow="1"]');
    const target = node || (grower && grower.closest(".panel")) || document.querySelector("#page-container .panel");
    if (!target) return "";
    const panelKey = target.dataset.panelKey || "";
    const url = "index.html?solo=" + encodeURIComponent(panelKey);
    window.open(url, "_blank", "width=1024,height=680");
    return panelKey;
  }

  /** 独立窗口里自报家门（自检探针用） */
  function openSolo(key) {
    setSolo(true);
    const node = document.querySelector('[data-panel-key="' + key + '"]');
    if (!node) {
      overlay = null;
      return false;
    }
    open(node);
    return true;
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
    applyChrome();
    markFull();
    if (panel) overlay.querySelector(".popout__title").textContent = titleOf(panel);
  }

  /** 给 --selftest 用：开 → 放大 → 关，把三段的实测值记到 window.__popout。 */
  function selftest() {
    const out = { steps: [] };
    window.__popout = out;
    const host = document.getElementById("page-container");
    // 挑一个「会长高」的面板（2D 瀑布 / 频谱），才能验到高度自适应
    const grower = host && host.querySelector('[data-fit-grow="1"]');
    const target = (grower && grower.closest(".panel")) || (host && host.querySelector(".panel"));
    if (!target) {
      out.error = "no panel";
      out.done = true;
      return out;
    }
    const canvasOf = (node) => node.querySelector("canvas");
    const sizeOf = (node, key) => (node ? Math.round(node.getBoundingClientRect()[key]) : 0);
    open(target);
    const c0 = canvasOf(target);
    out.open = {
      overlay: !!document.querySelector(".popout"),
      opened: target.classList.contains("panel--popped"),
      inOverlay: target.parentElement === stage,
      width: c0 ? sizeOf(c0, "width") : sizeOf(target, "width"),
      height: c0 ? sizeOf(c0, "height") : 0,
      zoom: zoom,
    };
    setZoom(1.5);
    setTimeout(() => {
      const c1 = canvasOf(target);
      out.zoom = {
        zoom: zoom,
        width: c1 ? sizeOf(c1, "width") : sizeOf(target, "width"),
        height: c1 ? sizeOf(c1, "height") : 0,
        fitScale: target.style.getPropertyValue("--fit-scale"),
        fitGrow: target.style.getPropertyValue("--fit-grow"),
        stageH: sizeOf(stage, "height"),
        panelH: sizeOf(target, "height"),
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
    setSolo,
    openSolo,
    openDocked,
    selftest,
    get current() { return panel; },
    get zoom() { return zoom; },
  };
})();