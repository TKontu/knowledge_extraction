"""Report generation API endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import ReportRequest, ReportResponse
from orm_models import Report
from services.llm.client import LLMClient
from services.projects.repository import ProjectRepository
from services.reports.pdf import PDFConversionError, PDFConverter
from services.reports.service import ReportService
from services.storage.repositories.entity import EntityRepository
from services.storage.repositories.extraction import ExtractionRepository

router = APIRouter(prefix="/api/v1", tags=["reports"])


@router.post(
    "/projects/{project_id}/reports",
    response_model=ReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_report(
    project_id: UUID,
    request: ReportRequest,
    db: Session = Depends(get_db),
) -> ReportResponse:
    """Generate a report for a project.

    Args:
        project_id: Project UUID
        request: Report generation request
        db: Database session

    Returns:
        Generated report response

    Raises:
        HTTPException: If project not found
    """
    # Validate project exists
    project_repo = ProjectRepository(db)
    project = await project_repo.get(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    # Create service dependencies
    extraction_repo = ExtractionRepository(db)
    entity_repo = EntityRepository(db)
    llm_client = LLMClient(settings)

    # Generate report
    report_service = ReportService(
        extraction_repo=extraction_repo,
        entity_repo=entity_repo,
        llm_client=llm_client,
        db_session=db,
    )

    report = await report_service.generate(project_id, request)

    # Convert to response
    return ReportResponse(
        id=str(report.id),
        type=report.type,
        title=report.title or "",
        content=report.content or "",
        source_groups=report.source_groups,
        extraction_count=len(report.extraction_ids),
        entity_count=0,  # TODO: count entities from report data
        generated_at=report.created_at.isoformat(),
    )


@router.get(
    "/projects/{project_id}/reports",
    status_code=status.HTTP_200_OK,
)
async def list_reports(
    project_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """List reports for a project.

    Args:
        project_id: Project UUID
        limit: Maximum number of reports to return
        offset: Number of reports to skip
        db: Database session

    Returns:
        Dict with reports list and pagination info

    Raises:
        HTTPException: If project not found
    """
    # Validate project exists
    project_repo = ProjectRepository(db)
    project = await project_repo.get(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    # Query reports
    query = db.query(Report).filter(Report.project_id == project_id)
    total = query.count()

    reports = query.order_by(Report.created_at.desc()).limit(limit).offset(offset).all()

    # Convert to response format
    report_list = [
        {
            "id": str(r.id),
            "type": r.type,
            "title": r.title,
            "source_groups": r.source_groups,
            "created_at": r.created_at.isoformat(),
        }
        for r in reports
    ]

    return {
        "reports": report_list,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/projects/{project_id}/reports/{report_id}",
    response_model=ReportResponse,
    status_code=status.HTTP_200_OK,
)
async def get_report(
    project_id: UUID,
    report_id: UUID,
    db: Session = Depends(get_db),
) -> ReportResponse:
    """Get a specific report.

    Args:
        project_id: Project UUID
        report_id: Report UUID
        db: Database session

    Returns:
        Report response

    Raises:
        HTTPException: If project or report not found
    """
    # Validate project exists
    project_repo = ProjectRepository(db)
    project = await project_repo.get(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    # Get report
    report = (
        db.query(Report)
        .filter(Report.id == report_id, Report.project_id == project_id)
        .first()
    )

    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )

    # Convert to response
    return ReportResponse(
        id=str(report.id),
        type=report.type,
        title=report.title or "",
        content=report.content or "",
        source_groups=report.source_groups,
        extraction_count=len(report.extraction_ids),
        entity_count=0,  # TODO: count entities from report data
        generated_at=report.created_at.isoformat(),
    )


@router.get(
    "/projects/{project_id}/reports/{report_id}/pdf",
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "PDF file",
        },
        404: {"description": "Report not found"},
        503: {"description": "PDF conversion unavailable"},
    },
)
async def export_report_pdf(
    project_id: UUID,
    report_id: UUID,
    db: Session = Depends(get_db),
) -> Response:
    """Export a report as PDF.

    Args:
        project_id: Project UUID
        report_id: Report UUID
        db: Database session

    Returns:
        PDF file response

    Raises:
        HTTPException: If report not found or PDF conversion fails
    """
    # Get report
    report = (
        db.query(Report)
        .filter(
            Report.id == report_id,
            Report.project_id == project_id,
        )
        .first()
    )

    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )

    if not report.content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Report has no content to export",
        )

    # Convert to PDF
    converter = PDFConverter()

    # Check if pandoc is available
    if not await converter.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF export not available (Pandoc not installed)",
        )

    try:
        pdf_content = await converter.convert(
            markdown=report.content,
            title=report.title,
        )
    except PDFConversionError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF conversion failed: {str(e)}",
        ) from e

    # Generate filename
    filename = f"report_{report_id}.pdf"
    if report.title:
        # Sanitize title for filename
        safe_title = "".join(c for c in report.title if c.isalnum() or c in " -_")[:50]
        filename = f"{safe_title}.pdf"

    return Response(
        content=pdf_content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/projects/{project_id}/reports/{report_id}/download")
async def download_report(
    project_id: UUID,
    report_id: UUID,
    db: Session = Depends(get_db),
) -> Response:
    """Download report in original format (markdown or xlsx)."""
    report = (
        db.query(Report)
        .filter(Report.id == report_id, Report.project_id == project_id)
        .first()
    )

    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )

    # Sanitize filename
    safe_title = "".join(c for c in report.title if c.isalnum() or c in " -_")[:50]

    if report.format == "xlsx" and report.binary_content:
        return Response(
            content=report.binary_content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_title}.xlsx"'
            },
        )

    return Response(
        content=report.content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.md"'},
    )
