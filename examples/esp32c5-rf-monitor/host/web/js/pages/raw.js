/* pages/raw.js —— 通用兜底页：任何模块的 Output 都用它渲染，新实验不用改前端。 */
window.Pages = window.Pages || {};

window.Pages.raw = (function () {
  let root = null;
  let textTimer = null;
  let tablesSeen = 0;

  function onEnter(host) {
    root = host;
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
    if (!root || !tables || !tables.plugins) return;
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
        '</span><span class="panel__hint">权重 ' + plugin.weight + "</span>";
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
        empty.textContent = "该模块暂无表格产出（看下面的 ASCII 输出或还没开始扫）";
        panel.appendChild(empty);
      }
      box.appendChild(panel);
    });
    if (!tables.plugins.length) box.innerHTML = '<div class="muted">没有模块</div>';
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

  return { onEnter, onLeave, onTables, probe: () => ({ tablesSeen: tablesSeen }) };
})();