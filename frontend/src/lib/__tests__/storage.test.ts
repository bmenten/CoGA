import { describe, expect, it, vi } from 'vitest';
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
