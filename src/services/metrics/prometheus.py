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

    return "\n".join(lines) + "\n"
