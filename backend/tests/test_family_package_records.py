"""A family's package records, one per analysis it was imported for (``family_package_records``),
and what an import of one analysis keeps of another's. A family holds the monogenic and the
conventional NIPT of one pregnancy, each from a package of its own (the owner's decision of
2026-11-19).

An import into a family that holds another analysis keeps every active member but those only its
own analysis's record lists (``other_analyses_of``): in the update check
(``family_member_differences``) and in an overwrite's pedigree (``take_package_pedigree``), the
members editor's too. A member the family holds keeps every value it knows, its sex, parents,
role, statuses and links: the package fills an unknown one, and one it states otherwise refuses the
import before anything is written (the owner's decision of 2026-11-20), in the check and again in
the registration (``_register_package_samples``). The capture panel a package names for a sample
is kept per analysis. A family that holds only the package's analysis takes the package as its
pedigree. All values are synthetic."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.services import family_package_registration as registration
from backend.app.services import family_structure_service as structure
from backend.app.services.family_package_records import (
    SOURCE_KINDS,
    PackageRecordShapeError,
    analysis_data_words,
    family_package_folders,
    family_package_records,
    package_folder_of,
    package_record_key,
    package_sources,
    source_collision_message,
    source_collisions,
)

_RECORDS = {
    "monogenic_nipt": {"folder_path": "/imports/MNIPT/F1", "members": ["CFDNA1", "FATHER1", "FETUS1"]},
    "conventional_nipt": {"folder_path": "/imports/CNIPT/F1", "members": ["CFDNA1"]},
    "unspecified": "not a record",
}


def test_a_record_is_keyed_by_its_packages_analysis() -> None:
    assert package_record_key("conventional_nipt") == "conventional_nipt"
    assert package_record_key(" monogenic_nipt ") == "monogenic_nipt"
    for none in (None, "", "  "):
        assert package_record_key(none) == "unspecified"


def test_a_record_lists_the_sources_its_import_writes_per_kind() -> None:
    # A conventional NIPT: ccnv's calls and tracks.
    assert package_sources(["ccnv"], None) == {
        "small_variants": [],
        "structural_variants": ["ccnv"],
        "tracks": ["ccnv"],
        "repeats": [],
        "paraphase": [],
    }
    # A monogenic NIPT: its per-sample calls and the per-target coverage.
    sources = package_sources(["snv", "coverage"], "nipt")
    assert (sources["small_variants"], sources["tracks"]) == (["nipt"], ["coverage_table", "web"])
    # A long-read family: each caller's own source, the repeats and Paraphase.
    sources = package_sources(["snv", "sv_needlr", "cnv", "mito", "repeats_trgt", "paraphase", "qc"], "clair3")
    assert sources == {
        "small_variants": ["clair3", "mito"],
        "structural_variants": ["hificnv", "mito_sv", "needlr"],
        "tracks": ["hificnv"],
        "repeats": ["trgt"],
        "paraphase": ["paraphase"],
    }
    # An SNV callset the import cannot tell writes nothing it can name; every kind is listed.
    assert package_sources(["snv"], None) == {kind: [] for kind in SOURCE_KINDS}


def test_a_family_reads_each_of_its_records_and_their_folders() -> None:
    metadata = {"package_imports": _RECORDS}
    assert sorted(family_package_records(metadata)) == ["conventional_nipt", "monogenic_nipt"]
    assert family_package_folders(metadata) == ["/imports/CNIPT/F1", "/imports/MNIPT/F1"]
    for none in (None, {}, {"package_imports": "x"}):
        assert family_package_records(none) == {} and family_package_folders(none) == []


def test_a_samples_file_is_read_in_the_folder_of_the_analysis_that_recorded_it() -> None:
    assert package_folder_of(_RECORDS, {"path": "a.cram", "package": "monogenic_nipt"}) == "/imports/MNIPT/F1"
    assert package_folder_of(_RECORDS, {"path": "a.cram", "package": "conventional_nipt"}) == "/imports/CNIPT/F1"
    # A file that names no analysis, or one the family does not hold, is read in no package folder.
    for record in ({"path": "a.cram"}, {"package": "pgt"}, {"package": "unspecified"}, None):
        assert package_folder_of(_RECORDS, record) is None
    assert package_folder_of(None, {"package": "monogenic_nipt"}) is None


def test_a_message_names_the_analysiss_data() -> None:
    assert analysis_data_words("conventional_nipt") == "this family's conventional NIPT data"
    assert analysis_data_words("unspecified") == "this family's data"
    assert analysis_data_words(None) == "this family's data"


def _sourced(datasets: list[str], snv_callset: str | None = None) -> dict[str, Any]:
    return {"datasets": {name: {} for name in datasets}, "sources": package_sources(datasets, snv_callset)}


def test_two_analyses_never_share_a_storage_source() -> None:
    # Short-read WGS with DeepVariant, then long-read WGS with DeepVariant and mtDNA calls.
    records = {"short_read_wgs": _sourced(["snv", "mito"], "deepvariant"), "monogenic_nipt": _sourced(["snv"], "nipt")}
    long_read = package_sources(["snv", "mito", "repeats_trgt"], "deepvariant")
    assert source_collisions(records, "long_read_wgs", long_read) == [
        ("small_variants", "deepvariant", "short_read_wgs"),
        ("small_variants", "mito", "short_read_wgs"),
        ("structural_variants", "mito_sv", "short_read_wgs"),
    ]
    # Another caller shares nothing, and an import of the same analysis replaces its own record.
    assert source_collisions(records, "long_read_wgs", package_sources(["snv"], "clair3")) == []
    assert source_collisions(records, "short_read_wgs", package_sources(["snv", "mito"], "deepvariant")) == []
    # A record not in the shape the import writes is never read as sharing nothing.
    with pytest.raises(PackageRecordShapeError):
        source_collisions({"short_read_wgs": {"datasets": {}}}, "long_read_wgs", long_read)


def test_a_shared_source_is_refused_with_what_to_change() -> None:
    message = source_collision_message("long_read_wgs", [("small_variants", "deepvariant", "short_read_wgs")])
    assert message.startswith(
        "The long-read WGS package would store its small variants as 'deepvariant', which this family's "
        "short-read WGS data already holds."
    )
    assert "name its caller differently (the snv dataset's source_format)" in message
    assert source_collision_message("unspecified", [("repeats", "trgt", "pgt")]).startswith(
        "This package would store its repeat calls as 'trgt', which this family's PGT data already holds."
    )


def test_an_import_into_a_family_is_refused_when_it_shares_a_source(monkeypatch: pytest.MonkeyPatch) -> None:
    def bundle(analysis: str, callset: str) -> Any:
        manifest = SimpleNamespace(
            analysis_key=analysis,
            pregnancy=None,
            datasets={"snv": SimpleNamespace(enabled=True), "mito": SimpleNamespace(enabled=True)},
        )
        return SimpleNamespace(manifest=manifest, callset=callset)

    monkeypatch.setattr(
        registration, "dataset_scopes", lambda bundle, types: {"snv": {"source": bundle.callset, "samples": None}}
    )
    family = {"package_imports": {"short_read_wgs": _sourced(["snv"], "haplotypecaller")}}
    assert registration._shared_source_message(family, bundle("long_read_wgs", "deepvariant")) is None
    assert registration._shared_source_message(family, bundle("short_read_wgs", "haplotypecaller")) is None
    refused = registration._shared_source_message(family, bundle("long_read_wgs", "haplotypecaller"))
    assert refused is not None and "as 'haplotypecaller'" in refused
    assert "shape the import writes" in str(
        registration._shared_source_message({"package_imports": {"pgt": {}}}, bundle("long_read_wgs", "x1"))
    )


class _Family:
    """A family as an import of one of its analyses reads it: its package records, its active
    members and their statuses (``other_analyses_of``, ``_held_member_values``)."""

    def __init__(self, records: Any, active: Any = (), statuses: Any = None) -> None:
        self.records = records
        self.active = list(active)
        self.statuses = statuses or {}

    async def execute(self, statement: Any, _params: Any = None) -> Any:
        sql = " ".join(str(statement).split())
        if "AS active_members" in sql:
            row = {"package_imports": self.records, "active_members": self.active}
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))
        rows = [
            {
                "sample_id": sample_id,
                "clinical_status": "unknown",
                "carrier_status": "unknown",
                "carrier_type": None,
                "carrier_evidence": {},
                **values,
            }
            for sample_id, values in self.statuses.items()
        ]
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))


def _others(family: _Family, analysis: str) -> registration.OtherAnalyses:
    return asyncio.run(registration.other_analyses_of(family, family_id="F1", analysis=analysis))  # type: ignore[arg-type]


def test_an_import_keeps_every_member_of_a_family_holding_another_analysis_but_its_own_analysiss() -> None:
    records = {
        "monogenic_nipt": {"members": ["CFDNA1", "FATHER1", "FETUS1"]},
        "conventional_nipt": {"members": ["CFDNA1", "CNIPT1"]},
    }
    # EDITED1 was added in the members editor: no record lists it.
    family = _Family(records, active=["CFDNA1", "CNIPT1", "EDITED1", "FATHER1", "FETUS1"])

    others = _others(family, "conventional_nipt")
    assert others and others.analyses == ("monogenic_nipt",)
    # Every active member but CNIPT1, whom only the conventional NIPT's record lists.
    assert others.kept == {"CFDNA1", "EDITED1", "FATHER1", "FETUS1"}
    others = _others(family, "monogenic_nipt")
    assert others.analyses == ("conventional_nipt",)
    assert others.kept == {"CFDNA1", "CNIPT1", "EDITED1"}
    # A family that holds this analysis alone, or none: the package is its pedigree's source.
    alone = _Family({"monogenic_nipt": records["monogenic_nipt"]}, active=family.active)
    assert not _others(alone, "monogenic_nipt") and _others(alone, "monogenic_nipt").kept == frozenset()
    assert not _others(_Family(None, active=family.active), "unspecified")


_STORED = "F1\tFATHER1\t0\t0\t1\t1\nF1\tCFDNA1\t0\t0\t2\t1\nF1\tFETUS1\tFATHER1\tCFDNA1\t0\t2\nF1\tEDITED1\t0\t0\t1\t0\n"
_RECORDS_HELD = {"monogenic_nipt": {"members": ["CFDNA1", "FATHER1", "FETUS1"]}}
_ACTIVE = ["CFDNA1", "EDITED1", "FATHER1", "FETUS1"]
_STATUSES = {
    "FATHER1": {"clinical_status": "unaffected", "carrier_status": "carrier", "carrier_type": "obligate"},
    "CFDNA1": {"clinical_status": "unaffected"},
    "FETUS1": {"clinical_status": "affected"},
}


def _differences(monkeypatch: pytest.MonkeyPatch, family: _Family, ped_text: str, **kwargs: Any) -> Any:
    async def existing(*_args: Any, **_kwargs: Any) -> Any:
        return {"family_uuid": "u1"}

    async def stored(*_args: Any, **_kwargs: Any) -> str:
        return _STORED

    monkeypatch.setattr(registration, "_fetch_existing_family", existing)
    monkeypatch.setattr(registration.ped_service, "build_pedigree_text", stored)
    ped = registration._parse_ped_text_strict(ped_text)[0]
    return asyncio.run(
        registration.family_member_differences(
            family, family_id="F1", ped=ped, analysis="conventional_nipt", **kwargs  # type: ignore[arg-type]
        )
    )


def test_a_package_of_another_analysis_may_leave_out_the_familys_other_members(monkeypatch: pytest.MonkeyPatch) -> None:
    family = _Family(_RECORDS_HELD, active=_ACTIVE, statuses=_STATUSES)
    # The conventional NIPT's package lists the plasma alone, its phenotype not given.
    differences = _differences(monkeypatch, family, "F1\tCFDNA1\t0\t0\t2\t-9\n")
    # The father and the fetus are the monogenic NIPT's, and EDITED1 the members editor's: not
    # missing, so an update keeps them; the plasma keeps the status the family knows.
    assert differences.missing == () and differences.kept_by_an_update
    assert (differences.conflicting, differences.filled) == ((), ())
    assert differences.others == ("monogenic_nipt",)

    # A family holding no other analysis: they are missing, as before.
    differences = _differences(monkeypatch, _Family(None, active=_ACTIVE), "F1\tCFDNA1\t0\t0\t2\t-9\n")
    assert differences.missing == ("EDITED1", "FATHER1", "FETUS1") and not differences.kept_by_an_update


def test_a_package_of_another_analysis_that_states_a_known_value_otherwise_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    family = _Family(_RECORDS_HELD, active=_ACTIVE, statuses=_STATUSES)
    # The plasma affected (unaffected in CoGA), the fetus's father another man (FATHER1 in CoGA),
    # and its sex given (unknown in CoGA).
    differences = _differences(
        monkeypatch,
        family,
        "F1\tFATHER2\t0\t0\t1\t1\nF1\tCFDNA1\t0\t0\t2\t2\nF1\tFETUS1\tFATHER2\tCFDNA1\t1\t2\n",
    )
    assert differences.conflicting == (
        ("CFDNA1", "CFDNA1's clinical status is affected in the package, unaffected in CoGA"),
        ("FETUS1", "FETUS1's father is FATHER2 in the package, FATHER1 in CoGA"),
    )
    assert differences.filled == (("FETUS1", "FETUS1's sex, unknown in CoGA, is male in the package"),)
    # With another analysis held a conflict is no change an overwrite makes: it refuses the import.
    assert differences.changed == () and differences.added == ("FATHER2",)
    message = registration.member_values_conflict_message("F1", differences.others, differences.conflicting)
    assert "CFDNA1's clinical status is affected in the package, unaffected in CoGA" in message
    assert "(monogenic_nipt)" in message and "members editor" in message

    # The manifest's carrier status of the father, otherwise than CoGA's: a conflict too.
    members = [
        {"sample_id": "FATHER1", "clinical_status": "unknown", "carrier_status": "not_carrier", "carrier_type": None},
    ]
    differences = _differences(monkeypatch, family, "F1\tFATHER1\t0\t0\t1\t0\n", members=members)
    assert differences.conflicting == (
        ("FATHER1", "FATHER1's carrier status is not_carrier in the package, carrier in CoGA"),
    )


def test_a_member_keeps_each_known_value_and_takes_an_unknown_one() -> None:
    held = {"clinical_status": "unaffected", "carrier_status": "unknown", "carrier_type": None, "carrier_evidence": {}}
    given = {"clinical_status": "unknown", "carrier_status": "carrier", "carrier_type": "proven", "carrier_evidence": {"by": "test"}}
    values = registration.merge_member_values("S1", given=given, held=held, fields=registration.MEMBER_STATUS_FIELDS)
    assert values.merged == {
        "clinical_status": "unaffected",
        "carrier_status": "carrier",
        "carrier_type": "proven",
        "carrier_evidence": {"by": "test"},
    }
    assert values.conflicts == () and len(values.filled) == 3
    # Two known values that differ: the family's is kept, and the conflict named.
    values = registration.merge_member_values(
        "S1", given={"carrier_type": "obligate"}, held={"carrier_type": "proven"}, fields=("carrier_type",)
    )
    assert values.merged == {"carrier_type": "proven"}
    assert values.conflicts == (("S1", "S1's carrier type is obligate in the package, proven in CoGA"),)


class _PedigreeFamily:
    """The structure ``take_package_pedigree`` reads of a family holding a monogenic NIPT: the
    father, the plasma, the fetus (sex unknown), a member added in the editor, and CNIPT1, whom
    only the conventional NIPT's record lists."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.held = {
            sample: {"sample_id": sample, "active": True, "sex": sex}
            for sample, sex in (("CFDNA1", "female"), ("CNIPT1", "female"), ("EDITED1", "male"), ("FATHER1", "male"), ("FETUS1", "und"))
        }
        self.links = [
            {"relationship_type": "parent_child", "sample_id_a": "FATHER1", "sample_id_b": "FETUS1", "role_a": "father"},
            {"relationship_type": "parent_child", "sample_id_a": "CFDNA1", "sample_id_b": "FETUS1", "role_a": "mother"},
            {"relationship_type": "relative", "sample_id_a": "EDITED1", "sample_id_b": "CFDNA1"},
            {"relationship_type": "same_individual", "sample_id_a": "CNIPT1", "sample_id_b": "CFDNA1"},
        ]
        self.updates: list[Any] = []

        async def family(*_args: Any, **_kwargs: Any) -> Any:
            return {"id": "u1"}

        async def version(*_args: Any, **_kwargs: Any) -> int:
            return 3

        async def members(*_args: Any, **_kwargs: Any) -> Any:
            return {structure._sample_key(sample): row for sample, row in self.held.items()}

        async def links(*_args: Any, **_kwargs: Any) -> Any:
            return self.links

        async def update(_session: Any, **kwargs: Any) -> Any:
            self.updates.append(kwargs["update"])
            return None

        monkeypatch.setattr(structure, "get_accessible_family_mapping", family)
        monkeypatch.setattr(structure, "_fetch_current_structure_version", version)
        monkeypatch.setattr(structure, "_fetch_family_member_rows", members)
        monkeypatch.setattr(structure, "_fetch_current_relationship_rows", links)
        monkeypatch.setattr(structure, "update_family_structure_for_admin", update)

    def take(self, *, replace: bool, members: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> Any:
        asyncio.run(
            structure.take_package_pedigree(
                None,  # type: ignore[arg-type]
                family_id="F1",
                members=members,
                relationships=relationships,
                user=SimpleNamespace(id="u", username="admin"),  # type: ignore[arg-type]
                replace=replace,
                kept={"CFDNA1", "EDITED1", "FATHER1", "FETUS1"},
                merge=True,
            )
        )
        return self.updates[-1] if self.updates else None


_PACKAGE_MEMBERS = [
    {"sample_id": "CFDNA1", "sex": "female", "role": "proband", "clinical_status": "affected"},
    {"sample_id": "FETUS1", "sex": "male", "role": "embryo"},
    {"sample_id": "FATHER2", "sex": "male", "role": "father"},
    {"sample_id": "SIB1", "sex": "female", "role": "sibling"},
]
_PACKAGE_LINKS = [
    # A second father of the fetus: the family's stays.
    {"relationship_type": "parent_child", "sample_id_a": "FATHER2", "sample_id_b": "FETUS1", "role_a": "father"},
    # The plasma as the fetus's mother, a link the family has: once.
    {"relationship_type": "parent_child", "sample_id_a": "CFDNA1", "sample_id_b": "FETUS1", "role_a": "mother", "source": "package"},
    # A new child of the plasma: added.
    {"relationship_type": "parent_child", "sample_id_a": "CFDNA1", "sample_id_b": "SIB1", "role_a": "mother"},
]


@pytest.mark.parametrize("replace", [False, True], ids=["update", "overwrite"])
def test_a_family_holding_another_analysis_keeps_its_members_values_and_links(
    monkeypatch: pytest.MonkeyPatch, replace: bool
) -> None:
    family = _PedigreeFamily(monkeypatch)

    taken = family.take(replace=replace, members=_PACKAGE_MEMBERS, relationships=_PACKAGE_LINKS)

    # The members the family lacks are added; a member it holds keeps its role and statuses, and
    # takes only the sex it did not know.
    assert sorted(member.sample_id for member in taken.add_members) == ["FATHER2", "SIB1"]
    assert [(member.sample_id, member.model_dump(exclude_unset=True)) for member in taken.members] == [
        ("FETUS1", {"sample_id": "FETUS1", "sex": "male"})
    ]
    # An overwrite makes inactive only CNIPT1, whom the package lacks and no other analysis keeps,
    # with its link; an update makes none inactive.
    assert taken.remove_members == (["CNIPT1"] if replace else [])
    assert sorted((link.parent, link.child, link.parent_role) for link in taken.relationships.parent_child) == [
        ("CFDNA1", "FETUS1", "mother"),
        ("CFDNA1", "SIB1", "mother"),
        ("FATHER1", "FETUS1", "father"),
    ]
    assert [(link.related_to, link.member) for link in taken.relationships.relatives] == [("EDITED1", "CFDNA1")]
    assert [sorted(link.samples) for link in taken.relationships.same_individual] == (
        [] if replace else [["CFDNA1", "CNIPT1"]]
    )


def test_a_package_that_adds_nothing_to_a_family_holding_another_analysis_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    family = _PedigreeFamily(monkeypatch)

    taken = family.take(
        replace=False,
        members=[{"sample_id": "CFDNA1", "sex": "female", "role": "mother"}],
        relationships=[],
    )

    assert taken is None and family.updates == []


def test_an_overwrite_of_a_family_holding_this_analysis_alone_makes_the_packages_members_its_pedigree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    family = _PedigreeFamily(monkeypatch)

    taken = asyncio.run(
        structure.take_package_pedigree(
            None,  # type: ignore[arg-type]
            family_id="F1",
            members=[{"sample_id": "CFDNA1", "sex": "female", "role": "mother", "clinical_status": "affected"}],
            relationships=[],
            user=SimpleNamespace(id="u", username="admin"),  # type: ignore[arg-type]
            replace=True,
        )
    )

    assert taken is None
    (update,) = family.updates
    assert sorted(update.remove_members) == ["CNIPT1", "EDITED1", "FATHER1", "FETUS1"]
    assert [(member.sample_id, member.clinical_status) for member in update.members] == [("CFDNA1", "affected")]
    assert update.relationships.parent_child == []


class _Samples:
    """The family's samples and member statuses as ``_register_package_samples`` reads them, under
    their rows' lock, and what it writes."""

    def __init__(self) -> None:
        self.rows = [
            {
                "sample_uuid": "uuid-cfdna1",
                "sample_id": "CFDNA1",
                "metadata": {"assays": ["nipt_cfdna"], "assay_panels": {"monogenic_nipt": "PANEL1"}},
                "is_member": True,
                "clinical_status": "unaffected",
                "carrier_status": "unknown",
                "carrier_type": None,
                "carrier_evidence": {},
            },
            {
                "sample_uuid": "uuid-fetus1",
                "sample_id": "FETUS1",
                "metadata": {},
                "is_member": True,
                "clinical_status": "unknown",
                "carrier_status": "unknown",
                "carrier_type": None,
                "carrier_evidence": {},
            },
        ]
        self.statuses: dict[str, dict[str, Any]] = {}
        self.metadata: dict[str, dict[str, Any]] = {}
        self.locked = False

    async def execute(self, statement: Any, params: Any = None) -> Any:
        import json

        sql = " ".join(str(statement).split())
        if sql.startswith("SELECT"):
            self.locked = "FOR UPDATE OF s" in sql
            rows = self.rows
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))
        if sql.startswith("UPDATE family_members"):
            self.statuses[params["sample_uuid"]] = {
                key: params[key] for key in ("clinical_status", "carrier_status", "carrier_type", "affected")
            }
        elif sql.startswith("UPDATE samples"):
            self.metadata[params["sample_uuid"]] = json.loads(params["metadata"])
        return SimpleNamespace()


