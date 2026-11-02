const { test } = require('node:test');
const assert = require('node:assert/strict');
const { validateReport, display } = require('../../src/anaxigraph/interfaces/assets/evals.js');
const fixture = () => ({
  arms: { charter: { verification_status: 'incomplete', final_cumulative_rate: 0.75 } },
  decision: { verdict: 'incomplete' },
  chains: [{ chain: 'charter-r1', arm: 'charter', steps: [{ step: 1, cumulative_rate: 0.75, agent_seconds: null }], probes: [] }],
});

test('accepts actual report envelope and preserves missing measurements', () => {
  const report = fixture();
  assert.equal(validateReport(report), report);
  assert.equal(report.chains[0].steps[0].agent_seconds, null);
  for (const value of [null, undefined, NaN, Infinity, -1, '0', false]) assert.equal(display(value), 'Unknown');
  assert.equal(display(0), '0');
});

test('rejects malformed envelopes, unknown arms and duplicate chain identities', () => {
  for (const value of [null, [], {}, { arms: {}, chains: [] }, { arms: {}, chains: [], decision: null }]) {
    assert.throws(() => validateReport(value));
  }
  const unknown = fixture();
  unknown.chains[0].arm = 'missing';
  assert.throws(() => validateReport(unknown));
  const duplicate = fixture();
  duplicate.chains.push(structuredClone(duplicate.chains[0]));
  assert.throws(() => validateReport(duplicate));
});

test('rejects invalid chart data instead of plotting invented zeroes', () => {
  for (const value of [-1, Infinity, NaN, '20', true, {}]) {
    const report = fixture();
    report.chains[0].steps[0].agent_seconds = value;
    assert.throws(() => validateReport(report));
  }
  for (const value of [-0.1, 1.1, Infinity, '1']) {
    const report = fixture();
    report.arms.charter.final_cumulative_rate = value;
    assert.throws(() => validateReport(report));
  }
});

test('bounds collections and rejects duplicate or impossible steps', () => {
  for (const step of [-1, 21, 1.5, '1']) {
    const report = fixture();
    report.chains[0].steps[0].step = step;
    assert.throws(() => validateReport(report));
  }
  const report = fixture();
  report.chains[0].steps.push({ step: 1 });
  assert.throws(() => validateReport(report));
  report.chains = Array(101).fill(report.chains[0]);
  assert.throws(() => validateReport(report));
});

test('labels are retained as data and missing optional metrics stay unknown', () => {
  const report = fixture();
  report.chains[0].chain = '<img src=x onerror=alert(1)>';
  assert.equal(validateReport(report).chains[0].chain, '<img src=x onerror=alert(1)>');
  assert.equal(display(report.arms.charter.analysis_minutes_total), 'Unknown');
  report.chains[0].steps[0].new_pass = '2/3';
  assert.equal(validateReport(report).chains[0].steps[0].new_pass, '2/3');
  report.chains[0].steps[0].new_pass = { passed: 2 };
  assert.throws(() => validateReport(report));
  delete report.chains[0].steps[0].new_pass;
  report.chains[0].probes = [null];
  assert.throws(() => validateReport(report));
});


test('rejects deeply nested extra evidence before any candidate can be accepted', () => {
  const report = fixture();
  let nested = 'leaf';
  for (let index = 0; index < 10000; index++) nested = [nested];
  report.decision.extra = nested;
  assert.throws(() => validateReport(report), /nesting/);
  const valid = fixture();
  valid.chains[0].verification = { status: 'incomplete', evidence: { note: 'bounded' } };
  assert.equal(validateReport(valid), valid);
});

test('accepts a measured initial design at step zero plus twenty requests', () => {
  const report = fixture();
  report.chains[0].steps = Array.from({ length: 21 }, (_, step) => ({ step }));
  assert.equal(validateReport(report).chains[0].steps.length, 21);
});
