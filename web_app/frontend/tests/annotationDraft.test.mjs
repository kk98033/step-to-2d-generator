import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
const source = readFileSync(new URL('../src/annotationDraft.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { annotationDraftKey, readAnnotationDraft, saveAnnotationDraft, draftFromRecords } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('selection, views and sides survive reload and stay scoped by account/model/engine', () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  const key = annotationDraftKey('a', 'model', 'shaft', 'experimental');
  const draft = draftFromRecords([{ rule_id: 'one', enabled: true, views: ['right'], target_views: ['right'], sides: ['TOP', 'BOTTOM'] }, { rule_id: 'two', enabled: false }]);
  saveAnnotationDraft(storage, key, draft);
  assert.deepEqual(readAnnotationDraft(storage, key), draft);
  assert.equal(readAnnotationDraft(storage, annotationDraftKey('b', 'model', 'shaft', 'experimental')), null);
  assert.equal(readAnnotationDraft(storage, annotationDraftKey('a', 'model', 'shaft', 'legacy')), null);
  assert.deepEqual(draft.selectedIds, ['one']);
});
