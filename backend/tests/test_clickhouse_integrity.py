from __future__ import annotations

import asyncio

import pytest

from backend.app.services import clickhouse_variant_storage as cvs

DATASET = "GRCh38"


def _as_client_returns(parts, *, single_value: bool):
    """A CHECK TABLE result the way the ClickHouse client hands it back.

    clickhouse-connect runs CHECK as a command and splits the tab-separated body on tabs
    only (``parse_command_body``): the part-by-part result is ONE flattened row, and a
    table without parts one empty value; the single value is one integer. These shapes
    were captured from ClickHouse 26.8 (the CI server) through the app's client.
    """
    if single_value:
        return [[int(all(passed for _path, passed, _message in parts))]]
    if not parts:
        return [[""]]
    body = "".join(f"{path}\t{int(passed)}\t{message}\n" for path, passed, message in parts)
    fields = body[:-1].split("\t")
    return [fields] if len(fields) > 1 else [[fields[0]]]
DATA_TABLES = [
    name for _vt, kind, name in cvs._expected_clickhouse_variant_tables(DATASET) if kind == "table"
]
GENE_INDEX = f"{DATASET}/SNV_INDEL/variants/gene_index"
ANNOTATION_INDEX = f"{DATASET}/SNV_INDEL/variants/annotation_index"
# Imported data leaves several active parts in a table.
_CLEAN = [("all_1_1_0", True, ""), ("all_2_2_0", True, "")]


def _integrity_execute(*, existing, check_results, detached, gene_keys, annotation_keys):
    """An async `_execute` stub that dispatches on the query text."""

    async def _execute(query, params=None, data=None):
        q = " ".join(query.split())
        if "FROM system.tables" in q:
            return [(name,) for name in existing]
        if q.startswith("CHECK TABLE"):
            parts = next(
                (check_results.get(name, _CLEAN) for name in existing if f"`{name}`" in query), _CLEAN
            )
            return _as_client_returns(
                parts, single_value="check_query_single_value_result = 1" in q
            )
        if "FROM system.detached_parts" in q:
            return detached
        if "uniqExact(key)" in q and "WHERE" in q:  # gene-bearing annotation keys
            return [(annotation_keys,)]
        if "uniqExact(key)" in q:  # gene_index keys
            return [(gene_keys,)]
        raise AssertionError(f"unexpected query: {q[:80]}")

    return _execute


def test_integrity_ok_when_clean(monkeypatch) -> None:
    monkeypatch.setattr(
        cvs, "_execute",
        _integrity_execute(existing=DATA_TABLES, check_results={}, detached=[],
                           gene_keys=100, annotation_keys=100),
    )
    report = asyncio.run(cvs.check_clickhouse_variant_integrity(DATASET))
    assert report["status"] == "ok"
    assert report["gene_index_consistency"]["consistent"] is True
    assert all(t["passed"] for t in report["table_checks"] if t["exists"])


def test_integrity_corrupt_on_failed_part(monkeypatch) -> None:
    monkeypatch.setattr(
        cvs, "_execute",
        _integrity_execute(
            existing=DATA_TABLES,
            check_results={GENE_INDEX: [("part_a", True, ""), ("part_b", False, "CHECKSUM_DOESNT_MATCH")]},
            detached=[], gene_keys=100, annotation_keys=100,
        ),
    )
    report = asyncio.run(cvs.check_clickhouse_variant_integrity(DATASET))
    assert report["status"] == "corrupt"
    gi = next(t for t in report["table_checks"] if t["name"] == GENE_INDEX)
    assert gi["passed"] is False and gi["failed_parts"] == 1
    assert "CHECKSUM_DOESNT_MATCH" in gi["messages"][0]


def test_integrity_degraded_on_detached_parts(monkeypatch) -> None:
    monkeypatch.setattr(
        cvs, "_execute",
        _integrity_execute(
            existing=DATA_TABLES, check_results={},
            detached=[(f"{DATASET}/SNV_INDEL/entries", "broken-on-start", 52)],
            gene_keys=100, annotation_keys=100,
        ),
    )
    report = asyncio.run(cvs.check_clickhouse_variant_integrity(DATASET))
    assert report["status"] == "degraded"
    assert report["detached_broken_parts"][0]["count"] == 52
    assert any("storage-volume" in note for note in report["notes"])


