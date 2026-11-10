import { createServer } from 'node:http';
import { readFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve, extname } from 'node:path';
import { spawn } from 'node:child_process';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

// Run after npm run build. Uses a local API fixture and a disposable profile;
// it never signs into a production service or starts a real CAD job.
const root = fileURLToPath(new URL('../../../', import.meta.url));
const profile = resolve(root, '.codex_runtime/browser-annotation-' + Date.now());
mkdirSync(profile, { recursive: true });
let completed = false;
let generation = 1;
let modelLoads = 0;
let experimentalRecommendations = 0;
const testRole = process.env.CAD_TEST_ROLE || 'ENGINEER';
let activeEngine = 'legacy_smart';
let lastRenderPayload = null;
const libraryCustomers = [], libraryCollections = [], libraryCases = [];
const errors = [];
const job = () => ({ job_id: 'annotation-fixture-' + generation, status: completed ? 'completed' : 'queued',
  model_id: 'model-fixture', part_id: 'shaft', message: completed ? 'Drawing completed' : 'Queued',
  queue_position: completed ? 0 : 2, progress: { current: completed ? 3 : 0, total: 3 }, worker_limit: 1,
  ...(completed ? { result: { png_url: '/mock.svg', candidate_engine: activeEngine, timestamp: 'fixture-' + generation,
    annotation_settings: lastRenderPayload?.feature_records || [] } } : {}) });
