"use strict";

const $ = (selector) => document.querySelector(selector);
const state = { sort: "path", descending: false, offset: 0, limit: 25, selected: null, request: null };
let moduleGeneration = 0;
let entityGeneration = 0;
let reviewOffset = 0;

function notice(message, error = false) {
  const element = $("#notice");
  element.hidden = !message;
  element.classList.toggle("error", error);
  element.textContent = message;
}

async function api(path, body, timeout = 20000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("The request timed out. Refresh to check the saved state.");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

async function action(button, operation) {
  if (button.disabled) return;
  button.disabled = true;
  const label = button.textContent;
  button.textContent = "Working…";
  notice("");
  try {
    await operation();
  } catch (error) {
    notice(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

function formatJSON(value) { return JSON.stringify(value, null, 2); }
function lines(value) { return value.split("\n").map((line) => line.trim()).filter(Boolean); }
function title(node) {
  return node.kind !== "module" && node.path ? `${node.name} · ${node.path}` : (node.path || node.name || node.id);
}
function short(value, length = 37) { return value.length > length ? `…${value.slice(-length + 1)}` : value; }
function bytes(value) {
  if (!Number.isFinite(value)) return "—";
  return value < 1048576 ? `${(value / 1024).toFixed(0)} KB` : `${(value / 1048576).toFixed(1)} MB`;
}

function switchPage(name) {
  document.querySelectorAll(".page").forEach((page) => { page.hidden = page.id !== `page-${name}`; });
  document.querySelectorAll("[data-page]").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === name);
    if (button.dataset.page === name) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

function renderCheckout(snapshot, root) {
  $("#repository-name").textContent = String(root).split("/").filter(Boolean).pop() || "Your codebase";
  $("#checkout").textContent = snapshot
    ? `${root} · ${(snapshot.revision || "No Git revision").slice(0, 12)}${snapshot.dirty ? " · working changes" : ""}`
    : `${root} · build the first structural map to begin`;
  $("#snapshot-state").textContent = snapshot ? `Snapshot ${snapshot.id.slice(0, 12)}` : "No saved snapshot";
}

function renderCoverage(data) {
  const diagnostics = data.diagnostics || [];
  $("#map-state").textContent = data.snapshot ? (diagnostics.length ? "Partial coverage" : "Available") : "Not mapped";
  $("#coverage-summary").textContent = `${data.coverage || "Python/PYI syntax and scoped references"}. Other languages are not parsed in this milestone. ${data.unresolved_relationships || 0} ambiguous or unresolved relationships.`;
  $("#coverage-diagnostics").hidden = !diagnostics.length;
  $("#diagnostic-count").textContent = `${diagnostics.length} reported analysis diagnostics — inspect coverage gaps`;
  $("#diagnostic-items").replaceChildren(...diagnostics.map((item) => textElement("li", item)));
}

function renderMetrics(data) {
  $("#entity-count").textContent = `${data.modules || 0} / ${data.symbols || 0}`;
  const annotations = data.annotations || {};
  $("#annotation-count").textContent = `${annotations.current || 0} / ${data.modules || 0}`;
  $("#annotation-state").textContent = `${annotations.stale || 0} stale · ${annotations.missing || 0} awaiting agent annotation`;
  const storage = data.storage || {};
  $("#storage-size").textContent = bytes(storage.bytes ?? storage.database_bytes ?? storage.size_bytes);
}

async function refreshStatus() {
  const data = await api("/api/status");
  renderCheckout(data.snapshot, data.snapshot?.root || data.root || data.repository || "Checkout");
  renderCoverage(data);
  renderMetrics(data);
  if (data.baseline_id) $("#change-baseline").textContent = `Pinned baseline ${data.baseline_id} · preserved across scans until you start another change.`;
  return data;
}

function moduleRow(node) {
  const row = document.createElement("tr");
  row.classList.toggle("selected", node.id === state.selected);
  const moduleCell = document.createElement("td");
  const button = textElement("button", title(node), "module-link");
  button.addEventListener("click", () => selectEntity(node.id).catch((error) => notice(error.message, true)));
  moduleCell.append(button);
  const overview = (node.data?.annotation?.overview || "Purpose not annotated yet")
    + (node.data?.annotation_status === "stale" ? " · stale annotation" : "");
  moduleCell.append(textElement("div", overview, "module-overview"));
  row.append(moduleCell);
  const parseFailed = node.data?.parse_status && node.data.parse_status !== "parsed";
  for (const value of [node.data?.loc ?? "Unknown", parseFailed ? "Unknown" : (node.data?.function_count ?? "Unknown")]) {
    row.append(textElement("td", String(value)));
  }
  const boundary = node.data?.boundary || {};
  const publicNames = textElement("td", String(boundary.public_names ?? "Unknown"));
  publicNames.title = `Public names observed using ${boundary.basis || "unrecorded"}. A count is not a behavioral contract.`;
  const concerns = [];
  if (boundary.private_accesses > 0) concerns.push(`${boundary.private_accesses} private accesses`);
  if (boundary.cycle_size > 0) concerns.push(`${boundary.cycle_size}-module cycle`);
  const boundaryCell = textElement("td", parseFailed ? "Unknown" : node.data?.is_test ? "Test observations" : (concerns.join(" · ") || "None observed"));
  boundaryCell.title = node.data?.is_test
    ? "Test imports are not classified as production boundary violations."
    : "Observed private accesses and production dependency cycles. No observed concerns does not prove safety.";
  row.append(publicNames, boundaryCell);
  return row;
}

function renderModulePage(data) {
  const items = data.items || [];
  document.querySelectorAll("[data-sort]").forEach((button) => {
    button.closest("th").setAttribute("aria-sort", button.dataset.sort === state.sort
      ? (state.descending ? "descending" : "ascending") : "none");
  });
  $("#module-total").textContent = `${data.total || 0} modules`;
  $("#module-rows").replaceChildren(...items.map(moduleRow));
  $("#module-empty").hidden = items.length > 0;
  $("#page-label").textContent = `${items.length ? state.offset + 1 : 0}–${state.offset + items.length} of ${data.total || 0}`;
  $("#previous").disabled = state.offset === 0;
  $("#next").disabled = state.offset + items.length >= (data.total || 0);
}

async function refreshModules() {
  const generation = ++moduleGeneration;
  const query = new URLSearchParams({ sort: state.sort, descending: state.descending, limit: state.limit, offset: state.offset });
  const data = await api(`/api/modules?${query}`);
  if (generation === moduleGeneration) renderModulePage(data);
}

function addLinkGroup(name, edges, direction, nodes) {
  const section = document.createElement("section");
  section.className = "link-group";
  const heading = document.createElement("h3");
  heading.textContent = `${name} (${edges.length})`;
  section.append(heading);
  for (const edge of edges.slice(0, 12)) {
    const id = direction === "incoming" ? edge.source : edge.target;
    const button = document.createElement("button");
    button.textContent = `${edge.kind} · ${short(nodes.get(id)?.name || id, 55)}${edge.resolution !== "resolved" ? ` · ${edge.resolution}` : ""}`;
    button.addEventListener("click", () => selectEntity(id).catch((error) => notice(error.message, true)));
    section.append(button);
  }
  $("#entity-links").append(section);
}

async function selectEntity(id) {
  const generation = ++entityGeneration;
  const result = await api(`/api/entity?${new URLSearchParams({ entity_id: id, limit: 20 })}`);
  if (generation !== entityGeneration) return;
  const entity = result.entity;
  state.selected = entity.id;
  const annotation = entity.data?.annotation || {};
  $("#entity-title").textContent = title(entity);
  $("#entity-overview").textContent = annotation.overview || entity.data?.overview || "No semantic annotation yet. Source facts are available below.";
  $("#entity-detail").textContent = annotation.detail || "";
  $("#entity-facts").replaceChildren();
  for (const value of [entity.kind, entity.data?.loc === undefined ? null : `${entity.data.loc} lines`, `line ${entity.line || 1}`, entity.data?.annotation_status, entity.data?.parse_status].filter(Boolean)) {
    const chip = document.createElement("span");
    chip.textContent = value;
    $("#entity-facts").append(chip);
  }
  $("#entity-json").textContent = formatJSON(result);
  const nodes = new Map((result.neighbors || []).map((node) => [node.id, node]));
  nodes.set(entity.id, entity);
  $("#entity-links").replaceChildren();
  addLinkGroup("Incoming", result.incoming || [], "incoming", nodes);
  addLinkGroup("Outgoing", result.outgoing || [], "outgoing", nodes);
  drawGraph(entity, result.incoming || [], result.outgoing || [], nodes);
  await refreshModules();
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  return element;
}

function drawGraph(entity, incoming, outgoing, nodes) {
  const left = [...new Map(incoming.filter((edge) => edge.source !== entity.id).map((edge) => [edge.source, edge])).values()].slice(0, 9);
  const right = [...new Map(outgoing.filter((edge) => edge.target !== entity.id).map((edge) => [edge.target, edge])).values()].slice(0, 9);
  const height = Math.max(260, Math.max(left.length, right.length) * 54 + 70);
  const center = { x: 440, y: height / 2 };
  const svg = svgElement("svg", { viewBox: `0 0 880 ${height}`, role: "img", "aria-label": `Connections around ${title(entity)}` });
  const defs = svgElement("defs");
  const marker = svgElement("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" });
  marker.append(svgElement("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#648273" }));
  defs.append(marker);
  svg.append(defs);
  function addNode(id, point, central = false) {
    const node = nodes.get(id);
    const group = svgElement("g", { class: `graph-node${central ? " center" : ""}`, transform: `translate(${point.x - 116},${point.y - 18})`, tabindex: "0", role: "button", "aria-label": node?.name || id });
    group.append(svgElement("rect", { width: 232, height: 36 }));
    const label = svgElement("text", { x: 116, y: 23, "text-anchor": "middle" });
    label.textContent = short(node?.name || id, 29);
    const full = svgElement("title");
    full.textContent = node ? title(node) : id;
    group.append(label, full);
    const activate = () => selectEntity(id).catch((error) => notice(error.message, true));
    group.addEventListener("click", activate);
    group.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); } });
    svg.append(group);
  }
  for (const [edges, isLeft] of [[left, true], [right, false]]) {
    edges.forEach((edge, index) => {
      const point = { x: isLeft ? 135 : 745, y: height / 2 + (index - (edges.length - 1) / 2) * 54 };
      const start = isLeft ? { x: point.x + 116, y: point.y } : { x: center.x + 116, y: center.y };
      const end = isLeft ? { x: center.x - 116, y: center.y } : { x: point.x - 116, y: point.y };
      const path = svgElement("path", { d: `M ${start.x} ${start.y} C ${(start.x + end.x) / 2} ${start.y}, ${(start.x + end.x) / 2} ${end.y}, ${end.x} ${end.y}`, class: `graph-edge ${edge.resolution !== "resolved" ? "uncertain" : ""}`, "marker-end": "url(#arrow)" });
      const label = svgElement("title");
      label.textContent = `${edge.kind} · ${edge.resolution}`;
      path.append(label);
      svg.append(path);
      addNode(isLeft ? edge.source : edge.target, point);
    });
  }
  addNode(entity.id, center, true);
  $("#graph").classList.remove("graph-empty");
  $("#graph").replaceChildren(svg);
}

