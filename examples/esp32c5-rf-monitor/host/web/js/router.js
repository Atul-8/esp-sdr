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
      btn.innerHTML = page.title + (page.hint ? '<span class="hint">' + page.hint + "</span>" : "");
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
    if (current && current.mod && current.mod.onLeave) {
      try { current.mod.onLeave(); } catch (err) { console.error(err); }
    }
    const host = document.getElementById("page-container");
    let html = "";
    try {
      html = await loadText("pages/" + pageId + "/page.html");
    } catch (err) {
      html = '<div class="error-box">页面片段加载失败：' + String(err.message || err) + "</div>";
    }
    host.innerHTML = html;
    host.scrollTop = 0;
    const mod = (window.Pages && window.Pages[pageId]) || {};
    current = { id: pageId, entry: entry, mod: mod, root: host };
    mark(pageId);
    if (mod.onEnter) {
      try { mod.onEnter(host); } catch (err) { console.error("[page]", pageId, err); }
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

  return {
    init,
    navigate,
    dispatch,
    get current() { return current; },
    get pages() { return registry; },
  };
})();