def test_integrity_degraded_on_gene_index_drift(monkeypatch) -> None:
    monkeypatch.setattr(
        cvs, "_execute",
        _integrity_execute(existing=DATA_TABLES, check_results={}, detached=[],
                           gene_keys=90, annotation_keys=100),
    )
    report = asyncio.run(cvs.check_clickhouse_variant_integrity(DATASET))
    assert report["status"] == "degraded"
    assert report["gene_index_consistency"]["consistent"] is False
    assert report["gene_index_consistency"]["drift"] == -10


def test_integrity_missing_when_no_tables(monkeypatch) -> None:
    monkeypatch.setattr(
        cvs, "_execute",
        _integrity_execute(existing=[], check_results={}, detached=[],
                           gene_keys=0, annotation_keys=0),
    )
    report = asyncio.run(cvs.check_clickhouse_variant_integrity(DATASET))
    assert report["status"] == "missing"
    assert report["gene_index_consistency"]["checked"] is False


# --- safe gene_index rebuild -------------------------------------------------

def _rebuild_execute(recorder, *, check_rows, rebuilt_keys, source_keys):
    async def _execute(query, params=None, data=None):
        q = " ".join(query.split())
        recorder.append(q)
        if q.startswith("CHECK TABLE"):
            return _as_client_returns(
                check_rows, single_value="check_query_single_value_result = 1" in q
            )
        if "uniqExact(key)" in q and "WHERE" in q:
            return [(source_keys,)]
        if "uniqExact(key)" in q:
            return [(rebuilt_keys,)]
        return None

    return _execute


