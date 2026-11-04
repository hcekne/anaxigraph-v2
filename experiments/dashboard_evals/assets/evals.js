"use strict";

const AnaxiGraphEvals = (() => {
  const metrics = { existing_prod_files_edited: "Existing files edited", agent_seconds: "Coding time (seconds)", cumulative_rate: "Cumulative acceptance rate" };
  const numeric = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;
  const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const label = (value) => typeof value === "string" && value.length > 0 && value.length <= 200;
  function validateReport(value) {
    const pending = [[value, 0]];
    while (pending.length) {
      const [item, depth] = pending.pop();
      if (depth > 64) throw new Error("Report nesting must not exceed 64 levels.");
      if (item !== null && typeof item === "object") {
        for (const child of Object.values(item)) pending.push([child, depth + 1]);
      }
    }
    if (!object(value) || !object(value.arms) || !object(value.decision) || !Array.isArray(value.chains) || value.chains.length > 100 || Object.keys(value.arms).length > 20) {
      throw new Error("Expected report.py JSON with arms and up to 100 chains.");
    }
    const numbers = (row, keys) => keys.every((key) => row[key] == null || numeric(row[key]));
    const rates = (row, keys) => keys.every((key) => row[key] == null || (numeric(row[key]) && row[key] <= 1));
    const names = new Set();
    for (const chain of value.chains) {
      if (!object(chain) || !label(chain.chain) || names.has(chain.chain) || !label(chain.arm) || !Object.hasOwn(value.arms, chain.arm)
          || !Array.isArray(chain.steps) || chain.steps.length > 21 || !Array.isArray(chain.probes) || chain.probes.length > 3) {
        throw new Error("Each chain needs a unique name, known arm, steps and probes.");
      }
      if (chain.probes.some((row) => !object(row) || !label(row.probe))) throw new Error("Invalid probe evidence.");
      if (chain.verification != null && !object(chain.verification)) throw new Error("Invalid chain qualification.");
      names.add(chain.chain);
      const steps = new Set();
      for (const row of chain.steps) {
        if (!object(row) || !Number.isInteger(row.step) || row.step < 0 || row.step > 20 || steps.has(row.step)
            || !numbers(row, ["existing_prod_files_edited", "agent_seconds", "regressions", "analysis_seconds"])
            || !rates(row, ["cumulative_rate", "ci_selected_fraction"])) {
          throw new Error("Steps need unique numbers 0–20 and finite, nonnegative metrics; rates must be 0–1.");
        }
        if (row.new_pass != null && (typeof row.new_pass !== "string" || row.new_pass.length > 40 || !/^\d+\/\d+$/.test(row.new_pass))) {
          throw new Error("New acceptance counts must use passed/total form.");
        }
        steps.add(row.step);
      }
    }
    for (const [name, arm] of Object.entries(value.arms)) {
      if (!label(name) || !object(arm) || !numbers(arm, ["chains", "last_step", "final_regressions", "agent_minutes_total", "analysis_minutes_total", "mutants_missed_by_selection"])
          || !rates(arm, ["final_cumulative_rate", "final_ci_selected_fraction", "final_ci_executing_recall"])) throw new Error("Invalid arm summary metrics.");
    }
    return value;
  }
  function display(value, suffix = "") {
    return numeric(value) ? `${Number(value.toFixed(3)).toLocaleString()}${suffix}` : "Unknown";
  }
  function bind() {
    let request = null;
    async function requestAnalysis(kind) {
      const scope = lines($("#analysis-scope").value);
      const packet = await api("/api/analysis/request", { kind, scope: scope.length ? scope : null });
      request = packet;
      $("#analysis-title").textContent = { annotate: "Module annotation", patterns: "Exhaustive pattern task", architecture: "Component boundary scout", delivery: "CI/CD pipeline review", "fresh-eyes": "Blind Fresh Eyes design" }[kind];
      $("#analysis-state").textContent = "Awaiting agent result";
      $("#analysis-packet").textContent = formatJSON(packet);
      $("#blind-note").hidden = kind !== "fresh-eyes";
      $("#submit-analysis").disabled = false;
      $("#copy-task").disabled = false;
      $("#analysis-result").value = "";
      switchPage("evals");
      notice(kind === "patterns"
        ? "Exhaustive task prepared: every catalogue pattern needs detailed evaluation. Run pattern analysis uses fast screening by default."
        : "Task prepared. Give its packet and response schema to your agent.");
    }

    document.querySelectorAll("[data-kind]").forEach((button) => button.addEventListener("click", () => action(button, () => requestAnalysis(button.dataset.kind))));
    $("#copy-task").addEventListener("click", () => action($("#copy-task"), async () => {
      await navigator.clipboard.writeText(formatJSON(request));
      notice("Task copied.");
    }));
    $("#submit-analysis").addEventListener("click", () => action($("#submit-analysis"), async () => {
      if (!request) throw new Error("Prepare a task first.");
      const result = JSON.parse($("#analysis-result").value);
      await api("/api/analysis/submit", { request_id: request.request_id, result });
      $("#analysis-state").textContent = "Validated and saved";
      await Promise.all([refresh(), refreshReviews()]);
      notice("Agent result validated and saved for its requested snapshot.");
    }));
    let loading = false, importGeneration = 0, events = [], next = 0, report = null;
    const node = (tag, text, className) => textElement(tag, text, className);
    const evidence = (value) => { $("#eval-inspection").textContent = formatJSON(value); };
    function download(value, name) {
      const url = URL.createObjectURL(new Blob([formatJSON(value)], { type: "application/json" }));
      const link = node("a", "");
      link.href = url;
      link.download = name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    function card(parent, title, value, note) {
      const item = node("article", "");
      item.append(node("span", title, "metric-label"), node("strong", value), node("small", note));
      parent.append(item);
    }
    function filters() {
      for (const [id, field] of [["experiment", "experiment_id"], ["arm", "arm"], ["task", "task_id"]]) {
        const select = $(`#eval-${id}`), selected = select.value;
        const values = [...new Set(events.map((row) => field === "task_id" ? row.task_id : row.payload?.[field]).filter(Boolean))].sort();
        select.replaceChildren(new Option(`All ${id}s`, ""), ...values.map((value) => new Option(value, value)));
        select.value = values.includes(selected) ? selected : "";
      }
    }
    function renderEvents() {
      const matching = events.filter((row) => [["experiment", row.payload?.experiment_id], ["arm", row.payload?.arm], ["task", row.task_id]]
        .every(([id, value]) => !$(`#eval-${id}`).value || $(`#eval-${id}`).value === value));
      $("#eval-event-list").replaceChildren(...matching.map((row) => {
        const button = node("button", `${row.kind} · ${row.payload?.arm || "unassigned"} · ${row.at}`, "eval-event");
        button.addEventListener("click", () => {
          $("#eval-event-list").querySelectorAll("button").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
          evidence(row);
        });
        button.setAttribute("aria-pressed", "false");
        return button;
      }));
      $("#eval-loaded").textContent = `${matching.length} matching / ${events.length} loaded events. Filters apply to loaded pages; summary covers the retained ledger.`;
      if (!matching.length) $("#eval-event-list").append(node("p", "No matching events loaded.", "empty"));
    }
    async function load(reset) {
      if (loading) return;
      loading = true;
      try {
        const result = await api(`/api/evidence?limit=50&offset=${reset ? 0 : next}`);
        events = reset ? result.events.items : [...events, ...result.events.items];
        next = result.events.next_offset;
        const summary = result.summary, parent = $("#eval-ledger-metrics");
        parent.replaceChildren();
        card(parent, "RETAINED EVENTS", display(summary.events), `${result.events.total} available · ${summary.enabled ? "Recording enabled" : "Recording disabled"}`);
        card(parent, "RETENTION", summary.complete ? "Complete" : "Partial", `${summary.dropped_events || 0} older events dropped`);
        card(parent, "REPORTED COST", display(summary.known_cost_usd, " USD"), `${summary.unknown_cost_calls || 0} calls with unknown cost · ${summary.cost_complete ? "Complete estimates" : "Incomplete estimates"}`);
        card(parent, "EXPERIMENT", summary.experiment_id || "Unassigned", `Arm: ${summary.arm || "unassigned"}`);
        $("#eval-more").hidden = next == null;
        $("#eval-total").textContent = `${events.length} / ${result.events.total} loaded`;
        filters();
        renderEvents();
      } finally { loading = false; }
    }
    function reportArms() {
      const parent = $("#eval-arm-cards");
      parent.replaceChildren();
      for (const [name, arm] of Object.entries(report.arms)) {
        const item = node("article", "", "panel eval-arm");
        item.append(node("h3", name), node("span", `Reported qualification: ${["passed", "failed", "incomplete"].includes(arm.verification_status) ? arm.verification_status : "unknown"}`, "pill"));
        const list = node("dl", "", "eval-stat-list");
        const rows = report.chains.filter((chain) => chain.arm === name).flatMap((chain) => chain.steps);
        const completeTime = (field, total) => rows.length && rows.every((row) => numeric(row[field])) ? total : null;
        const comparisons = [
          ["Chains", arm.chains], ["Last step", arm.last_step],
          ["Final acceptance rate", arm.final_cumulative_rate],
          ["Final regressions", arm.final_regressions],
          ["Final CI selected fraction", arm.final_ci_selected_fraction],
          ["Final observed consumer recall", arm.final_ci_executing_recall],
          ["Required mutants missed by selection", arm.mutants_missed_by_selection],
          ["Reported mean coding minutes", completeTime("agent_seconds", arm.agent_minutes_total)],
          ["Reported mean analysis minutes", completeTime("analysis_seconds", arm.analysis_minutes_total)],
        ];
        for (const [title, value] of comparisons) {
          const row = node("div", "");
          row.append(node("dt", title), node("dd", display(value)));
          list.append(row);
        }
        const inspect = node("button", "Inspect qualification and summary", "quiet");
        inspect.addEventListener("click", () => evidence(arm));
        item.append(list, inspect);
        parent.append(item);
      }
    }
    function chart(steps, metric) {
      const svgNS = "http://www.w3.org/2000/svg";
      function svg(tag, attributes, text) {
        const element = document.createElementNS(svgNS, tag);
        for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
        if (text !== undefined) element.textContent = text;
        return element;
      }
      const root = svg("svg", { viewBox: "0 0 680 220", role: "img", "aria-label": `${metrics[metric]} by step; missing values leave gaps. Exact values in the table below.` });
      const maximum = Math.max(1, ...steps.map((row) => numeric(row[metric]) ? row[metric] : 0));
      for (let tick = 0; tick <= 4; tick++) {
        const y = 180 - tick * 40;
        root.append(svg("line", { x1: 55, y1: y, x2: 650, y2: y, class: "eval-gridline" }));
        root.append(svg("text", { x: 48, y: y + 4, "text-anchor": "end" }, display(maximum * tick / 4)));
      }
      let previous = null;
      for (const row of steps) {
        const x = 55 + row.step / 20 * 595;
        root.append(svg("text", { x, y: 205, "text-anchor": "middle" }, row.step));
        if (!numeric(row[metric])) { previous = null; continue; }
        const y = 180 - row[metric] / maximum * 160;
        if (previous && previous.step === row.step - 1) root.append(svg("line", { x1: previous.x, y1: previous.y, x2: x, y2: y, class: "eval-trend" }));
        root.append(svg("circle", { cx: x, cy: y, r: 4, class: "eval-point" }));
        previous = { x, y, step: row.step };
      }
      $("#eval-chart").replaceChildren(root);
    }
    function renderChain() {
      const chain = report?.chains.find((item) => item.chain === $("#eval-chain").value);
      if (!chain) return;
      const steps = [...chain.steps].sort((a, b) => a.step - b.step);
      $("#eval-step-rows").replaceChildren(...steps.map((row) => {
        const tr = node("tr", ""), cell = node("td", ""), button = node("button", `Step ${row.step}`, "module-link");
        button.addEventListener("click", () => evidence(row));
        cell.append(button);
        tr.append(cell, node("td", row.new_pass ?? "Unknown"), ...[row.cumulative_rate, row.existing_prod_files_edited, row.regressions, row.ci_selected_fraction, row.agent_seconds].map((value) => node("td", display(value))));
        return tr;
      }));
      chart(steps, $("#eval-metric").value);
      $("#eval-chain-state").textContent = `${steps.length} measured steps · ${chain.probes.length} probe records. Qualification and probes are available in source evidence.`;
      evidence(chain);
    }
    $("#eval-load").addEventListener("click", () => action($("#eval-load"), () => load(true)));
    $("#eval-more").addEventListener("click", () => action($("#eval-more"), () => load(false)));
    for (const id of ["experiment", "arm", "task"]) $(`#eval-${id}`).addEventListener("change", renderEvents);
    $("#eval-export").addEventListener("click", () => action($("#eval-export"), async () => {
      const receipt = await api("/api/evidence/export", {});
      download(receipt, "anaxigraph-trial.json");
      $("#eval-export-state").textContent = `Export prepared: ${receipt.events?.length || 0} events · ${receipt.format || "unknown format"}. Browser download requested.`;
      evidence(receipt.summary);
    }));
    $("#eval-import").addEventListener("change", async (event) => {
      const generation = ++importGeneration;
      const file = event.target.files[0];
      if (!file) return;
      try {
        if (file.size > 2_000_000) throw new Error("Report must be at most 2 MB.");
        const candidate = validateReport(JSON.parse(await file.text()));
        if (generation !== importGeneration) return;
        report = candidate;
        $("#eval-report-state").textContent = `${file.name} · ${report.chains.length} chains. Local file, unverified provenance; numbers and qualification are reported claims, not independent proof.`;
        reportArms();
        $("#eval-chain").replaceChildren(...report.chains.map((chain) => new Option(`${chain.chain} · ${chain.arm}`, chain.chain)));
        $("#eval-report-view").hidden = false;
        $("#eval-chain-view").hidden = !report.chains.length;
        renderChain();
        evidence({ decision: report.decision ?? "Not reported", qualification: "Imported claims; inspect chain evidence before drawing conclusions." });
        notice("Report loaded locally. No model calls were started.");
      } catch (error) { notice(`Report not loaded: ${error.message}`, true); }
      finally { if (generation === importGeneration) event.target.value = ""; }
    });
    $("#refresh").addEventListener("click", () => {
      if (!$("#page-evals").hidden) action($("#eval-load"), () => load(true));
    });
    $("#eval-chain").addEventListener("change", renderChain);
    $("#eval-metric").addEventListener("change", renderChain);
    $("[data-page='evals']").addEventListener("click", () => {
      action($("#eval-load"), () => load(true));
    });
  }
  return { validateReport, display, bind };
})();
if (typeof module !== "undefined") module.exports = AnaxiGraphEvals;
if (typeof document !== "undefined") AnaxiGraphEvals.bind();