const server = createServer(async (req, res) => {
  const path = new URL(req.url, 'http://localhost').pathname;
  if (path.startsWith('/api/')) {
    let payload = { status: 'ok' };
    if (path === '/api/auth/me') payload = { user: { id: 'browser-engineer', role: testRole, display_name: 'Test engineer' } };
    else if (path === '/api/tolerance/experimental/recommend') {
      experimentalRecommendations++;
      payload = { status: 'ok', total_rules: 1, recommendations: { 'fixture-rule': {
        rule_id: 'fixture-rule', recommended_mode: 'NONE', tier_level: 'NOT_APPLICABLE',
        decision_status: 'NOT_APPLICABLE', confidence: 0, tolerance_config: { mode: 'NONE' },
        reasoning_description: 'Native geometry evidence is insufficient', evidence_cases: [],
      } } };
    }
    else if (path === '/api/case-library/collections') {
      if (req.method === 'POST') {
        let body = ''; for await (const chunk of req) body += chunk;
        libraryCollections.push({ ...JSON.parse(body), id: 'private-collection', case_count: 0 });
      }
      payload = { collections: libraryCollections, customers: libraryCustomers };
    }
    else if (path === '/api/case-library/customers') {
      let body = ''; for await (const chunk of req) body += chunk;
      libraryCustomers.push({ ...JSON.parse(body), id: 'private-customer' }); payload = libraryCustomers.at(-1);
    }
    else if (path === '/api/case-library/artifacts') payload = { artifacts: [{ id: 'native-artifact', title: 'Native shaft',
      created_at: '2026-11-10T08:00:00Z', can_curate: true,
      features: [{ rule_id: 'fixture-rule', nominal_value: 21.4, tolerance_config: { mode: 'CUSTOM_SYMMETRIC', dev: .1 } }] }] };
    else if (path === '/api/case-library/artifacts/native-artifact/cases') {
      let body = ''; for await (const chunk of req) body += chunk;
      const saved = JSON.parse(body); assert.equal(saved.confirmed, true);
      assert.deepEqual(saved.rule_ids, ['fixture-rule']); assert.deepEqual(saved.collection_ids, ['private-collection']);
      libraryCases.push({ id: 'native-case-record', collection_ids: saved.collection_ids, case: { case_id: 'NATIVE_fixture',
        nominal_dimensions: { length: 21.4 }, tolerance_config: { mode: 'CUSTOM_SYMMETRIC', dev: .1 },
        source_metadata: { part_id: 'shaft', measurement_role: 'OVERALL_LENGTH' } } });
      libraryCollections[0].case_count = 1; payload = { case_count: 1 };
    }
    else if (path === '/api/case-library/cases') payload = { cases: libraryCases, count: libraryCases.length, has_more: false };
    else if (path === '/api/case-library/share-requests') payload = { requests: [] };
    else if (path === '/api/annotation/jobs/active') payload = { job: completed ? null : job() };
    else if (path.startsWith('/api/annotation/jobs/annotation-fixture-')) payload = job();
    else if (path === '/api/annotation/render' && req.method === 'POST') {
      let body = ''; for await (const chunk of req) body += chunk;
      lastRenderPayload = JSON.parse(body); activeEngine = lastRenderPayload.candidate_engine;
      generation++; completed = false; payload = job();
    }
    else if (path.startsWith('/api/annotation/history/')) payload = { drawings: completed && new URL(req.url, 'http://localhost').searchParams.get('engine') === activeEngine ? [{
      operation_id: 'saved-drawing', completed_at: '2026-11-09T08:00:00Z', candidate_engine: 'legacy_smart',
      result: { png_url: '/mock.svg', candidate_engine: activeEngine, operation_id: 'saved-drawing', timestamp: 'saved', annotation_settings: lastRenderPayload?.feature_records || [] },
    }] : [] };
    else if (path === '/api/jobs/active') payload = { job: null };
    else if (path === '/api/model/model-fixture') {
      modelLoads++;
      payload = { model_id: 'model-fixture', output_dir: 'model-fixture',
        tree: { name: 'Shaft model', file_prefix: 'shaft', children: [] },
        parts_map: { shaft: { name: 'Shaft' } } };
    } else if (path.includes('/candidate-rules/')) {
      const engine = new URL(req.url, origin).searchParams.get('engine') || 'legacy_smart';
      const first = { rule_id: 'fixture-rule', category: 'shaft', nominal_value: 21.4, render_eligible: true,
        default_selected: true, verification_status: 'AUTO_VERIFIED', target_views: ['front'], sides: ['BOTTOM'],
        source_engine: engine, supported_views: ['front', 'right'] };
      payload = { rules: engine === 'experimental_v026' ? [first, { ...first, rule_id: 'fixture-second' }] : [first], candidate_engine: engine };
    }
    else if (path.startsWith('/api/features/')) payload = { records: [] };
    else if (path.includes('templates')) payload = { templates: [] };
    else if (path.includes('tags')) payload = { tags: [] };
    else if (path === '/api/models') payload = { models: [] };
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(payload));
    return;
  }
  if (path === '/mock.svg') {
    res.writeHead(200, { 'Content-Type': 'image/svg+xml' });
    res.end('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200"><rect width="300" height="200" fill="#171717"/></svg>');
    return;
  }
  const file = resolve(root, 'web_app/frontend/dist', path === '/' ? 'index.html' : '.' + path);
  if (!existsSync(file)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'Content-Type': ({ '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' })[extname(file)] || 'application/octet-stream' });
  res.end(readFileSync(file));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = spawn(process.env.CAD_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank',
], { windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let socket;
try {
  for (let n = 0; n < 100 && !existsSync(resolve(profile, 'DevToolsActivePort')); n++) await delay(100);
  const port = readFileSync(resolve(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0];
  const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => socket.addEventListener('open', resolve, { once: true }));
  let seq = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const data = JSON.parse(event.data);
    if (data.id) { const cb = pending.get(data.id); pending.delete(data.id); if (data.error) cb.reject(data.error); else cb.resolve(data.result); }
    if (data.method === 'Runtime.exceptionThrown') errors.push(data.params.exceptionDetails.exception?.description || data.params.exceptionDetails.text);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++seq; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw Error(result.exceptionDetails.text);
    return result.result.value;
  };
  const waitFor = async expression => {
    for (let n = 0; n < 100; n++) {
      try { if (await evaluate(`Boolean(document.body && (${expression}))`)) return; } catch { /* navigation replaced the execution context */ }
      await delay(100);
    }
    throw Error('Timed out: ' + expression + '\n' + await evaluate('document.body.innerText'));
  };
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: origin });
  await waitFor("document.body.innerText.includes('智慧標註工作') && document.body.innerText.includes('第 2 位')");
  const queuedScreenshot = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(resolve(root, '.codex_runtime/annotation-progress-browser.png'), Buffer.from(queuedScreenshot.data, 'base64'));
  await send('Page.reload');
  await waitFor("document.body.innerText.includes('智慧標註工作') && document.body.innerText.includes('第 2 位')");
  completed = true;
  await waitFor("[...document.querySelectorAll('button')].some(b => b.textContent.includes('查看工程圖成果'))");
  await evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.includes('查看工程圖成果')).click()");
  await waitFor("document.body.innerText.includes('客製工程圖 (shaft)') && !!document.querySelector('img[src*=\"mock.svg\"]')");
  await send('Page.reload');
  await waitFor("document.body.innerText.includes('客製工程圖 (shaft)') && document.body.innerText.includes('查看工程圖成果')");
  // Rendering again on this drawing should open its result without clicking the task card.
  await evaluate("[...document.querySelectorAll('button')].find(b => b.title.includes('清除當前圖紙')).click()");
  await waitFor("[...document.querySelectorAll('button')].some(b => b.textContent.includes('產出標註工程圖') && !b.disabled)");
  await evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.includes('產出標註工程圖')).click()");
  await waitFor("document.querySelector('.annotation-task-badge')?.textContent === '排隊中'");
  assert.equal(generation, 2);
  completed = true;
  await waitFor("!!document.querySelector('img[src*=\"fixture-2\"]')");
  // Remove both browser preview and task ID: disk-backed history must still restore the drawing.
  await evaluate("{ const key = 'cad-workspace:browser-engineer'; const saved = JSON.parse(sessionStorage.getItem(key)); saved.drawing = null; sessionStorage.setItem(key, JSON.stringify(saved)); localStorage.removeItem('cad-annotation-job:browser-engineer'); }");
  await send('Page.reload');
  await waitFor("!!document.querySelector('img[src*=\"saved\"]') && !!document.querySelector('select[aria-label=\"已生成圖面紀錄\"]')");
  if (testRole === 'ADMIN') {
    await evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '新版繪圖引擎').click()");
    await waitFor("!!document.querySelector('select[aria-label=\"公差推薦引擎\"]')");
    await evaluate("{ const selector = document.querySelector('select[aria-label=\"公差推薦引擎\"]'); selector.value = 'native_v1'; selector.dispatchEvent(new Event('change', { bubbles: true })); }");
    await waitFor("document.body.innerText.includes('尚未證明未知零件的推薦精準度')");
    await waitFor("[...document.querySelectorAll('button')].some(b => b.textContent.includes('一鍵智慧推薦公差') && !b.disabled)");
    await evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.includes('一鍵智慧推薦公差')).click()");
    await waitFor("document.body.innerText.includes('已完成 1 條規則推薦')");
    assert.equal(experimentalRecommendations, 1);
    await evaluate("{ const rule = document.querySelector('[data-rule-id=\"fixture-rule\"]'); const expand = rule.querySelector('button[title=\"展開特徵設定\"]'); expand?.click(); }");
    await waitFor("!!document.querySelector('[data-rule-id=\"fixture-rule\"] button[aria-label=\"標註視圖 right\"]')");
    await evaluate("{ const rule = document.querySelector('[data-rule-id=\"fixture-rule\"]'); rule.querySelector('button[aria-label=\"標註視圖 front\"]').click(); }");
    await evaluate("document.querySelector('[data-rule-id=\"fixture-rule\"] button[aria-label=\"標註視圖 right\"]').click()");
    await evaluate("document.querySelector('[data-rule-id=\"fixture-rule\"] button[aria-label=\"標註側向 BOTTOM\"]').click()");
    await evaluate("document.querySelector('[data-rule-id=\"fixture-rule\"] button[aria-label=\"標註側向 TOP\"]').click()");
    await evaluate("document.querySelector('[data-rule-id=\"fixture-second\"] input[type=checkbox]').click()");
    await evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.includes('產出標註工程圖')).click()");
    await waitFor("document.querySelector('.annotation-task-badge')?.textContent === '排隊中'");
    const chosen = lastRenderPayload.feature_records.filter(rule => rule.enabled);
    assert.equal(chosen.length, 1);
    assert.deepEqual(chosen[0].target_views, ['right']);
    assert.deepEqual(chosen[0].sides, ['TOP']);
    completed = true;
    await waitFor("document.querySelector('.annotation-task-badge')?.textContent === '已完成'");
    const loadsBeforeResult = modelLoads;
    await evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.includes('查看工程圖成果')).click()");
    await waitFor("!!document.querySelector('img[src*=\"mock.svg\"]')");
    assert.equal(modelLoads, loadsBeforeResult, 'same drawing should not reload candidate defaults');
    assert.equal(await evaluate("document.querySelector('[data-rule-id=\"fixture-rule\"] button[aria-label=\"標註視圖 right\"]').getAttribute('aria-pressed')"), 'true');
    await send('Page.reload');
    await waitFor("!!document.querySelector('[data-rule-id=\"fixture-second\"] input[type=checkbox]')");
    assert.equal(await evaluate("document.querySelector('[data-rule-id=\"fixture-second\"] input[type=checkbox]').checked"), false);
    await evaluate("document.querySelector('[data-rule-id=\"fixture-rule\"] button[title=\"展開特徵設定\"]')?.click()");
    await waitFor("document.querySelector('[data-rule-id=\"fixture-rule\"] button[aria-label=\"標註視圖 right\"]')?.getAttribute('aria-pressed') === 'true'");
    assert.equal(await evaluate("document.querySelector('[data-rule-id=\"fixture-rule\"] button[aria-label=\"標註側向 TOP\"]').getAttribute('aria-pressed')"), 'true');
  } else {
    assert.equal(await evaluate("!!document.querySelector('select[aria-label=\"公差推薦引擎\"]')"), false);
  }
  assert.equal(libraryCases.length, 0, 'rendering must not implicitly learn cases');
  await evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '我的推薦案例庫').click()");
  await waitFor("!!document.querySelector('[aria-label=\"私人客戶名稱\"]')");
  const fill = async (label, value) => evaluate(`{ const input = document.querySelector('[aria-label="${label}"]'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(value)}); input.dispatchEvent(new Event('input', { bubbles: true })); }`);
  await fill('私人客戶名稱', 'Private client');
  await evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '建立私人客戶').click()");
  await waitFor("document.body.innerText.includes('私人客戶已建立')");
  await fill('案例集名稱', 'Private project');
  await evaluate("{ const select = document.querySelector('[aria-label=\"案例集所屬客戶\"]'); select.value = 'private:private-customer'; select.dispatchEvent(new Event('change', { bubbles: true })); }");
  await evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '建立案例集').click()");
  await waitFor("document.body.innerText.includes('案例集已建立')");
  await evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '從成果加入').click()");
  await evaluate("{ const select = document.querySelector('[aria-label=\"選擇產圖成果\"]'); select.value = 'native-artifact'; select.dispatchEvent(new Event('change', { bubbles: true })); }");
  await waitFor("!!document.querySelector('.case-library-confirm')");
  assert.equal(await evaluate("document.querySelector('.case-library-primary').disabled"), true);
  await evaluate("{ const inputs = [...document.querySelectorAll('.case-library-list input')]; inputs.at(-1).click(); document.querySelector('.case-library-confirm input').click(); }");
  await waitFor("!document.querySelector('.case-library-primary').disabled");
  await evaluate("document.querySelector('.case-library-primary').click()");
  await waitFor("document.body.innerText.includes('已加入推薦案例庫')");
  assert.equal(libraryCases.length, 1);
  console.log(JSON.stringify({ caseLibrary: 'PASS', privateCustomers: libraryCustomers.length,
    privateCollections: libraryCollections.length, explicitCases: libraryCases.length }));
  await evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '已加入案例').click()");
  await waitFor("document.body.innerText.includes('OVERALL_LENGTH')");
  await evaluate("document.querySelector('[aria-label=\"關閉案例庫\"]').click()");
  await evaluate("document.querySelector('button[title=\"工程師帳號與偏好\"]').click()");
  await waitFor("document.body.innerText.includes('舊版推薦偏好') && document.body.innerText.includes('私人客戶與案例集')");
  await evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '私人客戶與案例集').click()");
  await waitFor("!!document.querySelector('[aria-label=\"私人客戶名稱\"]') && document.body.innerText.includes('Private client')");
  await evaluate("document.querySelector('[aria-label=\"關閉案例庫\"]').click()");
  assert.ok(modelLoads >= 2);
  assert.deepEqual(errors, []);
  const snapshot = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(resolve(root, '.codex_runtime/annotation-recovery-browser.png'), Buffer.from(snapshot.data, 'base64'));
  console.log(JSON.stringify({ role: testRole, experimentalRecommendations, passed: ['home task card', 'reload active job', 'completion button', 'open correct model/part/drawing', 'reload completed job and workspace', 'auto-open same drawing on completion', 'server history restores missing browser preview', 'experimental tolerance admin visibility/routing', ...(testRole === 'ADMIN' ? ['right view and top side submitted', 'result opens without resetting controls', 'reload preserves selected features/views/sides'] : [])], modelLoads, errors }));
} catch (error) {
  console.error(JSON.stringify({ errors, modelLoads }));
  throw error;
} finally {
  socket?.close(); browser.kill(); server.closeAllConnections(); server.close();
}
