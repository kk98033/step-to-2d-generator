import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const source = readFileSync(new URL('../src/workspaceSession.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { readWorkspace, saveWorkspace } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const storage = () => {
  const entries = new Map();
  return { getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value), removeItem: key => entries.delete(key) };
};

test('reload preserves the exact model, part, engine, tab and drawing for that account', () => {
  const local = storage();
  const location = { modelId: 'model-uuid', partId: 'shaft', viewTab: 'smart_annotation', engine: 'experimental_v026', drawing: { png_url: '/api/files/result.png' } };
  saveWorkspace(local, 'engineer-a', location);
  assert.deepEqual(readWorkspace(local, 'engineer-a'), location);
  assert.equal(readWorkspace(local, 'engineer-b'), null);
  saveWorkspace(local, 'engineer-a', null);
  assert.equal(readWorkspace(local, 'engineer-a'), null);
});

test('malformed or obsolete snapshots and unavailable storage are safe', () => {
  const local = storage();
  local.setItem('cad-workspace:a', '{broken');
  assert.equal(readWorkspace(local, 'a'), null);
  local.setItem('cad-workspace:a', JSON.stringify({ version: 99, modelId: 'a' }));
  assert.equal(readWorkspace(local, 'a'), null);
  const unavailable = { getItem() { throw Error(); }, setItem() { throw Error(); }, removeItem() { throw Error(); } };
  assert.equal(readWorkspace(unavailable, 'a'), null);
  assert.doesNotThrow(() => saveWorkspace(unavailable, 'a', null));
});
