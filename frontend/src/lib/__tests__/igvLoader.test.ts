import { afterEach, describe, expect, it, vi } from 'vitest';

describe('loadIgv', () => {
  afterEach(() => {
    document.head.innerHTML = '';
    delete (window as Window & { igv?: unknown }).igv;
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it('reuses an existing global IGV instance', async () => {
    const fakeIgv = { createBrowser: vi.fn() };
    (window as Window & { igv?: unknown }).igv = fakeIgv;
    const appendSpy = vi.spyOn(document.head, 'appendChild');

    const { loadIgv } = await import('../igvLoader');

    await expect(loadIgv()).resolves.toBe(fakeIgv);
    expect(appendSpy).not.toHaveBeenCalled();
  });

  it('injects the browser bundle once and resolves concurrent callers', async () => {
    const appendSpy = vi.spyOn(document.head, 'appendChild');
    const { loadIgv } = await import('../igvLoader');

    const firstLoad = loadIgv();
    const secondLoad = loadIgv();

    expect(appendSpy).toHaveBeenCalledTimes(1);

    const script = document.getElementById('igv-browser-script') as HTMLScriptElement | null;
    expect(script).not.toBeNull();

    const fakeIgv = { createBrowser: vi.fn() };
    (window as Window & { igv?: unknown }).igv = fakeIgv;
    script?.dispatchEvent(new Event('load'));

    await expect(firstLoad).resolves.toBe(fakeIgv);
    await expect(secondLoad).resolves.toBe(fakeIgv);
  });
});
