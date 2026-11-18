/*
 * router.js —— 按 registry.json 注册表加载页面片段并注入 #page-container。
 *
 * **换实验 = 换网页**：新增一个实验只要
 *   1. 建 web/pages/<id>/page.html（静态结构）
 *   2. 在 web/pages/registry.json 里加一行
 *   3. 需要实时数据的话，写 web/js/pages/<id>.js 并挂到 window.Pages[id]
 * 不需要改 host/app.py。
 *
 * 注意：QWebEngine 里 fetch() 不支持 file://，这里一律用 XHR
 * （依赖 QWebEngineSettings.LocalContentCanAccessFileUrls）。
 */
window.Router = (function () {
  let registry = [];
  let current = null;

  function loadText(path) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("GET", path, true);
      xhr.onreadystatechange = () => {
        if (xhr.readyState !== 4) return;
        if (xhr.status === 200 || (xhr.status === 0 && xhr.responseText)) return resolve(xhr.responseText);
        reject(new Error("加载失败：" + path + " (status " + xhr.status + ")"));
      };
      xhr.send();
    });
  }

  async function init() {
    try {
      registry = JSON.parse(await loadText("pages/registry.json"));
    } catch (err) {
      console.error(err);
      registry = [];
    }
    renderNav();
    return registry;
  }

  function renderNav() {
    const nav = document.getElementById("nav");
    nav.innerHTML = "";
    registry.forEach((page) => {
      const btn = document.createElement("button");
      btn.className = "nav-item";
      btn.dataset.page = page.id;
      const title = I18N.entry("title", page);
      const hint = I18N.entry("hint", page);
      btn.innerHTML = title + (hint ? '<span class="hint">' + hint + "</span>" : "");
      btn.addEventListener("click", () => navigate(page.id));
      nav.appendChild(btn);
    });
  }

  function mark(pageId) {
    document.querySelectorAll("#nav .nav-item").forEach((b) => {
      b.classList.toggle("active", b.dataset.page === pageId);
    });
  }

  async function navigate(pageId) {
    const entry = registry.find((p) => p.id === pageId) || { id: pageId, title: pageId };
    // 浮层里那个面板属于上一页的 DOM，切页前先安顿好它：
    //   · 汇总型（判读/门槛表/体检结论）→ 直接关掉
    //   · 实时流 → 不关，留在浮层里暂停 + 提示「模式已切换」，切回来继续刷新
    // 另外无论哪种都要把浮层**摘下来**，否则会被下面的 innerHTML 一起清掉。
    if (window.Popout) Popout.beforePageChange(pageId);
    if (current && current.mod && current.mod.onLeave) {
      try { current.mod.onLeave(); } catch (err) { console.error(err); }
    }
    const host = document.getElementById("page-container");
    let html = "";
    try {
      html = await loadText("pages/" + pageId + "/page.html");
    } catch (err) {
      html = '<div class="error-box">页面片段加载失败 / failed to load fragment: ' + String(err.message || err) + "</div>";
    }
    host.innerHTML = html;
    host.scrollTop = 0;
    const mod = (window.Pages && window.Pages[pageId]) || {};
    current = { id: pageId, entry: entry, mod: mod, root: host };
    mark(pageId);
    if (mod.onEnter) {
      try { mod.onEnter(host); } catch (err) { console.error("[page]", pageId, err); }
    }
    // 双击弹出：任何页面的任何 .panel 都通用（新实验页不用写代码）
    if (window.Popout) {
      Popout.attach(host);
      Popout.afterPageChange(pageId);
    }
    // 告诉 Python 侧「我现在停在这一页」：射频线程只算这一页要的模块
    // （前端不显示的页不该占 CPU、也不该抢射频时间片；见 host/app.py PAGE_MODULES）
    if (window.Bridge && Bridge.ready()) {
      try {
        Bridge.call("setView", JSON.stringify({ page: pageId, win: window.__winTag || "main" }))
          .catch(() => {});
      } catch (err) { /* 老版本没有这个槽，忽略 */ }
    }
  }

  /** 把桥事件转给当前页（页面只实现自己关心的那几个） */
  function dispatch(kind, payload) {
    if (!current || !current.mod) return;
    const fn = current.mod["on" + kind];
    if (typeof fn === "function") {
      try { fn(payload, current.root); } catch (err) { console.error("[page:" + kind + "]", err); }
    }
  }

  /** 语言切换：重画导航 + 让当前页自己重画动态部分 */
  function relabel() {
    renderNav();
    if (current) {
      mark(current.id);
      dispatch("Lang", I18N.lang());
    }
  }

  window.addEventListener("langchange", relabel);

  return {
    init,
    navigate,
    dispatch,
    relabel,
    get current() { return current; },
    get pages() { return registry; },
  };
})();