async function requestAnalysis(kind) {
  const scope = lines($("#analysis-scope").value);
  const packet = await api("/api/analysis/request", { kind, scope: scope.length ? scope : null });
  state.request = packet;
  $("#analysis-title").textContent = { annotate: "Module annotation", patterns: "Exhaustive pattern task", architecture: "Component boundary scout", "fresh-eyes": "Blind Fresh Eyes design" }[kind];
  $("#analysis-state").textContent = "Awaiting agent result";
  $("#analysis-packet").textContent = formatJSON(packet);
  $("#blind-note").hidden = kind !== "fresh-eyes";
  $("#submit-analysis").disabled = false;
  $("#copy-task").disabled = false;
  $("#analysis-result").value = "";
  switchPage("workflows");
  notice(kind === "patterns"
    ? "Exhaustive task prepared: every catalogue pattern needs detailed evaluation. Run pattern analysis uses fast screening by default."
    : "Task prepared. Give its packet and response schema to your agent.");
}

async function refreshReviews(append = false) {
  if (!append) reviewOffset = 0;
  const data = await api(`/api/reviews?${new URLSearchParams({ limit: 12, offset: reviewOffset })}`);
  const items = data.items || data.reviews || [];
  if (!append) $("#saved-reviews").replaceChildren();
  $("#saved-reviews").classList.toggle("empty", !data.total);
  if (!items.length && !append) $("#saved-reviews").textContent = "No saved reviews yet.";
  reviewOffset += items.length;
  $("#more-reviews").hidden = data.next_offset == null;
  for (const item of items) {
    const section = document.createElement("details");
    section.className = "review-card";
    const summary = document.createElement("summary");
    summary.textContent = `${item.kind || "Review"} · ${item.status || "saved"} · ${item.producer || "producer unspecified"}`;
    const content = document.createElement("div");
    content.className = "review-content";
    content.textContent = item.summary || "Expand to load the full saved review.";
    section.append(summary, content);
    let loaded = false;
    let loading = false;
    section.addEventListener("toggle", async () => {
      if (!section.open || loaded || loading) return;
      loading = true;
      content.textContent = "Loading saved review…";
      try {
        const id = item.id || item.review_id;
        const detail = await api(`/api/entity?${new URLSearchParams({ entity_id: `review:${id}` })}`);
        renderReview(content, detail.entity.data);
        loaded = true;
      } catch (error) {
        content.textContent = `${error.message}. Close and expand this review to retry.`;
      } finally {
        loading = false;
      }
    });
    $("#saved-reviews").append(section);
  }
}

