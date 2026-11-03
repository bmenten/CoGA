import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import api from '../../../lib/api';
import { AUTH_STORAGE_KEYS } from '../../../lib/auth';
import { storage } from '../../../lib/storage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import { useFactoryGet } from '../../../test/factoryGet';
import { ACCESSIBLE_PROJECT_ID, projectSelect, waitForProject } from '../../../test/projectSelect';
import FamilyBuilderPage from '../FamilyBuilderPage';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn((url: string) => {
      if (url === '/projects') {
        return Promise.resolve({
          data: [
            {
              id: '11111111-1111-1111-1111-111111111111',
              name: 'Accessible Project',
              families: [],
              samples: [],
            },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    }),
    post: vi.fn(),
  },
}));

const renderPage = (role?: 'admin') => {
  if (role) storage.setItem(AUTH_STORAGE_KEYS.role, role);
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <FamilyBuilderPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

/** Renders the page and waits until the user's analyst project is selected. */
const renderReadyPage = async (role?: 'admin') => {
  renderPage(role);
  await waitForProject();
};

type MemberKind = 'proband' | 'father' | 'mother' | 'child' | 'relative';
const addMember = (kind: MemberKind) => fireEvent.click(screen.getByRole('button', { name: `Add ${kind}` }));

/** Sets the fields given on the `index`th member card (in the order the cards are listed). */
const editMember = (
  index: number,
  values: { sampleId?: string; sex?: string; father?: string; mother?: string },
) => {
  const fields: Array<[RegExp, string | undefined]> = [
    [/^sample id$/i, values.sampleId],
    [/^sex$/i, values.sex],
    [/^father$/i, values.father],
    [/^mother$/i, values.mother],
  ];
  for (const [label, value] of fields) {
    if (value !== undefined) {
      fireEvent.change(screen.getAllByLabelText(label)[index], { target: { value } });
    }
  }
};

const addCouple = () => fireEvent.click(screen.getByRole('button', { name: 'Add couple' }));
const setPartner = (couple: number, partner: 1 | 2, sampleId: string) =>
  fireEvent.change(screen.getAllByLabelText(partner === 1 ? /^partner 1$/i : /^partner 2$/i)[couple], {
    target: { value: sampleId },
  });

/** A proband, its father and its mother, in that order. */
const buildTrio = () => {
  editMember(0, { sampleId: 'PROBAND' });
  addMember('father');
  editMember(1, { sampleId: 'FATHER1' });
  addMember('mother');
  editMember(2, { sampleId: 'MOTHER' });
};

const validationPanel = () =>
  screen.getByRole('heading', { name: 'Validation' }).closest('.surface-card-muted') as HTMLElement;
const issuesShown = () =>
  within(validationPanel())
    .queryAllByRole('listitem')
    .map((item) => item.textContent);
const statusNote = () => document.querySelector('p[aria-live="polite"]');
const pedPreview = () =>
  (screen.getByRole('heading', { name: 'PED preview' }).parentElement!.querySelector('textarea') as HTMLTextAreaElement)
    .value;
const probandBoxes = () =>
  screen.getAllByLabelText(/^proband$/i).map((box) => (box as HTMLInputElement).checked);

const conflict = () => Object.assign(new Error('HTTP 409'), { response: { status: 409 } });
const refusal = (detail: string) =>
  Object.assign(new Error('HTTP 400'), { response: { status: 400, data: { detail } } });

describe('FamilyBuilderPage', () => {
  const { get, factory } = useFactoryGet(api.get);

  beforeEach(() => {
    vi.mocked(api.post).mockReset();
    storage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // SEC-1: a family is added to a project by an analyst of it or an admin.
  it('offers only the projects where the user is an analyst, and says so to a reader', async () => {
    const project = (id: string, name: string, readOnly: boolean) => ({
      id,
      name,
      read_only: readOnly,
      families: [],
      samples: [],
    });
    get.mockImplementation((url, config) =>
      url === '/projects'
        ? Promise.resolve({
            data: [
              project('22222222-2222-2222-2222-222222222222', 'Teaching cohort', true),
              project(ACCESSIBLE_PROJECT_ID, 'Accessible Project', false),
            ],
          })
        : factory(url, config),
    );
    renderPage();
    await waitForProject();
    expect(screen.queryByRole('option', { name: 'Teaching cohort' })).not.toBeInTheDocument();
  });

  it('tells a user who reads all their projects that they cannot add a family', async () => {
    get.mockImplementation((url, config) =>
      url === '/projects'
        ? Promise.resolve({
            data: [{ id: '22222222-2222-2222-2222-222222222222', name: 'Teaching cohort', read_only: true, families: [], samples: [] }],
          })
        : factory(url, config),
    );
    renderPage();
    expect(
      await screen.findByText(
        'You have read-only access (reader) to each of your projects, so you cannot add a family.',
      ),
    ).toBeInTheDocument();
    expect(projectSelect()).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Create family' })).toBeDisabled();
  });

  it.each([
    ['has no project', () => Promise.resolve({ data: [] }), 'No projects are available for this account.', 'No accessible projects'],
    [
      'could not load the projects',
      () => Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } })),
      'Project list could not be loaded.',
      'No accessible projects',
    ],
  ])('says so, and creates no family, when the user %s', async (_case, projects, message, option) => {
    get.mockImplementation((url, config) => (url === '/projects' ? projects() : factory(url, config)));
    renderPage();

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(projectSelect()).toBeDisabled();
    expect(within(projectSelect()).getByRole('option')).toHaveTextContent(option);
    expect(screen.getByRole('button', { name: 'Create family' })).toBeDisabled();
  });

  it('renders the family builder workspace with a dashboard back link', () => {
    renderPage();

    expect(screen.getByRole('heading', { name: 'Family builder', level: 1 })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /back to dashboard/i })).toHaveAttribute(
      'href',
      '/dashboard'
    );
    // Only an admin uploads a PED file.
    expect(screen.queryByRole('button', { name: 'Upload PED' })).not.toBeInTheDocument();
  });

  it('submits the manual family builder and renders the pedigree sketch', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: {
        families: [{ family_id: 'FAM-100', samples: ['PROB-1'] }],
      },
    });

    await renderReadyPage();

    fireEvent.change(screen.getByLabelText(/family id/i), {
      target: { value: 'FAM-100' },
    });
    fireEvent.change(screen.getByLabelText(/sample id/i), {
      target: { value: 'PROB-1' },
    });
    fireEvent.click(screen.getByLabelText(/^affected$/i));
    fireEvent.click(screen.getByLabelText(/^carrier$/i));
    fireEvent.change(screen.getByLabelText(/carrier type/i), {
      target: { value: 'obligate' },
    });

    await waitFor(() => {
      const sketch = screen.getByTestId('pedigree-sketch');
      expect(sketch.querySelector('svg')).not.toBeNull();
    });

    fireEvent.click(screen.getByRole('button', { name: /create family/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/ped/manual', {
        family_id: 'FAM-100',
        project_id: ACCESSIBLE_PROJECT_ID,
        members: [
          {
            sample_id: 'PROB-1',
            father_id: null,
            mother_id: null,
            sex: 'und',
            clinical_status: 'unaffected',
            affected: false,
            is_proband: true,
            carrier_status: 'carrier',
            carrier_type: 'obligate',
          },
        ],
        couples: [],
      })
    );

    expect(
      screen.getByText(/created fam-100 with 1 sample.*accessible project/i)
    ).toBeInTheDocument();
    // A saved family clears the form for the next one.
    expect(screen.getByLabelText(/family id/i)).toHaveValue('');
    expect(screen.getAllByLabelText(/^sample id$/i).map((input) => (input as HTMLInputElement).value)).toEqual(['']);
    expect(probandBoxes()).toEqual([true]);
  });

  it('submits explicit partner relationships from the manual builder', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: {
        families: [{ family_id: 'FAM-COUPLE', samples: ['PARTNER-F', 'PARTNER-M'] }],
      },
    });

    await renderReadyPage();

    fireEvent.change(screen.getByLabelText(/family id/i), {
      target: { value: 'FAM-COUPLE' },
    });
    fireEvent.change(screen.getByLabelText(/sample id/i), {
      target: { value: 'PARTNER-F' },
    });
    fireEvent.change(screen.getByLabelText(/^sex$/i), {
      target: { value: 'female' },
    });
    fireEvent.click(screen.getByRole('button', { name: /add father/i }));
    fireEvent.change(screen.getAllByLabelText(/sample id/i)[1], {
      target: { value: 'PARTNER-M' },
    });
    fireEvent.click(screen.getByRole('button', { name: /add couple/i }));
    fireEvent.change(screen.getByLabelText(/^context$/i), {
      target: { value: 'ECS' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create family/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/ped/manual', {
        family_id: 'FAM-COUPLE',
        project_id: ACCESSIBLE_PROJECT_ID,
        members: [
          {
            sample_id: 'PARTNER-F',
            father_id: null,
            mother_id: null,
            sex: 'female',
            clinical_status: 'affected',
            affected: true,
            is_proband: true,
            carrier_status: 'not_carrier',
            carrier_type: null,
          },
          {
            sample_id: 'PARTNER-M',
            father_id: null,
            mother_id: null,
            sex: 'male',
            clinical_status: 'unaffected',
            affected: false,
            is_proband: false,
            carrier_status: 'not_carrier',
            carrier_type: null,
          },
        ],
        couples: [
          {
            partners: ['PARTNER-M', 'PARTNER-F'],
            context: 'ECS',
            metadata: {},
          },
        ],
      })
    );
  });

  it('tags a monogenic NIPT family and its cfDNA sample', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: { families: [{ family_id: 'FAM-NIPT', samples: ['CFDNA-1'] }] },
    });

    await renderReadyPage();

    fireEvent.change(screen.getByLabelText(/family id/i), {
      target: { value: 'FAM-NIPT' },
    });
    fireEvent.change(screen.getByLabelText(/sample id/i), {
      target: { value: 'CFDNA-1' },
    });
    // The cfDNA toggle only appears once the family is marked monogenic NIPT.
    fireEvent.click(screen.getByLabelText(/monogenic nipt/i));
    fireEvent.click(screen.getByLabelText(/cfdna \(maternal plasma\)/i));
    fireEvent.click(screen.getByRole('button', { name: /create family/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/ped/manual',
        expect.objectContaining({
          family_id: 'FAM-NIPT',
          metadata: { analysis_type: 'monogenic_nipt' },
          members: expect.arrayContaining([
            expect.objectContaining({
              sample_id: 'CFDNA-1',
              metadata: { assay: 'nipt_cfdna' },
            }),
          ]),
        })
      )
    );
  });

  // The builder refuses a pedigree it cannot save consistently: it lists every issue as it is
  // built, and Create family says the first one and sends nothing. Each case starts from
  // family FAM1 with its proband PROBAND.
  it.each<[string, () => void, string[]]>([
    ['no family ID', () => fireEvent.change(screen.getByLabelText(/family id/i), { target: { value: ' ' } }), ['Family ID is required.']],
    ['a member without a sample ID', () => addMember('relative'), ['Member 2: sample ID is required.']],
    [
      'two members under one sample ID',
      () => {
        addMember('relative');
        editMember(1, { sampleId: 'PROBAND' });
      },
      ['Sample IDs must be unique.'],
    ],
    [
      'a member who is their own father',
      () => {
        addMember('father');
        editMember(1, { sampleId: 'FATHER1' });
        editMember(0, { father: 'FATHER1' });
        editMember(0, { sampleId: 'FATHER1' });
      },
      ['Sample IDs must be unique.', 'FATHER1: father cannot reference the same sample.'],
    ],
    [
      'a member who is their own mother',
      () => {
        addMember('mother');
        editMember(1, { sampleId: 'MOTHER' });
        editMember(0, { mother: 'MOTHER' });
        editMember(0, { sampleId: 'MOTHER' });
      },
      ['Sample IDs must be unique.', 'MOTHER: mother cannot reference the same sample.'],
    ],
    [
      'one sample as both parents',
      () => {
        addMember('father');
        editMember(1, { sampleId: 'FATHER1' });
        editMember(0, { father: 'FATHER1' });
        editMember(1, { sex: 'female' });
        editMember(0, { mother: 'FATHER1' });
      },
      ['PROBAND: father and mother must be different.', 'PROBAND: selected father has female sex.'],
    ],
    [
      'a father of female sex',
      () => {
        addMember('father');
        editMember(1, { sampleId: 'FATHER1' });
        editMember(0, { father: 'FATHER1' });
        editMember(1, { sex: 'female' });
      },
      ['PROBAND: selected father has female sex.'],
    ],
    [
      'a mother of male sex',
      () => {
        addMember('mother');
        editMember(1, { sampleId: 'MOTHER' });
        editMember(0, { mother: 'MOTHER' });
        editMember(1, { sex: 'male' });
      },
      ['PROBAND: selected mother has male sex.'],
    ],
    [
      'a couple with one partner',
      () => {
        buildTrio();
        addCouple();
        setPartner(0, 2, '');
      },
      ['Couple 1: select two partners.'],
    ],
    [
      'a couple of one sample',
      () => {
        buildTrio();
        addCouple();
        setPartner(0, 2, 'FATHER1');
      },
      ['Couple 1: partners must be different samples.'],
    ],
    [
      'one couple listed twice',
      () => {
        buildTrio();
        addCouple();
        addCouple();
      },
      ['Couple 2: this partner relationship is already listed.'],
    ],
    // A couple holds its partners' trimmed IDs, so a partner typed with a trailing space and
    // then renamed leaves the couple naming the old ID: the form refuses it until the partner
    // is chosen again.
    [
      'a partner no longer in the form',
      () => {
        buildTrio();
        editMember(1, { sampleId: 'FATHER1 ' });
        addCouple();
        editMember(1, { sampleId: 'FATHER2' });
      },
      ['Couple 1: partner FATHER1 is not present in this form.'],
    ],
    [
      'a second partner no longer in the form',
      () => {
        buildTrio();
        editMember(2, { sampleId: 'MOTHER ' });
        addCouple();
        editMember(2, { sampleId: 'MOTHER2' });
      },
      ['Couple 1: partner MOTHER is not present in this form.'],
    ],
  ])('refuses a pedigree with %s, and says why', async (_case, build, issues) => {
    await renderReadyPage();
    fireEvent.change(screen.getByLabelText(/family id/i), { target: { value: 'FAM1' } });
    editMember(0, { sampleId: 'PROBAND' });
    expect(within(validationPanel()).getByText('Form is consistent and ready to submit.')).toBeInTheDocument();

    build();

    expect(issuesShown()).toEqual(issues);
    expect(within(validationPanel()).getByText('Needs review')).toBeInTheDocument();
    expect(screen.getByText(`${issues.length} issue(s)`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create family' }));
    await waitFor(() => expect(statusNote()).toHaveTextContent(issues[0]));
    expect(statusNote()).toHaveClass('status-note--error');
    expect(api.post).not.toHaveBeenCalled();
  });

  it('previews each member’s role and the PED rows, and follows a renamed or removed parent', async () => {
    await renderReadyPage();
    // Without a family ID the preview names none yet.
    editMember(0, { sampleId: 'PROBAND' });
    expect(pedPreview()).toBe('FAMILY_ID PROBAND 0 0 0 2');

    fireEvent.change(screen.getByLabelText(/family id/i), { target: { value: 'FAM1' } });
    buildTrio();
    editMember(0, { father: 'FATHER1', mother: 'MOTHER' });
    // A child added takes the first named male and female as its parents.
    addMember('child');
    editMember(3, { sampleId: 'SIB1', sex: 'female' });
    addMember('relative');
    fireEvent.click(screen.getAllByLabelText(/^carrier$/i)[2]);
    fireEvent.change(screen.getAllByLabelText(/^carrier type$/i)[2], { target: { value: 'obligate' } });
    fireEvent.click(screen.getAllByLabelText(/^carrier$/i)[1]);

    expect(screen.getAllByText(/^Role preview:/).map((line) => line.textContent)).toEqual([
      'Role preview: Proband',
      'Role preview: Father',
      'Role preview: Mother',
      'Role preview: Child / Sibling',
      'Role preview: Relative',
    ]);
    const cards = screen.getAllByRole('article');
    expect(within(cards[0]).queryByText(/^Children:/)).not.toBeInTheDocument();
    expect(within(cards[1]).getByText(/^Children:/)).toHaveTextContent('Children: PROBAND, SIB1');
    expect(within(cards[2]).getByText(/^Children:/)).toHaveTextContent('Children: PROBAND, SIB1');
    expect(screen.getByText('Carrier obligate')).toBeInTheDocument();
    expect(screen.getByText('Carrier proven')).toBeInTheDocument();
    expect(pedPreview()).toBe(
      [
        'FAM1 PROBAND FATHER1 MOTHER 0 2',
        'FAM1 FATHER1 0 0 1 1',
        'FAM1 MOTHER 0 0 2 1',
        'FAM1 SIB1 FATHER1 MOTHER 2 1',
      ].join('\n'),
    );
    // The unnamed relative is a draft member, not a PED row.
    expect(screen.getByText('5 draft members')).toBeInTheDocument();
    expect(screen.getByText('4 PED row(s)')).toBeInTheDocument();

    // Renaming a parent renames them in their children's rows; removing one clears it there.
    editMember(1, { sampleId: 'FATHER2' });
    editMember(2, { sampleId: 'MOTHER2' });
    expect(pedPreview()).toBe(
      [
        'FAM1 PROBAND FATHER2 MOTHER2 0 2',
        'FAM1 FATHER2 0 0 1 1',
        'FAM1 MOTHER2 0 0 2 1',
        'FAM1 SIB1 FATHER2 MOTHER2 2 1',
      ].join('\n'),
    );
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[2]);
    expect(pedPreview()).toBe(
      ['FAM1 PROBAND FATHER2 0 0 2', 'FAM1 FATHER2 0 0 1 1', 'FAM1 SIB1 FATHER2 0 2 1'].join('\n'),
    );
  });

  // A couple whose two partners are both left unchosen is no relationship: it is neither
  // refused nor sent.
  it('leaves out a couple with no partner chosen', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { families: [{ family_id: 'FAM1', samples: ['PROBAND', 'FATHER1', 'MOTHER'] }] } });
    await renderReadyPage();
    fireEvent.change(screen.getByLabelText(/family id/i), { target: { value: 'FAM1' } });
    buildTrio();
    addCouple();
    setPartner(0, 1, '');
    setPartner(0, 2, '');

    expect(issuesShown()).toEqual([]);
    expect(screen.getByRole('heading', { name: 'Couple 1' }).nextElementSibling).toHaveTextContent('Partner 1 + Partner 2');
    fireEvent.click(screen.getByRole('button', { name: 'Create family' }));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.post).mock.calls[0][1]).toMatchObject({ couples: [] });
  });

  // One proband at a time: the proband added or ticked last is the proband, and removing the
  // proband makes the first member left the proband.
  it('keeps exactly one proband as members are added, ticked and removed', async () => {
    await renderReadyPage();
    editMember(0, { sampleId: 'PROBAND' });
    addMember('relative');
    editMember(1, { sampleId: 'SIB1' });
    expect(probandBoxes()).toEqual([true, false]);

    addMember('proband');
    expect(probandBoxes()).toEqual([false, false, true]);
    fireEvent.click(screen.getAllByLabelText(/^proband$/i)[1]);
    expect(probandBoxes()).toEqual([false, true, false]);

    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[1]);
    expect(probandBoxes()).toEqual([true, false]);
    expect(screen.getAllByLabelText(/^sample id$/i).map((input) => (input as HTMLInputElement).value)).toEqual([
      'PROBAND',
      '',
    ]);

    // With every member removed, the form starts again from one affected proband.
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[0]);
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[0]);
    expect(probandBoxes()).toEqual([true]);
    expect((screen.getByLabelText(/^affected$/i) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole('heading', { name: 'Member 1' })).toBeInTheDocument();
  });

  it('renames a couple’s partner with the member, and drops the couple with a removed partner', async () => {
    await renderReadyPage();
    // Two members of unknown sex: the couple takes the first two named members.
    editMember(0, { sampleId: 'PROBAND' });
    addMember('relative');
    editMember(1, { sampleId: 'REL1' });
    addCouple();
    expect(screen.getByRole('heading', { name: 'Couple 1' }).nextElementSibling).toHaveTextContent('PROBAND + REL1');

    editMember(1, { sampleId: 'REL2' });
    expect(screen.getByRole('heading', { name: 'Couple 1' }).nextElementSibling).toHaveTextContent('PROBAND + REL2');
    setPartner(0, 1, '');
    expect(screen.getByRole('heading', { name: 'Couple 1' }).nextElementSibling).toHaveTextContent('Partner 1 + REL2');

    // The couple's own Remove comes after the members'.
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[2]);
    expect(screen.getByText('No explicit partner relationship has been added.')).toBeInTheDocument();

    // A male and a female member are the couple's default partners, and removing one of them
    // removes the couple.
    addMember('father');
    editMember(2, { sampleId: 'FATHER1' });
    addMember('mother');
    editMember(3, { sampleId: 'MOTHER' });
    addCouple();
    expect(screen.getByRole('heading', { name: 'Couple 1' }).nextElementSibling).toHaveTextContent('FATHER1 + MOTHER');
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[3]);
    expect(screen.queryByRole('heading', { name: 'Couple 1' })).not.toBeInTheDocument();
  });

  // A family that already exists (409) is replaced only by an admin who confirms it; anyone
  // else is told to ask one. Any other refusal says why.
  it.each<[string, 'admin' | undefined, boolean, Array<'ok' | 'conflict' | string>, string, string[]]>([
    ['an analyst', undefined, false, ['conflict'], 'Family already exists; ask an admin to update or replace it.', ['/ped/manual']],
    [
      'an admin who confirms the overwrite',
      'admin',
      true,
      ['conflict', 'ok'],
      'Replaced FAM1 with 1 sample(s) in Accessible Project.',
      ['/ped/manual', '/ped/manual?overwrite=true'],
    ],
    ['an admin who cancels the overwrite', 'admin', false, ['conflict'], 'Family creation cancelled.', ['/ped/manual']],
    [
      'an admin whose overwrite is refused',
      'admin',
      true,
      ['conflict', 'An import of this family is running.'],
      'An import of this family is running.',
      ['/ped/manual', '/ped/manual?overwrite=true'],
    ],
  ])('handles a family that already exists for %s', async (_case, role, confirms, answers, message, urls) => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(confirms);
    for (const answer of answers) {
      if (answer === 'ok') {
        vi.mocked(api.post).mockResolvedValueOnce({ data: { families: [{ family_id: 'FAM1', samples: ['PROBAND'] }] } });
      } else {
        vi.mocked(api.post).mockRejectedValueOnce(answer === 'conflict' ? conflict() : refusal(answer));
      }
    }
    await renderReadyPage(role);
    fireEvent.change(screen.getByLabelText(/family id/i), { target: { value: 'FAM1' } });
    editMember(0, { sampleId: 'PROBAND' });

    fireEvent.click(screen.getByRole('button', { name: 'Create family' }));

    await waitFor(() => expect(statusNote()).toHaveTextContent(message));
    expect(vi.mocked(api.post).mock.calls.map(([url]) => url)).toEqual(urls);
    expect(confirm).toHaveBeenCalledTimes(role === 'admin' ? 1 : 0);
  });

  it.each([
    ['with the server’s reason', refusal('Sample PROBAND is already in another family.'), 'Sample PROBAND is already in another family.'],
    ['with a fixed sentence when there is no reason', {}, 'Family creation failed.'],
  ])('says a refused family creation failed, %s', async (_case, error, message) => {
    vi.mocked(api.post).mockRejectedValueOnce(error);
    await renderReadyPage();
    fireEvent.change(screen.getByLabelText(/family id/i), { target: { value: 'FAM1' } });
    editMember(0, { sampleId: 'PROBAND' });

    fireEvent.click(screen.getByRole('button', { name: 'Create family' }));

    await waitFor(() => expect(statusNote()).toHaveTextContent(message));
    expect(statusNote()).toHaveClass('status-note--error');
    // What was typed stays, to correct and send again.
    expect(screen.getByLabelText(/family id/i)).toHaveValue('FAM1');
  });

  it('says so when the server saved no family', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ data: { families: [] } });
    await renderReadyPage();
    fireEvent.change(screen.getByLabelText(/family id/i), { target: { value: 'FAM1' } });
    editMember(0, { sampleId: 'PROBAND' });

    fireEvent.click(screen.getByRole('button', { name: 'Create family' }));

    await waitFor(() => expect(statusNote()).toHaveTextContent('Family creation returned no family. Please try again.'));
    expect(statusNote()).toHaveClass('status-note--error');
  });

  describe('PED upload (admins)', () => {
    const openUpload = async () => {
      await renderReadyPage('admin');
      fireEvent.click(screen.getByRole('button', { name: 'Upload PED' }));
      expect(screen.getByRole('heading', { name: 'Import pedigree' })).toBeInTheDocument();
    };
    const choosePedFile = () =>
      fireEvent.change(screen.getByLabelText(/^ped file$/i), {
        target: { files: [new File(['FAM1 PROBAND 0 0 1 2\n'], 'family.ped')] },
      });
    const uploadedForm = () => vi.mocked(api.post).mock.calls[0][1] as FormData;

    it('asks for a PED file before it uploads', async () => {
      await openUpload();

      fireEvent.click(screen.getByRole('button', { name: 'Import PED' }));

      await waitFor(() => expect(statusNote()).toHaveTextContent('Select a PED file first.'));
      expect(api.post).not.toHaveBeenCalled();
    });

    it('uploads the PED file with its ROI, inheritance model and carriers, then clears them', async () => {
      vi.mocked(api.post).mockResolvedValueOnce({
        data: { families: [{ family_id: 'FAM1', samples: ['PROBAND', 'FATHER1', 'MOTHER'] }] },
      });
      await openUpload();
      choosePedFile();
      fireEvent.change(screen.getByLabelText(/^roi gene or region$/i), { target: { value: ' CFTR ' } });
      fireEvent.change(screen.getByLabelText(/^inheritance model$/i), { target: { value: 'AR' } });
      fireEvent.change(screen.getByLabelText(/^obligate carriers$/i), { target: { value: ' MOTHER ' } });
      fireEvent.change(screen.getByLabelText(/^proven carriers$/i), { target: { value: 'FATHER1' } });

      fireEvent.click(screen.getByRole('button', { name: 'Import PED' }));

      await waitFor(() =>
        expect(statusNote()).toHaveTextContent('Imported FAM1 with 3 sample(s) in Accessible Project.'),
      );
      const [url, , config] = vi.mocked(api.post).mock.calls[0];
      expect(url).toBe(`/ped/upload?project_id=${ACCESSIBLE_PROJECT_ID}`);
      expect(config).toEqual({ headers: { 'Content-Type': 'multipart/form-data' } });
      expect((uploadedForm().get('file') as File).name).toBe('family.ped');
      expect(Object.fromEntries([...uploadedForm().entries()].filter(([key]) => key !== 'file'))).toEqual({
        roi_query: 'CFTR',
        inheritance_model: 'AR',
        obligate_carriers: 'MOTHER',
        proven_carriers: 'FATHER1',
      });
      expect(screen.getByLabelText(/^roi gene or region$/i)).toHaveValue('');
      expect(screen.getByLabelText(/^inheritance model$/i)).toHaveValue('');
      expect(screen.getByLabelText(/^obligate carriers$/i)).toHaveValue('');
      expect(screen.getByLabelText(/^proven carriers$/i)).toHaveValue('');
    });

    it('replaces an existing family on a confirmed overwrite, and sends only the fields given', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(true);
      vi.mocked(api.post)
        .mockRejectedValueOnce(conflict())
        .mockResolvedValueOnce({ data: { families: [{ family_id: 'FAM1', samples: ['PROBAND'] }] } });
      await openUpload();
      choosePedFile();

      fireEvent.click(screen.getByRole('button', { name: 'Import PED' }));

      await waitFor(() => expect(statusNote()).toHaveTextContent('Replaced FAM1 with 1 sample(s) in Accessible Project.'));
      expect(vi.mocked(api.post).mock.calls.map(([url]) => url)).toEqual([
        `/ped/upload?project_id=${ACCESSIBLE_PROJECT_ID}`,
        `/ped/upload?project_id=${ACCESSIBLE_PROJECT_ID}&overwrite=true`,
      ]);
      expect([...uploadedForm().keys()]).toEqual(['file']);
    });

    it('says so when no family could be read from the PED file', async () => {
      vi.mocked(api.post).mockResolvedValueOnce({ data: { families: [] } });
      await openUpload();
      choosePedFile();

      fireEvent.click(screen.getByRole('button', { name: 'Import PED' }));

      await waitFor(() =>
        expect(statusNote()).toHaveTextContent(
          'No families were parsed from the PED file. Check that each row has at least six space-separated columns.',
        ),
      );
      expect(statusNote()).toHaveClass('status-note--error');
    });

    it('goes back to the manual builder', async () => {
      await openUpload();
      fireEvent.click(screen.getByRole('button', { name: 'Manual builder' }));
      expect(screen.queryByRole('heading', { name: 'Import pedigree' })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Create family' })).toBeInTheDocument();
    });
  });
});
