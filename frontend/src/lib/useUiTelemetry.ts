import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router';
import { logUiEvent, startUiTelemetry } from './telemetry';

/**
 * Wires the global UI telemetry listeners (clicks, form submits, page-hide
 * flush) for the app, and records an in-app navigation event whenever the route
 * changes. Mount once, near the root of the authenticated app.
 */
export const useUiTelemetry = (): void => {
  const location = useLocation();
  const previousPath = useRef<string | null>(null);

  useEffect(() => startUiTelemetry(), []);

  useEffect(() => {
    const to = location.pathname + location.search;
    logUiEvent({
      event_type: 'navigation',
      category: 'route',
      path: previousPath.current ?? undefined,
      to_path: to,
    });
    previousPath.current = to;
  }, [location.pathname, location.search]);
};
