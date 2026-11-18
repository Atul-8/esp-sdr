/* pages/raw.js —— 通用兜底页：任何模块的 Output 都用它渲染，新实验不用改前端。 */
window.Pages = window.Pages || {};

window.Pages.raw = (function () {
  let root = null;
  let textTimer = null;
  let tablesSeen = 0;
  let lastTables = null;

  function onEnter(host) {
    root = host;
    // 进页先按缓存重画一次：表格是按时间片推下来的，切页那一刻多半还没轮到下一片。
    // 踩过：真板自检里 raw 页 `panels 0`——页面刚进来、下一片还没到，探针就抓了个空
    // （别的页 onEnter 都会 `if (lastPayload) render(...)`，只有这页漏了）。
    if (lastTables) onTables(lastTables);
    refreshText();
    textTimer = setInterval(refreshText, 3000); // ASCII 版全量渲染较贵，3s 一次足够
  }

  function onLeave() {
    if (textTimer) clearInterval(textTimer);
    textTimer = null;
    root = null;
  }

  function refreshText() {
    if (!root || !window.Bridge || !Bridge.ready()) return;
    Bridge.call("renderText").then((text) => {
      if (root && typeof text === "string") root.querySelector("#raw-text").textContent = text;
    }).catch(() => { /* 未连接时静默 */ });
  }

  function onTables(tables) {
    if (!tables || !tables.plugins) return;
    lastTables = tables;
    if (!root) return;
    tablesSeen += 1;
    const box = root.querySelector("#raw-tables");
    box.innerHTML = "";
    tables.plugins.forEach((plugin) => {
      const panel = document.createElement("div");
      panel.className = "panel";
      panel.style.marginBottom = "10px";
      const head = document.createElement("div");
      head.className = "panel__head";
      head.innerHTML = '<span class="panel__title">' + plugin.name + " · " + plugin.id +
        '</span><span class="panel__hint">' + esc(T("raw.weight", { w: plugin.weight })) + "</span>";
      panel.appendChild(head);

      if (plugin.error) {
        const err = document.createElement("div");
        err.className = "error-box";
        err.textContent = plugin.error;
        panel.appendChild(err);
      } else if (plugin.table && plugin.table.rows && plugin.table.rows.length) {
        const title = document.createElement("div");
        title.className = "panel__hint";
        title.textContent = plugin.table.title;
        panel.appendChild(title);
        panel.appendChild(buildTable(plugin.table));
      } else {
        const empty = document.createElement("div");
        empty.className = "muted";
        empty.textContent = T("raw.empty");
        panel.appendChild(empty);
      }
      box.appendChild(panel);
    });
    if (!tables.plugins.length) box.innerHTML = '<div class="muted">' + esc(T("raw.noModules")) + "</div>";
  }

  function buildTable(table) {
    const el = document.createElement("table");
    el.className = "chtable";
    const thead = document.createElement("thead");
    const htr = document.createElement("tr");
    (table.columns || []).forEach((c) => {
      const th = document.createElement("th");
      th.textContent = c;
      htr.appendChild(th);
    });
    thead.appendChild(htr);
    el.appendChild(thead);
    const tbody = document.createElement("tbody");
    (table.rows || []).forEach((row) => {
      const tr = document.createElement("tr");
      row.forEach((cell) => {
        const td = document.createElement("td");
        td.textContent = cell === null || cell === undefined ? "" : String(cell);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    el.appendChild(tbody);
    return el;
  }

  function esc(text) {
    return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  /** 语言切换：静态部分是 data-i18n 自动翻，动态部分（面板/表格）重画一次 */
  function onLang() {
    if (root && lastTables) onTables(lastTables);
  }

  return { onEnter, onLeave, onTables, onLang, probe: () => ({ tablesSeen: tablesSeen }) };
})();