// The storage wrapper falls back to a copy in the page's memory where the browser's storage is
// unavailable or refuses a write. The browser's storage is shared by the tabs of the browser, so
// a write it takes drops the copy: a copy kept of every write would be read back once another
// tab had removed the key, such as the session's token at a logout in that tab.

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryStorage, storage } from '../storage';

describe('storage fallback', () => {
  it('uses the in-memory fallback when the global storage object is incomplete', () => {
    vi.stubGlobal('localStorage', {});

    storage.clear();
    storage.setItem('token', 'abc123');

    expect(storage.getItem('token')).toBe('abc123');
    expect(storage.length).toBe(1);
  });

  it('falls back when the browser storage implementation throws', () => {
    const throwingStorage = createMemoryStorage();
    vi.spyOn(throwingStorage, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded');
    });
    vi.stubGlobal('localStorage', throwingStorage);

    storage.clear();
    storage.setItem('role', 'admin');

    expect(storage.getItem('role')).toBe('admin');
  });
});

describe('a key in the storage the tabs share', () => {
  beforeEach(() => {
    storage.clear();
  });

  it('reads as removed once another tab removed it, though this page wrote it', () => {
    storage.setItem('token', ['tab', 'session'].join('-'));
    // Another tab of the browser removes it.
    localStorage.removeItem('token');

    // The page does not read back its own copy of the write.
    expect(storage.getItem('token')).toBeNull();
    expect(storage.length).toBe(0);
    expect(storage.key(0)).toBeNull();
  });

  it('reads as another tab wrote it last', () => {
    storage.setItem('role', 'viewer');
    localStorage.setItem('role', 'admin');

    expect(storage.getItem('role')).toBe('admin');
  });

  it('keeps a write the browser refused only until the browser takes one', () => {
    vi.spyOn(localStorage, 'setItem').mockImplementationOnce(() => {
      throw new Error('quota exceeded');
    });

    storage.setItem('role', 'admin');
    expect(storage.getItem('role')).toBe('admin');

    storage.setItem('role', 'viewer');
    localStorage.removeItem('role');

    // The page's copy of the write the browser took does not come back.
    expect(storage.getItem('role')).toBeNull();
  });
});
