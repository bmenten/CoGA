// ACMG points-scale readout — REQ-UI-003 (risk H3).
// The bar must show the class, the signed point total, the VUS sub-tier, and a
// BA1 stand-alone-benign override, with an accessible aria-label describing them.
// The ticks carry the posterior probability of each point (Tavtigian: prior 0.10,
// odds 350^(points/8)); the outer class boundaries read Benign < 0.1%, Pathogenic > 99%.

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import AcmgScaleBar, {
  SCALE_TICKS,
  posteriorForPoints,
  scalePositionPct,
} from '../AcmgScaleBar';
import type { AcmgClassification } from '../../../lib/acmg';

describe('AcmgScaleBar', () => {
  it('shows the class label and signed point total for a pathogenic call', () => {
    const classification: AcmgClassification = {
      points: 12,
      classKey: 'acmg_class_5',
      label: 'Pathogenic - class 5',
      standAloneBenign: false,
      vusTier: null,
    };
    render(<AcmgScaleBar classification={classification} />);

    expect(screen.getByText('Pathogenic - class 5')).toBeInTheDocument();
    expect(screen.getByText('+12 pts')).toBeInTheDocument();
    expect(screen.getByRole('img')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('+12 points'),
    );
  });

  it('surfaces the VUS sub-tier chip and aria-label', () => {
    const classification: AcmgClassification = {
      points: 5,
      classKey: 'acmg_class_3',
      label: 'VUS - class 3',
      standAloneBenign: false,
      vusTier: 'hot',
    };
    render(<AcmgScaleBar classification={classification} />);

    expect(screen.getByText('Hot VUS')).toBeInTheDocument();
    expect(screen.getByRole('img')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('(Hot VUS)'),
    );
  });

  it('renders the BA1 stand-alone benign override', () => {
    const classification: AcmgClassification = {
      points: -3,
      classKey: 'acmg_class_1',
      label: 'Benign - class 1',
      standAloneBenign: true,
      vusTier: null,
    };
    render(<AcmgScaleBar classification={classification} />);

    expect(screen.getByText('BA1 pts')).toBeInTheDocument();
    expect(screen.getByRole('img')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('BA1 points'),
    );
  });

  it('labels each VUS tick with the posterior of its point total', () => {
    for (const tick of SCALE_TICKS.filter((t) => t.points >= 0 && t.points <= 6)) {
      const expected = `${Number((posteriorForPoints(tick.points) * 100).toFixed(1))}%`;
      expect(tick.label).toBe(expected);
    }
  });

  it('puts the class boundaries where the posterior crosses 0.1% and 99%', () => {
    expect(posteriorForPoints(-7)).toBeLessThan(0.001);
    expect(posteriorForPoints(-6)).toBeGreaterThanOrEqual(0.001);
    expect(posteriorForPoints(9)).toBeLessThan(0.99);
    expect(posteriorForPoints(10)).toBeGreaterThan(0.99);
    const boundaries = SCALE_TICKS.filter((t) => t.boundary).map((t) => [t.points, t.label]);
    expect(boundaries).toEqual([
      [-6, '0.1%'],
      [0, '10%'],
      [6, '90%'],
      [10, '99%'],
    ]);
  });

  it('renders every tick with its posterior label', () => {
    const classification: AcmgClassification = {
      points: 0,
      classKey: 'acmg_class_3',
      label: 'VUS - class 3',
      standAloneBenign: false,
      vusTier: 'cold',
    };
    const { container } = render(<AcmgScaleBar classification={classification} />);

    expect(container.querySelectorAll('.acmg-scalebar-tick')).toHaveLength(SCALE_TICKS.length);
    expect(container.querySelectorAll('.acmg-scalebar-tick--boundary')).toHaveLength(4);
    for (const tick of SCALE_TICKS) {
      expect(screen.getByText(tick.label)).toBeInTheDocument();
    }
  });

  it('points the arrow into the cell of its own class, in point order', () => {
    const positions = [-9, -7, -6, -1, 0, 1, 5, 6, 9, 10, 14].map(scalePositionPct);
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
    expect(new Set(positions.slice(0, 2)).size).toBe(1); // all Benign totals share the cap
    expect(new Set(positions.slice(-2)).size).toBe(1); // all Pathogenic totals share the cap
    // Class bands, left to right: B 0–6 · LB 6–20 · VUS 20–80 · LP 80–94 · P 94–100.
    expect(scalePositionPct(-7)).toBeLessThan(6);
    expect(scalePositionPct(-6)).toBeGreaterThan(6);
    expect(scalePositionPct(-1)).toBeLessThan(20);
    expect(scalePositionPct(0)).toBeGreaterThan(20);
    expect(scalePositionPct(5)).toBeLessThan(80);
    expect(scalePositionPct(6)).toBeGreaterThan(80);
    expect(scalePositionPct(9)).toBeLessThan(94);
    expect(scalePositionPct(10)).toBeGreaterThan(94);
  });
});
