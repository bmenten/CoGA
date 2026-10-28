from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_admin_user, get_current_user
from ..services.bed_service import (
    fetch_bed_batch_json,
    fetch_bed_json,
    upload_bed_data,
    validate_bed_type,
)
from ..services.coverage_content import DECLARED_COVERAGE_CONTENTS, DeclaredCoverageContent
from ..services.family_metadata_context import build_sample_metadata_context
from ..services.family_variant_write_lock import VARIANT_TYPES, lock_family_variant_writes
from ..services.access_control import CurrentUser
from ..services.raw_import_files_pg import record_upload_file_obj

router = APIRouter(prefix="/bed", tags=["bed"])

# The reads answer JSON. Their text format (a header line and tab-separated rows), once the
# default, was removed with the routes no screen called (ENG-5): a read that asks for it is
# refused (422) before anything is read, rather than answered in another format.
_FORMAT_DESCRIPTION = "The answer's format: json, the only one (the text format was removed)."


@router.post("/upload/{sample_id}/{bed_type}")
async def upload_bed(
    sample_id: str,
    bed_type: str,
    file: UploadFile = File(...),
    overwrite: bool = False,
    # What a coverage BED holds (TRACE-9): required for `coverage`, refused for the other
    # types. A query parameter, so the request's audit row records it.
    content: DeclaredCoverageContent | None = Query(
        None,
        description=(
            "What a coverage BED holds: read_depth or log2_ratio. Required for coverage, "
            "not accepted for the other track types."
        ),
    ),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_admin_user),
) -> dict[str, int]:
    # Before anything is read: the request names what it loads, or it is refused (422). A
    # coverage BED that declared nothing was read as depth and drawn as a log2 ratio.
    validate_bed_type(bed_type)
    if bed_type == "coverage" and content not in DECLARED_COVERAGE_CONTENTS:
        raise HTTPException(
            status_code=422,
            detail="Declare what the coverage BED holds: content=read_depth or content=log2_ratio",
        )
    if bed_type != "coverage" and content in DECLARED_COVERAGE_CONTENTS:
        raise HTTPException(
            status_code=422,
            detail="content declares what a coverage BED holds; it does not apply to a "
            f"{bed_type} BED",
        )
    sample_context = await build_sample_metadata_context(
        session,
        sample_identifier=sample_id,
        user=user,
    )
    # From the upload's first read until it commits: a package import's restore rewrites the
    # family's tracks too, and the import holds these locks from its snapshot to its
    # restore, so the upload never falls in between. The sample must still be stored once
    # it is this upload's turn.
    await lock_family_variant_writes(
        session,
        sample_context.family_uuid,
        VARIANT_TYPES,
        samples=[sample_context.sample_uuid],
    )
    result = await upload_bed_data(
        session,
        sample_context=sample_context,
        bed_type=bed_type,
        file=file,
        overwrite=overwrite,
        record_upload=True,
        content=content if bed_type == "coverage" else None,
        # The track's record says a single-file upload loaded it: a package's coverage BED
        # shares its source (`web`), and the signed record names which of the two did.
        uploaded_from="web",
    )
    await record_upload_file_obj(
        session,
        file=file,
        family_uuid=sample_context.family_uuid,
        family_id=sample_context.family_id,
        sample_uuid=sample_context.sample_uuid,
        scope="individual",
        dataset=bed_type,
        # The raw file's record says what it was declared to hold, beside its checksum.
        metadata={"content": content} if bed_type == "coverage" else None,
    )
    return result


@router.get("/{sample_id}/{bed_type}/batch", response_model=None)
async def fetch_bed_batch(
    sample_id: str,
    bed_type: str,
    chrom: list[str] = Query(...),
    window: int | None = Query(None, ge=1),
    start: int | None = Query(None, ge=0),
    end: int | None = Query(None, ge=0),
    format: Literal["json"] = Query("json", description=_FORMAT_DESCRIPTION),
    limit: int = Query(100000, ge=1, le=1000000),
    # Which caller's rows to return. A sample can carry HiFiCNV, WisecondorX and
    # QDNAseq coverage at once; merged, a windowed coverage read averaged three
    # independent measurements. A coverage read without this is refused (400) where
    # the sample holds several callers' coverage (TRACE-10).
    source: str | None = Query(None, max_length=64),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    del format
    sample_context = await build_sample_metadata_context(
        session,
        sample_identifier=sample_id,
        user=user,
    )
    return await fetch_bed_batch_json(
        session,
        sample_context=sample_context,
        bed_type=bed_type,
        chroms=chrom,
        window=window,
        start=start,
        end=end,
        limit=limit,
        source=source,
    )


@router.get("/{sample_id}/{bed_type}", response_model=None)
async def fetch_bed(
    sample_id: str,
    bed_type: str,
    chrom: str,
    window: int | None = Query(None, ge=1),
    start: int | None = Query(None, ge=0),
    end: int | None = Query(None, ge=0),
    format: Literal["json"] = Query("json", description=_FORMAT_DESCRIPTION),
    limit: int = Query(100000, ge=1, le=1000000),
    # See the batch endpoint above: a coverage read without this is refused where the
    # sample holds several callers' coverage.
    source: str | None = Query(None, max_length=64),
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    del format
    sample_context = await build_sample_metadata_context(
        session,
        sample_identifier=sample_id,
        user=user,
    )
    return await fetch_bed_json(
        session,
        sample_context=sample_context,
        bed_type=bed_type,
        chrom=chrom,
        window=window,
        start=start,
        end=end,
        limit=limit,
        source=source,
    )
