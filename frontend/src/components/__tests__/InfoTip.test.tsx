import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import InfoTip from '../InfoTip';

describe('InfoTip', () => {
  it('shows the tooltip on hover and hides on leave', () => {
    render(<InfoTip label="Derived from the analysis">Affected / at risk</InfoTip>);
    const trigger = screen.getByText('Affected / at risk');
    expect(screen.queryByRole('tooltip')).toBeNull();

    fireEvent.mouseEnter(trigger);
    expect(screen.getByRole('tooltip').textContent).toBe('Derived from the analysis');

    fireEvent.mouseLeave(trigger);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('also shows on keyboard focus (accessible)', () => {
    render(<InfoTip label="explain">chip</InfoTip>);
    fireEvent.focus(screen.getByText('chip'));
    expect(screen.getByRole('tooltip').textContent).toBe('explain');
  });

  it('makes a static child focusable without a button role, described by the tip', async () => {
    const user = userEvent.setup();
    render(
      <InfoTip label="Warning: Mean depth 18.6x below 20x" staticChild>
        Warning 18.6x
      </InfoTip>,
    );
    const chip = screen.getByText('Warning 18.6x');
    // Nothing happens when it is pressed, so it is no button, and its own text is not
    // replaced by the tip as its name.
    expect(screen.queryByRole('button')).toBeNull();
    expect(chip).not.toHaveAttribute('aria-label');
    expect(chip).not.toHaveAttribute('aria-describedby');

    await user.tab();
    expect(chip).toHaveFocus();
    expect(screen.getByRole('tooltip')).toHaveTextContent('Warning: Mean depth 18.6x below 20x');
    expect(chip).toHaveAccessibleDescription('Warning: Mean depth 18.6x below 20x');

    await user.tab();
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(chip).not.toHaveAttribute('aria-describedby');
  });
});

describe('InfoTip around a control', () => {
  // ENG-10: the QC chip that opens a report is a button inside the tip; focused, it was
  // described by nothing, so a screen reader heard its value alone.
  it('describes the control by the tip while it is shown, keeping its own name', async () => {
    const user = userEvent.setup();
    render(
      <InfoTip label="Warning: Mean depth 18.6x below 20x — Click to open the pipeline QC report" interactiveChild>
        <button type="button">Warning 18.6x</button>
      </InfoTip>,
    );
    const chip = screen.getByRole('button', { name: 'Warning 18.6x' });
    expect(chip).not.toHaveAttribute('aria-describedby');

    await user.tab();
    expect(chip).toHaveFocus();
    expect(chip).toHaveAccessibleDescription(
      'Warning: Mean depth 18.6x below 20x — Click to open the pipeline QC report',
    );
    expect(chip).toHaveAccessibleName('Warning 18.6x');

    await user.tab();
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(chip).not.toHaveAttribute('aria-describedby');
  });
});
