"use strict";

(function () {
  const SOURCE = document.querySelector('meta[name="sync-analyzer-data"]').content;
  // The graphs and zips are served next to the run list, under /sync-analyzer/.
  const API = new URL("sync-analyzer/", new URL(SOURCE).origin).href;
  const REPORTS_PAGE = "../test_results/sync_tests.html";
  const REFRESH_MS = 300000;
  const MAX_PICK = 4;
  const LETTERS = ["A", "B", "C", "D"];
  const COLORS = ["var(--run-a)", "var(--run-b)", "var(--run-c)", "var(--run-d)"];
  const ERAS = ["byron", "shelley", "allegra", "mary", "alonzo", "babbage", "conway"];
  const NETWORK_ORDER = ["mainnet", "preprod", "preview"];
  const GRAPHS = [
    ["nodesync_progress", "Sync progress: chain tip over time since start, with the era boundaries"],
    ["nodesync_time_per_era", "Sync time per era"],
    ["nodesync_duration_per_epoch", "Sync duration per epoch"],
    ["nodesync_ram_consumption", "RAM (heap)"],
    ["nodesync_cpu_consumption_smoothed", "CPU load, mean per slot band"],
  ];
  const GB = 1e9;

  let data = null;
  let network = "mainnet";
  let picks = [];
  let shown = null;

  // The address names a comparison, so it can be shared: #<run ids joined by ->.
  const hashPicks = () => (/^#\d+(-\d+)*$/.test(location.hash) ? location.hash.slice(1).split("-").map(Number) : []);

  // Small DOM helpers. Data is only ever set as text, never as markup.
  const h = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const byId = (id) => document.getElementById(id);
  const fmt = (n) => Math.round(n).toLocaleString("en-US");
  const hms = (s) => {
    const t = Math.round(s);
    const hh = Math.floor(t / 3600);
    const m = Math.floor((t % 3600) / 60);
    return `${hh}:${String(m).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
  };
  const stamp = (iso) => iso.slice(0, 16).replace("T", " ");
  const dash = (v) => (v === null || v === undefined ? "–" : v);

  function valid(doc) {
    return Boolean(doc) && doc.schema === 1 && typeof doc.generated_at === "string" && Array.isArray(doc.runs);
  }

  const runsOf = (net) => data.runs.filter((r) => r.environment === net).slice().sort((a, b) => a.started.localeCompare(b.started));
  const runById = (id) => data.runs.find((r) => r.id === id);
  // The picked runs oldest first, the order the server draws them in.
  const ordered = () => picks.map(runById).filter(Boolean).sort((a, b) => a.started.localeCompare(b.started) || a.id - b.id);
  const colorOf = (id) => COLORS[ordered().findIndex((r) => r.id === id)] || "var(--line)";
  const sameAsShown = () => shown !== null && shown.length === picks.length && ordered().every((r, i) => r.id === shown[i]);
  const runLabel = (r) => `${r.label} · ${r.started.slice(0, 10)}`;

  function note(text, isError) {
    const el = byId("hint");
    el.textContent = text;
    el.className = isError ? "hint err" : "hint";
  }

  function renderNetworks() {
    const present = [...new Set(data.runs.map((r) => r.environment))];
    const nets = [...NETWORK_ORDER.filter((n) => present.includes(n)), ...present.filter((n) => !NETWORK_ORDER.includes(n)).sort()];
    if (!nets.includes(network)) network = nets[0] || "mainnet";
    byId("networks").replaceChildren(...nets.map((n) => {
      const b = h("button", "", n);
      b.type = "button";
      b.setAttribute("aria-pressed", String(n === network));
      b.addEventListener("click", () => { network = n; resetPicks(); });
      return b;
    }));
  }

  function resetPicks() {
    const runs = runsOf(network);
    picks = runs.length ? [runs[runs.length - 1].id] : [];
    renderNetworks();
    renderPicks();
    renderRuns();
    clearResults();
  }

  function renderPicks() {
    const runs = runsOf(network);
    byId("versions").replaceChildren(...picks.map((id, i) => {
      const row = h("div", "vrow");
      row.style.setProperty("--sw", colorOf(id));
      const label = h("label");
      const caption = h("span");
      caption.append(h("span", "swatch"), document.createTextNode(`Version ${LETTERS[i]}`));
      const select = h("select");
      for (const r of runs) select.add(new Option(runLabel(r), String(r.id)));
      select.value = String(id);
      select.addEventListener("change", () => { picks[i] = Number(select.value); renderPicks(); renderRuns(); });
      label.append(caption, select);
      const remove = h("button", "remove", "×");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove version ${LETTERS[i]}`);
      remove.disabled = picks.length < 2;
      remove.addEventListener("click", () => { picks.splice(i, 1); renderPicks(); renderRuns(); });
      row.append(label, remove);
      return row;
    }));
    const add = byId("add");
    add.disabled = picks.length >= MAX_PICK || picks.length >= runs.length;
    add.textContent = picks.length >= MAX_PICK ? `${MAX_PICK} of ${MAX_PICK} versions` : "+ Add version";
    byId("gen").disabled = !picks.length;
  }

  function addPick() {
    const used = new Set(picks);
    const next = runsOf(network).slice().reverse().find((r) => !used.has(r.id));
    if (!next || picks.length >= MAX_PICK) return;
    picks.push(next.id);
    renderPicks();
    renderRuns();
  }

  function runCard(r) {
    const card = h("div", "run");
    card.style.setProperty("--sw", shown === null || sameAsShown() ? colorOf(r.id) : "var(--line)");
    const title = h("h3");
    title.append(h("span", "tag", LETTERS[picks.indexOf(r.id)]), document.createTextNode(`cardano-node ${r.label}`));
    const f = r.facts || {};
    const dl = h("dl");
    const rows = [
      ["Environment", r.environment],
      ["Run", f.started && f.ended ? `${stamp(f.started)} to ${stamp(f.ended)} UTC` : stamp(r.started)],
      ["cardano-cli", dash(f.cli)],
      ["Sync time", f.seconds ? `${hms(f.seconds)} (${fmt(f.seconds)} s)` : "–"],
      ["Chain size", f.chain_bytes ? `${(f.chain_bytes / GB).toFixed(1)} GB` : "–"],
    ];
    for (const [k, v] of rows) dl.append(h("dt", "", k), h("dd", "", v));
    const link = h("a", "btn secondary", `Download results zip${r.zip_bytes ? ` (${(r.zip_bytes / 1e6).toFixed(1)} MB)` : ""}`);
    link.href = `${API}runs/${encodeURIComponent(r.id)}/zip`;
    card.append(title, dl, link);
    return card;
  }

  function renderRuns() {
    const runs = ordered();
    byId("runs").replaceChildren(...(runs.length ? runs.map(runCard) : [h("div", "empty", `No stored runs on ${network} yet.`)]));
  }

  function clearResults() {
    shown = null;
    if (location.hash) history.replaceState(null, "", location.pathname + location.search);
    byId("graphs-title").textContent = "Graphs";
    byId("graphs").replaceChildren(h("div", "empty", "Pick one to four versions, then Generate graphs."));
    byId("numbers").replaceChildren(h("div", "empty", "The tables fill in with the graphs."));
    note("");
  }

  function captioned(title, table, foot) {
    const block = h("div");
    const cap = h("p", "tablecap");
    cap.append(h("b", "", title));
    const wrap = h("div", "tablewrap");
    wrap.append(table);
    block.append(cap, wrap, h("p", "tablecap", foot));
    return block;
  }

  function headerRow(runs, extra) {
    const tr = h("tr");
    tr.append(h("th", "", ""));
    runs.forEach((r, i) => {
      const th = h("th");
      if (extra) th.colSpan = 2;
      const sw = h("span", "swatch");
      sw.style.setProperty("--sw", COLORS[i]);
      th.append(sw, document.createTextNode(r.label));
      tr.append(th);
    });
    return tr;
  }

  function delta(cur, base) {
    if (base === null || base === undefined || cur === null || cur === undefined) return null;
    const d = cur - base;
    const pct = base ? (d / base) * 100 : 0;
    const sign = d > 0 ? "+" : d < 0 ? "−" : "±";
    return h("span", `delta ${d < 0 ? "faster" : d > 0 ? "slower" : ""}`, `${sign}${fmt(Math.abs(d))} s (${sign}${Math.abs(pct).toFixed(1)}%)`);
  }

  function eraTable(runs) {
    const table = h("table");
    table.append(h("thead"), h("tbody"));
    const head = headerRow(runs, true);
    const sub = h("tr");
    sub.append(h("th", "", ""));
    for (const r of runs) sub.append(h("th", "", "time"), h("th", "", "slots/s"));
    table.tHead.replaceChildren(head, sub);
    const base = runs[0].facts;
    const eras = ERAS.filter((e) => runs.some((r) => r.facts && r.facts.eras[e]));
    const rows = eras.map((era) => {
      const tr = h("tr");
      tr.append(h("td", "", era));
      runs.forEach((r, i) => {
        const x = r.facts && r.facts.eras[era];
        const time = h("td", "mono", x && x.seconds !== null ? hms(x.seconds) : "–");
        const d = i && x && base && base.eras[era] ? delta(x.seconds, base.eras[era].seconds) : null;
        if (d) time.append(d);
        tr.append(time, h("td", "mono", x && x.slots_per_second !== null ? fmt(x.slots_per_second) : "–"));
      });
      return tr;
    });
    const total = h("tr", "total");
    total.append(h("td", "", "Total"));
    runs.forEach((r, i) => {
      const f = r.facts || {};
      const time = h("td", "mono", f.seconds ? hms(f.seconds) : "–");
      const d = i && f.seconds && base && base.seconds ? delta(f.seconds, base.seconds) : null;
      if (d) time.append(d);
      total.append(time, h("td", "mono", f.seconds && f.last_slot ? fmt(f.last_slot / f.seconds) : "–"));
    });
    table.tBodies[0].replaceChildren(...rows, total);
    const foot = runs.length > 1
      ? `Deltas are against ${runs[0].label}, the oldest pick. Slots per second is the era's slot count divided by its sync time, so it does not depend on chain length.`
      : "Slots per second is the era's slot count divided by its sync time. Add a second version to see deltas.";
    return captioned("Sync time per era", table, foot);
  }

  function factsTable(runs) {
    const table = h("table");
    table.append(h("thead"), h("tbody"));
    table.tHead.replaceChildren(headerRow(runs, false));
    const gb = (bytes) => (bytes ? `${(bytes / GB).toFixed(1)} GB` : "–");
    const rows = [
      ["Run (UTC)", (f) => (f.started && f.ended ? `${stamp(f.started)} to ${stamp(f.ended)}` : "–")],
      ["cardano-cli", (f) => dash(f.cli)],
      ["Final epoch / slot", (f) => (f.last_slot ? `${dash(f.last_epoch)} / ${fmt(f.last_slot)}` : "–")],
      ["Chain size", (f) => gb(f.chain_bytes)],
      ["Seconds per GB of chain", (f) => (f.seconds && f.chain_bytes ? fmt(f.seconds / (f.chain_bytes / GB)) : "–")],
      ["Host", (f) => (f.cores ? `${f.cores} cores, ${dash(f.ram_gb)} GB RAM, Linux ${dash(f.kernel)}` : "–")],
      ["RSS peak / at end", (f) => (f.rss_peak_bytes ? `${gb(f.rss_peak_bytes)} / ${gb(f.rss_end_bytes)}` : "–")],
      ["CPU median / p95", (f) => (f.cpu_median !== null && f.cpu_median !== undefined ? `${fmt(f.cpu_median)}% / ${fmt(f.cpu_p95)}%` : "–")],
      ["Slow epochs", (f) => (f.slow_epochs && f.slow_epochs.length
        ? f.slow_epochs.map((o) => `epoch ${o.epoch}: ${fmt(o.seconds)} s (neighbours ${fmt(o.neighbours)} s)`).join("; ")
        : f.slow_epochs ? "none" : "–")],
    ];
    table.tBodies[0].replaceChildren(...rows.map(([label, cell]) => {
      const tr = h("tr");
      tr.append(h("td", "", label));
      for (const r of runs) tr.append(h("td", "mono", cell(r.facts || {})));
      return tr;
    }));
    const foot = runs.some((r) => !r.facts)
      ? "A run shown with dashes has no readable results file; its zip is still there to download."
      : "Slow epochs took far longer than the epochs around them.";
    return captioned("Run facts", table, foot);
  }

  function notes(runs) {
    const items = [
      "The RAM graph shows the heap. RSS peak and end are in the run facts table.",
      "Runs are weeks apart on a growing chain. Chain size and kernel are in the run facts table; compare seconds per GB when chain size differs.",
      "Sync time per era depends on the era's length in slots; slots per second does not.",
      "An epoch slow in every run is a property of the chain, not of a version.",
    ];
    if (runs.length > 1) items.unshift("Most versions were synced once, so run-to-run noise is not measured. Treat small deltas as unconfirmed until a version has been synced twice.");
    const box = h("div", "facts");
    const list = h("ul");
    for (const text of items) list.append(h("li", "", text));
    box.append(h("h3", "", "About these numbers"), list);
    return box;
  }

  function renderGraphs(runs) {
    const ids = runs.map((r) => r.id).sort((a, b) => a - b).join("-");
    const version = Math.max(...runs.map((r) => Date.parse(r.uploaded) || 0));
    const grid = h("div", "graphs");
    const images = GRAPHS.map(([name, caption], i) => {
      const figure = h("figure", i === 0 ? "lead" : "");
      const img = h("img", "loading");
      img.alt = `${caption}, ${runs.map((r) => r.label).join(" vs ")}`;
      figure.append(img, h("figcaption", "", caption));
      grid.append(figure);
      return { img, figure, url: `${API}graphs/${ids}/${name}.png?v=${version}` };
    });
    byId("graphs").replaceChildren(grid);
    // The first graph makes the server draw the pick; the others are then
    // served from its cache. Asking for all five at once would queue them.
    const RETRY_MS = 6000;
    const RETRIES = 10;
    const start = (i, tries) => {
      if (i >= images.length || shown === null || !grid.isConnected) return;
      const { img, figure, url } = images[i];
      img.onload = () => { img.classList.remove("loading"); start(i + 1, RETRIES); };
      img.onerror = async () => {
        // A 503 means the server is drawing another pick; try again in a while.
        let busy = false;
        try { busy = (await fetch(url, { method: "HEAD" })).status === 503; } catch (e) { /* treated as a failure */ }
        if (busy && tries > 0) {
          setTimeout(() => start(i, tries - 1), RETRY_MS);
          return;
        }
        img.className = "failed";
        figure.prepend(h("div", "problem", busy
          ? "The server is busy drawing other graphs. Reload the page in a minute."
          : "This graph could not be drawn. The server may have no graph script configured, or the run's results file is missing."));
      };
      img.src = url;
    };
    start(0, RETRIES);
  }

  function showFromHash(ids) {
    const wanted = (ids || hashPicks()).filter((id) => runById(id));
    if (!wanted.length || wanted.length > MAX_PICK) return;
    network = runById(wanted[0]).environment;
    picks = wanted.filter((id) => runById(id).environment === network);
    renderNetworks();
    renderPicks();
    renderRuns();
    show();
  }

  function show() {
    if (new Set(picks).size !== picks.length) {
      note("Two picks are the same run. Pick a different one or remove it.", true);
      return;
    }
    const runs = ordered();
    if (!runs.length) return;
    shown = runs.map((r) => r.id);
    const hash = `#${shown.slice().sort((a, b) => a - b).join("-")}`;
    if (location.hash !== hash) history.replaceState(null, "", hash);
    byId("graphs-title").textContent = `Graphs: ${runs.map((r) => r.label).join(" vs ")} (${network})`;
    renderGraphs(runs);
    byId("numbers").replaceChildren(eraTable(runs), factsTable(runs), notes(runs));
    note(runs.length === 1 ? "Rendered for one run." : `Rendered for ${runs.length} runs on one graph. The legend in each graph names the version; the colours match the swatches above.`);
  }

  function renderUpdated(problem) {
    if (!data) {
      byId("updated").textContent = problem || "Loading…";
      return;
    }
    const minutes = Math.max(0, Math.round((Date.now() - Date.parse(data.generated_at)) / 60000));
    const ago = minutes < 1 ? "just now" : minutes < 120 ? `${minutes} minutes ago` : minutes < 2880 ? `${Math.round(minutes / 60)} hours ago` : `${Math.round(minutes / 1440)} days ago`;
    byId("updated").textContent = `Run list updated ${ago}, ${fmt(data.runs.length)} stored runs${problem ? ` (${problem})` : ""}`;
  }

  async function load() {
    try {
      const response = await fetch(SOURCE, { cache: "no-cache" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const doc = await response.json();
      if (!valid(doc)) throw new Error("unexpected data");
      const first = !data;
      const changed = first || doc.generated_at !== data.generated_at;
      data = doc;
      if (first) {
        // Read the address before the reset clears it.
        const wanted = hashPicks();
        resetPicks();
        showFromHash(wanted);
      } else if (changed) {
        picks = picks.filter((id) => runById(id));
        renderNetworks();
        renderPicks();
        renderRuns();
      }
      renderUpdated();
    } catch (err) {
      // Keep the last good data on screen and try again on the next poll.
      renderUpdated(`could not refresh (${err.message})`);
    }
  }

  // The published reports are the pages under the Sync Tests section of this site.
  async function loadReports() {
    const list = byId("reports");
    try {
      const response = await fetch(REPORTS_PAGE, { cache: "no-cache" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const seen = new Set();
      const items = [];
      for (const a of page.querySelectorAll('a[href*="sync_reports/"]')) {
        const href = a.getAttribute("href").split("#")[0];
        if (!href.endsWith(".html") || seen.has(href)) continue;
        seen.add(href);
        const li = h("li");
        const link = h("a", "", a.textContent.trim());
        link.href = new URL(href, new URL(REPORTS_PAGE, location.href)).href;
        li.append(link);
        items.push(li);
      }
      byId("reports-count").textContent = String(items.length);
      list.replaceChildren(...(items.length ? items : [h("li", "meta", "No reports published yet")]));
    } catch (err) {
      list.replaceChildren(h("li", "meta", "The report list could not be read; use the link below."));
    }
  }

  byId("add").addEventListener("click", addPick);
  byId("gen").addEventListener("click", show);

  window.addEventListener("hashchange", () => { if (data) showFromHash(); });

  load();
  loadReports();
  setInterval(() => { if (document.visibilityState === "visible") load(); }, REFRESH_MS);
})();
