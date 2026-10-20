"""Find a signed analysis record by its report code (the code the EPD report quotes)."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.postgres import get_postgres_session
from ..dependencies import get_current_user
from ..schemas import ReportCodeLookupOut
from ..services.access_control import CurrentUser
from ..services.report_signout_service import find_report_signout_by_code

router = APIRouter(prefix="/report-codes", tags=["report_codes"])


@router.get("/{code}", response_model=ReportCodeLookupOut)
async def find_signed_record_by_report_code(
    code: str,
    session: AsyncSession = Depends(get_postgres_session),
    user: CurrentUser = Depends(get_current_user),
) -> ReportCodeLookupOut:
    """The family and version of the signed record ``code`` names.

    422 for a string that is not a report code (a mistyped or swapped symbol is caught by its
    check symbol); 404, the same for both, for a code no record has and for one whose
    record's family is outside the caller's projects.
    """
    return ReportCodeLookupOut.model_validate(
        await find_report_signout_by_code(session, code=code, user=user)
    )
