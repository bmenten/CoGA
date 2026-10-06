import { describe, expect, it } from 'vitest';

import {
  datasetProgressText,
  jobProgressText,
  runningDatasetText,
  secondsLeftAt,
  type TimedDataset,
} from '../importProgress';

const START = Date.parse('2026-01-05T09:00:00Z');
const at = (seconds: number) => new Date(START + seconds * 1000).toISOString();

const running = (progress: TimedDataset['progress']): TimedDataset => ({
  dataset_type: 'snv',
  enabled: true,
  status: 'running',
  progress,
});

describe('secondsLeftAt', () => {
  it('counts the estimate down from when it was measured', () => {
    const progress = { started_at: at(0), measured_at: at(100), fraction_read: 0.4, seconds_left: 600 };
    expect(secondsLeftAt(progress, START + 100_000)).toBe(600);
    expect(secondsLeftAt(progress, START + 160_000)).toBe(540);
    expect(secondsLeftAt(progress, START + 900_000)).toBe(0);
  });

  it('has no time left to tell before there is an estimate', () => {
    expect(secondsLeftAt({ started_at: at(0), fraction_read: 0.01 }, START)).toBeNull();
  });
});

describe('datasetProgressText', () => {
  it('tells a running dataset’s share read and its time left', () => {
    const dataset = running({
      started_at: at(0),
      measured_at: at(600),
      fraction_read: 0.4837,
      seconds_left: 85 * 60,
    });
    expect(datasetProgressText(dataset, START + 600_000)).toBe('48% read, about 1 h 25 min left');
    expect(datasetProgressText(dataset, START + 600_000 + 85 * 60_000 - 30_000)).toBe(
      '48% read, less than a minute left',
    );
  });

  it('says the time left is being estimated until the import has a pace', () => {
    const dataset = running({ started_at: at(0), measured_at: at(20), fraction_read: 0.02 });
    expect(datasetProgressText(dataset, START + 20_000)).toBe('2% read, estimating the time left');
  });

  it('says a dataset read in full is finishing', () => {
    const dataset = running({ started_at: at(0), measured_at: at(90), fraction_read: 1, seconds_left: 0 });
    expect(datasetProgressText(dataset, START + 95_000)).toBe('read in full, finishing');
  });

  it('tells how long a dataset whose importer counts no bytes has run', () => {
    expect(datasetProgressText(running({ started_at: at(0) }), START + 180_000)).toBe(
      'running for 3 min',
    );
  });

  it('tells how long an ended dataset took, whichever way it ended', () => {
    const progress = { started_at: at(0), finished_at: at(2 * 3600 + 49 * 60 + 42), fraction_read: 0.999 };
    expect(
      datasetProgressText({ dataset_type: 'snv', enabled: true, status: 'imported', progress }, START),
    ).toBe('took 2 h 50 min');
    expect(
      datasetProgressText({ dataset_type: 'snv', enabled: true, status: 'failed', progress }, START),
    ).toBe('took 2 h 50 min');
  });

  it('says a dataset that only registered its files took under a second', () => {
    const progress = { started_at: at(0), finished_at: new Date(START + 300).toISOString() };
    expect(
      datasetProgressText({ dataset_type: 'alignments', enabled: true, status: 'registered', progress }, START),
    ).toBe('took under a second');
  });

  it('says nothing of a dataset the job has not run', () => {
    expect(datasetProgressText({ dataset_type: 'qc', enabled: true, status: 'valid' }, START)).toBeNull();
  });
});

describe('jobProgressText', () => {
  const datasets: TimedDataset[] = [
    { dataset_type: 'qc', enabled: true, status: 'imported', progress: { started_at: at(0), finished_at: at(60) } },
    running({ started_at: at(60), measured_at: at(660), fraction_read: 0.5, seconds_left: 600 }),
    { dataset_type: 'sv_needlr', enabled: true, status: 'valid' },
    { dataset_type: 'cnv', enabled: true, status: 'valid' },
    { dataset_type: 'apcad', enabled: false, status: 'disabled' },
  ];

  it('tells the running dataset’s time left, when it should be done and what follows', () => {
    const doneAround = new Date(START + 1260_000).toLocaleString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
    });
    expect(jobProgressText(datasets, START + 660_000)).toBe(
      `snv: 50% read, about 10 min left (done around ${doneAround}). 2 more datasets to follow.`,
    );
  });

  it('says nothing when no dataset runs', () => {
    expect(jobProgressText(datasets.filter((dataset) => dataset.status !== 'running'), START)).toBeNull();
  });

  it('gives the running dataset in a few words for the jobs table', () => {
    expect(runningDatasetText(datasets, START + 660_000)).toBe('snv: 50% read, about 10 min left');
    expect(runningDatasetText([], START)).toBeNull();
  });
});
