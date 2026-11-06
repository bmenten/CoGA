"""The segregation flags of a small-variant record a unit test builds by hand.

The family search reads the single-variant inheritance modes a variant fits from the SQL its
inheritance filter applies (``_small_segregation_flag_columns``, built from
``_small_inheritance_mode_clauses``): each record it fetches for the ranking or a paired
search carries them (``SmallVariantRecord.segregation``), and a record without them is
refused where they are read. A record a unit test builds, or a fake fetch returns, has none;
these helpers give it the flags the SQL computes for it, by the same rules, which the
real-ClickHouse filter spec holds the SQL to
(``integration/test_small_variant_filter_parity_clickhouse.py``):

* de novo / dominant: one copy in every affected member (a heterozygous call or, for a male
  where he is hemizygous, an ALT-only call), no ALT call in any unaffected member;
* homozygous recessive: off chromosome X, homozygous ALT in every affected member and in no
  unaffected one;
* X-linked: on chromosome X, an ALT call in every affected member, none in an unaffected
  male, and no unaffected female homozygous.

Without an affected member a variant fits no mode.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from backend.app.services.clickhouse_variant_records import SmallSegregationFlags, SmallVariantRecord
from backend.app.services.data_scope import normalize_chromosome
from backend.app.services.genotypes import ALT_CLASSES, HET, HOM_ALT, classify_genotype
from backend.app.services.sex_chromosomes import hemizygous_chromosome

_MALE = frozenset({"m", "male", "1"})


def segregation_flags(
    record: SmallVariantRecord,
    *,
    affected: Sequence[str],
    unaffected: Sequence[str],
    sexes: Mapping[str, str] | None = None,
    assembly_name: str | None = "GRCh38",
) -> SmallSegregationFlags:
    """The modes ``record`` fits, its family's members being ``affected`` and ``unaffected``."""
    if not affected:
        return SmallSegregationFlags(de_novo_dominant=False, recessive_homozygous=False, x_linked=False)
    classes = {call.sample: classify_genotype(call.gt) for call in record.calls}
    sex = {sample: str(value).strip().lower() for sample, value in (sexes or {}).items()}
    on_x = normalize_chromosome(record.chr).upper().removeprefix("CHR") in {"X", "23"}
    hemizygous = hemizygous_chromosome(assembly_name, record.chr, record.start) is not None

    def male(sample: str) -> bool:
        return sex.get(sample, "") in _MALE

    def has_alt(sample: str) -> bool:
        return classes.get(sample) in ALT_CLASSES

    def homozygous(sample: str) -> bool:
        return classes.get(sample) == HOM_ALT

    def one_copy(sample: str) -> bool:
        return classes.get(sample) == HET or (hemizygous and male(sample) and homozygous(sample))

    return SmallSegregationFlags(
        de_novo_dominant=all(one_copy(sample) for sample in affected)
        and not any(has_alt(sample) for sample in unaffected),
        recessive_homozygous=not on_x
        and all(homozygous(sample) for sample in affected)
        and not any(homozygous(sample) for sample in unaffected),
        x_linked=on_x
        and all(has_alt(sample) for sample in affected)
        and not any(has_alt(sample) if male(sample) else homozygous(sample) for sample in unaffected),
    )


def flag(records: Iterable[SmallVariantRecord], **members: Any) -> list[SmallVariantRecord]:
    """Copies of ``records``, each with its flags (``segregation_flags``'s keywords), as a fetch
    with ``segregation_flags`` returns them."""
    return [replace(record, segregation=segregation_flags(record, **members)) for record in records]


def members_of(context: Any) -> dict[str, Any]:
    """``segregation_flags``'s keywords for a family's context, as the SQL reads it: its
    affected and unaffected members, their sexes and its assembly."""
    from backend.app.services.clickhouse_variant_queries import (
        _family_affected_unaffected_sample_names,
        _sample_sex_map,
    )

    affected, unaffected = _family_affected_unaffected_sample_names(context)
    return {
        "affected": affected,
        "unaffected": unaffected,
        "sexes": _sample_sex_map(context.sample_rows),
        "assembly_name": context.assembly_name,
    }
