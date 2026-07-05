import React, { createContext, useContext, useMemo, useRef } from 'react';

// A single element that hosts the shared interaction state for every windowed
// track. Individual ViewerTrackBlocks report their pointer/drag position through
// this context; the provider writes it to CSS custom properties on one ancestor
// element so the guide line, selection band and pan preview render across ALL
// tracks at once — without re-rendering (and re-fetching) the heavy track
// components on every mouse move.
interface ViewerInteractionSurfaceApi {
  // Vertical guide line, as a 0..1 fraction of the track width. null hides it.
  setGuide: (fraction: number | null) => void;
  // Drag-to-zoom selection band, as 0..1 fractions. Pass null to clear.
  setSelection: (start: number | null, end?: number) => void;
  // Live pan/zoom preview applied to every track's content as a CSS transform:
  // translateX (px) plus an optional horizontal scale (for wheel-zoom preview).
  // null clears the preview (content snaps back before the region commits).
  setShift: (translateX: number | null, scaleX?: number) => void;
}

const ViewerInteractionSurfaceContext = createContext<ViewerInteractionSurfaceApi | null>(null);

export const useViewerInteractionSurface = (): ViewerInteractionSurfaceApi | null =>
  useContext(ViewerInteractionSurfaceContext);

interface ViewerInteractionSurfaceProps {
  className?: string;
  children: React.ReactNode;
}

const ViewerInteractionSurface: React.FC<ViewerInteractionSurfaceProps> = ({ className, children }) => {
  const surfaceRef = useRef<HTMLDivElement | null>(null);

  const api = useMemo<ViewerInteractionSurfaceApi>(
    () => ({
      setGuide(fraction) {
        const el = surfaceRef.current;
        if (!el) return;
        if (fraction === null || Number.isNaN(fraction)) {
          el.removeAttribute('data-guide');
          return;
        }
        el.style.setProperty('--viewer-guide-x', String(Math.min(Math.max(fraction, 0), 1)));
        el.setAttribute('data-guide', '');
      },
      setSelection(start, end) {
        const el = surfaceRef.current;
        if (!el) return;
        if (start === null || end === undefined || Number.isNaN(start) || Number.isNaN(end)) {
          el.removeAttribute('data-selecting');
          return;
        }
        const a = Math.min(Math.max(Math.min(start, end), 0), 1);
        const b = Math.min(Math.max(Math.max(start, end), 0), 1);
        el.style.setProperty('--viewer-sel-start', String(a));
        el.style.setProperty('--viewer-sel-width', String(b - a));
        el.setAttribute('data-selecting', '');
      },
      setShift(translateX, scaleX = 1) {
        const el = surfaceRef.current;
        if (!el) return;
        if (translateX === null || Number.isNaN(translateX)) {
          el.style.setProperty('--viewer-shift-tx', '0px');
          el.style.setProperty('--viewer-shift-sx', '1');
          el.removeAttribute('data-shifting');
          return;
        }
        el.style.setProperty('--viewer-shift-tx', `${translateX}px`);
        el.style.setProperty('--viewer-shift-sx', String(scaleX));
        el.setAttribute('data-shifting', '');
      },
    }),
    [],
  );

  return (
    <ViewerInteractionSurfaceContext.Provider value={api}>
      <div
        ref={surfaceRef}
        className={className ? `viewer-interaction-surface ${className}` : 'viewer-interaction-surface'}
      >
        {children}
      </div>
    </ViewerInteractionSurfaceContext.Provider>
  );
};

export default ViewerInteractionSurface;
