// What the package import view says of a dataset's progress: for the running dataset, the
// share of its files read and the time it should still take; for one that has ended, how
// long it took. The backend measures both (backend/app/services/import_progress.py): the
// time left is the rest of the files at the pace read so far, as of `measured_at`.

import { useEffect, useState } from 'react';

import type { FamilyImportDatasetProgress } from '../../lib/apiSchema.generated';
import { formatDuration } from '../../lib/format';

/** A dataset's progress as the job reports it; a key it may leave null may be left out. */
export type DatasetProgress = Pick<FamilyImportDatasetProgress, 'started_at'> &
  Partial<FamilyImportDatasetProgress>;

export type TimedDataset = {
  dataset_type: string;
  enabled: boolean;
  status: string;
  progress?: DatasetProgress | null;
};

// The statuses of a dataset the job has still to import.
const TO_IMPORT_STATUSES = new Set(['pending', 'valid', 'warning']);

const toMillis = (value?: string | null): number | null => {
  if (!value) return null;
  const millis = new Date(value).getTime();
  return Number.isNaN(millis) ? null : millis;
};

/** The time left at `now`: the estimate, less the time since it was measured. */
export const secondsLeftAt = (progress: DatasetProgress, now: number): number | null => {
  if (progress.seconds_left == null) return null;
  const measuredAt = toMillis(progress.measured_at);
  const sinceMeasured = measuredAt == null ? 0 : Math.max(0, (now - measuredAt) / 1000);
  return Math.max(0, progress.seconds_left - sinceMeasured);
};

const timeLeftText = (seconds: number): string =>
  seconds < 60 ? 'less than a minute left' : `about ${formatDuration(seconds)} left`;

/**
 * What the view says of a dataset's progress at `now`: how long it took, once it has
 * ended; while it runs, the share of its files read and the time left, or how long it has
 * run when its importer does not count what it reads. Null when there is nothing to say.
 */
export const datasetProgressText = (dataset: TimedDataset, now: number): string | null => {
  const progress = dataset.progress;
  if (!progress) return null;
  const startedAt = toMillis(progress.started_at);
  const finishedAt = toMillis(progress.finished_at);
  if (startedAt != null && finishedAt != null) {
    const seconds = (finishedAt - startedAt) / 1000;
    return seconds < 1 ? 'took under a second' : `took ${formatDuration(seconds)}`;
  }
  if (dataset.status !== 'running') return null;
  const fraction = progress.fraction_read;
  if (fraction == null) {
    return startedAt == null
      ? null
      : `running for ${formatDuration(Math.max(0, (now - startedAt) / 1000))}`;
  }
  // Read in full: what is left is the import's last step, such as the summaries refresh.
  if (fraction >= 1) return 'read in full, finishing';
  const read = `${Math.floor(fraction * 100)}% read`;
  const left = secondsLeftAt(progress, now);
  return left == null ? `${read}, estimating the time left` : `${read}, ${timeLeftText(left)}`;
};

const clockTime = (millis: number, now: number): string => {
  const date = new Date(millis);
  const sameDay = date.toDateString() === new Date(now).toDateString();
  return date.toLocaleString(
    undefined,
    sameDay
      ? { hour: '2-digit', minute: '2-digit' }
      : { weekday: 'short', hour: '2-digit', minute: '2-digit' },
  );
};

/**
 * What the view says of a job's progress at `now`: its running dataset's, with the time of
 * day it should be done and the datasets to follow, or null when none runs. The time left
 * is the running dataset's: the others have yet to be read.
 */
export const jobProgressText = (datasets: TimedDataset[], now: number): string | null => {
  const running = datasets.find((dataset) => dataset.status === 'running');
  if (!running) return null;
  const parts = [`${running.dataset_type}: ${datasetProgressText(running, now) ?? 'running'}`];
  const progress = running.progress;
  const measuredAt = toMillis(progress?.measured_at);
  if (progress?.seconds_left && measuredAt != null && (progress.fraction_read ?? 0) < 1) {
    parts[0] += ` (done around ${clockTime(measuredAt + progress.seconds_left * 1000, now)})`;
  }
  const toFollow = datasets.filter(
    (dataset) => dataset.enabled && TO_IMPORT_STATUSES.has(dataset.status),
  ).length;
  if (toFollow) parts.push(`${toFollow} more dataset${toFollow === 1 ? '' : 's'} to follow`);
  return `${parts.join('. ')}.`;
};

/** The running dataset's progress in a few words, for a table cell; null when none runs. */
export const runningDatasetText = (datasets: TimedDataset[], now: number): string | null => {
  const running = datasets.find((dataset) => dataset.status === 'running');
  if (!running) return null;
  return `${running.dataset_type}: ${datasetProgressText(running, now) ?? 'running'}`;
};

/** The time now, moved on every `intervalMs` while `active`: a running import's time left
 * counts down between the job's reports. */
export const useNow = (active: boolean, intervalMs = 15_000): number => {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const tick = () => setNow(Date.now());
    // At once too: the page may have been open a while before the import started.
    const first = window.setTimeout(tick, 0);
    const timer = window.setInterval(tick, intervalMs);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, [active, intervalMs]);
  return now;
};
