import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const source = readFileSync(new URL('../src/recommendationScopeStorage.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { readRecommendationScope, saveRecommendationScope } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const memory = () => { const values = new Map(); return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }; };
const scope = { caseSource: 'BOTH', customerId: 'customer-a', collectionIds: ['project-a'], tagIds: ['type-shaft'], match: 'ALL', nativeEnabled: true };

test('all query choices persist per account without leaking to another account', () => {
  const storage = memory();
  saveRecommendationScope('engineer-a', scope, storage);
  assert.deepEqual(readRecommendationScope('engineer-a', storage), scope);
  assert.equal(readRecommendationScope('engineer-b', storage).caseSource, 'COMPANY');
  saveRecommendationScope('engineer-b', { ...scope, customerId: 'other', caseSource: 'PERSONAL' }, storage);
  assert.deepEqual(readRecommendationScope('engineer-a', storage), scope);
  assert.equal(readRecommendationScope('engineer-b', storage).customerId, 'other');
});

test('corrupt and incompatible saved preferences fall back safely', () => {
  const storage = memory();
  for (const value of ['{broken', 'null', '{"version":99,"caseSource":"PERSONAL"}']) {
    storage.setItem('cad-recommendation-scope:a', value);
    assert.equal(readRecommendationScope('a', storage).caseSource, 'COMPANY');
  }
  storage.setItem('cad-recommendation-scope:a', JSON.stringify({ version: 1, caseSource: 'ALL_USERS', customerId: {},
    collectionIds: ['project', 'project', '', null], tagIds: {}, nativeEnabled: 'true', match: 'unknown' }));
  const restored = readRecommendationScope('a', storage);
  assert.equal(restored.caseSource, 'COMPANY'); assert.equal(restored.customerId, '');
  assert.deepEqual(restored.collectionIds, ['project']); assert.deepEqual(restored.tagIds, []);
  assert.equal(restored.nativeEnabled, false); assert.equal(restored.match, 'ANY');
});

test('blocked and full browser storage never prevents normal work', () => {
  const storage = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('full'); } };
  assert.equal(readRecommendationScope('a', storage).caseSource, 'COMPANY');
  assert.doesNotThrow(() => saveRecommendationScope('a', scope, storage));
});
