import React, { useEffect, useRef } from 'react';
import RoiMarkerOverlay from '../../components/visualizations/RoiMarkerOverlay';
import { useViewerInteractionSurface } from './ViewerInteractionSurface';

interface ViewerRoiRange {
  startX: number;
  endX: number;
}

type ViewerInteractionMode = 'pan' | 'zoom';

interface ViewerTrackBlockProps {
  label: string;
  width: number;
  children: React.ReactNode;
  meta?: React.ReactNode;
  frameClassName?: string;
  roiRange?: ViewerRoiRange | null;
  roiTitle?: string;
  viewportInteraction?: {
    chromSize: number;
    regionStart: number;
    regionEnd: number;
    // What a plain click-drag does; defaults to 'zoom' for callers that don't set it.
    mode?: ViewerInteractionMode;
    // Commit a new [start, end] window (drag-to-zoom result or pan result).
    onChange: (start: number, end: number) => void;
    // Wheel zoom, keeping the genomic position under the cursor fixed. focus is a
    // 0..1 fraction of the track width; factor < 1 zooms in, > 1 zooms out.
    onZoomAt?: (factor: number, focus: number) => void;
  };
}

// One wheel notch. < 1 zooms in (shrinks the window), its inverse zooms out.
const WHEEL_ZOOM_FACTOR = 1 / 1.2;
// How long the wheel must be idle before the accumulated zoom is committed (and
// the tracks refetch). Until then the zoom is shown as an instant CSS transform.
const WHEEL_COMMIT_DELAY = 140;
// Minimum drag travel (px) before a gesture counts as a zoom-select / pan rather
// than a click.
const ZOOM_DRAG_THRESHOLD = 5;
const PAN_DRAG_THRESHOLD = 2;

const clampViewport = (start: number, end: number, chromSize: number) => {
  const safeSpan = Math.max(Math.round(end - start), 1);
  if (safeSpan >= chromSize) {
    return { start: 0, end: chromSize };
  }

  let nextStart = Math.round(start);
  let nextEnd = nextStart + safeSpan;

  if (nextStart < 0) {
    nextStart = 0;
    nextEnd = safeSpan;
  }
  if (nextEnd > chromSize) {
    nextEnd = chromSize;
    nextStart = chromSize - safeSpan;
  }

  return { start: nextStart, end: Math.max(nextEnd, nextStart + 1) };
};

interface DragSession {
  mode: ViewerInteractionMode;
  startX: number;
  startFraction: number;
  regionStart: number;
  regionEnd: number;
  chromSize: number;
  width: number;
  onChange: (start: number, end: number) => void;
}

