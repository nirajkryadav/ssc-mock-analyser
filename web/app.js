/* SSC Mock Analyser — browser front end.
 *
 * One page, three jobs:
 *   1. Progress   every saved mock, charted (GET /api/records)
 *   2. Analyse    PDFs posted one at a time to /api/parse (Cloud Run caps a
 *                 request at 32 MB and a full mock is ~40 MB), then the records
 *                 posted once to /api/analyse with save=true
 *   3. All mocks  a table of every saved mock; open one to see its full report
 *                 (GET /api/records/:id) or delete it
 */
(() => {
  "use strict";

  const API = () => (window.SSC_API_BASE || "").replace(/\/$/, "");
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const SVG = "http://www.w3.org/2000/svg";
  const svg = (tag, attrs = {}) => {
    const n = document.createElementNS(SVG, tag);
    Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
    return n;
  };
  const esc = (s) => String(s ?? "");
  const pct = (n) => `${Number(n).toFixed(1)}%`;
  const num = (n) => (Math.round(Number(n) * 100) / 100).toString();
  const signed = (n, unit = "") => `${n > 0 ? "+" : n < 0 ? "−" : "±"}${num(Math.abs(n))}${unit}`;
  const SHORT = {
    "General Intelligence and Reasoning": "Reasoning",
    "General Awareness": "General Awareness",
    "Quantitative Aptitude": "Quant",
    "English Comprehension": "English",
  };
  const short = (label) => SHORT[label] || label;

  let files = [];
  let records = [];
  let analysis = null;

  /* --------------------------------------------------------------- request */

  async function call(path, { method = "GET", body, json = false } = {}) {
    let res;
    try {
      res = await fetch(`${API()}${path}`, {
        method,
        body: json ? JSON.stringify(body) : body,
        headers: json ? { "Content-Type": "application/json" } : undefined,
      });
    } catch (err) {
      throw new Error(
        `Could not reach the analyser at ${API() || window.location.origin}${path}.\n` +
        `The backend may be starting up; Cloud Run cold starts take a few seconds. ` +
        `Try again in a moment.\n\n(${err.message})`);
    }
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { /* not JSON */ }
    if (!res.ok) {
      const detail = (data && (data.detail || data.message)) || text || res.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data ?? text;
  }

  /* ------------------------------------------------------------- tooltip */

  const tip = $("tooltip");
  function showTip(evt, lines) {
    tip.replaceChildren();
    lines.forEach(([k, v], i) => {
      const row = el("div", i === 0 ? "t-head" : "t-row");
      if (i === 0) row.textContent = k;
      else row.append(el("span", null, k), el("b", null, v));
      tip.append(row);
    });
    tip.classList.remove("hidden");
    const pad = 12;
    const { innerWidth: W } = window;
    const r = tip.getBoundingClientRect();
    let x = evt.clientX + pad;
    if (x + r.width > W - 8) x = evt.clientX - r.width - pad;
    let y = evt.clientY - r.height - pad;
    if (y < 8) y = evt.clientY + pad;
    tip.style.left = `${Math.max(8, x)}px`;
    tip.style.top = `${y}px`;
  }
  const hideTip = () => tip.classList.add("hidden");

  /* ----------------------------------------------------------- line chart */

  // One series over the mocks, oldest first. Single series: the heading names
  // it, so there is no legend; the last point carries a direct value label.
  function lineChart(host, points, { max, height = 220, width = 640, unit = "", ticks = 4, compact = false }) {
    host.replaceChildren();
    // Draw at the container's real width so text renders at its CSS size.
    const W = Math.max(200, Math.round(host.clientWidth || width));
    const H = height;
    const m = compact
      ? { t: 14, r: 34, b: 10, l: 30 }
      : { t: 16, r: 44, b: 34, l: 40 };
    const iw = W - m.l - m.r;
    const ih = H - m.t - m.b;
    const n = points.length;
    const x = (i) => m.l + (n === 1 ? iw / 2 : (i * iw) / (n - 1));
    const y = (v) => m.t + ih - (Math.max(0, v) / max) * ih;

    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "viz", role: "img" });
    root.setAttribute("aria-label",
      points.map((p) => `${p.label}: ${num(p.value)}${unit}`).join("; "));

    for (let i = 0; i <= ticks; i++) {
      const v = (max * i) / ticks;
      root.append(svg("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v),
        class: i === 0 ? "axis" : "grid" }));
      const t = svg("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end", class: "tick" });
      t.textContent = `${Math.round(v)}${unit}`;
      root.append(t);
    }

    if (!compact) {
      const every = Math.ceil(n / 8);
      points.forEach((p, i) => {
        if (i % every && i !== n - 1) return;
        const t = svg("text", { x: x(i), y: H - 12, "text-anchor": "middle", class: "tick" });
        t.textContent = p.label.length > 12 ? `${p.label.slice(0, 11)}…` : p.label;
        root.append(t);
      });
    }

    const pts = points.map((p, i) => [x(i), y(p.value)]);
    if (n > 1) {
      const d = pts.map(([a, b], i) => `${i ? "L" : "M"}${a},${b}`).join("");
      root.append(svg("path", {
        d: `${d}L${pts[n - 1][0]},${y(0)}L${pts[0][0]},${y(0)}Z`, class: "area" }));
      root.append(svg("path", { d, class: "line" }));
    }
    pts.forEach(([a, b]) => root.append(svg("circle", { cx: a, cy: b, r: 4.5, class: "dot" })));

    const last = pts[n - 1];
    const lbl = svg("text", { x: last[0] + 9, y: last[1] + 4, class: "end-label" });
    lbl.textContent = `${num(points[n - 1].value)}${unit}`;
    root.append(lbl);

    // Hover: one tall hit column per mock, wider than the dot.
    const cross = svg("line", { y1: m.t, y2: m.t + ih, class: "cross hidden-svg" });
    root.append(cross);
    const colW = n === 1 ? iw : iw / (n - 1);
    points.forEach((p, i) => {
      const hit = svg("rect", { x: x(i) - colW / 2, y: m.t, width: colW, height: ih,
        class: "hit", tabindex: 0 });
      const show = (evt) => {
        cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i));
        cross.classList.remove("hidden-svg");
        const r = hit.getBoundingClientRect();
        showTip(evt.clientX ? evt : { clientX: r.left + r.width / 2, clientY: r.top + 20 },
          p.tip);
      };
      hit.addEventListener("pointermove", show);
      hit.addEventListener("focus", show);
      hit.addEventListener("pointerleave", () => { cross.classList.add("hidden-svg"); hideTip(); });
      hit.addEventListener("blur", () => { cross.classList.add("hidden-svg"); hideTip(); });
      root.append(hit);
    });

    host.append(root);
  }

  /* ---------------------------------------------------------- progress */

  function tile(label, value, delta, note) {
    const d = el("div", "metric");
    d.append(el("div", "k", label), el("div", "v", value));
    if (delta) {
      const up = delta.value > 0;
      const good = delta.value === 0 ? "" : (up === delta.upIsGood ? "good" : "bad");
      const arrow = delta.value > 0 ? "▲" : delta.value < 0 ? "▼" : "■";
      d.append(el("div", `n delta ${good}`, `${arrow} ${delta.text}`));
    } else if (note) {
      d.append(el("div", "n", note));
    }
    return d;
  }

  function renderProgress() {
    const n = records.length;
    $("progress-status").textContent = n ? `${n} mock${n === 1 ? "" : "s"} saved` : "";
    $("progress-empty").classList.toggle("hidden", n > 0);
    $("progress-body").classList.toggle("hidden", n === 0);
    if (!n) return;

    const last = records[n - 1];
    const prev = n > 1 ? records[n - 2] : null;
    const best = records.reduce((a, r) => (r.summary.score > a.summary.score ? r : a));
    const tiles = $("tiles");
    tiles.replaceChildren(
      tile("Latest score", num(last.summary.score),
        prev && { value: last.summary.score - prev.summary.score, upIsGood: true,
          text: `${signed(last.summary.score - prev.summary.score)} vs ${prev.mock_name}` },
        `out of ${num(last.summary.max_score)} · ${last.mock_name}`),
      tile("Best score", num(best.summary.score), null, `${best.mock_name} · ${best.mock_date}`),
      tile("Latest accuracy", pct(last.summary.accuracy),
        prev && { value: last.summary.accuracy - prev.summary.accuracy, upIsGood: true,
          text: `${signed(last.summary.accuracy - prev.summary.accuracy, " pts")}` },
        `${last.summary.correct} of ${last.summary.attempted} attempted`),
      tile("Mocks analysed", String(n), null,
        n > 1 ? `${records[0].mock_date} to ${last.mock_date}` : "Trends appear from the second mock"),
    );

    const maxScore = Math.max(...records.map((r) => r.summary.max_score || 200));
    lineChart($("chart-score"), records.map((r) => ({
      label: r.mock_name,
      value: r.summary.score,
      tip: [[`${r.mock_name} · ${r.mock_date}`],
        ["Score", `${num(r.summary.score)} / ${num(r.summary.max_score)}`],
        ["Accuracy", pct(r.summary.accuracy)],
        ["Attempted", pct(r.summary.attempt_rate)]],
    })), { max: maxScore, height: 240 });

    /* section small multiples — each its own single-series chart */
    const host = $("chart-sections");
    host.replaceChildren();
    const sectionNames = records[n - 1].sections.map((s) => s.label);
    // Lay out every box before drawing, so each chart measures its final width.
    const pending = sectionNames.map((name) => {
      const box = el("div", "multiple");
      const series = records.map((r) => {
        const s = (r.sections || []).find((x) => x.label === name);
        return { r, s };
      }).filter((x) => x.s);
      const head = el("div", "m-head");
      head.append(el("b", null, short(name)));
      const chart = el("div", "chart");
      box.append(head, chart);
      host.append(box);
      return { name, chart, series };
    });
    pending.forEach(({ name, chart, series }) => lineChart(chart, series.map(({ r, s }) => ({
      label: r.mock_name,
      value: s.accuracy,
      tip: [[`${short(name)} · ${r.mock_name}`],
        ["Accuracy", pct(s.accuracy)],
        ["Score", `${num(s.score)} / ${num(s.max_score)}`],
        ["Correct · wrong · skipped", `${s.correct} · ${s.wrong} · ${s.skipped}`]],
    })), { max: 100, width: 220, height: 110, unit: "%", ticks: 2, compact: true }));

    /* recurring weak topics */
    const lost = new Map();
    records.forEach((r) => (r.topics || []).forEach((t) => {
      if (t.label === "Unclassified") return;
      const cur = lost.get(t.label) || { label: t.label, section: t.section, lost: 0, mocks: 0, wrong: 0, skipped: 0 };
      cur.lost += t.marks_lost;
      cur.wrong += t.wrong;
      cur.skipped += t.skipped;
      if (t.marks_lost > 0) cur.mocks += 1;
      lost.set(t.label, cur);
    }));
    const top = [...lost.values()].filter((t) => t.lost > 0)
      .sort((a, b) => b.lost - a.lost).slice(0, 8);
    const bars = $("chart-topics");
    bars.replaceChildren();
    $("topics-hint").textContent = n > 1
      ? `Marks lost per topic, added up over all ${n} mocks. The top of this list is where revision pays best.`
      : "Marks lost per topic in this mock. Over several mocks this shows which gaps keep coming back.";
    const maxLost = Math.max(...top.map((t) => t.lost), 1);
    top.forEach((t) => {
      const row = el("div", "hbar");
      row.tabIndex = 0;
      const name = el("div", "hb-name");
      name.append(el("span", null, t.label), el("small", null, short(t.section)));
      const track = el("div", "hb-track");
      const fill = el("i");
      fill.style.width = `${(t.lost / maxLost) * 100}%`;
      track.append(fill, el("span", "hb-val", num(t.lost)));
      row.append(name, track);
      const lines = [[t.label], ["Marks lost", num(t.lost)], ["Wrong", String(t.wrong)],
        ["Skipped", String(t.skipped)]];
      if (n > 1) lines.push(["Lost marks in", `${t.mocks} of ${n} mocks`]);
      row.addEventListener("pointermove", (e) => showTip(e, lines));
      row.addEventListener("pointerleave", hideTip);
      row.addEventListener("focus", () => {
        const r = row.getBoundingClientRect();
        showTip({ clientX: r.left + 40, clientY: r.top }, lines);
      });
      row.addEventListener("blur", hideTip);
      bars.append(row);
    });
    if (!top.length) bars.append(el("p", "hint", "No marks lost to any tagged topic. Nice."));
  }

  /* ------------------------------------------------------------- records */

  function renderRecords() {
    const t = $("records");
    t.replaceChildren();
    $("records-empty").classList.toggle("hidden", records.length > 0);
    t.classList.toggle("hidden", records.length === 0);
    $("dl-all").disabled = records.length === 0;
    if (!records.length) return;

    const sectionNames = records[records.length - 1].sections.map((s) => s.label);
    const thead = el("thead");
    const htr = el("tr");
    [["Date"], ["Mock"], ["Score", true], ["Accuracy", true], ["Attempted", true],
      ...sectionNames.map((s) => [short(s), true]), [""]].forEach(([h, isNum]) =>
      htr.append(el("th", isNum ? "num" : null, h)));
    thead.append(htr);

    const tb = el("tbody");
    [...records].reverse().forEach((r, i, arr) => {
      const tr = el("tr");
      if (analysis && analysis.record_id === r.id) tr.className = "current";
      const older = arr[i + 1];
      const scoreCell = el("td", "num");
      scoreCell.append(el("b", null, num(r.summary.score)));
      if (older) {
        const d = r.summary.score - older.summary.score;
        scoreCell.append(el("span", `chg ${d > 0 ? "good" : d < 0 ? "bad" : ""}`,
          ` ${d > 0 ? "▲" : d < 0 ? "▼" : "■"}${num(Math.abs(d))}`));
      }
      const nameCell = el("td");
      nameCell.append(el("span", null, r.mock_name));
      if (r.validation_passed === false) nameCell.append(el("span", "flag", " incomplete"));
      tr.append(el("td", null, r.mock_date), nameCell, scoreCell,
        el("td", "num", pct(r.summary.accuracy)), el("td", "num", pct(r.summary.attempt_rate)));
      sectionNames.forEach((name) => {
        const s = (r.sections || []).find((x) => x.label === name);
        tr.append(el("td", "num", s ? num(s.score) : "–"));
      });
      const act = el("td", "actions");
      const view = el("button", "small", "Open");
      view.addEventListener("click", () => openRecord(r.id));
      const del = el("button", "small ghost", "Delete");
      del.addEventListener("click", () => {
        if (del.dataset.armed) { deleteRecord(r.id); return; }
        del.dataset.armed = "1";
        del.textContent = "Confirm delete";
        del.classList.add("danger");
        setTimeout(() => {
          if (!del.isConnected) return;
          delete del.dataset.armed;
          del.textContent = "Delete";
          del.classList.remove("danger");
        }, 4000);
      });
      act.append(view, del);
      tr.append(act);
      tb.append(tr);
    });
    t.append(thead, tb);
  }

  async function loadRecords() {
    try {
      records = (await call("/api/records")).records || [];
    } catch (err) {
      $("progress-status").textContent = "Could not load saved mocks.";
      $("progress-empty").classList.add("hidden");
      showError(err.message);
      return;
    }
    renderProgress();
    renderRecords();
    suggestName();
  }

  async function openRecord(id) {
    try {
      const a = await call(`/api/records/${encodeURIComponent(id)}`);
      showReport(a, true);
    } catch (err) {
      showError(err.message);
    }
  }

  async function deleteRecord(id) {
    try {
      await call(`/api/records/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (analysis && analysis.record_id === id) {
        analysis = null;
        $("results").classList.add("hidden");
      }
      await loadRecords();
    } catch (err) {
      showError(err.message);
    }
  }

  /* -------------------------------------------------------------- upload */

  function suggestName() {
    const nameInput = $("mock-name");
    if (!nameInput.dataset.touched) nameInput.value = `Mock ${records.length + 1}`;
    noteName();
  }
  function noteName() {
    const name = $("mock-name").value.trim().toLowerCase();
    const clash = records.find((r) => r.mock_name.trim().toLowerCase() === name);
    $("name-note").textContent = clash
      ? `A mock called "${clash.mock_name}" is already saved (${clash.mock_date}). Analysing again will replace it.`
      : "";
  }
  $("mock-name").addEventListener("input", (e) => { e.target.dataset.touched = "1"; noteName(); });

  function renderFiles() {
    const list = $("filelist");
    list.replaceChildren();
    files.forEach((f, i) => {
      const li = el("li");
      li.append(el("span", null, `${f.name} · ${(f.size / 1e6).toFixed(1)} MB`));
      const rm = el("button", null, "×");
      rm.title = "Remove";
      rm.addEventListener("click", () => { files.splice(i, 1); renderFiles(); });
      li.append(rm);
      list.append(li);
    });
    $("analyse").disabled = files.length === 0;
  }

  function addFiles(incoming) {
    for (const f of incoming) {
      if (f.type !== "application/pdf" && !f.name.toLowerCase().endsWith(".pdf")) continue;
      if (!files.some((x) => x.name === f.name && x.size === f.size)) files.push(f);
    }
    renderFiles();
  }

  const dz = $("dropzone");
  dz.addEventListener("click", () => $("files").click());
  dz.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("files").click(); }
  });
  $("files").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));

  $("mock-date").value = new Date().toISOString().slice(0, 10);

  function step(text) {
    const li = el("li", null, `… ${text}`);
    $("steps").append(li);
    return {
      done: (t) => { li.className = "done"; li.textContent = `✓ ${t || text}`; },
      fail: (t) => { li.className = "fail"; li.textContent = `✗ ${t || text}`; },
    };
  }

  function showError(message) {
    const box = $("error");
    box.textContent = message;
    box.classList.remove("hidden");
  }

  $("analyse").addEventListener("click", async () => {
    $("analyse").disabled = true;
    $("error").classList.add("hidden");
    $("steps").replaceChildren();
    $("progress").classList.remove("hidden");

    try {
      const byNumber = new Map();
      let meta = {};
      const warnings = [];
      let source = "text";
      let backend = "";

      for (const file of files) {
        const s = step(`Reading ${file.name}`);
        const fd = new FormData();
        fd.append("file", file, file.name);
        let out;
        try {
          out = await call("/api/parse", { method: "POST", body: fd });
        } catch (err) {
          s.fail(`${file.name}: ${err.message.split("\n")[0]}`);
          throw err;
        }
        out.questions.forEach((q) => byNumber.set(q.q_no, q));
        if (out.source === "image") source = "image";
        if (!backend) backend = out.ocr_backend || "";
        warnings.push(...(out.warnings || []));
        if (!meta.roll_no && out.meta && out.meta.roll_no) meta = out.meta;
        s.done(`${file.name}: ${out.questions.length} questions`);
      }

      const questions = [...byNumber.values()].sort((a, b) => a.q_no - b.q_no);
      const s2 = step(`Scoring and saving ${questions.length} questions`);
      const a = await call("/api/analyse", {
        method: "POST",
        json: true,
        body: {
          questions,
          meta,
          marking: {
            correct: parseFloat($("mark-correct").value) || 2,
            wrong: parseFloat($("mark-wrong").value) || 0,
            skipped: 0,
          },
          mock_name: $("mock-name").value.trim() || `Mock ${records.length + 1}`,
          mock_date: $("mock-date").value,
          save: true,
          source,
          ocr_backend: backend,
          warnings,
        },
      });
      s2.done(a.replaced_existing
        ? `Scored ${questions.length} questions; replaced the saved "${a.mock_name}"`
        : `Scored ${questions.length} questions; saved as "${a.mock_name}"`);
      a.source = source;
      a.ocr_backend = backend;
      a.warnings = warnings;

      files = [];
      renderFiles();
      delete $("mock-name").dataset.touched;
      await loadRecords();
      showReport(a, true);
    } catch (err) {
      showError(err.message);
    } finally {
      $("analyse").disabled = files.length === 0;
    }
  });

  /* -------------------------------------------------------------- report */

  function table(headers, rows) {
    const t = el("table");
    const thead = el("thead");
    const htr = el("tr");
    headers.forEach((h) => htr.append(el("th", h.num ? "num" : null, h.label ?? h)));
    thead.append(htr);
    t.append(thead);
    const tb = el("tbody");
    rows.forEach((r) => {
      const tr = el("tr");
      r.forEach((c, i) => {
        const td = el("td", headers[i] && headers[i].num ? "num" : null);
        if (c instanceof Node) td.append(c); else td.textContent = esc(c);
        tr.append(td);
      });
      tb.append(tr);
    });
    t.append(tb);
    const wrap = el("div", "scroll");
    wrap.append(t);
    return wrap;
  }

  function bar(value, max, tone) {
    const wrap = el("div", `bar${tone ? " " + tone : ""}`);
    const fill = el("i");
    fill.style.width = `${Math.max(0, Math.min(100, (value / (max || 1)) * 100))}%`;
    wrap.append(fill);
    return wrap;
  }

  function showReport(a, scroll) {
    analysis = a;
    render(a);
    renderRecords();
    document.querySelector('.tab[data-tab="summary"]').click();
    $("results").classList.remove("hidden");
    if (scroll) $("results").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function render(a) {
    const head = $("headline");
    head.replaceChildren();
    const top = el("div", "card-head");
    top.append(el("h2", null, `${a.mock_name} · ${a.mock_date}`));
    const close = el("button", "small ghost", "Close");
    close.addEventListener("click", () => {
      $("results").classList.add("hidden");
      analysis = null;
      renderRecords();
    });
    top.append(close);
    head.append(top);
    const who = [a.meta.candidate_name, a.meta.roll_no, a.meta.test_date, a.meta.shift]
      .filter(Boolean).join(" · ");
    if (who) head.append(el("p", "hint", who));

    const m = el("div", "metrics");
    m.append(
      tile("Score", num(a.summary.score), null, `out of ${num(a.summary.max_score)}`),
      tile("Accuracy", pct(a.summary.accuracy), null, `${a.summary.correct} of ${a.summary.attempted} attempted`),
      tile("Attempt rate", pct(a.summary.attempt_rate), null, `${a.summary.skipped} left blank`),
      tile("Wrong", `${a.summary.wrong}`, null, `${num(a.summary.wrong * a.marking.wrong)} marks`),
    );
    head.append(m);

    if (a.trend.available) {
      const tr = el("div", "trend");
      tr.append(el("p", null, a.trend.text));
      const fmt = (xs) => xs.map(([t, x, y]) => `${t} (${Math.round(x)}% → ${Math.round(y)}%)`).join(", ");
      if (a.trend.improved.length) tr.append(el("p", "hint", `Improved: ${fmt(a.trend.improved)}`));
      if (a.trend.declined.length) tr.append(el("p", "hint", `Slipped: ${fmt(a.trend.declined)}`));
      head.append(tr);
    }
    if (!a.validation.passed) {
      const n = el("div", "notice");
      n.append(el("b", null, `This sheet did not validate: ${a.validation.problems.length} problem(s).`));
      const ul = el("ul");
      a.validation.problems.slice(0, 15).forEach((p) => ul.append(el("li", null, p)));
      n.append(ul, el("p", "hint", "The usual cause is uploading only some of the four PART files."));
      head.append(n);
    }
    if (a.source === "image") {
      head.append(el("div", "notice",
        `This sheet had no text layer, so it was read from the page images (OCR: ${a.ocr_backend || "none"}). ` +
        `Scores come from the option colours and are unaffected; question text may contain OCR errors.`));
    }
    if (a.warnings && a.warnings.length) {
      const d = el("details", "notice");
      d.append(el("summary", null, `Parser notes (${a.warnings.length})`));
      const ul = el("ul");
      a.warnings.slice(0, 40).forEach((w) => ul.append(el("li", null, w)));
      d.append(ul);
      head.append(d);
    }

    /* summary */
    const ps = $("panel-summary");
    ps.replaceChildren(el("h3", null, "By section"));
    ps.append(table(
      ["Section", { label: "Score", num: true }, { label: "Correct", num: true },
        { label: "Wrong", num: true }, { label: "Skipped", num: true },
        { label: "Accuracy", num: true }, "", { label: "Attempted", num: true }],
      a.sections.map((s) => [
        s.label, `${num(s.score)} / ${num(s.max_score)}`, s.correct, s.wrong,
        s.skipped, pct(s.accuracy), bar(s.accuracy, 100, s.accuracy >= 60 ? "good" : "bad"),
        pct(s.attempt_rate),
      ])));
    ps.append(el("h3", null, "Attempt strategy"), el("p", null, a.advice.text));
    if (a.weak.length) {
      ps.append(el("h3", null, "Weak areas"));
      ps.append(el("p", "hint", "Sorted by marks lost. The top of this list is where a week of work pays best."));
      ps.append(table(
        ["Topic", "Section", { label: "Marks lost", num: true }, "Why"],
        a.weak.map((w) => [w.topic, w.section, num(w.marks_lost), w.reason])));
    }
    if (a.unclassified) {
      ps.append(el("p", "hint",
        `${a.unclassified} question(s) matched no keyword in topics.yaml and are excluded from that table.`));
    }

    /* topics */
    const pt = $("panel-topics");
    pt.replaceChildren(el("h3", null, "Every topic, worst first"));
    pt.append(table(
      ["Topic", "Section", { label: "Q", num: true }, { label: "Correct", num: true },
        { label: "Wrong", num: true }, { label: "Skipped", num: true },
        { label: "Accuracy", num: true }, "", { label: "Marks lost", num: true }],
      a.topics.map((t) => [
        t.label, t.section, t.total, t.correct, t.wrong, t.skipped, pct(t.accuracy),
        bar(t.accuracy, 100, t.accuracy >= 60 ? "good" : "bad"), num(t.marks_lost),
      ])));

    /* plan */
    const pp = $("panel-plan");
    pp.replaceChildren();
    a.plan.forEach((d) => {
      const det = el("details", "day");
      if (d.day === 1) det.open = true;
      det.append(el("summary", null, `Day ${d.day}: ${d.focus}`));
      d.blocks.forEach((b) => {
        const wrap = el("div", "block");
        wrap.append(el("b", null, b.title), el("p", null, b.body));
        det.append(wrap);
      });
      pp.append(det);
    });

    /* wrong answers */
    const pw = $("panel-wrong");
    pw.replaceChildren(el("h3", null, `Wrong answers (${a.wrong_answers.length})`));
    if (!a.wrong_answers.length) {
      pw.append(el("p", null, "Nothing wrong on this paper."));
    } else {
      pw.append(table(
        [{ label: "Q", num: true }, "Topic", "Question", "You answered", "Correct answer"],
        a.wrong_answers.map((r) => [
          r["Q.No"], r.Topic, r.Question, r["You answered"], r["Correct answer"]])));
    }
    pw.append(el("h3", null, `Skipped (${a.skipped_answers.length})`));
    if (a.skipped_answers.length) {
      pw.append(table(
        [{ label: "Q", num: true }, "Topic", "Question", "Correct answer"],
        a.skipped_answers.map((r) => [r["Q.No"], r.Topic, r.Question, r["Correct answer"]])));
    } else {
      pw.append(el("p", null, "Nothing was left blank."));
    }

    $("dl-unmatched").classList.toggle("hidden", !a.unclassified);
  }

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((p) => p.classList.add("hidden"));
      btn.classList.add("active");
      $(`panel-${btn.dataset.tab}`).classList.remove("hidden");
    });
  });

  /* ------------------------------------------------------------ downloads */

  function download(name, text, type) {
    const blob = new Blob([text], { type });
    const url = URL.createObjectURL(blob);
    const a = el("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const slug = () => (analysis ? analysis.mock_name : "mock").replace(/\s+/g, "_").toLowerCase();

  $("dl-report").addEventListener("click", () =>
    analysis && download(`${slug()}_analysis.md`, analysis.report_markdown, "text/markdown"));
  $("dl-unmatched").addEventListener("click", () =>
    analysis && download("unmatched.csv", analysis.unmatched_csv, "text/csv"));
  $("dl-all").addEventListener("click", async () => {
    try {
      download("history.csv", await call("/api/records/history.csv"), "text/csv");
    } catch (err) {
      showError(err.message);
    }
  });

  /* ------------------------------------------------------------- start up */

  call("/api/health")
    .then((h) => {
      const note = $("ocr-note");
      note.textContent = h.ocr_backends && h.ocr_backends.length
        ? "Screen-capture PDFs (one image per page, no text) are supported. They are read with OCR, which takes a few seconds per page."
        : "This server has no OCR, so screen-capture PDFs cannot be read. Open the sheet in Chrome and use Print → Save as PDF.";
    })
    .catch(() => { /* advisory only */ });

  let resizeTimer = 0;
  let lastWidth = window.innerWidth;
  window.addEventListener("resize", () => {
    if (window.innerWidth === lastWidth) return;
    lastWidth = window.innerWidth;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => records.length && renderProgress(), 150);
  });

  loadRecords();
})();
