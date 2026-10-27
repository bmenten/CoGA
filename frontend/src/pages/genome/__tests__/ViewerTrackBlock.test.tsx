import { readFileSync } from 'node:fs';
import path from 'node:path';
import { createEvent, fireEvent, render, screen } from '@testing-library/react';
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

describe('ViewerTrackBlock', () => {
  it('zooms the viewport when dragging across a track block (default/zoom mode)', () => {
    const onChange = vi.fn();

    render(
      <ViewerTrackBlock
        label="Coverage"
        width={200}
        viewportInteraction={{
          chromSize: 1000,
          regionStart: 100,
          regionEnd: 300,
          onChange,
        }}
      >
        <div style={{ height: 20 }} />
      </ViewerTrackBlock>,
    );

    const track = screen.getByRole('application', { name: /coverage viewport/i });
    dispatchMouseEvent(track, 'mouseDown', 50);
    dispatchMouseEvent(track, 'mouseMove', 150);
    dispatchMouseEvent(track, 'mouseUp', 150);

    expect(onChange).toHaveBeenCalledWith(150, 250);
  });

  it('zooms when the drag starts on a nested child element (e.g. an SVG feature)', () => {
    const onChange = vi.fn();

    render(
      <ViewerTrackBlock
        label="SVs"
        width={200}
        viewportInteraction={{
          chromSize: 1000,
          regionStart: 100,
          regionEnd: 300,
          mode: 'zoom',
          onChange,
        }}
      >
        <svg>
          <rect data-testid="feature" x={120} y={0} width={8} height={20} />
        </svg>
      </ViewerTrackBlock>,
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

    render(
      <ViewerTrackBlock
        label="Coverage"
        width={200}
        viewportInteraction={{
          chromSize: 1000,
          regionStart: 100,
          regionEnd: 300,
          mode: 'pan',
          onChange,
        }}
      >
        <div style={{ height: 20 }} />
      </ViewerTrackBlock>,
    );

    const track = screen.getByRole('application', { name: /coverage viewport/i });
    // Drag content 50px to the right → window shifts 50px * (200bp / 200px) = 50bp earlier.
    dispatchMouseEvent(track, 'mouseDown', 100);
    dispatchMouseEvent(track, 'mouseMove', 150);
    dispatchMouseEvent(track, 'mouseUp', 150);

    expect(onChange).toHaveBeenCalledWith(50, 250);
  });

  it('treats a near-stationary pan drag as a click (no region change)', () => {
    const onChange = vi.fn();

    render(
      <ViewerTrackBlock
        label="Coverage"
        width={200}
        viewportInteraction={{
          chromSize: 1000,
          regionStart: 100,
          regionEnd: 300,
          mode: 'pan',
          onChange,
        }}
      >
        <div style={{ height: 20 }} />
      </ViewerTrackBlock>,
    );

    const track = screen.getByRole('application', { name: /coverage viewport/i });
    dispatchMouseEvent(track, 'mouseDown', 100);
    dispatchMouseEvent(track, 'mouseUp', 101);

    expect(onChange).not.toHaveBeenCalled();
  });

  it('leaves the wheel to the page instead of zooming', async () => {
    const onChange = vi.fn();

    render(
      <ViewerTrackBlock
        label="SVs"
        width={200}
        viewportInteraction={{
          chromSize: 1000,
          regionStart: 200,
          regionEnd: 400,
          onChange,
        }}
      >
        <div style={{ height: 20 }} />
      </ViewerTrackBlock>,
    );

    const track = screen.getByRole('application', { name: /svs viewport/i });
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
      <ViewerInteractionSurface>
        <ViewerTrackBlock
          label="Coverage"
          width={200}
          viewportInteraction={{
            chromSize: 1000,
            regionStart: 100,
            regionEnd: 300,
            mode: 'zoom',
            onChange,
          }}
        >
          <div style={{ height: 20 }} />
        </ViewerTrackBlock>
      </ViewerInteractionSurface>,
    );

    const surface = container.querySelector('.viewer-interaction-surface') as HTMLElement;
    const track = screen.getByRole('application', { name: /coverage viewport/i });

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
      <ViewerInteractionSurface>
        <ViewerTrackBlock
          label="Coverage"
          width={200}
          viewportInteraction={{
            chromSize: 1000,
            regionStart: 100,
            regionEnd: 300,
            mode: 'pan',
            onChange,
          }}
        >
          <div style={{ height: 20 }} />
        </ViewerTrackBlock>
      </ViewerInteractionSurface>,
    );

    const surface = container.querySelector('.viewer-interaction-surface') as HTMLElement;
    const track = screen.getByRole('application', { name: /coverage viewport/i });

    dispatchMouseEvent(track, 'mouseDown', 100);
    dispatchMouseEvent(track, 'mouseMove', 130);
    expect(surface.hasAttribute('data-panning')).toBe(true);
    expect(surface.style.getPropertyValue('--viewer-shift-tx')).toBe('30px');

    dispatchMouseEvent(track, 'mouseUp', 130);
    expect(surface.hasAttribute('data-panning')).toBe(false);
    expect(surface.style.getPropertyValue('--viewer-shift-tx')).toBe('0px');
    expect(onChange).toHaveBeenCalledWith(70, 270);
  });

  // ENG-7: the stylesheet gives every track a grabbing cursor while a pan is live, on
  // `[data-panning]`, but nothing set that attribute (the surface marked a pan
  // `data-shifting`), so a pan kept the open hand of `cursor: grab`.
  it('shows the grabbing cursor on every track while a pan drag is live', () => {
    const style = document.createElement('style');
    style.textContent = readFileSync(GENOME_CSS, 'utf8');
    document.head.appendChild(style);
    const viewport = { chromSize: 1000, regionStart: 100, regionEnd: 300, mode: 'pan' as const, onChange: vi.fn() };

    try {
      render(
        <ViewerInteractionSurface>
          <ViewerTrackBlock label="Coverage" width={200} viewportInteraction={viewport}>
            <div style={{ height: 20 }} />
          </ViewerTrackBlock>
          <ViewerTrackBlock label="SVs" width={200} viewportInteraction={viewport}>
            <div style={{ height: 20 }} />
          </ViewerTrackBlock>
        </ViewerInteractionSurface>,
      );
      const coverage = screen.getByRole('application', { name: /coverage viewport/i });
      const svs = screen.getByRole('application', { name: /svs viewport/i });
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
