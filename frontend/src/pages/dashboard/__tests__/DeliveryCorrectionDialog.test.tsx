import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import api from '../../../lib/api';
import type { ImportDeliveryOut } from '../../../lib/apiSchema.generated';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import DeliveryCorrectionDialog, {
  PackageFileDialog,
  changesBetween,
  newMember,
  packageJson,
  toForm,
  type PackageDocument,
} from '../DeliveryCorrectionDialog';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

// A synthetic PGT package as CoGA keeps it.
const pkg: PackageDocument = {
  project: 'PGT',
  analysis_type: 'pgt',
  family: { family_id: 'FAM001', pgt: { inheritance_model: 'AR' } },
  members: [
    { sample_id: 'FATHER1', sex: 'male', role: 'father' },
    { sample_id: 'MOTHER', sex: 'female', role: 'mother' },
    {
      sample_id: 'EMB1',
      sex: 'female',
      father: 'FATHER1',
      mother: 'MOTHER',
      role: 'embryo',
      hpo: { present: ['HP:0001250'], absent: [] },
    },
  ],
  datasets: {
    apcad: { per_sample: { EMB1: { file: 'apcad/EMB1.csv' } } },
    qc: { enabled: false, report: 'qc.html' },
  },
  pipeline_used_pedigree: ['apcad'],
};

const delivery: ImportDeliveryOut = {
  _id: 'id-FAM001',
  watch_root: '/data/share',
  folder_path: '/data/share/FAM001',
  folder_name: 'FAM001',
  state: 'ready',
  package_fingerprint: 'a'.repeat(64),
  marker_sha256: 'b'.repeat(64),
  run_id: 'RUN1',
  family_id: 'FAM001',
  project_id: 'p1',
  project: 'PGT',
  analysis_type: 'pgt',
  pipeline_name: 'nf-cmgg/copgtm',
  pipeline_version: '1.1.0',
  request_id: null,
  issues: [],
  warnings: [],
  manifest_sha256: null,
  revision: 1,
  origin: 'watched',
  job_id: null,
  job_status: null,
  job_dry_run: null,
  first_seen_at: '2026-11-10T10:00:00Z',
  last_seen_at: '2026-11-10T10:05:00Z',
  state_changed_at: '2026-11-10T10:00:00Z',
  source_change: null,
  overwrite_approvable: false,
};

const packageOut = {
  delivery_id: 'id-FAM001',
  revision: 1,
  package: pkg,
  package_sha256: 'c'.repeat(64),
  original: pkg,
  proposed: ['datasets'],
  origin: 'watched',
  addable_members: [] as string[],
  members_as_checked: ['FATHER1', 'MOTHER', 'EMB1'],
  revisions: [
    {
      revision: 1,
      changes: [
        {
          path: ['members', 'EMB1', 'role'],
          kind: 'laboratory',
          before: null,
          after: 'embryo',
        },
      ],
      reason: 'The samplesheet names it an embryo.',
      created_by_name: 'superuser',
      created_at: '2026-11-10T11:00:00Z',
      package_sha256: 'c'.repeat(64),
    },
  ],
};

