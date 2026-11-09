import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const source = readFileSync(new URL('../src/analysisCache.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { AnalysisCache } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('engine switching reuses results without sharing mutable edits', async () => {
  const cache = new AnalysisCache();
  let calls = 0;
  const loader = async () => { calls++; return { rules: [{ value: 1 }] }; };
  const first = await cache.load('user:model:legacy', loader);
  first.rules[0].value = 99;
  await cache.load('user:model:experimental', loader);
  assert.equal((await cache.load('user:model:legacy', loader)).rules[0].value, 1);
  assert.equal(calls, 2);
  await cache.load('other-user:model:legacy', loader);
  assert.equal(calls, 3);
});

test('in-flight reads coalesce and invalidation cannot resurrect stale results', async () => {
  const cache = new AnalysisCache();
  let finish;
  let calls = 0;
  const loader = () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  const first = cache.load('user:model:legacy', loader);
  const second = cache.load('user:model:legacy', loader);
  assert.equal(calls, 1);
  cache.invalidate('user:model:');
  await cache.load('user:model:legacy', async () => 'fresh');
  finish('old');
  assert.equal(await first, 'old');
  assert.equal(await second, 'old');
  assert.equal(await cache.load('user:model:legacy', async () => 'unexpected'), 'fresh');
});

test('expired and failed entries reload', async () => {
  const cache = new AnalysisCache(0);
  await cache.load('key', async () => 'old');
  assert.equal(await cache.load('key', async () => 'new'), 'new');
  await assert.rejects(cache.load('bad', async () => { throw new Error('failed'); }));
  assert.equal(await cache.load('bad', async () => 'recovered'), 'recovered');
});
