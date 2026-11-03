import { readFileSync } from 'node:fs';
import path from 'node:path';
import { createEvent, fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import ViewerTrackBlock from '../ViewerTrackBlock';
import ViewerInteractionSurface from '../ViewerInteractionSurface';

/** The viewer's own stylesheet module, as the browser receives it. */
const GENOME_CSS = path.resolve(process.cwd(), 'src/styles/theme/genome.css');

const dispatchMouseEvent = (
  element: HTMLElement,
  type: 'mouseDown' | 'mouseMove' | 'mouseUp',
  clientX: number,
  options?: { button?: number },
) => {
  const event = createEvent[type](element, {
    bubbles: true,
    button: options?.button ?? 0,
  });
  // The block measures x from the frame's bounding box + clientX (jsdom reports a
  // zero-origin rect, so clientX maps directly to the in-frame x). We deliberately
  // do NOT set offsetX — the fix must not depend on it. mousemove/mouseup are
  // handled by window-level listeners, so bubbling from the target is required.
  Object.defineProperty(event, 'clientX', { value: clientX });
  fireEvent(element, event);
};

/** A 200 px track over 200 bp (100–300 of a 1 kb chromosome, unless given): 1 px is 1 bp. */
const viewport = (
  onChange: (start: number, end: number) => void,
  mode?: 'pan' | 'zoom',
  region: { regionStart: number; regionEnd: number } = { regionStart: 100, regionEnd: 300 },
) => ({ chromSize: 1000, ...region, mode, onChange });

const block = (label: string, viewportInteraction: ReturnType<typeof viewport>, children?: ReactNode) => (
  <ViewerTrackBlock label={label} width={200} viewportInteraction={viewportInteraction}>
    {children ?? <div style={{ height: 20 }} />}
  </ViewerTrackBlock>
);

const surfaceOf = (container: HTMLElement) =>
  container.querySelector('.viewer-interaction-surface') as HTMLElement;

const trackNamed = (name: RegExp) => screen.getByRole('application', { name });

describe('ViewerTrackBlock', () => {
  it('zooms the viewport when dragging across a track block (default/zoom mode)', () => {
    const onChange = vi.fn();
    render(block('Coverage', viewport(onChange)));

    const track = trackNamed(/coverage viewport/i);
    dispatchMouseEvent(track, 'mouseDown', 50);
    dispatchMouseEvent(track, 'mouseMove', 150);
    dispatchMouseEvent(track, 'mouseUp', 150);

    expect(onChange).toHaveBeenCalledWith(150, 250);
  });

  it('zooms when the drag starts on a nested child element (e.g. an SVG feature)', () => {
    const onChange = vi.fn();
    render(
      block(
        'SVs',
        viewport(onChange, 'zoom'),
        <svg>
          <rect data-testid="feature" x={120} y={0} width={8} height={20} />
        </svg>,
      ),
    );

    // Events fire on the inner <rect>, not the frame — they must still be measured
    // relative to the frame (clientX), not the child (which broke offsetX-based zoom).
    const feature = screen.getByTestId('feature');
    dispatchMouseEvent(feature, 'mouseDown', 50);
    dispatchMouseEvent(feature, 'mouseMove', 150);
    dispatchMouseEvent(feature, 'mouseUp', 150);

    expect(onChange).toHaveBeenCalledWith(150, 250);
  });

  it('pans the window when dragging in pan mode', () => {
    const onChange = vi.fn();
    render(block('Coverage', viewport(onChange, 'pan')));

    const track = trackNamed(/coverage viewport/i);
    // Drag content 50px to the right → window shifts 50px * (200bp / 200px) = 50bp earlier.
    dispatchMouseEvent(track, 'mouseDown', 100);
    dispatchMouseEvent(track, 'mouseMove', 150);
    dispatchMouseEvent(track, 'mouseUp', 150);

    expect(onChange).toHaveBeenCalledWith(50, 250);
  });

  it('treats a near-stationary pan drag as a click (no region change)', () => {
    const onChange = vi.fn();
    render(block('Coverage', viewport(onChange, 'pan')));

    const track = trackNamed(/coverage viewport/i);
    dispatchMouseEvent(track, 'mouseDown', 100);
    dispatchMouseEvent(track, 'mouseUp', 101);

    expect(onChange).not.toHaveBeenCalled();
  });

  // A pan never asks for a window off the chromosome: it stops at either end, and a window as
  // wide as the chromosome stays the whole chromosome.
  it.each([
    ['past its start', { regionStart: 100, regionEnd: 300 }, [100, 250], [0, 200]],
    ['past its end', { regionStart: 700, regionEnd: 900 }, [150, 0], [800, 1000]],
    ['of a window as wide as the chromosome', { regionStart: 0, regionEnd: 1000 }, [100, 150], [0, 1000]],
  ])('keeps a pan %s on the chromosome', (_case, region, [fromX, toX], [start, end]) => {
    const onChange = vi.fn();
    render(block('Coverage', viewport(onChange, 'pan', region)));

    const track = trackNamed(/coverage viewport/i);
    dispatchMouseEvent(track, 'mouseDown', fromX);
    dispatchMouseEvent(track, 'mouseMove', toX);
    dispatchMouseEvent(track, 'mouseUp', toX);

    expect(onChange.mock.calls).toEqual([[start, end]]);
  });

  it('changes nothing for a zoom drag under 5 px, nor for a drag with another button', () => {
    const onChange = vi.fn();
    render(block('Coverage', viewport(onChange, 'zoom')));

    const track = trackNamed(/coverage viewport/i);
    dispatchMouseEvent(track, 'mouseDown', 100);
    dispatchMouseEvent(track, 'mouseUp', 104);
    dispatchMouseEvent(track, 'mouseDown', 20, { button: 2 });
    dispatchMouseEvent(track, 'mouseMove', 180);
    dispatchMouseEvent(track, 'mouseUp', 180);

    expect(onChange).not.toHaveBeenCalled();
  });

  it('leaves the wheel to the page instead of zooming', async () => {
    const onChange = vi.fn();
    render(block('SVs', viewport(onChange, undefined, { regionStart: 200, regionEnd: 400 })));

    const track = trackNamed(/svs viewport/i);
    const wheel = createEvent.wheel(track, { bubbles: true, cancelable: true, clientX: 100, deltaY: -100 });
    fireEvent(track, wheel);

    // Not cancelled, so the scroll reaches the document: over a tall viewer, hijacking
    // the wheel meant a scroll that began on a track never moved the page.
    expect(wheel.defaultPrevented).toBe(false);
    // And no region change of any kind comes from a wheel.
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('drives the shared guide and selection band across the surface', () => {
    const onChange = vi.fn();
    const { container } = render(
      <ViewerInteractionSurface>{block('Coverage', viewport(onChange, 'zoom'))}</ViewerInteractionSurface>,
    );

    const surface = surfaceOf(container);
    const track = trackNamed(/coverage viewport/i);

    // Hovering publishes a guide fraction to the shared surface.
    dispatchMouseEvent(track, 'mouseMove', 120);
    expect(surface.hasAttribute('data-guide')).toBe(true);
    expect(surface.style.getPropertyValue('--viewer-guide-x')).toBe('0.6');

    // Dragging (zoom mode) hides the guide and shows the selection band.
    dispatchMouseEvent(track, 'mouseDown', 40);
    dispatchMouseEvent(track, 'mouseMove', 140);
    expect(surface.hasAttribute('data-guide')).toBe(false);
    expect(surface.hasAttribute('data-selecting')).toBe(true);
    expect(surface.style.getPropertyValue('--viewer-sel-start')).toBe('0.2');
    expect(Number(surface.style.getPropertyValue('--viewer-sel-width'))).toBeCloseTo(0.5);

    // Releasing clears the band and commits the zoom.
    dispatchMouseEvent(track, 'mouseUp', 140);
    expect(surface.hasAttribute('data-selecting')).toBe(false);
    expect(onChange).toHaveBeenCalledWith(140, 240);
  });

  it('previews a pan offset on the shared surface while dragging in pan mode', () => {
    const onChange = vi.fn();
    const { container } = render(
      <ViewerInteractionSurface>{block('Coverage', viewport(onChange, 'pan'))}</ViewerInteractionSurface>,
    );

    const surface = surfaceOf(container);
    const track = trackNamed(/coverage viewport/i);

    dispatchMouseEvent(track, 'mouseDown', 100);
    dispatchMouseEvent(track, 'mouseMove', 130);
    expect(surface.hasAttribute('data-panning')).toBe(true);
    expect(surface.style.getPropertyValue('--viewer-shift-tx')).toBe('30px');

    dispatchMouseEvent(track, 'mouseUp', 130);
    expect(surface.hasAttribute('data-panning')).toBe(false);
    expect(surface.style.getPropertyValue('--viewer-shift-tx')).toBe('0px');
    expect(onChange).toHaveBeenCalledWith(70, 270);
  });

  // Found while fixing ENG-7 (#1019): a track block removed mid-drag took its window listeners
  // with it, so no mouseup cleared the shared surface. Every track stayed shifted, with the
  // grabbing cursor (or the selection band stayed up), until the next drag. A cancelled
  // pointer, or a window that loses focus and with it the mouseup, did the same.
  describe('a gesture that cannot end with a mouseup', () => {
    it('clears the pan when its track is removed mid-drag, and moves nothing', () => {
      const onChange = vi.fn();
      const tracks = (withCoverage: boolean) => (
        <ViewerInteractionSurface>{withCoverage && block('Coverage', viewport(onChange, 'pan'))}</ViewerInteractionSurface>
      );
      const { container, rerender } = render(tracks(true));
      const surface = surfaceOf(container);
      const track = trackNamed(/coverage viewport/i);

      dispatchMouseEvent(track, 'mouseDown', 100);
      dispatchMouseEvent(track, 'mouseMove', 130);
      expect(surface.hasAttribute('data-panning')).toBe(true);

      rerender(tracks(false));
      expect(surface.hasAttribute('data-panning')).toBe(false);
      expect(surface.style.getPropertyValue('--viewer-shift-tx')).toBe('0px');
      dispatchMouseEvent(surface, 'mouseUp', 130);
      expect(onChange).not.toHaveBeenCalled();
    });

    it('clears the selection band when its track is removed mid-drag', () => {
      const tracks = (withCoverage: boolean) => (
        <ViewerInteractionSurface>{withCoverage && block('Coverage', viewport(vi.fn(), 'zoom'))}</ViewerInteractionSurface>
      );
      const { container, rerender } = render(tracks(true));
      const track = trackNamed(/coverage viewport/i);

      dispatchMouseEvent(track, 'mouseDown', 40);
      dispatchMouseEvent(track, 'mouseMove', 140);
      expect(surfaceOf(container).hasAttribute('data-selecting')).toBe(true);

      rerender(tracks(false));
      expect(surfaceOf(container).hasAttribute('data-selecting')).toBe(false);
    });

    it('leaves another track’s live pan alone when a track is removed', () => {
      const onChange = vi.fn();
      const tracks = (withSvs: boolean) => (
        <ViewerInteractionSurface>
          {block('Coverage', viewport(onChange, 'pan'))}
          {withSvs && block('SVs', viewport(vi.fn(), 'pan'))}
        </ViewerInteractionSurface>
      );
      const { container, rerender } = render(tracks(true));
      const surface = surfaceOf(container);
      const coverage = trackNamed(/coverage viewport/i);

      dispatchMouseEvent(coverage, 'mouseDown', 100);
      dispatchMouseEvent(coverage, 'mouseMove', 130);
      rerender(tracks(false));
      expect(surface.hasAttribute('data-panning')).toBe(true);
      expect(surface.style.getPropertyValue('--viewer-shift-tx')).toBe('30px');

      dispatchMouseEvent(coverage, 'mouseUp', 130);
      expect(surface.hasAttribute('data-panning')).toBe(false);
      expect(onChange).toHaveBeenCalledWith(70, 270);
    });

    it.each([
      ['a cancelled pointer', 'pointercancel'],
      ['a window that loses focus', 'blur'],
    ])('ends the pan on %s, and moves nothing', (_label, cancel) => {
      const onChange = vi.fn();
      const { container } = render(
        <ViewerInteractionSurface>{block('Coverage', viewport(onChange, 'pan'))}</ViewerInteractionSurface>,
      );
      const surface = surfaceOf(container);
      const track = trackNamed(/coverage viewport/i);

      dispatchMouseEvent(track, 'mouseDown', 100);
      dispatchMouseEvent(track, 'mouseMove', 130);
      fireEvent(window, new Event(cancel));
      expect(surface.hasAttribute('data-panning')).toBe(false);
      expect(surface.style.getPropertyValue('--viewer-shift-tx')).toBe('0px');

      // The gesture is over: a later move or release neither shifts nor pans.
      dispatchMouseEvent(track, 'mouseMove', 160);
      dispatchMouseEvent(track, 'mouseUp', 160);
      expect(surface.hasAttribute('data-panning')).toBe(false);
      expect(onChange).not.toHaveBeenCalled();
    });
  });

  // ENG-7: the stylesheet gives every track a grabbing cursor while a pan is live, on
  // `[data-panning]`, but nothing set that attribute (the surface marked a pan
  // `data-shifting`), so a pan kept the open hand of `cursor: grab`.
  it('shows the grabbing cursor on every track while a pan drag is live', () => {
    const style = document.createElement('style');
    style.textContent = readFileSync(GENOME_CSS, 'utf8');
    document.head.appendChild(style);
    const panning = viewport(vi.fn(), 'pan');

    try {
      render(
        <ViewerInteractionSurface>
          {block('Coverage', panning)}
          {block('SVs', panning)}
        </ViewerInteractionSurface>,
      );
      const coverage = trackNamed(/coverage viewport/i);
      const svs = trackNamed(/svs viewport/i);
      expect(getComputedStyle(coverage).cursor).toBe('grab');

      dispatchMouseEvent(coverage, 'mouseDown', 100);
      dispatchMouseEvent(coverage, 'mouseMove', 130);
      // The whole surface takes the gesture's cursor, not only the track it began on.
      expect(getComputedStyle(coverage).cursor).toBe('grabbing');
      expect(getComputedStyle(svs).cursor).toBe('grabbing');

      dispatchMouseEvent(coverage, 'mouseUp', 130);
      expect(getComputedStyle(coverage).cursor).toBe('grab');
      expect(getComputedStyle(svs).cursor).toBe('grab');
    } finally {
      style.remove();
    }
  });
});
