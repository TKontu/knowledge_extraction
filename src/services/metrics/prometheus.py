"""Prometheus metrics formatting."""

from services.metrics.collector import SystemMetrics


def format_prometheus(metrics: SystemMetrics) -> str:
    """Format metrics in Prometheus text exposition format.

    Args:
        metrics: System metrics to format.

    Returns:
        String in Prometheus text exposition format.
    """
    lines = []

    # Jobs total
    lines.append("# HELP scristill_jobs_total Total number of jobs")
    lines.append("# TYPE scristill_jobs_total gauge")
    lines.append(f"scristill_jobs_total {metrics.jobs_total}")

    # Jobs by type
    lines.append("# HELP scristill_jobs_by_type Number of jobs by type")
    lines.append("# TYPE scristill_jobs_by_type gauge")
    for job_type, count in metrics.jobs_by_type.items():
        lines.append(f'scristill_jobs_by_type{{type="{job_type}"}} {count}')

    # Jobs by status
    lines.append("# HELP scristill_jobs_by_status Number of jobs by status")
    lines.append("# TYPE scristill_jobs_by_status gauge")
    for status, count in metrics.jobs_by_status.items():
        lines.append(f'scristill_jobs_by_status{{status="{status}"}} {count}')

    # Sources
    lines.append("# HELP scristill_sources_total Total number of sources")
    lines.append("# TYPE scristill_sources_total gauge")
    lines.append(f"scristill_sources_total {metrics.sources_total}")

    # Sources by status
    lines.append("# HELP scristill_sources_by_status Number of sources by status")
    lines.append("# TYPE scristill_sources_by_status gauge")
    for status, count in metrics.sources_by_status.items():
        lines.append(f'scristill_sources_by_status{{status="{status}"}} {count}')

    # Extractions
    lines.append("# HELP scristill_extractions_total Total number of extractions")
    lines.append("# TYPE scristill_extractions_total gauge")
    lines.append(f"scristill_extractions_total {metrics.extractions_total}")

    # Entities
    lines.append("# HELP scristill_entities_total Total number of entities")
    lines.append("# TYPE scristill_entities_total gauge")
    lines.append(f"scristill_entities_total {metrics.entities_total}")

    # Extractions by type
    lines.append("# HELP scristill_extractions_by_type Number of extractions by type")
    lines.append("# TYPE scristill_extractions_by_type gauge")
    for ext_type, count in metrics.extractions_by_type.items():
        lines.append(f'scristill_extractions_by_type{{type="{ext_type}"}} {count}')

    # Average confidence by type
    lines.append(
        "# HELP scristill_extraction_confidence_avg Average extraction confidence by type"
    )
    lines.append("# TYPE scristill_extraction_confidence_avg gauge")
    for ext_type, conf in metrics.avg_confidence_by_type.items():
        lines.append(
            f'scristill_extraction_confidence_avg{{type="{ext_type}"}} {conf:.4f}'
        )

    # Entities by type
    lines.append("# HELP scristill_entities_by_type Number of entities by type")
    lines.append("# TYPE scristill_entities_by_type gauge")
    for ent_type, count in metrics.entities_by_type.items():
        lines.append(f'scristill_entities_by_type{{type="{ent_type}"}} {count}')

    # Job duration metrics
    lines.append(
        "# HELP scristill_job_duration_seconds_avg Average job duration in seconds by type"
    )
    lines.append("# TYPE scristill_job_duration_seconds_avg gauge")
    for job_type, stats in metrics.job_duration_by_type.items():
        lines.append(
            f'scristill_job_duration_seconds_avg{{type="{job_type}"}} {stats.avg_seconds:.2f}'
        )

    lines.append(
        "# HELP scristill_job_duration_seconds_min Minimum job duration in seconds by type"
    )
    lines.append("# TYPE scristill_job_duration_seconds_min gauge")
    for job_type, stats in metrics.job_duration_by_type.items():
        lines.append(
            f'scristill_job_duration_seconds_min{{type="{job_type}"}} {stats.min_seconds:.2f}'
        )

    lines.append(
        "# HELP scristill_job_duration_seconds_max Maximum job duration in seconds by type"
    )
    lines.append("# TYPE scristill_job_duration_seconds_max gauge")
    for job_type, stats in metrics.job_duration_by_type.items():
        lines.append(
            f'scristill_job_duration_seconds_max{{type="{job_type}"}} {stats.max_seconds:.2f}'
        )

    lines.append(
        "# HELP scristill_jobs_completed_total Number of completed jobs by type (for duration stats)"
    )
    lines.append("# TYPE scristill_jobs_completed_total gauge")
    for job_type, stats in metrics.job_duration_by_type.items():
        lines.append(
            f'scristill_jobs_completed_total{{type="{job_type}"}} {stats.count}'
        )

    # Orphaned extractions
    lines.append("# HELP scristill_orphaned_extractions_total Extractions without embeddings")
    lines.append("# TYPE scristill_orphaned_extractions_total gauge")
    lines.append(f"scristill_orphaned_extractions_total {metrics.orphaned_extractions_total}")

    return "\n".join(lines) + "\n"
