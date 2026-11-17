"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.value = ""; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute() {}
  addEventListener(name, callback) { this.listeners[name] = callback; }
}

const packets = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const elements = new Map();
const decisions = [];
const inspected = [];
let submitted = 0;
const context = {
  document: { createElement: (tag) => new Element(tag) },
  $: (selector) => { if (!elements.has(selector)) elements.set(selector, new Element("div")); return elements.get(selector); },
  formatJSON: (value) => JSON.stringify(value),
  notice: () => {},
  action: async (_button, operation) => operation(),
  selectEntity: async (id) => inspected.push(id),
  api: async (route, payload) => {
    assert.equal(route, "/api/assess");
    assert.equal(payload.task, "review delivery");
    const expected = submitted++ === 0 ? 12 : 1;
    assert.equal(payload.decisions.length, expected);
    assert.ok(payload.decisions.every((item) => item.decision === "defer" && item.reason.trim()));
    decisions.push(...payload.decisions);
    return submitted === 1 ? packets.second : packets.final;
  },
};
vm.createContext(context);
const source = fs.readFileSync(path.join(__dirname, "../src/anaxigraph/interfaces/assets/app.js"), "utf8");
for (const name of ["textElement", "entityButton", "renderPlanAssessment"]) {
  const match = source.match(new RegExp(`function ${name}\\([^]*?\\n}`, "m"));
  assert.ok(match, `Actual dashboard function ${name} must be present`);
  vm.runInContext(match[0], context);
}
function all(root) { return [root, ...root.children.flatMap(all)]; }

(async () => {
  context.renderPlanAssessment(packets.initial, { task: "review delivery", files: ["delivery.py"] });
  const panel = context.$("#architecture-decisions");
  assert.ok(all(panel).some((node) => node.textContent?.includes("13 remaining")));
  for (const remaining of [1, 0]) {
    for (const node of all(panel)) {
      if (node.tag === "select") node.value = "defer";
      if (node.tag === "textarea") node.value = "Review in a later task.";
    }
    const save = all(panel).find((node) => node.textContent === "Record this batch");
    assert.ok(save);
    await save.listeners.click();
    assert.ok(all(panel).some((node) => node.textContent?.includes(`${remaining} remaining`)));
  }
  const expand = all(panel).find((node) => node.textContent === "Inspect full saved plan");
  await expand.listeners.click();
  assert.equal(decisions.length, 13);
  process.stdout.write(JSON.stringify({ decisions, inspected }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
