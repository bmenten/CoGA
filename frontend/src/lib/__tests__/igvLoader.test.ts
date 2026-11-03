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

  // The viewer shows a failed load as a recoverable error (IgvViewer.test.tsx): the loader must
  // reject, not hang, and let the retry load the bundle afresh.
  it('fails when the bundle cannot load, and loads it afresh on the next call', async () => {
    const { loadIgv } = await import('../igvLoader');

    const failed = loadIgv();
    document.getElementById('igv-browser-script')?.dispatchEvent(new Event('error'));
    await expect(failed).rejects.toThrow('Failed to load IGV browser bundle');
    expect(document.getElementById('igv-browser-script')).toBeNull();

    const retried = loadIgv();
    const script = document.getElementById('igv-browser-script');
    expect(script).not.toBeNull();
    const fakeIgv = { createBrowser: vi.fn() };
    (window as Window & { igv?: unknown }).igv = fakeIgv;
    script?.dispatchEvent(new Event('load'));
    await expect(retried).resolves.toBe(fakeIgv);
  });

  it('fails when the bundle loads without exposing window.igv, and tries again on the next call', async () => {
    const { loadIgv } = await import('../igvLoader');

    const loaded = loadIgv();
    document.getElementById('igv-browser-script')?.dispatchEvent(new Event('load'));
    await expect(loaded).rejects.toThrow('IGV script loaded without exposing window.igv');

    // The script is marked loaded: the next call reads window.igv again at once, instead of
    // waiting for a load event that has already fired.
    await expect(loadIgv()).rejects.toThrow('IGV script loaded without exposing window.igv');
    const fakeIgv = { createBrowser: vi.fn() };
    (window as Window & { igv?: unknown }).igv = fakeIgv;
    await expect(loadIgv()).resolves.toBe(fakeIgv);
  });
});