describe('changesBetween', () => {
  it('finds nothing in a form left as it was', () => {
    expect(changesBetween(pkg, toForm(pkg))).toEqual([]);
  });

  it('turns each edited field into one change, set or removed', () => {
    const form = toForm(pkg);
    form.project = ' PGT2 ';
    form.roi = 'CFTR';
    form.inheritance_model = '';
    form.affected_parents = ['MOTHER'];
    form.members[2].clinical_status = 'affected';
    form.members[2].sex = 'male';
    form.members[0].role = '';
    form.members[2].hpo_present = 'HP:0001250; HP:0004322';
    form.members[2].hpo_absent = 'HP:0000001';
    form.enabled.apcad = false;
    form.enabled.qc = true;

    expect(changesBetween(pkg, form)).toEqual([
      { path: ['project'], value: 'PGT2' },
      { path: ['family', 'roi'], value: 'CFTR' },
      { path: ['family', 'pgt', 'inheritance_model'], value: null },
      { path: ['family', 'pgt', 'affected_parents'], value: ['MOTHER'] },
      { path: ['members', 'FATHER1', 'role'], value: null },
      { path: ['members', 'EMB1', 'sex'], value: 'male' },
      { path: ['members', 'EMB1', 'clinical_status'], value: 'affected' },
      {
        path: ['members', 'EMB1', 'hpo'],
        value: {
          present: ['HP:0001250', 'HP:0004322'],
          absent: ['HP:0000001'],
        },
      },
      {
        path: ['datasets', 'apcad'],
        value: {
          per_sample: { EMB1: { file: 'apcad/EMB1.csv' } },
          enabled: false,
        },
      },
      { path: ['datasets', 'qc'], value: { report: 'qc.html' } },
    ]);
  });

  it('removes HPO terms all cleared, and a list emptied', () => {
    const withIndexes: PackageDocument = {
      ...pkg,
      family: { family_id: 'FAM001', pgt: { indexes: ['MOTHER'] } },
    };
    const form = toForm(withIndexes);
    form.members[2].hpo_present = '  ';
    form.indexes = [];

    expect(changesBetween(withIndexes, form)).toEqual([
      { path: ['family', 'pgt', 'indexes'], value: null },
      { path: ['members', 'EMB1', 'hpo'], value: null },
    ]);
  });

  it('adds a member whole, removes one, and changes whom a member is related to', () => {
    const form = toForm(pkg);
    form.members = form.members.filter((member) => member.sample_id !== 'EMB1');
    form.members[1].related_to = ['FATHER1'];
    form.members.push({
      ...newMember('REL1'),
      sex: 'male',
      clinical_status: 'affected',
      related_to: ['MOTHER'],
      hpo_present: 'HP:0001250',
    });

    expect(changesBetween(pkg, form)).toEqual([
      { path: ['members', 'MOTHER', 'related_to'], value: ['FATHER1'] },
      { path: ['members', 'EMB1'], value: null },
      {
        path: ['members', 'REL1'],
        value: {
          sex: 'male',
          role: 'relative',
          clinical_status: 'affected',
          hpo: { present: ['HP:0001250'], absent: [] },
          related_to: ['MOTHER'],
        },
      },
    ]);
  });

  it('reads a package without members, datasets or a family', () => {
    const bare: PackageDocument = { project: null };
    expect(toForm(bare)).toEqual({
      project: '',
      analysis_type: '',
      roi: '',
      inheritance_model: '',
      affected_parents: [],
      indexes: [],
      members: [],
      enabled: {},
    });
    expect(changesBetween(bare, toForm(bare))).toEqual([]);
  });
});

