"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const { setImmediate } = require("node:timers/promises");

const root = process.env.ANAXIGRAPH_UI_SOURCE || path.resolve(__dirname, "..");
const assets = path.join(root, "src/anaxigraph/interfaces/assets");
const html = fs.readFileSync(path.join(assets, "index.html"), "utf8");

// Leaf DOM operations are doubles; map requests, timers and orchestration use production code.
class Element {
  constructor(document, selector) {
    this.document = document;
    this.selector = selector;
    this.children = [];
    this.events = {};
    this.attributes = {};
    this.dataset = {};
    this.textContent = "";
    this.value = "";
    this.classes = new Set();
    this.classList = {
      toggle: (name, enabled) => enabled ? this.classes.add(name) : this.classes.delete(name),
      remove: (name) => this.classes.delete(name),
      contains: (name) => this.classes.has(name),
    };
  }
  set className(value) { this.classes = new Set(value.split(/\s+/).filter(Boolean)); }
  get className() { return [...this.classes].join(" "); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, listener) { this.events[name] = listener; }
  querySelectorAll() { return []; }
  cloneNode() {
    const copy = new Element(this.document, this.selector);
    copy.textContent = this.textContent;
    copy.className = this.className;
    copy.children = this.children.slice();
    return copy;
  }
  replaceWith(replacement) {
    this.document.elements.set(this.selector, replacement);
    this.document.replaced.push(this.selector);
  }
}

class Document {
  constructor() {
    this.elements = new Map();
    this.replaced = [];
    this.pages = [...html.matchAll(/<section id="(page-[^"]+)"[^>]*>/g)].map(([tag, id]) => {
      const page = this.querySelector(`#${id}`);
      page.id = id;
      page.hidden = /\bhidden\b/.test(tag);
      page.inert = /\binert\b/.test(tag);
      return page;
    });
    this.navigation = ["map", "workflows", "change"].map((name) => {
      const button = this.querySelector(`[data-page=${name}]`);
      button.dataset.page = name;
      return button;
    });
    this.querySelector("#graph").className = "graph-empty";
  }
  querySelector(selector) {
    if (!this.elements.has(selector)) this.elements.set(selector, new Element(this, selector));
    return this.elements.get(selector);
  }
  querySelectorAll(selector) {
    return selector === ".page" ? this.pages : selector === "[data-page]" ? this.navigation : [];
  }
  createElement(name) { return new Element(this, name); }
  createElementNS(_namespace, name) { return this.createElement(name); }
}

function server() {
  const requests = [];
  const jobs = new Map();
  const value = {
    requests, jobs, starts: 0, loseNextPoll: false, empty: false,
    async fetch(url, options) {
      const repository = options.headers["X-AnaxiGraph-Repository"] || "left";
      requests.push({ url, repository, method: options.method });
      let result;
      if (url === "/api/repositories") {
        result = { default_repository_id: "left", repositories: ["left", "right"].map((id) => ({ id, name: id, path: `/${id}` })) };
      } else if (url === "/api/status") {
        result = { root: `/${repository}`, snapshot: { root: `/${repository}`, id: "current", dirty: false }, modules: 1, symbols: 1 };
      } else if (url === "/api/scan/start") {
        if (jobs.get(repository)?.status !== "running") {
          value.starts++;
          jobs.set(repository, { status: "running", phase: "Reading source", elapsed_seconds: 0 });
        }
        result = jobs.get(repository);
      } else if (url === "/api/scan/status") {
        if (value.loseNextPoll) {
          value.loseNextPoll = false;
          throw new TypeError("Connection lost");
        }
        result = jobs.get(repository) || { status: "idle" };
      } else if (url.startsWith("/api/modules?")) {
        assert.equal(jobs.get(repository)?.status, "completed", "A live module read started before structural refresh finished");
        result = { total: value.empty ? 0 : 1, items: value.empty ? [] : [value.node] };
      } else if (url.startsWith("/api/entity?")) {
        result = { entity: value.node, incoming: [], outgoing: [], neighbors: [], snapshot_id: "current" };
        if (value.holdEntity) await new Promise((resolve) => { value.releaseEntity = resolve; });
      } else if (url === "/api/workflows/config") {
        result = {};
      } else if (url === "/api/workflows") result = { items: [] };
      else throw new Error(`Unexpected request: ${url}`);
      return { ok: true, json: async () => result };
    },
    node: { id: "module:engine.py", kind: "module", name: "engine", path: "engine.py", line: 1, data: { loc: 2, function_count: 1 } },
  };
  return value;
}