def _cnipt_bundle(ped_text: str) -> Any:
    from backend.app.services.family_package_common import PackageManifest

    manifest = PackageManifest(
        family_id="F1",
        analysis_type="conventional_nipt",
        samples={"CFDNA1": {"assay": "cnipt_cfdna", "assay_panel": "LOWPASS"}},
    )
    return SimpleNamespace(manifest=manifest, ped=registration._parse_ped_text_strict(ped_text)[0])


def _register_samples(session: _Samples, bundle: Any, others: Any) -> Any:
    return asyncio.run(
        registration._register_package_samples(
            session,  # type: ignore[arg-type]
            bundle=bundle,
            family_uuid="u1",
            family_identifier="F1",
            record_key="conventional_nipt",
            registered_at="2026-11-20T00:00:00+00:00",
            others=others,
        )
    )


_HOLDS_A_MONOGENIC_NIPT = registration.OtherAnalyses(analyses=("monogenic_nipt",), kept=frozenset({"FETUS1"}))


def test_a_conventional_nipt_keeps_the_plasmas_status_and_its_monogenic_nipts_panel() -> None:
    session = _Samples()

    # The plasma's phenotype not given (-9); the fetus affected, unknown in CoGA.
    previous = _register_samples(
        session, _cnipt_bundle("F1\tCFDNA1\t0\t0\t2\t-9\nF1\tFETUS1\t0\t0\t0\t2\n"), _HOLDS_A_MONOGENIC_NIPT
    )

    assert session.locked
    # The plasma keeps "unaffected"; the fetus's unknown status is filled.
    assert session.statuses == {
        "uuid-fetus1": {"clinical_status": "affected", "carrier_status": "unknown", "carrier_type": None, "affected": True}
    }
    # Its assay is added, and its panel kept as the conventional NIPT's, beside the monogenic NIPT's.
    assert session.metadata == {
        "uuid-cfdna1": {
            "assays": ["cnipt_cfdna", "nipt_cfdna"],
            "assay_panels": {"conventional_nipt": "LOWPASS", "monogenic_nipt": "PANEL1"},
            "package_sample_metadata": {"assay": "cnipt_cfdna", "assay_panel": "LOWPASS"},
        }
    }
    # What an undone import puts back.
    assert previous == {
        "CFDNA1": {
            "package_sample_metadata": None,
            "assays": ["nipt_cfdna"],
            "assay_panels": {"monogenic_nipt": "PANEL1"},
            "package_imports": None,
        }
    }


