"use strict";

const AnaxiGraphWorkspace = (() => {
  let selected = new URLSearchParams(location.search).get("repository") || "";
  let scanTimer, wasRunning = false, reconnect = false, developerLoad;
  const headers = (path) => selected && path !== "/api/repositories" ? { "X-AnaxiGraph-Repository": selected } : {};
  function navigate(id) {
    const url = new URL(location.href);
    url.searchParams.set("repository", id);
    url.hash = "";
    location.assign(url);
  }
  async function bind() {
    const catalog = await api("/api/repositories");
    selected ||= catalog.default_repository_id;
    const picker = $("#repository-select");
    picker.replaceChildren(...catalog.repositories.map((repo) => new Option(`${repo.name} · ${repo.path}`, repo.id)));
    picker.value = selected;
    picker.addEventListener("change", () => navigate(picker.value));
    $("#repository-open").addEventListener("submit", (event) => {
      event.preventDefault();
      action(event.submitter, async () => {
        const repo = await api("/api/repositories", { path: $("#repository-path").value.trim() });
        navigate(repo.selected_repository_id);
      });
    });
    $("#scan").addEventListener("click", async () => {
      $("#scan").disabled = true;
      notice("");
      try {
        if (reconnect) return await scan();
        wasRunning = true;
        renderScan(await api("/api/scan/start", {}));
      } catch (error) { scanError(error); }
    });
    if (!catalog.repositories.some((repo) => repo.id === selected)) throw new Error("This repository is unavailable. Choose another repository above.");
  }
  function scanError(error) {
    reconnect = true;
    $("#scan").disabled = false;
    $("#scan").textContent = "Check map progress";
    $("#scan-progress").hidden = false;
    $("#scan-progress").textContent = "Progress could not be read. Check again to reconnect to the running refresh.";
    notice(error.message, true);
  }
  function renderScan(job) {
    clearTimeout(scanTimer);
    reconnect = false;
    const running = job.status === "running";
    $("#scan").disabled = running;
    $("#scan").textContent = running ? "Refreshing structural map…" : "Refresh structural map ↗";
    $("#scan-progress").hidden = job.status === "idle";
    $("#scan-progress").textContent = `${job.phase || job.status} · ${Math.round(job.elapsed_seconds || 0)} seconds${job.error ? ` · ${job.error}` : ""}`;
    if (running) {
      wasRunning = true;
      scanTimer = setTimeout(scan, 1200);
    } else if (wasRunning) {
      wasRunning = false;
      if (job.status === "completed") {
        state.offset = 0;
        refresh().then(() => notice("Structural map saved. Readiness counts now reflect the completed scan.")).catch((error) => notice(error.message, true));
      } else if (job.status === "failed") notice(job.error || "Structural map refresh failed.", true);
    }
  }
  async function scan() {
    try { renderScan(await api("/api/scan/status")); }
    catch (error) { scanError(error); }
  }
  async function development(enabled) {
    if (!enabled) return;
    if (!developerLoad) developerLoad = (async () => {
      const base = `/api/repositories/${encodeURIComponent(selected)}/development/`;
      const response = await fetch(`${base}evals.html`);
      if (!response.ok) throw new Error("Developer workspace assets are unavailable.");
      const template = document.createElement("template");
      template.innerHTML = await response.text();
      $(".workspace-footer").before(template.content);
      const style = document.createElement("link");
      style.rel = "stylesheet";
      style.href = `${base}evals.css`;
      document.head.append(style);
      const script = document.createElement("script");
      script.src = `${base}evals.js`;
      await new Promise((resolve, reject) => {
        script.onload = resolve;
        script.onerror = () => reject(new Error("Developer workspace could not load."));
        document.head.append(script);
      });
      $("[data-page='evals']").hidden = false;
    })().catch((error) => { developerLoad = null; throw error; });
    return developerLoad;
  }
  return { headers, bind, scan, development };
})();