async function load(backend = server(), repository = "left") {
  const document = new Document();
  const timers = new Map();
  let nextTimer = 0;
  const context = vm.createContext({
    document, URL, URLSearchParams, AbortController,
    Option: class { constructor(text, value) { this.text = text; this.value = value; } },
    location: { search: `?repository=${repository}`, href: `http://localhost/?repository=${repository}`, assign() {} },
    fetch: backend.fetch.bind(backend),
    setTimeout: (callback, delay) => { timers.set(++nextTimer, { callback, delay }); return nextTimer; },
    clearTimeout: (id) => timers.delete(id),
    AnaxiGraphCI: { bind() {} },
  });
  vm.runInContext(fs.readFileSync(path.join(assets, "workspace.js"), "utf8"), context);
  const boot = vm.runInContext(fs.readFileSync(path.join(assets, "app.js"), "utf8"), context);
  // Those independent collaborators have their own tests and actual-browser qualification.
  vm.runInContext(`
    renderModelSettings = () => {};
    refreshWorkflows = async () => {};
  `, context);
  await boot;
  const api = vm.runInContext("({state, selectEntity})", context);
  const fixture = {
    backend, document, timers, api, repository,
    get: (selector) => document.querySelector(selector),
    async click(selector) {
      await document.querySelector(selector).events.click();
      await setImmediate();
    },
    async poll() {
      const entry = [...timers].find(([, timer]) => timer.delay === 1200);
      assert.ok(entry, "No progress polling was scheduled");
      timers.delete(entry[0]);
      await entry[1].callback();
      await setImmediate();
    },
    async complete() {
      backend.jobs.set(repository, { status: "completed", phase: "Structural map ready" });
      await fixture.poll();
    },
  };
  return fixture;
}

test("startup starts one background refresh and waits before reading modules", async () => {
  const f = await load();
  assert.equal(f.backend.starts, 1);
  assert.equal(f.backend.requests.filter((r) => r.url.startsWith("/api/modules?")).length, 0);
  assert.ok(f.document.pages.every((page) => page.inert));
  assert.equal(f.get("#module-total").textContent, "Loading modules…");
  assert.equal(f.get("#module-empty").hidden, true);
  await f.click("#refresh");
  assert.equal(f.backend.starts, 1);
  assert.equal(f.backend.requests.filter((r) => r.url === "/api/scan/start").length, 1);
  await f.complete();
  assert.equal(f.get("#module-total").textContent, "1 modules");
  assert.ok(f.document.pages.every((page) => !page.inert));
  assert.equal(f.timers.size, 0, "Completion started another refresh or left a poll active");
});

test("lost progress reconnects to the same job and failure supports an explicit retry", async () => {
  const f = await load();
  f.backend.loseNextPoll = true;
  await f.poll();
  assert.equal(f.get("#scan").textContent, "Check map progress");
  assert.equal(f.get("#scan").disabled, false);
  await f.click("#scan");
  assert.equal(f.backend.starts, 1);
  f.backend.jobs.set("left", { status: "failed", phase: "Refresh failed", error: "Read failure" });
  await f.poll();
  assert.equal(f.get("#notice").textContent, "Read failure");
  assert.equal(f.get("#module-total").textContent, "Map not refreshed");
  await f.click("#scan");
  assert.equal(f.backend.starts, 2);
  await f.complete();
  assert.equal(f.get("#module-total").textContent, "1 modules");
});

test("reloading reconnects while repository headers keep other checkouts separate", async () => {
  const backend = server();
  await load(backend);
  const reloaded = await load(backend);
  assert.equal(backend.starts, 1);
  await reloaded.complete();
  const boundary = backend.requests.length;
  const second = await load(backend, "right");
  assert.equal(backend.starts, 2);
  await second.complete();
  assert.ok(second.get("#checkout").textContent.startsWith("/right"));
  assert.ok(backend.requests.slice(boundary).filter((r) => r.url !== "/api/repositories").every((r) => r.repository === "right"));
});

test("module navigation stays visible and refresh clears old inspection and graph state", async () => {
  const f = await load();
  await f.complete();
  await f.api.selectEntity("module:engine.py");
  assert.equal(f.get("#page-map").hidden, false);
  assert.equal(f.get("#graph").children.length, 1);
  assert.equal(f.api.state.selected, "module:engine.py");
  await f.click("#scan");
  assert.equal(f.api.state.selected, null);
  assert.equal(f.get("#graph").children.length, 0);
  assert.equal(f.get("#graph").classList.contains("graph-empty"), true);
  assert.ok(f.document.replaced.includes(".inspector"));
});

test("an old inspection response cannot restore the selection during a new refresh", async () => {
  const f = await load();
  await f.complete();
  f.backend.holdEntity = true;
  const pending = f.api.selectEntity("module:engine.py");
  await setImmediate();
  await f.click("#scan");
  f.backend.releaseEntity();
  await pending;
  assert.equal(f.api.state.selected, null);
  assert.equal(f.get("#graph").children.length, 0);
});

test("a completed empty map is interactive and distinct from a loading map", async () => {
  const f = await load();
  f.backend.empty = true;
  await f.complete();
  assert.equal(f.get("#module-total").textContent, "0 modules");
  assert.equal(f.get("#module-empty").hidden, false);
  assert.ok(f.document.pages.every((page) => !page.inert));
  assert.equal(f.api.state.selected, null);
});