def test_a_conventional_nipt_that_gives_the_plasma_another_status_is_refused_before_it_writes() -> None:
    session = _Samples()

    with pytest.raises(registration.ImportRefused) as refused:
        _register_samples(session, _cnipt_bundle("F1\tCFDNA1\t0\t0\t2\t2\n"), _HOLDS_A_MONOGENIC_NIPT)

    assert "CFDNA1's clinical status is affected in the package, unaffected in CoGA" in str(refused.value)
    assert (session.statuses, session.metadata) == ({}, {})


def test_a_package_of_the_familys_only_analysis_gives_its_members_their_statuses() -> None:
    session = _Samples()

    _register_samples(session, _cnipt_bundle("F1\tCFDNA1\t0\t0\t2\t-9\n"), registration.OtherAnalyses())

    # The package is the pedigree's source: the plasma's status is the package's.
    assert session.statuses == {
        "uuid-cfdna1": {"clinical_status": "unknown", "carrier_status": "unknown", "carrier_type": None, "affected": False}
    }


# --- A sample's records of each analysis: its alignment and its sequencing QC ----------------


class _SampleMetadataSession:
    """One sample's ``samples.metadata``, read and written as the QC writers read and write it."""

    def __init__(self, metadata: dict[str, Any]) -> None:
        self.metadata = metadata

    async def execute(self, statement: Any, params: Any = None) -> Any:
        import json

        sql = str(statement)
        if sql.lstrip().startswith("UPDATE"):
            self.metadata = json.loads(params["metadata_json"])
            return SimpleNamespace()
        for key in ("alignments", "sequencing_qc"):
            if f"metadata -> '{key}'" in sql:
                return SimpleNamespace(scalar_one_or_none=lambda key=key: self.metadata.get(key))
        return SimpleNamespace(scalar_one_or_none=lambda: self.metadata)

    async def commit(self) -> None:
        return None