function textElement(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

function reviewTradeoffs(parent, label, items) {
  parent.append(textElement("h4", label));
  const list = document.createElement("ul");
  for (const value of items || []) list.append(textElement("li", value));
  parent.append(list);
}

function alternativeCard(alternative, index) {
  const card = document.createElement("article");
  card.className = "design-alternative";
  card.append(textElement("h3", `${index + 1}. ${alternative.name}`));
  card.append(textElement("p", alternative.design));
  reviewTradeoffs(card, "Pros", alternative.pros);
  reviewTradeoffs(card, "Cons", alternative.cons);
  card.append(textElement("p", `Requirements: ${(alternative.requirements || []).join(", ")}`, "section-note"));
  card.append(textElement("h4", "Architecture diagram (Mermaid)"));
  card.append(textElement("pre", alternative.diagram, "design-diagram"));
  return card;
}

function patternCard(evaluation) {
  const card = document.createElement("details");
  card.className = "pattern-evaluation";
  const rating = evaluation.opportunity === null ? "insufficient evidence" : `opportunity ${evaluation.opportunity}/3`;
  card.append(textElement("summary", `${evaluation.pattern_id} · ${rating} · ${evaluation.module_id}`));
  if (evaluation.current_fit) card.append(textElement("p", `Current fit: ${evaluation.current_fit}`));
  if (evaluation.proposed_change) card.append(textElement("p", `Proposed change: ${evaluation.proposed_change}`));
  reviewTradeoffs(card, "Pros", evaluation.pros);
  reviewTradeoffs(card, "Cons", evaluation.cons);
  card.append(textElement("p", `Simpler alternative: ${evaluation.alternative}`));
  card.append(textElement("p", `Evidence: ${(evaluation.evidence || []).map((item) => `${item.path}:${item.line}`).join(", ") || "No source evidence supplied"}`, "section-note"));
  return card;
}

function renderPatternPages(parent, values) {
  const evaluations = [...values].sort((a, b) => (b.opportunity ?? -1) - (a.opportunity ?? -1));
  const count = textElement("p", `${evaluations.length} saved module × pattern evaluations`, "section-note");
  const list = document.createElement("div");
  let shown = 0;
  const more = textElement("button", "Show next 25 evaluations", "secondary");
  function appendPage() {
    list.append(...evaluations.slice(shown, shown + 25).map(patternCard));
    shown = Math.min(shown + 25, evaluations.length);
    count.textContent = `${shown} of ${evaluations.length} saved module × pattern evaluations · highest opportunities first`;
    more.hidden = shown >= evaluations.length;
  }
  more.addEventListener("click", appendPage);
  parent.append(count, list, more);
  appendPage();
}

function renderReview(parent, record) {
  parent.replaceChildren();
  const result = record.result?.result || record.result || {};
  parent.append(textElement("p", `${record.status || "saved"} · ${result.producer || "producer unspecified"} · snapshot ${record.snapshot_id}`, "section-note"));
  if (record.status !== "current") {
    parent.append(textElement("p", record.freshness_reason || "This review belongs to earlier or unknown analysis inputs and has not been refreshed.", "review-stale"));
  }
  if (Array.isArray(result.alternatives)) parent.append(...result.alternatives.map(alternativeCard));
  else if (Array.isArray(result.evaluations)) renderPatternPages(parent, result.evaluations);
  else if (Array.isArray(result.findings)) renderArchitecture(parent, result);
  const raw = document.createElement("details");
  raw.append(textElement("summary", "Full saved record and provenance"));
  raw.append(textElement("pre", formatJSON(record)));
  parent.append(raw);
}

function renderArchitecture(parent, result) {
  const outcomes = (result.assessments || []).reduce((counts, item) => ({ ...counts, [item.outcome]: (counts[item.outcome] || 0) + 1 }), {});
  parent.append(textElement("p", `${lensLabels[result.lens] || result.lens} scout · ${outcomes.finding || 0} modules with findings · ${outcomes.keep || 0} keep · ${outcomes.unknown || 0} unknown. Recommendations remain proposals.`, "section-note"));
  for (const finding of result.findings) {
    const card = textElement("article", "", "alternative-card");
    card.append(textElement("h3", finding.title), textElement("p", `Priority ${finding.priority} · ${finding.placement}`));
    card.append(textElement("p", finding.next_step));
    card.append(textElement("h4", "Proposed public contract"), textElement("p", finding.public_contract));
    card.append(textElement("h4", "Private implementation"), textElement("p", finding.private_implementation));
    reviewTradeoffs(card, "Dependencies", finding.dependencies);
    reviewTradeoffs(card, "Proposed verification; not executed", finding.verification);
    for (const option of finding.options || []) card.append(textElement("p", `${option.placement}: ${option.benefit} Cost: ${option.cost}`));
    reviewTradeoffs(card, "Uncertainty", finding.uncertainty);
    card.append(textElement("p", (finding.evidence || []).map((item) => `${item.path}:${item.line}`).join(" · "), "section-note"));
    parent.append(card);
  }
  reviewTradeoffs(parent, "Review uncertainty", result.uncertainty);
}

async function refresh() {
  const status = await refreshStatus();
  if (status.snapshot) {
    await refreshModules();
  } else {
    $("#module-rows").replaceChildren();
    $("#module-empty").hidden = false;
    $("#module-total").textContent = "0 modules";
    $("#page-label").textContent = "0–0";
    $("#previous").disabled = true;
    $("#next").disabled = true;
  }
}

const stageLabels = { annotate: "Mapping", "pattern-screen": "Fast pattern screening", architecture: "Component architecture scouts", patterns: "Detailed pattern review", "fresh-eyes": "Fresh Eyes" };
const lensLabels = { boundaries: "Component boundaries", packages: "Standalone packages or repos", verification: "Independent verification" };
let workflowSettings = null;
let workflowTimer = null;
const workflowStatuses = new Map();

function renderModelSettings(config) {
  workflowSettings = config;
  $("#workflow-workers").value = config.workers;
  $("#workflow-timeout").value = config.timeout_seconds ?? "";
  $("#workflow-exhaustive").checked = config.pattern_mode === "exhaustive";
  const parent = $("#stage-models");
  parent.replaceChildren();
  for (const [kind, label] of Object.entries(stageLabels)) {
    const group = document.createElement("div");
    const providerLabel = textElement("label", `${label} provider`);
    const provider = document.createElement("select");
    provider.id = `provider-${kind}`;
    for (const name of ["codex", "claude"]) {
      const option = textElement("option", name === "codex" ? "OpenAI / Codex" : "Claude");
      option.value = name;
      provider.append(option);
    }
    provider.value = config.stages[kind].provider;
    providerLabel.append(provider);
    const modelLabel = textElement("label", `${label} model`);
    const model = document.createElement("input");
    model.id = `model-${kind}`;
    model.value = config.stages[kind].model || "";
    model.placeholder = "CLI configured default";
    modelLabel.append(model);
    const effortLabel = textElement("label", `${label} Codex reasoning`);
    const effort = document.createElement("select");
    effort.id = `effort-${kind}`;
    for (const value of ["", "low", "medium", "high", "xhigh"]) {
      const option = textElement("option", value || "Provider default (unverified)");
      option.value = value;
      effort.append(option);
    }
    effort.value = config.stages[kind].reasoning_effort || "";
    effort.disabled = provider.value !== "codex";
    provider.addEventListener("change", () => { effort.disabled = provider.value !== "codex"; });
    effortLabel.append(effort);
    const fallbackLabel = textElement("label", "Stronger fallback models, in order");
    const fallback = document.createElement("input");
    fallback.id = `fallback-${kind}`;
    fallback.value = (config.stages[kind].fallbacks || []).map((x) => x.provider === config.stages[kind].provider ? x.model : `${x.provider}:${x.model}`).join(", ");
    fallback.placeholder = "model, claude:other-model";
    fallbackLabel.append(fallback);
    group.append(providerLabel, modelLabel, effortLabel, fallbackLabel);
    parent.append(group);
  }
  for (const provider of ["codex", "claude"]) {
    const pass = config.review_passes.find((x) => x.provider === provider);
    $(`#review-${provider}-enabled`).checked = !!pass;
    $(`#review-${provider}-model`).value = pass?.model || "";
  }
}

function readWorkflowSettings() {
  if (!workflowSettings) throw new Error("Model settings are still loading.");
  const timeout = $("#workflow-timeout").value.trim();
  const config = { workers: Number($("#workflow-workers").value), timeout_seconds: timeout === "" ? null : Number(timeout), pattern_mode: $("#workflow-exhaustive").checked ? "exhaustive" : "screened", stages: {}, review_passes: [] };
  for (const kind of Object.keys(stageLabels)) {
    const provider = $(`#provider-${kind}`).value;
    config.stages[kind] = { provider, model: $(`#model-${kind}`).value.trim(), fallbacks: $(`#fallback-${kind}`).value.split(",").map((x) => x.trim()).filter(Boolean).map((model) => {
      const separator = model.indexOf(":");
      const selected = separator > 0 ? model.slice(0, separator) : "";
      const worker = ["codex", "claude"].includes(selected) ? { provider: selected, model: model.slice(separator + 1) } : { provider, model };
      const previous = (workflowSettings.stages[kind].fallbacks || []).find((x) => x.provider === worker.provider && x.model === worker.model);
      return { ...previous, ...worker };
    }) };
    const effort = $(`#effort-${kind}`).value;
    if (provider === "codex" && effort) config.stages[kind].reasoning_effort = effort;
  }
  for (const provider of ["codex", "claude"]) {
    if ($(`#review-${provider}-enabled`).checked) {
      const model = $(`#review-${provider}-model`).value.trim();
      const previous = workflowSettings.review_passes.find((x) => x.provider === provider && x.model === model);
      config.review_passes.push({ ...previous, provider, model });
    }
  }
  return config;
}

function workflowTiming(runId) {
  const timing = document.createElement("details");
  timing.dataset.runId = runId;
  timing.append(textElement("summary", "Batch and model timing"));
  const content = document.createElement("div");
  const status = textElement("p", "Open to load recorded batch timings.", "section-note");
  const more = textElement("button", "Load more batch timings", "quiet");
  more.hidden = true;
  let offset = 0, shown = 0, loading = false;
  async function load(reset) {
    if (loading) return;
    loading = true;
    status.textContent = "Loading timings…";
    try {
      const query = new URLSearchParams({ run_id: runId, batch_offset: reset ? 0 : offset, batch_limit: 10 });
      const run = await api(`/api/workflows?${query}`);
      if (reset) {
        content.replaceChildren();
        shown = 0;
        if (run.timing_complete === false) content.append(textElement("p", "Earlier timing was not recorded; totals cover recorded work only.", "section-note"));
        for (const stage of run.stage_timings || []) content.append(textElement("p", `${stageLabels[stage.stage] || stage.stage}: ${stage.completed_batches}/${stage.total_batches} batches · ${stage.elapsed_seconds.toFixed(2)} summed batch seconds · ${stage.model_calls} worker calls · ${stage.reused_batches} reused.`, "section-note"));
        for (const model of run.model_usage || []) content.append(textElement("p", `${model.model || "Provider default"} ${model.reasoning_effort || ""}: ${model.calls} calls · ${model.elapsed_seconds.toFixed(2)} worker seconds · ${model.output_tokens === null ? `${model.known_output_tokens} known output tokens; ${model.unknown_output_calls} calls unknown` : `${model.output_tokens} output tokens`}.`, "section-note"));
      }
      for (const batch of run.batch_timings || []) content.append(textElement("p", `Batch ${batch.id} · ${stageLabels[batch.kind] || batch.kind}${batch.lens ? ` / ${lensLabels[batch.lens] || batch.lens}` : ""} · ${Number.isFinite(batch.elapsed_seconds) ? batch.elapsed_seconds.toFixed(2) : "Unknown"} seconds · ${batch.model_calls ?? "Unknown"} worker calls${batch.reused ? " · saved result reused" : batch.skipped ? " · empty shortlist; no detail call" : ""}.`, "section-note"));
      shown += (run.batch_timings || []).length;
      offset = run.next_batch_offset;
      more.hidden = offset === null;
      status.textContent = `Showing ${shown} of ${run.batch_timings_total} batch timings.`;
    } catch (error) {
      status.textContent = "Timing could not load. Close and reopen to retry.";
      notice(error.message, true);
    } finally { loading = false; }
  }
  timing.addEventListener("toggle", () => { if (timing.open) load(true); });
  more.addEventListener("click", () => action(more, () => load(false)));
  timing.append(content, status, more);
  return timing;
}

async function refreshWorkflows() {
  const data = await api("/api/workflows");
  const parent = $("#workflow-runs");
  const timings = new Map([...parent.querySelectorAll("details[data-run-id]")].map((element) => [element.dataset.runId, element]));
  parent.replaceChildren();
  parent.classList.toggle("empty", !data.items.length);
  if (!data.items.length) parent.textContent = "No workflows started.";
  let running = false;
  let finished = false;
  for (const run of data.items) {
    finished ||= run.status === "completed" && ["running", "queued"].includes(workflowStatuses.get(run.run_id));
    workflowStatuses.set(run.run_id, run.status);
    const card = textElement("article", "", "run-card");
    const heading = textElement("div", "", "run-heading");
    heading.append(textElement("h3", `${run.kind === "patterns" ? "Pattern discovery" : stageLabels[run.kind] || run.kind} · ${run.status}`));
    if (["failed", "interrupted"].includes(run.status)) {
      const button = textElement("button", "Retry unfinished batches", "secondary");
      button.addEventListener("click", () => action(button, async () => {
        await api(`/api/workflows/${run.run_id}/resume`, {});
        await refreshWorkflows();
      }));
      heading.append(button);
    }
    card.append(heading, textElement("p", run.phase, "run-phase"));
    const progress = document.createElement("progress");
    progress.max = Math.max(1, run.total_batches);
    progress.value = run.completed_batches;
    progress.setAttribute("aria-label", `${run.completed_batches} of ${run.total_batches} batches complete`);
    card.append(progress, textElement("p", `${run.completed_batches}/${run.total_batches} batches complete · ${run.active_workers} active workers · ${Math.round(run.elapsed_seconds)} seconds elapsed`, "section-note"));
    if (run.error) card.append(textElement("p", run.error, "run-error"));
    if (run.batch_retries || run.failed_attempts) card.append(textElement("p", `${run.batch_retries || 0} batch retries · ${run.failed_attempts || 0} failed attempts recorded${run.attempt_history_complete ? "" : " · earlier failure details unavailable"}.`, "section-note"));
    for (const failure of run.failures || []) card.append(textElement("p", `Batch ${failure.batch_id}: ${failure.error}`, "run-error"));
    for (const failure of run.attempt_failures || []) card.append(textElement("p", `Earlier attempt ${failure.attempt}, batch ${failure.batch_id} (${failure.phase}): ${failure.error}`, "run-error"));
    card.append(timings.get(run.run_id) || workflowTiming(run.run_id));
    if (run.reconciliation) card.append(textElement("p", `Final reconciliation: ${run.reconciliation.status}. ${run.record_exported ? "Portable record saved." : "Results saved locally."}`));
    if (run.reconciliation?.mode === "screened") card.append(textElement("p", `${run.reconciliation.screened_pairs} module × pattern checks screened · ${run.reconciliation.selected_pairs} selected · ${run.reconciliation.detailed_pairs} evaluated in detail. Screening is not a detailed rating.`, "section-note"));
    parent.append(card);
    running ||= ["running", "queued"].includes(run.status);
  }
  clearTimeout(workflowTimer);
  if (running) workflowTimer = setTimeout(() => refreshWorkflows().catch((error) => notice(error.message, true)), 1500);
  if (finished) await Promise.all([refresh(), refreshReviews()]);
}

async function startWorkflow(kind) {
  const scope = lines($("#workflow-scope").value);
  await api("/api/workflows/start", { kind, config: readWorkflowSettings(), scope: scope.length ? scope : null });
  await refreshWorkflows();
  notice("Full workflow started. Each completed batch is saved immediately; progress updates below.");
}

function renderPlanAssessment(result, request) {
  $("#change-validation").textContent = formatJSON(result);
  const parent = $("#architecture-decisions");
  parent.replaceChildren();
  const architecture = result.architecture;
  if (!architecture) return;
  const labels = { decision_required: "Choose how to address these proposals", decisions_recorded: "Decisions recorded", no_current_material_proposals: "No current material proposals for this plan", scope_incomplete: "Review a narrower scope to account for more proposals" };
  parent.append(textElement("h3", "Architecture decisions"), textElement("p", `${labels[architecture.status] || "Review status unavailable"}. Implementation remains unverified.`, "section-note"));
  if (architecture.truncated) parent.append(textElement("p", "More relevant proposals exist. Expand the saved reviews and assess a narrower scope.", "review-stale"));
  const entries = [];
  for (const requirement of architecture.requirements || []) {
    const card = textElement("div", "", "alternative-card");
    card.append(textElement("h4", requirement.title), textElement("p", requirement.suggestion));
    const choice = document.createElement("select");
    choice.setAttribute("aria-label", `Decision for ${requirement.title}`);
    for (const [value, label] of [["", "Choose a decision"], ["adopt", "Adopt"], ["defer", "Defer"], ["reject", "Reject"]]) {
      const option = textElement("option", label); option.value = value; choice.append(option);
    }
    const reason = document.createElement("textarea");
    reason.rows = 2; reason.placeholder = "Explain the decision"; reason.setAttribute("aria-label", `Reason for ${requirement.title}`);
    const saved = (architecture.decisions || []).find((item) => item.proposal_id === requirement.proposal_id);
    choice.value = saved?.decision || ""; reason.value = saved?.reason || "";
    card.append(choice, reason); parent.append(card);
    entries.push({ proposal_id: requirement.proposal_id, choice, reason });
  }
  if (!entries.length) return;
  const save = textElement("button", "Record architecture decisions", "secondary");
  save.addEventListener("click", () => action(save, async () => {
    const decisions = entries.map((item) => ({ proposal_id: item.proposal_id, decision: item.choice.value, reason: item.reason.value.trim() }));
    if (decisions.some((item) => !item.decision || !item.reason)) throw new Error("Choose a decision and give a reason for each proposal.");
    renderPlanAssessment(await api("/api/assess", { ...request, decisions }), request);
    notice("Architecture decisions recorded. This does not verify their implementation.");
  }));
  parent.append(save);
}

document.querySelectorAll("[data-run]").forEach((button) => button.addEventListener("click", () => action(button, () => startWorkflow(button.dataset.run))));
$("#load-workflows").addEventListener("click", () => action($("#load-workflows"), refreshWorkflows));
$("#save-workflow-settings").addEventListener("click", () => action($("#save-workflow-settings"), async () => {
  renderModelSettings(await api("/api/workflows/config", { config: readWorkflowSettings() }));
  notice("Model choices and parallel workers saved.");
}));
for (const operation of ["export", "import"]) {
  $(`#${operation}-record`).addEventListener("click", () => action($(`#${operation}-record`), async () => {
    const result = await api(`/api/record/${operation}`, {});
    const committed = result.committed === true ? "committed" : result.committed === false ? "not committed" : "commit unknown";
    const pushed = result.pushed === true ? "present in local upstream reference" : result.pushed === false ? "not in upstream reference" : "push status unknown";
    $("#portable-state").textContent = operation === "export" ? `Record saved (${result.bytes} bytes); ${committed}; ${pushed}.` : result.record_found === false ? "No portable record found in this repository." : `Reused ${result.annotations_imported || 0} annotations and ${result.analyses_imported || 0} analyses; incompatible saved results remain preserved.`;
    await refresh();
    notice(operation === "export" ? "Portable record saved in anaxigraph/. Commit and push it through Git." : "Compatible saved analyses imported into this checkout.");
  }));
}

document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => switchPage(button.dataset.page)));
document.querySelectorAll("[data-kind]").forEach((button) => button.addEventListener("click", () => action(button, () => requestAnalysis(button.dataset.kind))));
document.querySelectorAll("[data-sort]").forEach((button) => button.addEventListener("click", () => {
  state.descending = state.sort === button.dataset.sort ? !state.descending : button.dataset.sort !== "path";
  state.sort = button.dataset.sort;
  state.offset = 0;
  action(button, refreshModules);
}));
$("#refresh").addEventListener("click", () => action($("#refresh"), refresh));
$("#scan").addEventListener("click", () => action($("#scan"), async () => {
  await api("/api/scan", {}, 120000);
  state.offset = 0;
  await refresh();
  notice("Structural map saved. Semantic annotations and architectural reviews have separate readiness.");
}));
$("#previous").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); refreshModules().catch((error) => notice(error.message, true)); });
$("#next").addEventListener("click", () => { state.offset += state.limit; refreshModules().catch((error) => notice(error.message, true)); });
$("#copy-task").addEventListener("click", () => action($("#copy-task"), async () => {
  await navigator.clipboard.writeText(formatJSON(state.request));
  notice("Task copied.");
}));
$("#submit-analysis").addEventListener("click", () => action($("#submit-analysis"), async () => {
  if (!state.request) throw new Error("Prepare a task first.");
  const result = JSON.parse($("#analysis-result").value);
  await api("/api/analysis/submit", { request_id: state.request.request_id, result });
  $("#analysis-state").textContent = "Validated and saved";
  await Promise.all([refresh(), refreshReviews()]);
  notice("Agent result validated and saved for its requested snapshot.");
}));
$("#load-reviews").addEventListener("click", () => action($("#load-reviews"), refreshReviews));
$("#more-reviews").addEventListener("click", () => action($("#more-reviews"), () => refreshReviews(true)));
$("#new-change-baseline").addEventListener("click", () => action($("#new-change-baseline"), async () => {
  if (!$("#change-task").value.trim()) throw new Error("Describe the new change first.");
  const status = await api("/api/status");
  if (!status.snapshot) throw new Error("Refresh the structural map first.");
  const brief = await api("/api/prepare", { task: $("#change-task").value, baseline_id: status.snapshot.id });
  $("#change-brief").textContent = formatJSON(brief);
  $("#change-baseline").textContent = `Pinned baseline ${brief.baseline_id} · preserved across scans until you start another change.`;
}));
$("#prepare-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    const brief = await api("/api/prepare", { task: $("#change-task").value });
    $("#change-brief").textContent = formatJSON(brief);
    $("#change-baseline").textContent = `Pinned baseline ${brief.baseline_id} · preserved across scans until you start another change.`;
  });
});
$("#assess-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    if (!$("#change-task").value.trim()) throw new Error("Describe your intended change first.");
    const request = { task: $("#change-task").value, files: lines($("#planned-files").value) };
    renderPlanAssessment(await api("/api/assess", request), request);
  });
});
$("#validate-change").addEventListener("click", () => action($("#validate-change"), async () => {
  const receipts = $("#receipts").value.trim() ? JSON.parse($("#receipts").value) : null;
  $("#change-validation").textContent = formatJSON(await api("/api/validate", { receipts }));
}));
refresh().catch((error) => notice(error.message, true));
api("/api/workflows/config").then(renderModelSettings).catch((error) => notice(error.message, true));
refreshWorkflows().catch((error) => notice(error.message, true));
