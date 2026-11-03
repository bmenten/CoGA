import { createEvent, fireEvent } from '@testing-library/react';

type MouseEventName = 'click' | 'mouseDown' | 'mouseMove' | 'mouseUp';

/**
 * Fire a mouse event at a position on `target`: the charts and tracks read where the pointer is
 * from `offsetX`/`offsetY`, which jsdom does not compute. The client position is the same point
 * (jsdom lays every element out at the origin), for a tooltip placed at the pointer.
 */
export const fireMouseAt = (type: MouseEventName, target: Element, offsetX: number, offsetY = 0): void => {
  const event = createEvent[type](target, { clientX: offsetX, clientY: offsetY });
  Object.defineProperties(event, { offsetX: { value: offsetX }, offsetY: { value: offsetY } });
  fireEvent(target, event);
};