def test_a_samples_alignment_and_qc_of_each_analysis_are_kept_beside_each_other() -> None:
    from backend.app.services import family_package_qc

    sample = SimpleNamespace(sample_uuid="00000000-0000-4000-8000-000000000001", sample_id="S1")
    session = _SampleMetadataSession(
        {
            "alignments": {"short_read_wgs": {"format": "cram", "path": "S1/S1.cram"}},
            "sequencing_qc": {"short_read_wgs": {"depth": {"mean_depth": 35.0}}},
        }
    )
    asyncio.run(
        family_package_qc.record_sample_alignment_metadata(
            session, sample_context=sample, analysis="long_read_wgs", alignment={"format": "bam", "path": "S1.bam"}  # type: ignore[arg-type]
        )
    )
    asyncio.run(
        family_package_qc.record_sample_qc_metadata(
            session, sample_context=sample, analysis="long_read_wgs", metrics={"depth": {"mean_depth": 18.0}}  # type: ignore[arg-type]
        )
    )
    assert session.metadata["alignments"] == {
        "short_read_wgs": {"format": "cram", "path": "S1/S1.cram"},
        "long_read_wgs": {"format": "bam", "path": "S1.bam"},
    }
    assert session.metadata["sequencing_qc"] == {
        "short_read_wgs": {"depth": {"mean_depth": 35.0}},
        "long_read_wgs": {"depth": {"mean_depth": 18.0}},
    }


