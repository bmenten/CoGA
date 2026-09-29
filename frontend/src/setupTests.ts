import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';
import { createMemoryStorage } from './lib/storage';

function installStorageMocks(): void {
  vi.stubGlobal('localStorage', createMemoryStorage());
  vi.stubGlobal('sessionStorage', createMemoryStorage());
}

// jsdom does not implement HTMLCanvasElement.getContext, so canvas-drawing
// components (HaplotypePhasedTrack, SmallVariantTrack, ApcadChart, ...) bail at
// their `if (!ctx) return` guard and their entire draw body goes untested while
// jsdom spams "Not implemented: HTMLCanvasElement.prototype.getContext". A shared
// stub lets the draw path run under test. It is a plain prototype assignment (not
// vi.spyOn/vi.fn) so clearAllMocks/restoreAllMocks cannot strip it; per-test files
// that spyOn getContext themselves still work, since they capture this stub as the
// implementation they restore to.
function createMockCanvasContext(): CanvasRenderingContext2D {
  const noop = (): void => undefined;
  return {
    canvas: document.createElement('canvas'),
    clearRect: noop,
    fillRect: noop,
    strokeRect: noop,
    beginPath: noop,
    closePath: noop,
    moveTo: noop,
    lineTo: noop,
    stroke: noop,
    fill: noop,
    arc: noop,
    arcTo: noop,
    ellipse: noop,
    rect: noop,
    quadraticCurveTo: noop,
    bezierCurveTo: noop,
    clip: noop,
    fillText: noop,
    strokeText: noop,
    measureText: () => ({ width: 0 }) as TextMetrics,
    setLineDash: noop,
    getLineDash: () => [],
    save: noop,
    restore: noop,
    translate: noop,
    scale: noop,
    rotate: noop,
    transform: noop,
    setTransform: noop,
    resetTransform: noop,
    drawImage: noop,
    putImageData: noop,
    getImageData: () => ({ data: new Uint8ClampedArray() }) as ImageData,
    createLinearGradient: () => ({ addColorStop: noop }) as unknown as CanvasGradient,
    createRadialGradient: () => ({ addColorStop: noop }) as unknown as CanvasGradient,
    createPattern: () => null,
  } as unknown as CanvasRenderingContext2D;
}

function installCanvasMock(): void {
  // Server-side tests (the Node proxy in server.mjs) run with `@vitest-environment node`.
  if (typeof HTMLCanvasElement === 'undefined') return;
  HTMLCanvasElement.prototype.getContext = (() =>
    createMockCanvasContext()) as unknown as typeof HTMLCanvasElement.prototype.getContext;
}

installStorageMocks();
installCanvasMock();

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  installStorageMocks();
  installCanvasMock();
});
