import React, { useId, useState } from 'react';
import { createPortal } from 'react-dom';

/**
 * A small, reliable hover/focus tooltip that renders into document.body (fixed
 * position) so it is never clipped by a scrolling table container — and appears
 * immediately, unlike the native `title` attribute. Use for inline chips/badges in
 * tables where a CSS `::after` tooltip would be clipped by `overflow: auto`.
 */
interface Props {
  label: string;
  className?: string;
  children: React.ReactNode;
  /**
   * Set when the child is already a control (a button or link). The wrapper then
   * carries only the hover/focus handlers and drops its own `role`/`tabIndex`/
   * `aria-label`, so the child is not nested inside a second button and its own
   * accessible name is not shadowed by the tooltip text.
   */
  interactiveChild?: boolean;
  /**
   * Set when the child is content that does nothing when pressed, such as a chip holding
   * a value. The wrapper takes keyboard focus, so the tip is reachable without a mouse,
   * but no button role, which would announce a control that does nothing. Its text stays
   * what a screen reader reads, and the tip, while shown, is its description.
   */
  staticChild?: boolean;
}

const TIP_WIDTH = 260;

const InfoTip: React.FC<Props> = ({
  label,
  className,
  children,
  interactiveChild = false,
  staticChild = false,
}) => {
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const tipId = useId();

  const show = (el: HTMLElement) => setAnchor(el.getBoundingClientRect());
  const hide = () => setAnchor(null);

  const anchorProps = interactiveChild
    ? {}
    : staticChild
      ? { tabIndex: 0, 'aria-describedby': anchor ? tipId : undefined }
      : { tabIndex: 0, role: 'button', 'aria-label': label };

  return (
    <>
      <span
        className={className}
        {...anchorProps}
        onMouseEnter={(e) => show(e.currentTarget)}
        onMouseLeave={hide}
        // Capture phase so focusing the inner control still reveals the tip.
        onFocusCapture={(e) => show(e.currentTarget)}
        onBlurCapture={hide}
      >
        {children}
      </span>
      {anchor &&
        createPortal(
          <div
            id={tipId}
            role="tooltip"
            className="info-tip-popover"
            style={{
              position: 'fixed',
              left: Math.max(8, Math.min(anchor.left, window.innerWidth - TIP_WIDTH - 8)),
              top: anchor.bottom + 6,
              width: TIP_WIDTH,
            }}
          >
            {label}
          </div>,
          document.body,
        )}
    </>
  );
};

export default InfoTip;
