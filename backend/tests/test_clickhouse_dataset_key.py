"""The ClickHouse dataset key of an assembly (``core.clickhouse.clickhouse_dataset_key``): the
prefix of every variant and interval table. Existing tables are named by it, so it is the
identity on a name that is already a valid identifier, or current data becomes unaddressable;
any other character becomes ``_``, so no path separator, backtick or semicolon reaches a table
path; and ingestion and every read path derive it from the same function."""

from __future__ import annotations

import pytest

from backend.app.core.clickhouse import clickhouse_dataset_key
from backend.app.services import clickhouse_family_variants as family
from backend.app.services import clickhouse_interval_tracks as interval
from backend.app.services import clickhouse_small_variants as small
from backend.app.services import clickhouse_variant_storage as storage
from backend.app.services import variant_explorer_service as explorer


@pytest.mark.parametrize(
    ("assembly_name", "key"),
    [
        # Existing tables are named by these: the key MUST be the identity.
        ("GRCh38", "GRCh38"),
        ("GRCm39", "GRCm39"),
        ("T2T_CHM13v2.0", "T2T_CHM13v2.0"),
        ("T2T CHM13v2.0", "T2T_CHM13v2.0"),
        ("  spaced  name  ", "spaced__name"),
        # Path separators, backticks and semicolons cannot survive: injection-safe.
        ("a/b`c;d", "a_b_c_d"),
    ],
    ids=["GRCh38", "GRCm39", "T2T", "inner-space", "trimmed-and-inner-spaces", "separator-backtick-semicolon"],
)
def test_dataset_key_keeps_identifiers_and_replaces_disallowed_characters(assembly_name: str, key: str) -> None:
    assert clickhouse_dataset_key(assembly_name) == key


@pytest.mark.parametrize("assembly_name", ["   ", "", None], ids=["blank", "empty", "none"])
def test_dataset_key_rejects_empty(assembly_name: str | None) -> None:
    with pytest.raises(ValueError, match="Assembly name is empty"):
        clickhouse_dataset_key(assembly_name)  # type: ignore[arg-type]


def test_all_modules_agree_on_the_table_prefix() -> None:
    # Ingestion (storage) and every read path derive the dataset key from the
    # SAME function, so a messy assembly name maps to one prefix everywhere —
    # otherwise variants written for T2T would be invisible to queries.
    name = "T2T CHM13v2.0"
    key = "T2T_CHM13v2.0"

    snv_names = {
        storage._small_table_name(name, "entries"),
        explorer._small_table_name(name, "entries"),
        small._table_name(name, "entries"),
        family._small_table_name(name, "entries"),
    }
    assert snv_names == {f"coga.`{key}/SNV_INDEL/entries`"}
    assert interval._interval_table_name(name) == f"coga.`{key}/INTERVAL/entries`"
    assert storage._structural_table_name(name, "entries") == f"coga.`{key}/SV/entries`"
