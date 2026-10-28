"""The NIPT paternity check's two totals and the Sample QC summary say what they count (ENG-4).

The check reads the father's alleles in the plasma, at the autosomal sites where the mother
does not carry them and the depth leaves a transmitted allele all but impossible to miss: his
homozygous alleles must all be seen, about half of his heterozygous ones. Its totals were
served as ``cat7_transmitted`` and ``cat8_absent``, and the NIPT summary said paternity was
confirmed from "paternal-transmitted sites (categories 7/8)": the logic before #749, which
counted the classification's categories 7 and 8. They are now:

- ``transmitted_sites``: the sites where his allele is seen, homozygous and heterozygous;
- ``hom_alt_absent_sites``: his homozygous sites where it is not (``hom_alt_not_transmitted``).

A record signed from now on stores the new summary. No signed record holds either total: the
snapshot keeps the check's four counts, which both are worked out from. All data here are
synthetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.main import app
from backend.app.routers import families_reports
from backend.app.services import report_signout_service
from backend.app.services.access_control import CurrentUser
from backend.app.services.sample_integrity_qc import (
    PedigreeSpec,
    SampleIntegrityReport,
    evaluate_paternity,
    evaluate_sample_integrity,
    profile_for,
)

FAMILY, FATHER = "FAM_NIPT_ENG4", "FATHER_ENG4"
OLD_NAMES = {"cat7_transmitted", "cat8_absent"}


def _nipt_report() -> SampleIntegrityReport:
    """A NIPT family's Sample QC as the service builds it: the true father, his homozygous
    alleles all seen but 15 coverage dropouts, half his heterozygous ones."""
    paternity = evaluate_paternity(
        FATHER,
        hom_alt_transmitted=485,
        hom_alt_not_transmitted=15,
        het_transmitted=1020,
        het_not_transmitted=980,
    )
    spec = PedigreeSpec(recorded_sex={}, parents_of={})
    return evaluate_sample_integrity({}, {}, spec, profile=profile_for("nipt"), paternity_check=paternity)


def _admin() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-0000-0000-000000000001",
        username="admin@example.com",
        email="admin@example.com",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_the_paternity_check_serves_its_totals_under_what_they_count(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sample_qc(*_args: Any, **_kwargs: Any) -> SampleIntegrityReport:
        return _nipt_report()

    monkeypatch.setattr(families_reports, "get_family_sample_integrity_qc", sample_qc)

    served = await families_reports.get_family_sample_integrity_qc_endpoint(
        FAMILY, None, session=None, user=_admin()  # type: ignore[arg-type]
    )

    assert served.paternity_check is not None
    check = served.paternity_check.model_dump()
    assert not OLD_NAMES & set(check)
    assert check["transmitted_sites"] == 485 + 1020
    assert check["hom_alt_absent_sites"] == check["hom_alt_not_transmitted"] == 15
    assert check["informative_sites"] == 485 + 15 + 1020 + 980
    assert check["status"] == "pass"

    schema = app.openapi()["components"]["schemas"]["SampleIntegrityPaternityCheckOut"]
    assert not OLD_NAMES & set(schema["properties"])
    assert {"transmitted_sites", "hom_alt_absent_sites"} <= set(schema["required"])


def test_the_nipt_summary_says_what_the_paternity_check_counts() -> None:
    summary = profile_for("nipt").summary

    assert "categories 7/8" not in summary
    assert "paternal-transmitted" not in summary
    assert "the father's alleles in the plasma" in summary
    assert "homozygous" in summary and "heterozygous" in summary
    # The checks the classification does feed are still named.
    assert "the category distribution is checked" in summary


def test_a_record_signed_now_stores_the_new_summary_and_neither_total() -> None:
    stored = report_signout_service._canonical_sample_qc(_nipt_report())

    assert stored["application"] == "nipt"
    assert stored["application_summary"] == profile_for("nipt").summary
    assert "categories 7/8" not in stored["application_summary"]
    # The four counts both totals are worked out from, and neither total, under any name.
    assert set(stored["paternity_check"]) == {
        "father",
        "hom_alt_transmitted",
        "hom_alt_not_transmitted",
        "het_transmitted",
        "het_not_transmitted",
        "status",
        "message",
    }
