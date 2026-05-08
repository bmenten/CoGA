import { createEvent, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ViewerTrackBlock from '../ViewerTrackBlock';

const dispatchMouseEvent = (
  element: HTMLElement,
  type: 'mouseDown' | 'mouseMove' | 'mouseUp',
  offsetX: number,
  options?: { shiftKey?: boolean; button?: number },
) => {
  const event = createEvent[type](element, {
    bubbles: true,
    button: options?.button ?? 0,
    shiftKey: options?.shiftKey ?? false,
  });
  Object.defineProperty(event, 'offsetX', { value: offsetX });
  fireEvent(element, event);
};

describe('ViewerTrackBlock', () => {
  it('zooms the viewport when dragging across a track block', () => {
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

  it('ignores shift-drag panning and wheel input', () => {
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
    dispatchMouseEvent(track, 'mouseDown', 100, { shiftKey: true });
    dispatchMouseEvent(track, 'mouseMove', 150, { shiftKey: true });
    dispatchMouseEvent(track, 'mouseUp', 150, { shiftKey: true });

    expect(onChange).toHaveBeenCalledWith(300, 350);
    onChange.mockClear();

    const wheelEvent = createEvent.wheel(track, {
      bubbles: true,
      clientX: 100,
      deltaY: -100,
    });
    fireEvent(track, wheelEvent);

    expect(onChange).not.toHaveBeenCalled();
  });
});
