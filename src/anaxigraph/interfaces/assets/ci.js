"use strict";

// CI presentation takes its application/DOM helpers explicitly; it owns no analysis state.
window.AnaxiGraphCI = (() => {
  let view;
  function renderReview(parent, result) {
    const outcomes = (result.assessments || []).reduce((totals, item) => ({ ...totals, [item.outcome]: (totals[item.outcome] || 0) + 1 }), {});
    parent.append(view.textElement("p", `CI/CD review · ${outcomes.finding || 0} pipelines with findings · ${outcomes.keep || 0} keep · ${outcomes.unknown || 0} unknown. Plans remain proposals; checks were not executed.`, "section-note"));
    parent.append(view.textElement("p", (result.assessments || []).map((item) => `${item.pipeline_id}: ${item.outcome}`).join(" · "), "section-note"));
    const labels = { fast_path: "Component fast path", broaden_when: "When to widen checks", cache_strategy: "Cache inputs and invalidation", parallelism: "Parallel work and critical path", deployment_checks: "Deployment and compatibility", retain_checks: "Checks to retain", next_steps: "Implementation steps", uncertainty: "Unknowns" };
    for (const recommendation of result.recommendations) {
      const card = view.textElement("article", "", "alternative-card");
      card.append(view.textElement("h3", recommendation.title), view.textElement("p", `Priority ${recommendation.priority} · ${(recommendation.pipeline_ids || []).join(", ")}`));
      card.append(view.textElement("p", recommendation.bottleneck));
      for (const [field, label] of Object.entries(labels)) view.reviewTradeoffs(card, label, recommendation[field]);
      card.append(view.textElement("p", (recommendation.evidence || []).map((item) => `${item.path}:${item.line}`).join(" · "), "section-note"));
      parent.append(card);
    }
    view.reviewTradeoffs(parent, "Review uncertainty", result.uncertainty);
  }
  function bind(helpers) {
    view = helpers;
  }
  return { renderReview, bind };
})();
