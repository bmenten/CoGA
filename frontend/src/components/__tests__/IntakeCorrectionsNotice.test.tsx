import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import IntakeCorrectionsNotice, {
  intakeCorrectionsFromMetadata,
  memberAddedOrRemoved,
} from '../IntakeCorrectionsNotice';

// A family record's metadata after the approval of a corrected delivery (synthetic values).
const metadata = {
  package_import: {
    delivery: {
      id: 'd1',
      revision: 2,
      corrections: [
        {
          revision: 1,
          reason: 'From the request form.',
          by: 'superuser',
          at: '2026-11-10T11:00:00+00:00',
          changes: [
            {
              path: ['members', 'PROBAND', 'clinical_status'],
              kind: 'laboratory',
              before: null,
              after: 'affected',
            },
          ],
        },
        {
          revision: 2,
          reason: 'The request form gives another sex.',
          by: 'superuser',
          at: '2026-11-10T12:00:00+00:00',
          changes: [
            {
              path: ['members', 'PROBAND', 'sex'],
              kind: 'pipeline_used',
              before: 'male',
              after: 'female',
            },
            'x',
          ],
        },
        'not a correction',
      ],
    },
  },
};

describe('IntakeCorrectionsNotice', () => {
  it('reads the corrections the family record keeps, and none where there are none', () => {
    expect(intakeCorrectionsFromMetadata(metadata)).toHaveLength(2);
    expect(intakeCorrectionsFromMetadata(metadata)[1].changes).toEqual([
      {
        path: 'members.PROBAND.sex',
        kind: 'pipeline_used',
        before: 'male',
        after: 'female',
      },
    ]);
    for (const empty of [
      undefined,
      null,
      {},
      { package_import: {} },
      { package_import: { delivery: { corrections: 'x' } } },
    ]) {
      expect(intakeCorrectionsFromMetadata(empty)).toEqual([]);
    }
  });

  it('says the package was corrected at intake and lists each correction, marking what the pipeline used', () => {
    render(<IntakeCorrectionsNotice metadata={metadata} />);

    const notice = screen.getByText('Corrected at intake.').closest('details')!;
    expect(notice).toHaveTextContent(
      '(2 corrections, the pedigree, a member or a sex among them)'
    );
    expect(notice).toHaveTextContent('From the request form.');
    expect(notice).toHaveTextContent(
      'members.PROBAND.clinical_status: not set → affected'
    );
    expect(notice).toHaveTextContent(
      'members.PROBAND.sex: male → female (the pipeline used it)'
    );
  });

  it('marks a member added or removed at intake as a change of the pedigree', () => {
    const relative = { sample_id: 'REL1', sex: 'male', role: 'relative' };
    expect(memberAddedOrRemoved({ before: null, after: relative })).toBe(
      'added'
    );
    expect(memberAddedOrRemoved({ before: relative, after: null })).toBe(
      'removed'
    );
    expect(
      memberAddedOrRemoved({ before: null, after: { present: ['HP:1'] } })
    ).toBeNull();
    expect(
      memberAddedOrRemoved({ before: 'male', after: 'female' })
    ).toBeNull();

    render(
      <IntakeCorrectionsNotice
        metadata={{
          package_import: {
            delivery: {
              corrections: [
                {
                  revision: 1,
                  reason: 'The phased VCF holds the affected uncle.',
                  changes: [
                    {
                      path: ['members', 'REL1'],
                      kind: 'laboratory',
                      before: null,
                      after: relative,
                    },
                  ],
                },
              ],
            },
          },
        }}
      />
    );
    const notice = screen.getByText('Corrected at intake.').closest('details')!;
    expect(notice).toHaveTextContent(
      '(1 correction, the pedigree, a member or a sex among them)'
    );
    expect(notice).toHaveTextContent(
      'members.REL1: not set → {"sample_id":"REL1","sex":"male","role":"relative"} (a member added)'
    );
  });

  it('says nothing for a family whose package was not corrected', () => {
    const { container } = render(
      <IntakeCorrectionsNotice
        metadata={{
          package_import: { delivery: { id: 'd1', corrections: [] } },
        }}
      />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('counts one correction in the singular, without a pedigree change', () => {
    render(
      <IntakeCorrectionsNotice
        metadata={{
          package_import: {
            delivery: {
              corrections: [
                {
                  revision: 1,
                  changes: [{ path: 'family.roi', after: 'CFTR' }],
                },
              ],
            },
          },
        }}
      />
    );
    expect(
      screen.getByText('Corrected at intake.').closest('details')
    ).toHaveTextContent(
      '(1 correction).Revision 1: no reason recordedfamily.roi: not set → CFTR'
    );
  });
});
