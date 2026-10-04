
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_user
from ..schemas import (
    SampleIntegrityCategoryQcOut,
    SampleIntegrityFetalSexCheckOut,
    SampleIntegrityMendelianCheckOut,
    SampleIntegrityPaternityCheckOut,
    AnnotationManifestOut,
    AnnotationManifestUpdate,
    ClassificationDriftOut,
    ClinicalAuditOut,
    ReportSignoutCheckOut,
    ReportSignoutDetail,
    ReportSignoutListOut,
    ReportSignoutRequest,
    SampleIntegrityQcOut,
    SampleIntegrityRelatednessCheckOut,
    SampleIntegritySexCheckOut,
)
from ..services.access_control import CurrentUser
from ..services.sample_integrity_service import get_family_sample_integrity_qc
from ..services.annotation_manifest_service import (
    get_family_annotation_manifest,
    set_family_annotation_manifest,
)
from ..services.classification_drift_service import evaluate_classification_drift
from ..services.clinical_audit_service import list_clinical_audit
from ..services.report_signout_service import (
    compare_report_with_latest_signout,
    get_report_signout,
    list_report_signouts,
    sign_out_report,
)


router = APIRouter()


@router.get("/{family_id}/annotation-manifest", response_model=AnnotationManifestOut)
async def get_family_annotation_manifest_endpoint(
    family_id: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> AnnotationManifestOut:
    return AnnotationManifestOut.model_validate(
        await get_family_annotation_manifest(
            session, family_id=family_id, user=user, project_id=project_id
        )
    )


@router.put("/{family_id}/annotation-manifest", response_model=AnnotationManifestOut)
async def set_family_annotation_manifest_endpoint(
    family_id: str,
    payload: AnnotationManifestUpdate,
    session: AsyncSession = Depends(get_postgres_session),
    # Admin only: every later sign-out freezes this manifest into the signed report.
    # The replacement is recorded on the family's clinical audit chain.
    user: CurrentUser = Depends(get_current_admin_user),
) -> AnnotationManifestOut:
    # payload.source is ignored: a replacement through the API is recorded as 'manual'.
    return AnnotationManifestOut.model_validate(
        await set_family_annotation_manifest(
            session,
            family_id=family_id,
            user=user,
            modules=payload.modules,
        )
    )


@router.get("/{family_id}/classification-drift", response_model=ClassificationDriftOut)
async def get_family_classification_drift_endpoint(
    family_id: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> ClassificationDriftOut:
    return ClassificationDriftOut.model_validate(
        await evaluate_classification_drift(
            session, family_id=family_id, user=user, project_id=project_id
        )
    )


@router.get("/{family_id}/clinical-audit", response_model=ClinicalAuditOut)
async def get_family_clinical_audit_endpoint(
    family_id: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> ClinicalAuditOut:
    return ClinicalAuditOut.model_validate(
        await list_clinical_audit(
            session, family_id=family_id, user=user, project_id=project_id
        )
    )


@router.post("/{family_id}/report/sign-out", response_model=ReportSignoutDetail)
async def sign_out_family_report_endpoint(
    family_id: str,
    payload: ReportSignoutRequest | None = None,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> ReportSignoutDetail:
    return ReportSignoutDetail.model_validate(
        await sign_out_report(
            session,
            family_id=family_id,
            user=user,
            acknowledge_drift=bool(payload and payload.acknowledge_drift),
            drift_acknowledgement_reason=(
                payload.drift_acknowledgement_reason if payload else None
            ),
            acknowledge_qc=bool(payload and payload.acknowledge_qc),
            qc_acknowledgement_reason=(
                payload.qc_acknowledgement_reason if payload else None
            ),
            acknowledge_import_incomplete=bool(
                payload and payload.acknowledge_import_incomplete
            ),
            import_incomplete_acknowledgement_reason=(
                payload.import_incomplete_acknowledgement_reason if payload else None
            ),
            project_id=project_id,
        )
    )


@router.get("/{family_id}/report/sign-outs", response_model=ReportSignoutListOut)
async def list_family_report_signouts_endpoint(
    family_id: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> ReportSignoutListOut:
    return ReportSignoutListOut.model_validate(
        await list_report_signouts(
            session, family_id=family_id, user=user, project_id=project_id
        )
    )


@router.get("/{family_id}/report/sign-out-check", response_model=ReportSignoutCheckOut)
async def check_family_report_against_signout_endpoint(
    family_id: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> ReportSignoutCheckOut:
    """Whether the report as it would be signed now matches the latest sign-out (#508)."""
    return ReportSignoutCheckOut.model_validate(
        await compare_report_with_latest_signout(
            session, family_id=family_id, user=user, project_id=project_id
        )
    )


@router.get("/{family_id}/report/sign-outs/{version}", response_model=ReportSignoutDetail)
async def get_family_report_signout_endpoint(
    family_id: str,
    version: int,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> ReportSignoutDetail:
    return ReportSignoutDetail.model_validate(
        await get_report_signout(
            session, family_id=family_id, version=version, user=user, project_id=project_id
        )
    )


@router.get("/{family_id}/qc/sample-integrity", response_model=SampleIntegrityQcOut)
async def get_family_sample_integrity_qc_endpoint(
    family_id: str,
    project_id: str | None = None,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> SampleIntegrityQcOut:
    report = await get_family_sample_integrity_qc(
        session, family_id=family_id, user=user, project_id=project_id
    )
    return SampleIntegrityQcOut(
        family_id=family_id,
        overall_status=report.overall_status,
        application=report.application,
        application_label=report.application_label,
        application_summary=report.application_summary,
        genotype_source=report.genotype_source,
        paternity_check=(
            SampleIntegrityPaternityCheckOut(
                father=report.paternity_check.father,
                cat7_transmitted=report.paternity_check.cat7_transmitted,
                cat8_absent=report.paternity_check.cat8_absent,
                informative_sites=report.paternity_check.informative_sites,
                hom_alt_transmitted=report.paternity_check.hom_alt_transmitted,
                hom_alt_not_transmitted=report.paternity_check.hom_alt_not_transmitted,
                het_transmitted=report.paternity_check.het_transmitted,
                het_not_transmitted=report.paternity_check.het_not_transmitted,
                status=report.paternity_check.status,
                message=report.paternity_check.message,
            )
            if report.paternity_check is not None
            else None
        ),
        fetal_sex_check=(
            SampleIntegrityFetalSexCheckOut(
                inferred_sex=report.fetal_sex_check.inferred_sex,
                x_transmitted=report.fetal_sex_check.x_transmitted,
                x_not_transmitted=report.fetal_sex_check.x_not_transmitted,
                informative_sites=report.fetal_sex_check.informative_sites,
                status=report.fetal_sex_check.status,
                message=report.fetal_sex_check.message,
            )
            if report.fetal_sex_check is not None
            else None
        ),
        category_qc_check=(
            SampleIntegrityCategoryQcOut(
                denovo=report.category_qc_check.denovo,
                paternal_absent=report.category_qc_check.paternal_absent,
                maternal_informative=report.category_qc_check.maternal_informative,
                maternal_inherited=report.category_qc_check.maternal_inherited,
                maternal_inherited_rate=report.category_qc_check.maternal_inherited_rate,
                status=report.category_qc_check.status,
                message=report.category_qc_check.message,
            )
            if report.category_qc_check is not None
            else None
        ),
        sex_checks=[
            SampleIntegritySexCheckOut(
                sample_id=check.sample_id,
                recorded_sex=check.recorded_sex,
                inferred_sex=check.inferred_sex,
                x_het_rate=check.x_het_rate,
                x_sites=check.x_sites,
                status=check.status,
                message=check.message,
            )
            for check in report.sex_checks
        ],
        relatedness_checks=[
            SampleIntegrityRelatednessCheckOut(
                sample_a=check.sample_a,
                sample_b=check.sample_b,
                expected_relationship=check.expected_relationship,
                inferred_relationship=check.inferred_relationship,
                kinship=check.kinship,
                ibs0_rate=check.ibs0_rate,
                informative_sites=check.informative_sites,
                status=check.status,
                message=check.message,
            )
            for check in report.relatedness_checks
        ],
        mendelian_checks=[
            SampleIntegrityMendelianCheckOut(
                child=check.child,
                parents=check.parents,
                informative_sites=check.informative_sites,
                mendel_errors=check.mendel_errors,
                mendel_rate=check.mendel_rate,
                status=check.status,
                message=check.message,
            )
            for check in report.mendelian_checks
        ],
        autosomal_sites=report.autosomal_sites,
        notes=report.notes,
    )
