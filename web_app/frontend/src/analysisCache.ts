// Per-page, per-account cache. No CAD data is persisted in browser storage.
export class AnalysisCache {
  private entries = new Map<string, { expires: number; value: Promise<any> }>();

  private ttl: number;
  private capacity: number;

  constructor(ttl = 60_000, capacity = 48) {
    this.ttl = ttl;
    this.capacity = capacity;
  }

  invalidate(prefix: string) {
    for (const key of this.entries.keys()) {
      if (key.startsWith(prefix)) this.entries.delete(key);
    }
  }

  load<T>(key: string, loader: () => Promise<T>): Promise<T> {
    const existing = this.entries.get(key);
    if (existing && existing.expires > Date.now()) {
      return existing.value.then(value => structuredClone(value));
    }
    const entry = { expires: Number.POSITIVE_INFINITY, value: Promise.resolve() as Promise<any> };
    entry.value = loader().then(value => {
      entry.expires = Date.now() + this.ttl;
      return structuredClone(value);
    }).catch(error => {
      if (this.entries.get(key) === entry) this.entries.delete(key);
      throw error;
    });
    this.entries.delete(key);
    this.entries.set(key, entry);
    while (this.entries.size > this.capacity) {
      this.entries.delete(this.entries.keys().next().value!);
    }
    return entry.value.then(value => structuredClone(value));
  }
}
