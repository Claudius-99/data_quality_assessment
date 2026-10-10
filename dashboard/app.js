(function () {
  const DIMENSIONS = [
    "completeness",
    "uniqueness",
    "validity",
    "referential_integrity",
    "freshness",
    "drift",
  ];

  const banner = document.getElementById("banner");
  const lede = document.getElementById("lede");
  const specimen = document.getElementById("specimen");
  const tablesBody = document.getElementById("tables-body");
  const historyHead = document.getElementById("history-head");
  const historyBody = document.getElementById("history-body");
  const runList = document.getElementById("run-list");
  const worstBody = document.getElementById("worst-body");
  const worstEmpty = document.getElementById("worst-empty");
  const foot = document.getElementById("foot");

  fetch("data/snapshot.json", { cache: "no-store" })
    .then(function (response) {
      if (!response.ok) {
        throw new Error("snapshot HTTP " + response.status);
      }
      return response.json();
    })
    .then(render)
    .catch(function () {
      showBanner(
        "Could not load data/snapshot.json. Serve this folder, for example: python -m http.server --directory dashboard 8000"
      );
    });

  function render(snapshot) {
    if (!snapshot || snapshot.schema_version !== 1) {
      showBanner("Unsupported or missing snapshot schema_version (expected 1).");
      return;
    }
    specimen.textContent = snapshot.specimen_label || "Specimen, not client data";
    const current = snapshot.current_run;
    if (!current) {
      lede.textContent = "No runs in this snapshot.";
      return;
    }
    lede.textContent =
      current.datasource_name +
      " · " +
      current.run_id +
      " · as of " +
      current.as_of +
      " · " +
      current.overall_status;
    renderTables(snapshot.tables || []);
    renderHistory(snapshot.history || [], snapshot.tables || []);
    renderWorst(snapshot.worst_columns || []);
    foot.textContent =
      "schema " +
      snapshot.schema_version +
      " · generated " +
      snapshot.generated_at +
      " · " +
      (snapshot.specimen ? snapshot.specimen_label : "live source");
  }

  function renderTables(tables) {
    tablesBody.textContent = "";
    if (!tables.length) {
      tablesBody.appendChild(row(["No tables in snapshot."], 9));
      return;
    }
    tables.forEach(function (table) {
      const cells = [
        table.name,
        formatInt(table.row_count),
        statusNode(table.status),
      ];
      DIMENSIONS.forEach(function (dim) {
        const entry = (table.dimensions || {})[dim] || {};
        cells.push(statusNode(entry.status || "not_evaluated"));
      });
      tablesBody.appendChild(row(cells));
    });
  }

  function renderHistory(history, tables) {
    historyHead.textContent = "";
    historyBody.textContent = "";
    runList.textContent = "";
    const headRow = document.createElement("tr");
    headRow.appendChild(th("Table"));
    history.forEach(function (run) {
      const label = (run.as_of || run.run_id).slice(0, 10);
      headRow.appendChild(th(label));
    });
    historyHead.appendChild(headRow);

    const names = [];
    tables.forEach(function (table) {
      names.push(table.name);
    });
    history.forEach(function (run) {
      Object.keys(run.tables || {}).forEach(function (name) {
        if (names.indexOf(name) === -1) names.push(name);
      });
    });
    names.forEach(function (name) {
      const cells = [name];
      history.forEach(function (run) {
        cells.push(statusNode((run.tables || {})[name] || "not_evaluated"));
      });
      historyBody.appendChild(row(cells));
    });

    history.forEach(function (run) {
      const item = document.createElement("li");
      const fails = Number(run.fail_count || 0);
      const width = Math.min(100, 8 + fails * 10);
      appendText(item, run.run_id);
      appendText(item, (run.as_of || "").replace("T", " ").replace("+00:00", " UTC"));
      item.appendChild(statusNode(run.overall_status));
      appendText(item, fails + " fail / " + Number(run.warn_count || 0) + " warn");
      const bar = document.createElement("span");
      bar.className = "bar " + (run.overall_status || "");
      bar.style.width = width + "px";
      bar.setAttribute("aria-hidden", "true");
      item.appendChild(bar);
      runList.appendChild(item);
    });
  }

  function renderWorst(columns) {
    worstBody.textContent = "";
    if (!columns.length) {
      worstEmpty.hidden = false;
      return;
    }
    worstEmpty.hidden = true;
    columns.forEach(function (col) {
      const checks = (col.checks || [])
        .map(function (c) {
          return c.dimension + " " + c.status;
        })
        .join(", ");
      const label = col.run_id ? col.table + "." + col.column + " (" + col.run_id + ")" : col.table + "." + col.column;
      worstBody.appendChild(
        row([
          label,
          statusNode(col.status),
          formatRatio(col.failed_ratio),
          formatInt(col.failed_count),
          checks,
        ])
      );
    });
  }

  function row(cells, span) {
    const tr = document.createElement("tr");
    cells.forEach(function (cell, index) {
      const td = document.createElement("td");
      if (span && index === 0) td.colSpan = span;
      if (cell && cell.nodeType === 1) {
        td.appendChild(cell);
      } else {
        td.textContent = cell == null ? "" : String(cell);
      }
      tr.appendChild(td);
    });
    return tr;
  }

  function th(text) {
    const el = document.createElement("th");
    el.textContent = text;
    return el;
  }

  function statusNode(value) {
    const el = document.createElement("span");
    const status = value || "not_evaluated";
    el.className = "status " + status;
    el.textContent = status;
    return el;
  }

  function appendText(parent, text) {
    const span = document.createElement("span");
    span.textContent = text;
    parent.appendChild(span);
  }

  function formatInt(value) {
    return Number(value || 0).toLocaleString("en-US");
  }

  function formatRatio(value) {
    const n = Number(value || 0);
    return (n * 100).toFixed(1) + "%";
  }

  function showBanner(text) {
    banner.hidden = false;
    banner.textContent = text;
  }
})();
