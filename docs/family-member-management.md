# Family member management

What happens when an admin changes a family after it was created: its members, their
states, or the relationships between them. All the edits below are admin-only.

## Where a member's data lives

A family's graph is split across three Postgres tables: `family_members` (active
membership, role, clinical and carrier status), `samples` and `family_relationships`
(parent-child and couple edges; `sample_id_a` is the parent or first partner, `sample_id_b`
the child or second partner). The family's PED text is rebuilt into `families.pedigree`
after every change, and each change adds a row to `family_structure_versions`.

These also refer to a member:

- `individual_hpo` (the person's HPO terms) and `sample_projects` (project visibility);
- `sample_interval_track_sources`, `repeat_expansions` and `sample_paraphase_results`;
- the ClickHouse small-variant and structural-variant rows, which name the sample in
  `calls.sampleId`;
- family-level: `small_variant_reviews`, `structural_variant_reviews` and the derived
  browser tracks.

## The edit endpoints

| Endpoint | Does |
| --- | --- |
| `PUT /families/{family_id}/members/{sample_id}` | edits one member (states, sex, role, parents, rename) |
| `PUT /families/{family_id}/members/batch` | edits several members in one transaction |
| `DELETE /families/{family_id}/members/{sample_id}?confirm=true` | removes a member; without `confirm` it returns 409 with the impact |
| `PUT /families/{family_id}/structure` | adds, updates and removes members and replaces relationships in one request |

The member endpoints go through the structure update, so the rules below apply to all of
them. When ClickHouse cannot be reached, the impact says that the genotype linkage is
unknown.

## Rules checked on every change

- If the request carries `expected_structure_version` and the family changed since it was
  loaded, the change is refused (409), so two admins cannot overwrite each other.
- A family keeps at least one active member and at most one active proband.
- Relationships must name active members. A child has at most two parents, at most one
  father and one mother. Parent-child links cannot form a cycle, and no one is their own
  parent. Duplicate couples are refused.
- A father cannot be a member recorded as female, and a mother cannot be one recorded as
  male. A member of unknown sex may be either.
- Removing a member makes them inactive. The sample row is kept for auditability.
- Renaming a member is refused while imported genomic data still uses the old sample ID. It
  is also refused when ClickHouse cannot be reached, because CoGA cannot then confirm that
  nothing would be orphaned.
- A sample ID the request gives a member (its new ID, the father or mother a member edit
  names, the ID of a member the structure edit adds) is printable text without spaces, as on
  import ([data-import.md](data-import.md#validation)). The whitespace around it is stripped.
  One that holds a control character (a line break, a tab, an escape, a NUL) or whitespace is
  refused (400) before anything is read or written under it, so a batch holding one renames
  no member. The message writes the character as an escape (`\t`, `\x1b`).

## What an edit changes

Edits save even when the family already has imported data. The imported data is kept: small
and structural variants, interval tracks (coverage, segments, APCAD, haplotypes), repeat
expansions and Paraphase results. What depends on the edited facts is marked stale in
`families.metadata.derived_data_status.family_metadata`, with the reason, whether the
imported data was kept (`raw_datasets_preserved`) and the scopes that are affected:

- a new, reactivated or removed member: sample data;
- changed relationships: segregation, haplotypes and phasing;
- a changed sex or role, without relationship changes: segregation and haplotypes;
- a changed clinical or carrier status: the variant interpretation views.

The response lists these warnings and scopes. Stale views are not recomputed by the edit.
Two background jobs do follow every edit on this page (a structure save, and a member edit:
single, batch or removal) and a PED upload: the genome overview's haplotype lineage is
recomputed, and the prioritised variant ranking is warmed again. An HPO edit re-warms the
ranking only.

Changing an HPO term marks the phenotype-dependent views stale in
`derived_data_status.hpo_annotations`.

Saved variant reviews stay as they are. After a change to phenotypes, carriers or
relationships, re-check the saved interpretations before you rely on them.

### Clearing the data to reload it

`PUT /families/{family_id}/structure` with `clear_existing_genomic_data: true` deletes the
family's imported data, so it can be reloaded under the new structure: small and structural
variants, interval tracks, repeat expansions, Paraphase results, and the small- and
structural-variant reviews. It is the only edit that deletes data. The web interface never
sends it.

When it deleted data, the stale marker records `raw_datasets_preserved: false` and the
sample-data scope, and the response warns that the data was cleared, also when the request
changed nothing else. The new `family_structure_versions` row records how much of each kind
it deleted (`cleared_data_counts`). A family without imported data has nothing to delete, so
its marker keeps `raw_datasets_preserved: true`.

```yaml
expected_structure_version: 3
change_reason: family_detail_page
clear_existing_genomic_data: false
add_members:
  - {sample_id: FUTURE_EMBRYO_1, sex: und, role: embryo, clinical_status: unknown}
members:
  - {sample_id: FATHER, clinical_status: unaffected, carrier_status: carrier, carrier_type: proven}
remove_members: []
relationships:
  parent_child:
    - {parent: FATHER, child: PROBAND, parent_role: father}
    - {parent: MOTHER, child: PROBAND, parent_role: mother}
  couples:
    - {partners: [FATHER, MOTHER], context: reproductive}
```

## Limits of the pedigree model

- A classic PED row holds only a father and a mother, and the editor allows at most two
  parents per child.
- A `couple` relationship can record a partnership without children and consanguinity
  context.
- Divorce, adoption, twins, donor gametes, deceased symbols and proband arrows are not
  modelled.
- A couple that spans generations is drawn without forcing both partners onto one row.