const ViewerTrackBlock: React.FC<ViewerTrackBlockProps> = ({
  label,
  width,
  children,
  meta,
  frameClassName,
  roiRange,
  roiTitle,
  viewportInteraction,
}) => {
  const frameRef = useRef<HTMLDivElement | null>(null);
  const surface = useViewerInteractionSurface();
  const interactive = Boolean(viewportInteraction);
  const mode: ViewerInteractionMode = viewportInteraction?.mode ?? 'zoom';

  const dragRef = useRef<DragSession | null>(null);
  const moveHandlerRef = useRef<((event: MouseEvent) => void) | null>(null);
  const upHandlerRef = useRef<((event: MouseEvent) => void) | null>(null);
  // Cached frame left-edge; refreshed on scroll/resize so hover/drag math avoids
  // a getBoundingClientRect (a forced layout read) on every mouse event.
  const frameLeftRef = useRef<number | null>(null);
  // Wheel zoom accumulates multiplicatively and shows an instant CSS-transform
  // preview; the single region commit (and the track refetch) is debounced until
  // the wheel settles, so a fast scroll no longer fires a refetch per notch.
  const wheelAccumRef = useRef<number>(1);
  const wheelFocusRef = useRef<number>(0.5);
  const wheelTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // x within the track frame, derived from the frame's own bounding box rather
  // than event.offsetX. offsetX is relative to whichever child element the event
  // lands on (an SVG <rect>, a canvas, a tooltip hitbox, …), so interaction only
  // worked on tracks whose content was a single element at the frame's origin.
  // Measuring against the frame makes zoom/pan work uniformly on every track.
  const readFrameLeft = (): number => {
    if (frameLeftRef.current !== null) return frameLeftRef.current;
    const rect = frameRef.current?.getBoundingClientRect();
    const left = rect ? rect.left : 0;
    frameLeftRef.current = left;
    return left;
  };

  const frameMetrics = (clientX: number): { x: number; fraction: number } => {
    const x = Math.max(0, Math.min(clientX - readFrameLeft(), width));
    return { x, fraction: width > 0 ? x / width : 0 };
  };

  const endDrag = () => {
    if (moveHandlerRef.current) {
      window.removeEventListener('mousemove', moveHandlerRef.current);
      moveHandlerRef.current = null;
    }
    if (upHandlerRef.current) {
      window.removeEventListener('mouseup', upHandlerRef.current);
      upHandlerRef.current = null;
    }
    dragRef.current = null;
  };

  const handleMouseDown = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!viewportInteraction || event.button !== 0) return;
    // A missed mouseup (released off-window, OS dialog stealing focus) could leave
    // a stale drag session with orphaned window listeners; tear it down first.
    if (dragRef.current) endDrag();
    // Commit any pending wheel zoom before starting a drag so the two don't fight.
    commitWheelRef.current();
    event.preventDefault();
    frameLeftRef.current = null; // measure the frame fresh for this gesture
    const { x, fraction } = frameMetrics(event.clientX);
    const session: DragSession = {
      mode,
      startX: x,
      startFraction: fraction,
      regionStart: viewportInteraction.regionStart,
      regionEnd: viewportInteraction.regionEnd,
      chromSize: viewportInteraction.chromSize,
      width,
      onChange: viewportInteraction.onChange,
    };
    dragRef.current = session;

    // The cursor guide gives way to the active gesture's own affordance.
    surface?.setGuide(null);
    if (session.mode === 'zoom') {
      surface?.setSelection(fraction, fraction);
    } else {
      surface?.setShift(0);
    }

    const onMove = (moveEvent: MouseEvent) => {
      const active = dragRef.current;
      if (!active) return;
      const metrics = frameMetrics(moveEvent.clientX);
      if (active.mode === 'zoom') {
        surface?.setSelection(active.startFraction, metrics.fraction);
      } else {
        surface?.setShift(metrics.x - active.startX);
      }
    };

    const onUp = (upEvent: MouseEvent) => {
      const active = dragRef.current;
      endDrag();
      surface?.setSelection(null);
      surface?.setShift(null);
      if (!active) return;

      const { x: endX } = frameMetrics(upEvent.clientX);
      const span = Math.max(active.regionEnd - active.regionStart, 1);
      const commit = (start: number, end: number) => {
        const next = clampViewport(start, end, active.chromSize);
        active.onChange(next.start, next.end);
      };

      if (active.mode === 'zoom') {
        const x1 = Math.min(active.startX, endX);
        const x2 = Math.max(active.startX, endX);
        if (x2 - x1 < ZOOM_DRAG_THRESHOLD) return;
        commit(
          active.regionStart + (x1 / active.width) * span,
          active.regionStart + (x2 / active.width) * span,
        );
      } else {
        const dx = endX - active.startX;
        if (Math.abs(dx) < PAN_DRAG_THRESHOLD) return;
        const bpDelta = -(dx / active.width) * span;
        commit(active.regionStart + bpDelta, active.regionEnd + bpDelta);
      }
    };

    moveHandlerRef.current = onMove;
    upHandlerRef.current = onUp;
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const handleMouseMove = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!viewportInteraction || dragRef.current) return;
    surface?.setGuide(frameMetrics(event.clientX).fraction);
  };

  const handleMouseLeave = () => {
    if (dragRef.current) return;
    surface?.setGuide(null);
  };

  // Native, non-passive wheel listener so preventDefault can stop the page from
  // scrolling while the cursor zooms. Reads the latest handler through a ref so
  // the listener stays attached across region changes.
  // Commit the accumulated wheel zoom once as a real region change; the tracks
  // refetch here (once), not per notch. Kept in a ref so the debounce timer and
  // the drag handler always call the latest closure.
  const commitWheelRef = useRef<() => void>(() => {});
  commitWheelRef.current = () => {
    if (wheelTimerRef.current !== null) {
      clearTimeout(wheelTimerRef.current);
      wheelTimerRef.current = null;
    }
    const factor = wheelAccumRef.current;
    wheelAccumRef.current = 1;
    if (factor === 1) return;
    surface?.setShift(null);
    viewportInteraction?.onZoomAt?.(factor, wheelFocusRef.current);
  };

  const wheelHandlerRef = useRef<(event: WheelEvent) => void>(() => {});
  wheelHandlerRef.current = (event: WheelEvent) => {
    if (!viewportInteraction?.onZoomAt || event.deltaY === 0) return;
    event.preventDefault();
    const { fraction } = frameMetrics(event.clientX);
    const factor = event.deltaY < 0 ? WHEEL_ZOOM_FACTOR : 1 / WHEEL_ZOOM_FACTOR;
    wheelAccumRef.current *= factor;
    wheelFocusRef.current = fraction;
    // Instant, compositor-only preview: scale the current render around the
    // cursor. The actual region commit (and refetch) is deferred until the wheel
    // settles, collapsing a burst of notches into one fetch.
    const scaleX = 1 / wheelAccumRef.current;
    surface?.setShift(fraction * width * (1 - scaleX), scaleX);
    if (wheelTimerRef.current !== null) clearTimeout(wheelTimerRef.current);
    wheelTimerRef.current = setTimeout(() => commitWheelRef.current(), WHEEL_COMMIT_DELAY);
  };

  useEffect(() => {
    const el = frameRef.current;
    if (!el || !interactive) return undefined;
    const handler = (event: WheelEvent) => wheelHandlerRef.current(event);
    el.addEventListener('wheel', handler, { passive: false });
    return () => el.removeEventListener('wheel', handler);
  }, [interactive]);

  // Invalidate the cached frame position when the layout could have shifted, so
  // hover/drag coordinates stay correct without measuring on every mouse event.
  useEffect(() => {
    if (!interactive) return undefined;
    const invalidate = () => {
      frameLeftRef.current = null;
    };
    window.addEventListener('scroll', invalidate, true);
    window.addEventListener('resize', invalidate);
    return () => {
      window.removeEventListener('scroll', invalidate, true);
      window.removeEventListener('resize', invalidate);
    };
  }, [interactive]);

  // Tidy up any in-flight drag listeners / pending wheel commit on unmount.
  useEffect(
    () => () => {
      endDrag();
      if (wheelTimerRef.current !== null) clearTimeout(wheelTimerRef.current);
    },
    [],
  );

  const interactiveClassName = interactive
    ? `viewer-track-interactive viewer-track-interactive--${mode}`
    : undefined;

  const roiOverlay = (
    <RoiMarkerOverlay startX={roiRange?.startX ?? null} endX={roiRange?.endX ?? null} title={roiTitle} />
  );

  return (
    <div className="viewer-track-block" style={{ width }}>
      <div className="viewer-track-head">
        <span className="viewer-track-label">{label}</span>
        {meta}
      </div>
      <div
        ref={frameRef}
        className={[
          frameClassName ? `viz-frame ${frameClassName}` : 'viz-frame',
          interactiveClassName,
        ]
          .filter(Boolean)
          .join(' ')}
        style={{ width: '100%' }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        role={interactive ? 'application' : undefined}
        aria-label={interactive ? `${label} viewport` : undefined}
      >
        {interactive ? (
          <>
            {/* Content + ROI translate together during a pan preview; the guide
                and selection band sit above and are driven by the shared surface. */}
            <div className="viewer-track-shift">
              {children}
              {roiOverlay}
            </div>
            <div className="viewer-track-guide" aria-hidden="true" />
            <div className="viewer-track-band" aria-hidden="true" />
          </>
        ) : (
          <>
            {children}
            {roiOverlay}
          </>
        )}
      </div>
    </div>
  );
};

export default ViewerTrackBlock;
