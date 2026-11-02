// Opt-in end-to-end smoke: NODE_PATH=<Playwright installation>/node_modules node .../browser.cjs
// Starts its own disposable HTTP fixture; every model answer is labelled synthetic.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { chromium } = require('playwright');

async function main() {
  const project = path.resolve(__dirname, '../..');
  const parent = process.env.ANAXIGRAPH_SMOKE_OUTPUT || path.join(os.homedir(), '.tmp');
  await fs.mkdir(parent, { recursive: true });
  const output = await fs.mkdtemp(path.join(parent, 'anaxigraph-dashboard-'));
  const root = path.join(output, 'repo');
  await fs.mkdir(root);
  const socket = net.createServer();
  socket.listen(0, '127.0.0.1');
  await once(socket, 'listening');
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  const server = spawn(path.join(project, '.venv/bin/python'), [path.join(__dirname, 'fixture.py'), '--root', root, '--port', String(port)], { cwd: project });
  let logs = '';
  server.stdout.on('data', chunk => { logs += chunk; });
  server.stderr.on('data', chunk => { logs += chunk; });
  const base = `http://127.0.0.1:${port}`;
  let browser;
  const checks = [];
  const check = (name, condition) => { assert(condition, name); checks.push(name); };
  try {
    let ready = false;
    for (let attempt = 0; attempt < 200; attempt++) {
      if (server.exitCode !== null) throw new Error(`Fixture exited: ${logs}`);
      try { ready = (await fetch(`${base}/api/status`)).ok; } catch {}
      if (ready) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert(ready, `Fixture did not start: ${logs}`);
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, acceptDownloads: true });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    await page.waitForFunction(() => document.querySelectorAll('#module-rows tr').length === 2);
    check('real structural map rendered', (await page.locator('#module-total').innerText()).includes('2 modules'));
    await page.getByRole('button', { name: 'billing.py', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#entity-json').textContent.includes('module:billing.py'));
    check('module inspector and graph rendered', await page.locator('#graph svg').count() === 1);
    await page.locator('[data-sort="loc"]').click();
    await page.waitForFunction(() => document.querySelector('[data-sort="loc"]').closest('th').getAttribute('aria-sort') === 'descending');
    checks.push('module sorting preserved');

    await page.locator('[data-page="workflows"]').click();
    check('all five workflow controls preserved', await page.locator('[data-run]').count() === 5);
    await page.waitForFunction(() => document.querySelector('#workflow-runs').textContent.includes('Final reconciliation'));
    check('real saved workflow progress visible', (await page.locator('#workflow-runs').innerText()).includes('completed'));
    await page.locator('#load-reviews').click();
    await page.waitForFunction(() => document.querySelector('#saved-reviews').textContent.includes('synthetic-workflow-fixture'));
    checks.push('saved synthetic reviews rendered through real API');

    await page.locator('[data-page="change"]').click();
    await page.locator('#change-task').fill('Export invoices without changing billing behavior');
    await page.locator('#prepare-form button').click();
    await page.waitForFunction(() => document.querySelector('#change-brief').textContent.includes('baseline_id'));
    check('prepare change pins real baseline', JSON.parse(await page.locator('#change-brief').innerText()).baseline_id.length > 0);
    await page.locator('#planned-files').fill('billing.py');
    await page.locator('#assess-form button').click();
    await page.waitForFunction(() => document.querySelector('#change-validation').textContent.includes('baseline_id'));
    await page.locator('#validate-change').click();
    await page.waitForFunction(() => document.querySelector('#change-validation').textContent.includes('candidate_snapshot_id'));
    checks.push('assess and validate use shared application');

    const callsBefore = (await (await fetch(`${base}/api/evidence`)).json()).summary.event_kinds.model_call;
    await page.locator('[data-page="evals"]').click();
    await page.waitForFunction(() => document.querySelector('#eval-total').textContent.startsWith('50 /'));
    check('evidence ledger pages real retained events', await page.locator('.eval-event').count() === 50);
    check('unknown provider costs remain labelled', (await page.locator('#eval-ledger-metrics').innerText()).includes('unknown cost'));
    await page.locator('#eval-more').click();
    await page.waitForFunction(() => document.querySelector('#eval-total').textContent.startsWith('100 /'));
    check('next page appends without replacing first page', await page.locator('.eval-event').count() === 100);
    await page.locator('#eval-experiment').selectOption('synthetic-ui-qualification');
    await page.locator('.eval-event').first().click();
    check('event inspector preserves source envelope', (await page.locator('#eval-inspection').innerText()).includes('synthetic-ui-qualification'));
    const downloadReady = page.waitForEvent('download');
    await page.locator('#eval-export').click();
    const download = await downloadReady;
    const exportedPath = path.join(output, download.suggestedFilename());
    await download.saveAs(exportedPath);
    const exported = JSON.parse(await fs.readFile(exportedPath, 'utf8'));
    check('export downloads actual full ledger', exported.format === 'anaxigraph-trial-v1' && exported.events.length > 100);

    const report = {
      arms: { charter: { chains: 1, verification_status: 'incomplete', last_step: 2, final_cumulative_rate: 1, final_ci_selected_fraction: 0.5, agent_minutes_total: 123, analysis_minutes_total: null } },
      decision: { verdict: 'incomplete', reason: 'Synthetic browser fixture, not benchmark evidence' },
      chains: [{ chain: '<img src=x onerror="window.injected=true">', arm: 'charter', verification: { status: 'incomplete' }, steps: [
        { step: 1, new_pass: '4/4', cumulative_rate: 1, existing_prod_files_edited: 0, regressions: 0, ci_selected_fraction: 0.5, agent_seconds: 0 },
        { step: 2, cumulative_rate: 1, existing_prod_files_edited: 1, regressions: 0, ci_selected_fraction: null, agent_seconds: null },
      ], probes: [] }],
    };
    const upload = value => page.locator('#eval-import').setInputFiles({ name: 'browser-fixture.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(value)) });
    await upload(report);
    await page.waitForFunction(() => document.querySelector('#eval-report-state').textContent.includes('1 chains'));
    check('import identifies supplied, unverified provenance', (await page.locator('#eval-report-state').innerText()).includes('unverified provenance'));
    check('comparison table retains unknowns and genuine zero', (await page.locator('#eval-step-rows').innerText()).includes('Unknown') && (await page.locator('#eval-step-rows tr').first().innerText()).includes('0'));
    check('incomplete time data does not become a complete total', (await page.locator('#eval-arm-cards').innerText()).includes('Unknown'));
    check('untrusted report labels render as text', !(await page.evaluate(() => window.injected)) && await page.locator('#eval-chain option').innerText() === `${report.chains[0].chain} · charter`);
    await page.locator('#eval-metric').selectOption('agent_seconds');
    check('trajectory renders real points only', await page.locator('#eval-chart circle').count() === 1);
    await page.locator('#eval-step-rows button').last().click();
    check('step inspection retains null measurements', (await page.locator('#eval-inspection').innerText()).includes('"agent_seconds": null'));
    await upload({ arms: { charter: null }, decision: {}, chains: [] });
    await page.waitForFunction(() => document.querySelector('#notice').textContent.includes('Report not loaded'));
    check('invalid import preserves last valid report', await page.locator('#eval-step-rows tr').count() === 2);
    let nested = {};
    for (let depth = 0; depth < 100; depth++) nested = [nested];
    await upload({ ...report, decision: { nested } });
    await page.waitForFunction(() => document.querySelector('#notice').textContent.includes('nesting'));
    check('deep nested input rejected before replacing valid report', await page.locator('#eval-step-rows tr').count() === 2);
    await page.locator('#eval-import').setInputFiles({ name: 'large.json', mimeType: 'application/json', buffer: Buffer.alloc(2_000_001, 32) });
    await page.waitForFunction(() => document.querySelector('#notice').textContent.includes('at most 2 MB'));
    check('oversized import preserves last valid report', await page.locator('#eval-step-rows tr').count() === 2);
    const withZero = structuredClone(report);
    withZero.chains[0].steps = [{ step: 0, new_pass: '2/2', agent_seconds: 1 }, { step: 2, agent_seconds: 2 }];
    await upload(withZero);
    await page.waitForFunction(() => document.querySelector('#eval-step-rows').textContent.includes('Step 0'));
    check('initial design step zero retained', (await page.locator('#eval-step-rows').innerText()).includes('2/2'));
    check('trajectory leaves gaps for absent steps', await page.locator('#eval-chart .eval-trend').count() === 0);
    await upload(report);
    await page.waitForFunction(() => document.querySelector('#eval-step-rows').textContent.includes('Step 1'));
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.querySelector('#eval-total').textContent.startsWith('50 /'));
    checks.push('global refresh reloads the active evidence page');
    const callsAfter = (await (await fetch(`${base}/api/evidence`)).json()).summary.event_kinds.model_call;
    check('viewing and importing evals launches no model jobs', callsBefore === callsAfter);
    await page.screenshot({ path: path.join(output, 'evals-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    check('mobile page avoids document overflow', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(output, 'evals-mobile.png'), fullPage: true });
    check('no browser JavaScript errors', errors.length === 0);
    const receipt = { status: 'passed', basis: 'Real HTTP app, synthetic fixture providers; no live model calls', checks, output };
    await fs.writeFile(path.join(output, 'receipt.json'), JSON.stringify(receipt, null, 2));
    console.log(JSON.stringify(receipt));
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
    if (server.exitCode === null) await once(server, 'exit');
    await fs.writeFile(path.join(output, 'server.log'), logs);
    await fs.rm(root, { recursive: true, force: true });
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