def test_an_import_clears_only_its_own_analysis_entries_with_the_analysis_bound() -> None:
    from backend.app.services import family_package_qc

    class _Recording:
        def __init__(self) -> None:
            self.statements: list[tuple[str, dict[str, Any]]] = []
            self.committed = False

        async def execute(self, statement: Any, params: Any = None) -> Any:
            self.statements.append((" ".join(str(statement).split()), dict(params or {})))
            return SimpleNamespace()

        async def commit(self) -> None:
            self.committed = True

    session = _Recording()
    analysis = "long_read_wgs'; DROP TABLE samples; --"
    asyncio.run(
        family_package_qc.clear_analysis_import_records(session, family_uuid="family-uuid", analysis=analysis)  # type: ignore[arg-type]
    )

    (family_sql, family_params), (samples_sql, samples_params) = session.statements
    # The run parameters of the analysis, on its family; each of its samples' QC and alignment.
    assert family_sql.startswith("UPDATE families SET metadata = metadata #- ARRAY['pipeline', CAST(:analysis AS text)]")
    assert "#- ARRAY['sequencing_qc', CAST(:analysis AS text)] #- ARRAY['alignments', CAST(:analysis AS text)]" in samples_sql
    assert "WHERE family_id = CAST(:family_uuid AS uuid)" in samples_sql
    # The analysis is a bound parameter, never part of the statement.
    assert family_params == samples_params == {"family_uuid": "family-uuid", "analysis": analysis}
    assert "DROP TABLE" not in family_sql + samples_sql
    assert session.committed


