"use strict";

// CI presentation takes its application/DOM helpers explicitly; it owns no analysis state.
window.AnaxiGraphCI = (() => {
  let view;
  function addList(parent, label, values) {
    parent.append(view.textElement("h4", label));
    const list = document.createElement("ul");
    for (const value of values || []) list.append(view.textElement("li", value));
    parent.append(list);
  }
  function renderReview(parent, result) {
    const outcomes = (result.assessments || []).reduce((totals, item) => ({ ...totals, [item.outcome]: (totals[item.outcome] || 0) + 1 }), {});
    parent.append(view.textElement("p", `CI/CD review · ${outcomes.finding || 0} pipelines with findings · ${outcomes.keep || 0} keep · ${outcomes.unknown || 0} unknown. Plans remain proposals; checks were not executed.`, "section-note"));
    parent.append(view.textElement("p", (result.assessments || []).map((item) => `${item.pipeline_id}: ${item.outcome}`).join(" · "), "section-note"));
    const labels = { fast_path: "Component fast path", broaden_when: "When to widen checks", cache_strategy: "Cache inputs and invalidation", parallelism: "Parallel work and critical path", deployment_checks: "Deployment and compatibility", retain_checks: "Checks to retain", next_steps: "Implementation steps", uncertainty: "Unknowns" };
    for (const recommendation of result.recommendations) {
      const card = view.textElement("article", "", "alternative-card");
      card.append(view.textElement("h3", recommendation.title), view.textElement("p", `Priority ${recommendation.priority} · ${(recommendation.pipeline_ids || []).join(", ")}`));
      card.append(view.textElement("p", recommendation.bottleneck));
      for (const [field, label] of Object.entries(labels)) addList(card, label, recommendation[field]);
      card.append(view.textElement("p", (recommendation.evidence || []).map((item) => `${item.path}:${item.line}`).join(" · "), "section-note"));
      parent.append(card);
    }
    addList(parent, "Review uncertainty", result.uncertainty);
  }
  function renderPlan(result) {
    const parent = view.$("#ci-plan-summary");
    parent.replaceChildren();
    view.$("#ci-plan-json").textContent = view.formatJSON(result);
    const labels = { planned: "Declared check plan", ready: "Declared check plan", incomplete: "Check scope is incomplete", not_configured: "Declare component tasks before selecting checks", no_baseline: "Choose a comparison base", missing_baseline: "Choose a comparison base" };
    parent.append(view.textElement("h3", labels[result.status] || "Review check-plan status"));
    parent.append(view.textElement("p", "This selection is based on the supplied change and declared inputs. It does not report passed tests or completed deployment."));
    if (result.message) parent.append(view.textElement("p", result.message));
    if (result.next_step) parent.append(view.textElement("p", result.next_step));
    for (const target of result.selected_targets || []) {
      const card = view.textElement("article", "", "alternative-card");
      card.append(view.textElement("h4", target.id), view.textElement("p", target.command || "Declared target"));
      addList(card, "Why this check is included", target.reasons || []);
      card.append(view.textElement("p", target.cache_eligible ? "Input fingerprint available for trusted cache validation; no cache hit is claimed." : target.cache_reason || "No reusable test result has been established.", "section-note"));
      parent.append(card);
    }
    if (result.uncovered_paths?.length) addList(parent, "Paths needing ownership or broader checks", result.uncovered_paths);
    if (result.expansion_reasons?.length) addList(parent, "Why the scope widens", result.expansion_reasons);
    if (result.layers?.length) addList(parent, "Execution order; each group can run in parallel", result.layers.map((layer) => layer.join(", ")));
    if (result.reevaluation_reasons?.length) addList(parent, "CI/CD architecture review needed", result.reevaluation_reasons);
    if (result.critical_path_seconds != null) parent.append(view.textElement("p", `Declared critical path: ${result.critical_path_seconds} seconds. ${result.critical_path_basis || ""}`, "section-note"));
  }
  function bindPlanForm(helpers) {
    view = helpers;
    view.$("#ci-plan-form").addEventListener("submit", (event) => {
      event.preventDefault();
      view.action(event.submitter, async () => {
        const paths = view.lines(view.$("#ci-changed-paths").value);
        const base = view.$("#ci-base-ref").value.trim();
        if (paths.length && base) throw new Error("Choose a base revision or supplied paths, not both.");
        const request = { base_ref: base || null, changed_paths: paths.length ? paths : null, cadence: view.$("#ci-cadence").value };
        renderPlan(await view.api("/api/ci/plan", request));
      });
    });
  }
  return { renderReview, bindPlanForm };
})();
