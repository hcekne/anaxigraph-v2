"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const packets = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const elements = new Map();
const requests = [];
const context = {
  $: (selector) => {
    if (!elements.has(selector)) elements.set(selector, {
      value: selector === "#change-task" ? "Improve service" : "",
      listeners: {}, addEventListener(name, callback) { this.listeners[name] = callback; },
    });
    return elements.get(selector);
  },
  action: async (_button, operation) => operation(),
  formatJSON: (value) => JSON.stringify(value),
  api: async (route, payload) => {
    assert.equal(route, "/api/prepare", "A saved status snapshot cannot select the new baseline");
    assert.equal(payload.task, "Improve service");
    if (requests.length === 1) assert.equal(payload.baseline_id, undefined);
    else assert.equal(payload.baseline_id, "current");
    requests.push(payload);
    assert.ok(requests.length <= 3, "Each user action needs one request");
    return packets[requests.length - 1];
  },
};
vm.createContext(context);
const source = fs.readFileSync(path.join(__dirname, "../src/anaxigraph/interfaces/assets/app.js"), "utf8");
const helper = source.match(/async function prepareChange\([^]*?\n}/);
assert.ok(helper, "Preparation actions share the current-source baseline helper");
vm.runInContext(helper[0], context);
for (const [selector, next] of [["new-change-baseline", "prepare-form"], ["prepare-form", "assess-form"]]) {
  const start = source.indexOf(`$("#${selector}").addEventListener`);
  const end = source.indexOf(`$("#${next}").addEventListener`, start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(source.slice(start, end), context);
}
(async () => {
  const reset = context.$("#new-change-baseline").listeners.click;
  await reset();
  assert.equal(JSON.parse(context.$("#change-brief").textContent).baseline_id, packets[0].snapshot_id);
  let prevented = false;
  await context.$("#prepare-form").listeners.submit({ submitter: {}, preventDefault() { prevented = true; } });
  assert.ok(prevented);
  assert.equal(JSON.parse(context.$("#change-brief").textContent).baseline_id, packets[0].snapshot_id);
  await reset();
  assert.equal(JSON.parse(context.$("#change-brief").textContent).baseline_id, packets[2].snapshot_id);
  assert.ok(context.$("#change-baseline").textContent.includes(packets[2].snapshot_id));
  process.stdout.write(JSON.stringify({ requests: requests.length }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