describe('DeliveryCorrectionDialog', () => {
  const onSaved = vi.fn();
  const onCancel = vi.fn();

  const renderDialog = () =>
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <DeliveryCorrectionDialog
          delivery={delivery}
          onCancel={onCancel}
          onSaved={onSaved}
        />
      </QueryClientProvider>
    );

  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    onSaved.mockReset();
    onCancel.mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: packageOut });
  });

  it('shows the package, what CoGA proposed, what the pipeline used and the corrections made', async () => {
    renderDialog();

    expect(
      await screen.findByRole('combobox', { name: 'Sex of EMB1' })
    ).toHaveValue('female');
    expect(api.get).toHaveBeenCalledWith(
      '/family-imports/deliveries/id-FAM001/package'
    );
    expect(
      screen.getByText(
        'CoGA proposed the datasets from the folder: check them.'
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(/The pipeline computed apcad with the sex and parents/)
    ).toBeInTheDocument();
    expect(screen.getByText('Corrections made (1)')).toBeInTheDocument();
    expect(
      screen.getByText(/The samplesheet names it an embryo\./)
    ).toBeInTheDocument();
    // The run, the IDs and the files are the pipeline's: no field offers them.
    expect(screen.queryByDisplayValue('FAM001')).not.toBeInTheDocument();
    // The analyses by the laboratory's names.
    const analysis = screen.getByRole('combobox', { name: 'Analysis' });
    expect(analysis).toHaveValue('pgt');
    expect(
      within(analysis)
        .getAllByRole('option')
        .map((option) => option.textContent)
    ).toEqual([
      'Not set',
      'LR-WGS',
      'LR-AS (long-read adaptive sampling)',
      'SR-WGS',
      'PGT',
      'Expanded Carrier Screening',
      'mNIPT',
    ]);
  });

  it('saves a correction with its reason on the revision it was made on', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: { delivery: { ...delivery, revision: 2 }, package: packageOut },
    });
    renderDialog();
    const status = await screen.findByRole('combobox', {
      name: 'Clinical status of EMB1',
    });
    const save = await screen.findByRole('button', {
      name: 'Save the correction',
    });
    expect(save).toBeDisabled();

    fireEvent.change(status, { target: { value: 'affected' } });
    expect(save).toBeDisabled();
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Reason for the correction' }),
      { target: { value: 'From the request form.' } }
    );
    expect(
      screen.getByText('1 field(s) changed: members.EMB1.clinical_status.')
    ).toBeInTheDocument();
    fireEvent.click(save);

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/family-imports/deliveries/id-FAM001/revisions',
        {
          revision: 1,
          reason: 'From the request form.',
          changes: [
            { path: ['members', 'EMB1', 'clinical_status'], value: 'affected' },
          ],
        }
      )
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it('says why a correction was refused and stays open', async () => {
    vi.mocked(api.post).mockRejectedValue({
      response: {
        status: 409,
        data: { detail: 'The delivery was corrected since you looked at it.' },
      },
      isAxiosError: true,
    });
    renderDialog();
    fireEvent.click(
      await screen.findByRole('checkbox', { name: /apcad in the import/ })
    );
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Reason for the correction' }),
      { target: { value: 'Left out: the pedigree changed.' } }
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Save the correction' })
    );

    expect(
      await screen.findByText(/could not be saved|corrected since you looked/)
    ).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('tells a delivery whose package CoGA could not read', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { ...packageOut, package: null, revisions: [], proposed: [] },
    });
    renderDialog();

    expect(
      await screen.findByText(/CoGA could not read this delivery/)
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Save the correction' })
    ).toBeDisabled();
  });

  it('marks a member as the affected parent or an index', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: { delivery, package: packageOut },
    });
    renderDialog();
    const checkboxes = await screen.findAllByRole('checkbox', {
      name: 'Affected parent',
    });
    fireEvent.click(checkboxes[1]);
    fireEvent.click(screen.getAllByRole('checkbox', { name: 'Index' })[2]);
    fireEvent.click(screen.getAllByRole('checkbox', { name: 'Index' })[2]);
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Reason for the correction' }),
      { target: { value: 'The mother carries the condition.' } }
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Save the correction' })
    );

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/family-imports/deliveries/id-FAM001/revisions',
        expect.objectContaining({
          changes: [
            { path: ['family', 'pgt', 'affected_parents'], value: ['MOTHER'] },
          ],
        })
      )
    );
  });
  it('adds a sample the package files name as a member, related to a parent, as the PGT index', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { ...packageOut, addable_members: ['REL1'] },
    });
    vi.mocked(api.post).mockResolvedValue({
      data: { delivery, package: packageOut },
    });
    renderDialog();

    // A pipeline's member is not removed; the sample offered is added.
    expect(
      await screen.findByRole('combobox', { name: 'Sex of EMB1' })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Remove EMB1' })
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/The package's files name REL1, which no member is/)
    ).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Add a member' }), {
      target: { value: 'REL1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    fireEvent.change(screen.getByRole('combobox', { name: 'Sex of REL1' }), {
      target: { value: 'male' },
    });
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Clinical status of REL1' }),
      { target: { value: 'affected' } }
    );
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Member REL1 is related to' }),
      { target: { value: 'MOTHER' } }
    );
    fireEvent.click(screen.getAllByRole('checkbox', { name: 'Index' })[3]);
    expect(
      screen.getByRole('button', { name: 'Remove REL1' })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('combobox', { name: 'Add a member' })
    ).not.toBeInTheDocument();
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Reason for the correction' }),
      { target: { value: 'The affected uncle, read for segregation.' } }
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Save the correction' })
    );

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/family-imports/deliveries/id-FAM001/revisions',
        {
          revision: 1,
          reason: 'The affected uncle, read for segregation.',
          changes: [
            { path: ['family', 'pgt', 'indexes'], value: ['REL1'] },
            {
              path: ['members', 'REL1'],
              value: {
                sex: 'male',
                role: 'relative',
                clinical_status: 'affected',
                related_to: ['MOTHER'],
              },
            },
          ],
        }
      )
    );
  });

  it('removes a member of a package CoGA made, and edits it as JSON', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { ...packageOut, origin: 'manual' },
    });
    vi.mocked(api.post).mockResolvedValue({
      data: { delivery, package: packageOut },
    });
    renderDialog();

    fireEvent.click(await screen.findByRole('button', { name: 'Remove EMB1' }));
    expect(
      screen.queryByRole('combobox', { name: 'Sex of EMB1' })
    ).not.toBeInTheDocument();
    expect(
      screen.getByText('1 field(s) changed: members.EMB1.')
    ).toBeInTheDocument();
    // Any sample ID is added to a package CoGA made.
    expect(screen.getByRole('combobox', { name: 'Add a member' })).toHaveValue(
      ''
    );
    // The JSON is edited from the package as it is, not over changes in the form.
    expect(
      screen.queryByRole('button', { name: 'Edit as JSON' })
    ).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Add a member' }), {
      target: { value: ' EMB2 ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(screen.getByRole('combobox', { name: 'Sex of EMB2' })).toHaveValue(
      'unknown'
    );
    expect(
      screen.getByText('2 field(s) changed: members.EMB1, members.EMB2.')
    ).toBeInTheDocument();
  });

  it('edits the package CoGA made as JSON, and says when it is no JSON', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { ...packageOut, origin: 'manual' },
    });
    vi.mocked(api.post).mockResolvedValue({
      data: { delivery, package: packageOut },
    });
    renderDialog();

    fireEvent.click(
      await screen.findByRole('button', { name: 'Edit as JSON' })
    );
    const editor = screen.getByRole('textbox', { name: 'The package as JSON' });
    expect(editor).toHaveValue(packageJson(pkg));
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Reason for the correction' }),
      { target: { value: 'The uncle is in the phased VCF.' } }
    );
    const save = screen.getByRole('button', { name: 'Save the correction' });
    expect(save).toBeDisabled();

    fireEvent.change(editor, { target: { value: '{"members": [' } });
    expect(screen.getByText(/This is no JSON/)).toBeInTheDocument();
    expect(save).toBeDisabled();

    const edited = {
      ...pkg,
      members: [...(pkg.members ?? []), { sample_id: 'REL1', sex: 'male' }],
    };
    fireEvent.change(editor, { target: { value: JSON.stringify(edited) } });
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/family-imports/deliveries/id-FAM001/revisions',
        {
          revision: 1,
          reason: 'The uncle is in the phased VCF.',
          package: edited,
        }
      )
    );
  });
});

