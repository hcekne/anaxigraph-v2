"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const packets = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const elements = new Map();
let requests = 0;
const context = {
  $: (selector) => {
    if (!elements.has(selector)) elements.set(selector, {
      value: "", checked: false, listeners: {},
      addEventListener(name, callback) { this.listeners[name] = callback; },
    });
    return elements.get(selector);
  },
  action: async (_button, operation) => operation(),
  formatJSON: JSON.stringify,
  api: async (route, payload) => {
    assert.equal(route, "/api/validate");
    assert.equal(payload.full, requests === 1);
    assert.deepEqual(JSON.parse(JSON.stringify(payload.receipts)), requests === 1 ? packets.receipts : null);
    return packets.results[requests++];
  },
};
vm.createContext(context);
const source = fs.readFileSync(path.join(__dirname, "../src/anaxigraph/interfaces/assets/app.js"), "utf8");
const start = source.indexOf('$("#validate-change").addEventListener');
const end = source.indexOf("AnaxiGraphCI.bind", start);
assert.ok(start >= 0 && end > start);
vm.runInContext(source.slice(start, end), context);
(async () => {
  const validate = context.$("#validate-change").listeners.click;
  await validate();
  assert.deepEqual(JSON.parse(context.$("#change-validation").textContent).findings, []);
  context.$("#validation-full").checked = true;
  context.$("#receipts").value = JSON.stringify(packets.receipts);
  await validate();
  const result = JSON.parse(context.$("#change-validation").textContent);
  assert.equal(result.findings[0].change, "unchanged");
  assert.equal(result.checks[0].status, "passed");
  assert.equal(result.baseline_id, packets.results[0].baseline_id);
  assert.equal(requests, 2);
})().catch((error) => { console.error(error); process.exitCode = 1; });
