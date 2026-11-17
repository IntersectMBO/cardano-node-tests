"use strict";

(function () {
  const SOURCE = document.querySelector('meta[name="sync-analyzer-data"]').content;
  // The graphs and zips are served next to the run list, under sync-analyzer/.
  const API = new URL("sync-analyzer/", new URL(SOURCE, location.href)).href;
  const REPORTS_PAGE = "../test_results/sync_tests.html";
  const REFRESH_MS = 300000;
  const THEME_KEY = "cardano-test-runs-theme";
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
  // The server names how many runs a pick may hold.
  const maxPick = () => Math.min(LETTERS.length, (data && data.max_pick) || LETTERS.length);

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

  const runsOf = (net) => data.runs.filter((r) => r.environment === net).sort((a, b) => a.started.localeCompare(b.started));
  const runById = (id) => data.runs.find((r) => r.id === id);
  // The picked runs oldest first, the order the server draws them in.
  const ordered = () => picks.map(runById).filter(Boolean).sort((a, b) => a.started.localeCompare(b.started) || a.id - b.id);
  const colorOf = (id) => COLORS[ordered().findIndex((r) => r.id === id)] || "var(--line)";
  const sameAsShown = () => shown !== null && shown.length === picks.length && ordered().every((r, i) => r.id === shown[i]);
  const runLabel = (r) => `${r.label} · ${r.started.slice(0, 10)} · run ${r.id}`;

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
    add.disabled = picks.length >= maxPick() || picks.length >= runs.length;
    add.textContent = picks.length >= maxPick() ? `${maxPick()} of ${maxPick()} versions` : "+ Add version";
    byId("gen").disabled = !picks.length;
  }

  function addPick() {
    const used = new Set(picks);
    const next = runsOf(network).slice().reverse().find((r) => !used.has(r.id));
    if (!next || picks.length >= maxPick()) return;
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
    byId("graphs").replaceChildren(h("div", "empty", `Pick one to ${maxPick()} versions, then Generate graphs.`));
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

  // Two runs of one version are told apart by their start date, and by their run id
  // when the date repeats too, as in the graphs' legend.
  function columnLabel(r, runs) {
    const sameVersion = runs.filter((o) => o.label === r.label);
    if (sameVersion.length < 2) return r.label;
    const date = r.started.slice(0, 10);
    const sameDate = sameVersion.filter((o) => o.started.slice(0, 10) === date);
    return sameDate.length < 2 ? `${r.label} (${date})` : `${r.label} (${date}, run ${r.id})`;
  }

  function headerRow(runs, extra) {
    const tr = h("tr");
    tr.append(h("th", "", ""));
    runs.forEach((r, i) => {
      const th = h("th");
      if (extra) th.colSpan = 2;
      const sw = h("span", "swatch");
      sw.style.setProperty("--sw", COLORS[i]);
      th.append(sw, document.createTextNode(columnLabel(r, runs)));
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
    const present = new Set();
    for (const r of runs) if (r.facts) for (const e of Object.keys(r.facts.eras)) present.add(e);
    const eras = [...ERAS.filter((e) => present.has(e)), ...[...present].filter((e) => !ERAS.includes(e))];
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
      ? `Deltas are against ${columnLabel(runs[0], runs)}, the oldest pick. Slots per second is the era's slot count divided by its sync time, so it does not depend on chain length.`
      : "Slots per second is the era's slot count divided by its sync time. Add a second version to see deltas.";
    return captioned("Sync time per era", table, foot);
  }

  function factsTable(runs) {
    const table = h("table", "facts-table");
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
    // Each graph is fetched once: a busy server (503) is tried again, a graph
    // the server does not have (404) is shown as missing, any other failure
    // means the pick as a whole could not be drawn.
    const RETRY_MS = 6000;
    const RETRIES = 60;
    let pickProblem = null;
    const fail = (i, text) => {
      const { img, figure } = images[i];
      img.className = "failed";
      figure.prepend(h("div", "problem", text));
    };
    const start = async (i, tries) => {
      if (i >= images.length || shown === null || !grid.isConnected) return;
      const { img, url } = images[i];
      if (pickProblem) { fail(i, pickProblem); start(i + 1, RETRIES); return; }
      let status = 0;
      let message = "";
      let retryAfter = 0;
      try {
        const response = await fetch(url);
        status = response.status;
        retryAfter = Number(response.headers.get("Retry-After")) || 0;
        if (response.ok) {
          const blob = URL.createObjectURL(await response.blob());
          img.onload = () => { URL.revokeObjectURL(blob); img.classList.remove("loading"); start(i + 1, RETRIES); };
          img.onerror = () => { URL.revokeObjectURL(blob); fail(i, "The server's answer was not an image."); start(i + 1, RETRIES); };
          img.src = blob;
          return;
        }
        try { message = (await response.json()).message || ""; } catch (e) { /* not JSON */ }
      } catch (e) { /* no answer at all */ }
      // Busy answers carry Retry-After; the server keeps saying so until its render ends.
      if (status === 503 && retryAfter && tries > 0) {
        setTimeout(() => start(i, tries - 1), Math.max(RETRY_MS, retryAfter * 1000));
        return;
      }
      if (status === 404) {
        fail(i, message ? `${message}.` : "The server does not have this graph.");
      } else {
        pickProblem = message ? `These graphs could not be drawn: ${message}.` : "These graphs could not be drawn: the server did not answer.";
        fail(i, pickProblem);
      }
      start(i + 1, RETRIES);
    };
    start(0, RETRIES);
  }

  function showFromHash(ids) {
    const named = [...new Set(ids || hashPicks())];
    if (!named.length) return;
    const wanted = named.filter((id) => runById(id));
    if (!wanted.length) {
      note("The link names runs this page does not hold.", true);
      return;
    }
    if (wanted.length > maxPick()) {
      note(`The link names ${wanted.length} runs; this page shows at most ${maxPick()}.`, true);
      return;
    }
    network = runById(wanted[0]).environment;
    picks = wanted.filter((id) => runById(id).environment === network);
    renderNetworks();
    renderPicks();
    renderRuns();
    show();
    if (picks.length !== named.length) note("Some runs the link named are not stored or are on another network; the rest are shown.", true);
  }

  function show() {
    if (new Set(picks).size !== picks.length) {
      note("Two picks are the same run. Pick a different one or remove it.", true);
      return;
    }
    const runs = ordered();
    if (!runs.length) return;
    shown = runs.map((r) => r.id);
    renderRuns();
    const hash = `#${shown.slice().sort((a, b) => a - b).join("-")}`;
    if (location.hash !== hash) history.replaceState(null, "", hash);
    byId("graphs-title").textContent = `Graphs: ${runs.map((r) => r.label).join(" vs ")} (${network})`;
    renderGraphs(runs);
    byId("numbers").replaceChildren(eraTable(runs), factsTable(runs));
    note(runs.length === 1 ? "Rendered for one run." : `Rendered for ${runs.length} runs on one graph. The legend in each graph names the version; the colours match the swatches above.`);
  }

  function renderUpdated(problem) {
    if (!data) {
      byId("updated").textContent = problem || "Loading…";
      return;
    }
    const minutes = Math.max(0, Math.round((Date.now() - Date.parse(data.generated_at)) / 60000));
    const ago = minutes < 1 ? "just now" : minutes < 120 ? `${minutes} minutes ago` : minutes < 2880 ? `${Math.round(minutes / 60)} hours ago` : `${Math.round(minutes / 1440)} days ago`;
    byId("updated").textContent = `Run list updated ${ago} · ${fmt(data.runs.length)} stored runs${problem ? ` · ${problem}` : ""}`;
  }

  let loading = null;
  function load() {
    if (!loading) loading = loadOnce().finally(() => { loading = null; });
    return loading;
  }

  async function loadOnce() {
    const before = data;
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
        renderNetworks();
        picks = picks.filter((id) => runById(id) && runById(id).environment === network);
        if (!picks.length) resetPicks();
        else { renderPicks(); renderRuns(); }
      }
      renderUpdated();
    } catch (err) {
      // Keep the last data that rendered on screen and try again on the next poll.
      data = before;
      renderUpdated(`could not refresh (${err.message})`);
      if (!data) byId("runs").replaceChildren(h("div", "empty", `The run list could not be loaded (${err.message}). The address of the results service is set in this page's sync-analyzer-data tag.`));
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

  // Theme: "system" follows the operating system; light and dark override it.
  const themeButtons = document.querySelectorAll("[data-theme-choice]");
  function applyTheme(choice) {
    if (choice === "light" || choice === "dark") document.documentElement.setAttribute("data-theme", choice);
    else document.documentElement.removeAttribute("data-theme");
    for (const b of themeButtons) b.setAttribute("aria-pressed", String(b.dataset.themeChoice === choice));
  }
  let saved = "system";
  try { saved = localStorage.getItem(THEME_KEY) || "system"; } catch (e) { /* storage blocked */ }
  applyTheme(saved);
  for (const b of themeButtons) {
    b.addEventListener("click", () => {
      applyTheme(b.dataset.themeChoice);
      try { localStorage.setItem(THEME_KEY, b.dataset.themeChoice); } catch (e) { /* storage blocked */ }
    });
  }

  byId("add").addEventListener("click", addPick);
  byId("gen").addEventListener("click", show);

  window.addEventListener("hashchange", () => { if (data) showFromHash(); });

  load();
  loadReports();
  setInterval(() => { if (document.visibilityState === "visible") load(); }, REFRESH_MS);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") load(); });
})();
