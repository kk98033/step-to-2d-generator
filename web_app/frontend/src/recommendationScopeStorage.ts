export type RecommendationScope = {
  caseSource: 'COMPANY' | 'PERSONAL' | 'BOTH';
  customerId: string;
  collectionIds: string[];
  tagIds: string[];
  match: 'ANY' | 'ALL';
  nativeEnabled: boolean;
};
type ScopeStorage = Pick<Storage, 'getItem' | 'setItem'>;
const defaults = (): RecommendationScope => ({ caseSource: 'COMPANY', customerId: '', collectionIds: [], tagIds: [], match: 'ANY', nativeEnabled: false });
const ids = (value: unknown): string[] => Array.isArray(value)
  ? [...new Set(value.filter((id): id is string => typeof id === 'string' && !!id.trim() && id.length <= 160))].slice(0, 1000) : [];

export function readRecommendationScope(userId: string, storage?: ScopeStorage): RecommendationScope {
  try {
    const value = JSON.parse((storage ?? window.localStorage).getItem(`cad-recommendation-scope:${userId}`) || 'null');
    if (!userId || value?.version !== 1) return defaults();
    return {
      caseSource: ['COMPANY', 'PERSONAL', 'BOTH'].includes(value.caseSource) ? value.caseSource : 'COMPANY',
      customerId: typeof value.customerId === 'string' && value.customerId.length <= 160 ? value.customerId : '',
      collectionIds: ids(value.collectionIds), tagIds: ids(value.tagIds),
      match: value.match === 'ALL' ? 'ALL' : 'ANY', nativeEnabled: value.nativeEnabled === true,
    };
  } catch { return defaults(); }
}

export function saveRecommendationScope(userId: string, value: RecommendationScope, storage?: ScopeStorage) {
  try {
    if (userId) (storage ?? window.localStorage).setItem(`cad-recommendation-scope:${userId}`, JSON.stringify({ ...value, version: 1 }));
  } catch { /* Browser storage limits must not block engineering work. */ }
}
