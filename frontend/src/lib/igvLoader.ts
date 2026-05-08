import type { IGV } from 'igv';
import igvScriptUrl from 'igv/dist/igv.min.js?url';

const IGV_SCRIPT_ID = 'igv-browser-script';

type IgvWindow = Window &
  typeof globalThis & {
    igv?: IGV;
  };

let igvLoadPromise: Promise<IGV> | null = null;

function getGlobalIgv(): IGV | null {
  if (typeof window === 'undefined') {
    return null;
  }
  return (window as IgvWindow).igv ?? null;
}

export async function loadIgv(): Promise<IGV> {
  const existing = getGlobalIgv();
  if (existing) {
    return existing;
  }

  if (igvLoadPromise) {
    return igvLoadPromise;
  }

  if (typeof document === 'undefined') {
    throw new Error('IGV requires a browser environment');
  }

  igvLoadPromise = new Promise<IGV>((resolve, reject) => {
    const script =
      (document.getElementById(IGV_SCRIPT_ID) as HTMLScriptElement | null) ??
      document.createElement('script');
    const shouldAppend = !script.id;

    const cleanup = () => {
      script.removeEventListener('load', handleLoad);
      script.removeEventListener('error', handleError);
    };

    const handleLoad = () => {
      cleanup();
      script.dataset.loaded = 'true';
      const loaded = getGlobalIgv();
      if (!loaded) {
        igvLoadPromise = null;
        reject(new Error('IGV script loaded without exposing window.igv'));
        return;
      }
      resolve(loaded);
    };

    const handleError = () => {
      cleanup();
      if (shouldAppend) {
        script.remove();
      }
      igvLoadPromise = null;
      reject(new Error('Failed to load IGV browser bundle'));
    };

    if (script.dataset.loaded === 'true') {
      handleLoad();
      return;
    }

    script.id = IGV_SCRIPT_ID;
    script.async = true;
    script.src = igvScriptUrl;

    script.addEventListener('load', handleLoad, { once: true });
    script.addEventListener('error', handleError, { once: true });

    if (shouldAppend) {
      document.head.appendChild(script);
    }
  });

  return igvLoadPromise;
}