def _patch_rebuild_env(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    async def _status(dataset):
        return {"assembly_name": dataset}

    monkeypatch.setattr(cvs, "ensure_clickhouse_variant_tables", _noop)
    monkeypatch.setattr(cvs, "get_clickhouse_variant_storage_status", _status)


def test_rebuild_swaps_atomically_when_valid(monkeypatch) -> None:
    recorder: list[str] = []
    _patch_rebuild_env(monkeypatch)
    monkeypatch.setattr(
        cvs, "_execute",
        _rebuild_execute(recorder, check_rows=[("p", True, "")], rebuilt_keys=100, source_keys=100),
    )
    asyncio.run(cvs.rebuild_small_variant_gene_index(DATASET))
    joined = " || ".join(recorder)
    # Atomic swap, not the old empty-window TRUNCATE of the live table.
    assert "EXCHANGE TABLES" in joined
    assert "TRUNCATE" not in joined
    assert any(q.startswith("CREATE TABLE") for q in recorder)
    assert any(q.startswith("DROP TABLE IF EXISTS") for q in recorder)


def test_rebuild_aborts_without_swap_on_key_mismatch(monkeypatch) -> None:
    recorder: list[str] = []
    _patch_rebuild_env(monkeypatch)
    monkeypatch.setattr(
        cvs, "_execute",
        _rebuild_execute(recorder, check_rows=[("p", True, "")], rebuilt_keys=90, source_keys=100),
    )
    with pytest.raises(RuntimeError, match="key mismatch"):
        asyncio.run(cvs.rebuild_small_variant_gene_index(DATASET))
    joined = " || ".join(recorder)
    assert "EXCHANGE TABLES" not in joined  # never swap a bad index over good data
    assert any(q.startswith("DROP TABLE IF EXISTS") for q in recorder)  # shadow still cleaned up


def test_rebuild_aborts_on_corrupt_shadow(monkeypatch) -> None:
    recorder: list[str] = []
    _patch_rebuild_env(monkeypatch)
    monkeypatch.setattr(
        cvs, "_execute",
        _rebuild_execute(recorder, check_rows=[("p", False, "UNKNOWN_CODEC")], rebuilt_keys=100, source_keys=100),
    )
    with pytest.raises(RuntimeError, match="corrupt"):
        asyncio.run(cvs.rebuild_small_variant_gene_index(DATASET))
    assert "EXCHANGE TABLES" not in " || ".join(recorder)


# --- CHECK TABLE as the client returns it --------------------------------------
# Captured from ClickHouse 26.8 through the app's client (clickhouse-connect): a table
# without active parts, with one, and with three.
_NO_PARTS = [[""]]
_ONE_PART = [["all_1_1_0", "1", ""]]
_THREE_PARTS = [["all_1_1_0", "1", "\nall_3_3_0", "1", "\nall_2_2_0", "1", ""]]


def test_the_part_list_is_read_whatever_shape_the_client_returns() -> None:
    assert cvs._read_check_parts(_NO_PARTS) == []
    assert cvs._read_check_parts(_ONE_PART) == [("all_1_1_0", True, "")]
    assert cvs._read_check_parts(_THREE_PARTS) == [
        ("all_1_1_0", True, ""),
        ("all_3_3_0", True, ""),
        ("all_2_2_0", True, ""),
    ]
    # Rows of three fields, should the client ever return them so, read the same.
    assert cvs._read_check_parts([("all_1_1_0", 1, ""), ("all_2_2_0", 0, "x")]) == [
        ("all_1_1_0", True, ""),
        ("all_2_2_0", False, "x"),
    ]
    assert cvs._read_check_parts([]) == []


def test_a_failed_part_after_the_first_is_read_with_its_message() -> None:
    # The server escapes the message as tab-separated text; it is read back unescaped.
    flattened = [["all_1_1_0", "1", "\nall_2_2_0", "0", "Checksum doesn\\'t match:\\tcorrupted data"]]
    assert cvs._read_check_parts(flattened) == [
        ("all_1_1_0", True, ""),
        ("all_2_2_0", False, "Checksum doesn't match:\tcorrupted data"),
    ]


def test_output_that_is_not_a_part_list_is_not_read_as_one() -> None:
    for rows in ([[1]], [["all_1_1_0", "yes", ""]], [["all_1_1_0", "1"]], [["", "1", ""]]):
        assert cvs._read_check_parts(rows) is None, rows


def test_the_verdict_is_the_single_value() -> None:
    assert cvs._read_check_verdict([[1]]) is True
    assert cvs._read_check_verdict([[0]]) is False
    assert cvs._read_check_verdict([(1,)]) is True
    for rows in ([], None, _NO_PARTS, _ONE_PART, [[1], [1]]):
        assert cvs._read_check_verdict(rows) is None, rows


def _count_checks(execute):
    calls: list[str] = []

    async def _counting(query, params=None, data=None):
        if " ".join(query.split()).startswith("CHECK TABLE"):
            calls.append(query)
        return await execute(query, params, data)

    return _counting, calls


def test_a_table_is_judged_on_every_part_not_its_first(monkeypatch) -> None:
    # Read row by row, this flattened result was judged on all_1_1_0 alone: it passed.
    monkeypatch.setattr(
        cvs,
        "_execute",
        _integrity_execute(
            existing=DATA_TABLES,
            check_results={GENE_INDEX: [("all_1_1_0", True, ""), ("all_2_2_0", False, "CHECKSUM_DOESNT_MATCH")]},
            detached=[],
            gene_keys=100,
            annotation_keys=100,
        ),
    )
    report = asyncio.run(cvs.check_clickhouse_variant_integrity(DATASET))
    assert report["status"] == "corrupt"
    gi = next(t for t in report["table_checks"] if t["name"] == GENE_INDEX)
    assert (gi["passed"], gi["failed_parts"], gi["messages"]) == (
        False,
        1,
        ["all_2_2_0: CHECKSUM_DOESNT_MATCH"],
    )


def test_tables_without_parts_pass_instead_of_crashing_the_check(monkeypatch) -> None:
    monkeypatch.setattr(
        cvs,
        "_execute",
        _integrity_execute(
            existing=DATA_TABLES,
            check_results={name: [] for name in DATA_TABLES},
            detached=[],
            gene_keys=0,
            annotation_keys=0,
        ),
    )
    report = asyncio.run(cvs.check_clickhouse_variant_integrity(DATASET))
    assert report["status"] == "ok"
    assert all(t["passed"] and t["failed_parts"] == 0 for t in report["table_checks"])


def test_a_passing_table_is_checked_once(monkeypatch) -> None:
    # CHECK TABLE reads every part; a table that passes is not read a second time.
    counting, calls = _count_checks(
        _integrity_execute(existing=DATA_TABLES, check_results={}, detached=[], gene_keys=1, annotation_keys=1)
    )
    monkeypatch.setattr(cvs, "_execute", counting)
    asyncio.run(cvs.check_clickhouse_variant_integrity(DATASET))
    assert len(calls) == len(DATA_TABLES)
    assert all("check_query_single_value_result = 1" in call for call in calls)


def _fixed_check_execute(*, verdict, parts):
    async def _execute(query, params=None, data=None):
        q = " ".join(query.split())
        if "FROM system.tables" in q:
            return [(name,) for name in DATA_TABLES]
        if q.startswith("CHECK TABLE"):
            return verdict if "check_query_single_value_result = 1" in q else parts
        if "FROM system.detached_parts" in q:
            return []
        if "uniqExact(key)" in q:
            return [(100,)]
        raise AssertionError(f"unexpected query: {q[:80]}")

    return _execute


def test_a_result_that_cannot_be_read_never_passes(monkeypatch) -> None:
    monkeypatch.setattr(cvs, "_execute", _fixed_check_execute(verdict=[["?"]], parts=[["unexpected"]]))
    report = asyncio.run(cvs.check_clickhouse_variant_integrity(DATASET))
    assert report["status"] == "corrupt"
    check = report["table_checks"][0]
    assert (check["passed"], check["failed_parts"]) == (False, 0)
    assert check["messages"] == ["CHECK TABLE returned a result that could not be read."]


def test_a_failing_verdict_whose_parts_cannot_be_listed_still_fails(monkeypatch) -> None:
    monkeypatch.setattr(cvs, "_execute", _fixed_check_execute(verdict=[[0]], parts=[["unexpected"]]))
    report = asyncio.run(cvs.check_clickhouse_variant_integrity(DATASET))
    check = report["table_checks"][0]
    assert (report["status"], check["passed"], check["failed_parts"]) == ("corrupt", False, 1)
    assert check["messages"] == ["CHECK TABLE failed the table, and its failed parts could not be listed."]


def test_an_unreadable_verdict_rests_on_a_fully_read_part_list(monkeypatch) -> None:
    monkeypatch.setattr(cvs, "_execute", _fixed_check_execute(verdict=[["?"]], parts=_THREE_PARTS))
    report = asyncio.run(cvs.check_clickhouse_variant_integrity(DATASET))
    assert report["status"] == "ok"
    # ...but not on an empty one: no part says the table passed.
    monkeypatch.setattr(cvs, "_execute", _fixed_check_execute(verdict=[["?"]], parts=_NO_PARTS))
    report = asyncio.run(cvs.check_clickhouse_variant_integrity(DATASET))
    assert report["status"] == "corrupt"


def test_the_rebuild_does_not_swap_in_an_index_with_a_corrupt_later_part(monkeypatch) -> None:
    recorder: list[str] = []
    _patch_rebuild_env(monkeypatch)
    monkeypatch.setattr(
        cvs,
        "_execute",
        _rebuild_execute(
            recorder,
            check_rows=[("all_1_1_0", True, ""), ("all_2_2_0", False, "UNKNOWN_CODEC")],
            rebuilt_keys=100,
            source_keys=100,
        ),
    )
    with pytest.raises(RuntimeError, match=r"1 corrupt part\(s\) \(all_2_2_0: UNKNOWN_CODEC\)"):
        asyncio.run(cvs.rebuild_small_variant_gene_index(DATASET))
    joined = " || ".join(recorder)
    assert "EXCHANGE TABLES" not in joined
    assert any(q.startswith("DROP TABLE IF EXISTS") for q in recorder)  # the shadow is cleaned up
