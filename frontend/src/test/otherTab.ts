import { act } from '@testing-library/react';

// Every key the browser's storage holds, with its value.
const storedValues = (): Map<string, string> => {
  const values = new Map<string, string>();
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    const value = key === null ? null : localStorage.getItem(key);
    if (key !== null && value !== null) values.set(key, value);
  }
  return values;
};

/**
 * Runs `write` as another tab of the browser, and tells this tab of it as the browser does. The
 * tabs share the browser's storage, and the browser fires a `storage` event in every other tab
 * for each key whose value a write changed, with its old and new value; a key written with the
 * value it had fires none. `write` changes the shared `localStorage`, for example through
 * another module instance's `persistSession`, `updateSessionToken` or `clearSession`.
 */
export const inAnotherTab = (write: () => void): void => {
  const before = storedValues();
  write();
  const after = storedValues();
  act(() => {
    new Set([...before.keys(), ...after.keys()]).forEach((key) => {
      const oldValue = before.get(key) ?? null;
      const newValue = after.get(key) ?? null;
      if (oldValue !== newValue) {
        window.dispatchEvent(new StorageEvent('storage', { key, oldValue, newValue }));
      }
    });
  });
};
