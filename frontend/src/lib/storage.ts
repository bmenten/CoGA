export interface StorageLike {
  readonly length: number;
  clear(): void;
  getItem(key: string): string | null;
  key(index: number): string | null;
  removeItem(key: string): void;
  setItem(key: string, value: string): void;
}

export function createMemoryStorage(): StorageLike {
  const store = new Map<string, string>();

  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.has(key) ? store.get(key)! : null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
  };
}

const fallbackStorage = createMemoryStorage();

function hasStorageShape(value: unknown): value is StorageLike {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as StorageLike).clear === 'function' &&
    typeof (value as StorageLike).getItem === 'function' &&
    typeof (value as StorageLike).key === 'function' &&
    typeof (value as StorageLike).removeItem === 'function' &&
    typeof (value as StorageLike).setItem === 'function'
  );
}

function getBrowserStorage(): StorageLike | null {
  try {
    return hasStorageShape(globalThis.localStorage) ? globalThis.localStorage : null;
  } catch {
    return null;
  }
}

function runWithFallback<T>(action: (store: StorageLike) => T, fallback: () => T): T {
  const browserStorage = getBrowserStorage();
  if (browserStorage) {
    try {
      return action(browserStorage);
    } catch {
      // Fall back to in-memory storage when the browser implementation is unavailable.
    }
  }
  return fallback();
}

export const storage: StorageLike = {
  get length() {
    const browserLength = runWithFallback(
      (store) => store.length,
      () => 0,
    );
    return browserLength || fallbackStorage.length;
  },
  clear() {
    runWithFallback(
      (store) => store.clear(),
      () => undefined,
    );
    fallbackStorage.clear();
  },
  getItem(key: string) {
    const browserValue = runWithFallback(
      (store) => store.getItem(key),
      () => null,
    );
    return browserValue ?? fallbackStorage.getItem(key);
  },
  key(index: number) {
    const browserKey = runWithFallback(
      (store) => store.key(index),
      () => null,
    );
    return browserKey ?? fallbackStorage.key(index);
  },
  removeItem(key: string) {
    runWithFallback(
      (store) => store.removeItem(key),
      () => undefined,
    );
    fallbackStorage.removeItem(key);
  },
  setItem(key: string, value: string) {
    runWithFallback(
      (store) => store.setItem(key, value),
      () => undefined,
    );
    fallbackStorage.setItem(key, value);
  },
};