def test_each_analysiss_qc_of_a_member_is_judged_by_its_own_cut_offs(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone

    from backend.app.schemas import FamilyMemberOut, FamilyOut
    from backend.app.services import metadata_service, qc_threshold_service

    async def resolve(_session: Any, *, family_uuid: str, analysis: str) -> dict[str, Any]:
        warn = {"short_read_wgs": 30.0, "long_read_wgs": 15.0}[analysis]
        return {
            "profiles": [{"key": analysis, "label": analysis}],
            "thresholds": {
                "depth.mean_depth": {"warn_value": warn, "error_value": None, "warn_profile_key": analysis, "error_profile_key": None}
            },
        }

    monkeypatch.setattr(qc_threshold_service, "resolve_analysis_qc_thresholds", resolve)
    member = FamilyMemberOut(
        sample_id="S1",
        role="proband",
        affected=True,
        sample_metadata={
            "sequencing_qc": {
                "short_read_wgs": {"depth": {"mean_depth": 25.0}},
                "long_read_wgs": {"depth": {"mean_depth": 25.0}},
            }
        },
    )
    family = FamilyOut(id="u1", family_id="F1", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), members=[member])

    asyncio.run(metadata_service._attach_sequencing_qc_verdicts(object(), family_uuid="u1", family=family))  # type: ignore[arg-type]

    # The same depth warns against the short-read cut-off and passes the long-read one.
    verdicts = {analysis: evaluation.verdict for analysis, evaluation in family.members[0].sequencing_qc.items()}
    assert verdicts == {"long_read_wgs": "pass", "short_read_wgs": "warn"}