describe('packageJson', () => {
  it('writes the package in the format order, whatever order it was kept in', () => {
    const kept = {
      extra: { origin: 'manual' },
      members: [{ role: 'father', sex: 'male', sample_id: 'FATHER_1' }],
      family: { family_id: 'FAM001' },
      schema: 'coga-package/1',
    } as unknown as PackageDocument;

    expect(packageJson(kept)).toBe(
      JSON.stringify(
        {
          schema: 'coga-package/1',
          family: { family_id: 'FAM001' },
          members: [{ sample_id: 'FATHER_1', sex: 'male', role: 'father' }],
          extra: { origin: 'manual' },
        },
        null,
        2
      )
    );
  });
});

describe('member choices', () => {
  it('shows a sample ID as it is, underscores and all', async () => {
    const withUnderscores: PackageDocument = {
      ...pkg,
      members: [
        { sample_id: 'FATHER_1', sex: 'male', role: 'father' },
        { sample_id: 'MOTHER_1', sex: 'female', role: 'mother' },
      ],
    };
    vi.mocked(api.get).mockResolvedValue({
      data: { ...packageOut, package: withUnderscores },
    });
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <DeliveryCorrectionDialog
          delivery={delivery}
          onCancel={vi.fn()}
          onSaved={vi.fn()}
        />
      </QueryClientProvider>
    );

    const father = await screen.findByRole('combobox', {
      name: 'Father of MOTHER_1',
    });
    expect(
      [...(father as HTMLSelectElement).options].map((option) => option.text)
    ).toEqual(['—', 'FATHER_1']);
    // The format's own values still read with spaces.
    const carrier = screen.getByRole('combobox', {
      name: 'Carrier status of MOTHER_1',
    }) as HTMLSelectElement;
    expect([...carrier.options].map((option) => option.text)).toContain(
      'not carrier'
    );
  });
});

describe('PackageFileDialog', () => {
  it('shows the package as coga-package.json, with its corrections, and saves it', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: packageOut });
    const createObjectURL = vi.fn(() => 'blob:package');
    const revokeObjectURL = vi.fn();
    Object.assign(window.URL, { createObjectURL, revokeObjectURL });
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <PackageFileDialog
          delivery={{ ...delivery, state: 'imported' }}
          onClose={vi.fn()}
        />
      </QueryClientProvider>
    );

    expect(
      await screen.findByText(/"family_id": "FAM001"/)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/revision 1, corrected in CoGA/)
    ).toBeInTheDocument();
    expect(screen.getByText('Corrections made (1)')).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Download coga-package.json' })
    );
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:package');
  });
});