def test_a_package_of_another_analysis_keeps_the_family_s_region_of_interest(monkeypatch: pytest.MonkeyPatch) -> None:
    # A monogenic NIPT gave the family its region (a gene locus); a conventional NIPT package of
    # the same pregnancy that names another is refused, one naming the same or none is not, and a
    # family that holds no other analysis takes the package's (as a member's known values,
    # the owner's decision of 2026-11-20).
    held_roi = {"roi_query": "GENEA", "roi_label": "GENEA", "roi_chr": "chr7", "roi_start": 100, "roi_end": 200}
    state: dict[str, Any] = {"others": registration.OtherAnalyses(analyses=("monogenic_nipt",)), "roi": held_roi}

    async def others(_session: Any, *, family_id: str, analysis: str) -> Any:
        return state["others"]

    async def family_roi(_session: Any, _family_id: str) -> Any:
        return state["roi"]

    async def assembly(_session: Any, *, project_id: Any, family_id: Any) -> str:
        return "assembly-uuid"

    async def resolve(_session: Any, *, assembly_id: Any, query: str) -> Any:
        loci = {"GENEA": ("7", 100, 200), "GENEB": ("chr9", 500, 900)}
        if query not in loci:
            return None
        chrom, start, end = loci[query]
        return {"query": query, "label": query, "chr": chrom, "start": start, "end": end}

    monkeypatch.setattr(registration, "other_analyses_of", others)
    monkeypatch.setattr(registration, "_family_roi_row", family_roi)
    monkeypatch.setattr(registration, "_package_roi_assembly", assembly)
    monkeypatch.setattr(registration, "resolve_roi", resolve)

    def package(roi: str | None) -> Any:
        manifest = SimpleNamespace(
            analysis_type="conventional_nipt", analysis_key="conventional_nipt:ZW1", family={"roi": roi} if roi else {}, roi=roi
        )
        return SimpleNamespace(manifest=manifest)

    monkeypatch.setattr(registration, "_manifest_roi_value", lambda manifest: manifest.roi)

    def conflict(roi: str | None) -> str | None:
        return asyncio.run(
            registration.roi_conflict_message(None, family_id="F1", bundle=package(roi), project_id="p1")
        )

    refused = conflict("GENEB")
    assert refused is not None
    assert "GENEB (chr9:500-900)" in refused and "GENEA, chr7:100-200" in refused
    # The same locus (named otherwise: 7 or chr7), none, or one that does not resolve: no conflict.
    assert conflict("GENEA") is None
    assert conflict(None) is None
    assert conflict("NOT_A_GENE") is None
    # A family without a region, or one holding no other analysis, takes the package's.
    state["roi"] = None
    assert conflict("GENEB") is None
    state["roi"], state["others"] = held_roi, registration.OtherAnalyses()
    assert conflict("GENEB") is None
