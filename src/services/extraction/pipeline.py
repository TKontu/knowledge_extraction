"""Extraction pipeline service for orchestrating the complete extraction flow."""

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

import structlog

from models import ExtractionProfile
from src.services.extraction.extractor import ExtractionOrchestrator
from src.services.extraction.profiles import ProfileRepository
from src.services.knowledge.extractor import EntityExtractor
from src.services.projects.repository import ProjectRepository
from src.services.storage.deduplication import ExtractionDeduplicator
from src.services.storage.embedding import EmbeddingService
from src.services.storage.qdrant.repository import QdrantRepository
from src.services.storage.repositories.extraction import ExtractionRepository
from src.services.storage.repositories.source import SourceRepository

# Default fallback profile when database profile not found
DEFAULT_PROFILE = ExtractionProfile(
    name="general",
    categories=["general", "features", "technical", "integration"],
    prompt_focus="General technical facts about the product, features, integrations, and capabilities",
    depth="detailed",
    is_builtin=True,
)

logger = structlog.get_logger(__name__)


@dataclass
class PipelineResult:
    """Result from processing a single source."""

    source_id: UUID
    extractions_created: int
    extractions_deduplicated: int
    entities_extracted: int
    entities_deduplicated: int
    errors: list[str] = field(default_factory=list)


@dataclass
class BatchPipelineResult:
    """Result from processing multiple sources."""

    sources_processed: int
    sources_failed: int
    total_extractions: int
    total_deduplicated: int
    total_entities: int
    results: list[PipelineResult] = field(default_factory=list)


class ExtractionPipelineService:
    """Orchestrates the complete extraction pipeline."""

    def __init__(
        self,
        orchestrator: ExtractionOrchestrator,
        deduplicator: ExtractionDeduplicator,
        entity_extractor: EntityExtractor,
        extraction_repo: ExtractionRepository,
        source_repo: SourceRepository,
        project_repo: ProjectRepository,
        qdrant_repo: QdrantRepository,
        embedding_service: EmbeddingService,
        profile_repo: ProfileRepository | None = None,
    ):
        """Initialize pipeline with all dependencies."""
        self._orchestrator = orchestrator
        self._deduplicator = deduplicator
        self._entity_extractor = entity_extractor
        self._extraction_repo = extraction_repo
        self._source_repo = source_repo
        self._project_repo = project_repo
        self._qdrant_repo = qdrant_repo
        self._embedding_service = embedding_service
        self._profile_repo = profile_repo

    async def process_source(
        self,
        source_id: UUID,
        project_id: UUID,
        profile_name: str = "general",
    ) -> PipelineResult:
        """Process a single source through the full pipeline."""
        errors = []
        extractions_created = 0
        extractions_deduplicated = 0
        entities_extracted = 0
        entities_deduplicated = 0

        # Fetch source
        source = await self._source_repo.get(source_id)
        if not source or not source.content:
            return PipelineResult(
                source_id=source_id,
                extractions_created=0,
                extractions_deduplicated=0,
                entities_extracted=0,
                entities_deduplicated=0,
                errors=["Source not found or empty"],
            )

        # Get project for entity types
        project = await self._project_repo.get(project_id)
        entity_types = project.entity_types if project else []

        # Load extraction profile
        profile = None
        if self._profile_repo:
            profile = self._profile_repo.get_by_name(profile_name)
        if profile is None:
            profile = DEFAULT_PROFILE

        # Extract facts via orchestrator
        result = await self._orchestrator.extract(
            page_id=source_id,
            markdown=source.content,
            profile=profile,
        )

        # Process each fact
        for fact in result.facts:
            try:
                # Check for duplicate
                dedup_result = await self._deduplicator.check_duplicate(
                    project_id=project_id,
                    source_group=source.source_group,
                    text_content=fact.fact,
                )

                if dedup_result.is_duplicate:
                    extractions_deduplicated += 1
                    continue

                # Store extraction
                extraction = await self._extraction_repo.create(
                    project_id=project_id,
                    source_id=source_id,
                    data={"fact_text": fact.fact, "category": fact.category},
                    extraction_type=fact.category,
                    source_group=source.source_group,
                    confidence=fact.confidence,
                    profile_used=profile_name,
                )
                extractions_created += 1

                # Generate and store embedding
                embedding = await self._embedding_service.embed(fact.fact)
                await self._qdrant_repo.upsert(
                    extraction_id=extraction.id,
                    embedding=embedding,
                    payload={
                        "project_id": str(project_id),
                        "source_group": source.source_group,
                        "extraction_type": fact.category,
                    },
                )

                # Extract entities
                entities = await self._entity_extractor.extract(
                    extraction_id=extraction.id,
                    extraction_data={"fact_text": fact.fact, "category": fact.category},
                    project_id=project_id,
                    entity_types=entity_types,
                    source_group=source.source_group,
                )
                entities_extracted += len(entities)

            except Exception as e:
                errors.append(f"Error processing fact: {str(e)}")
                logger.error("fact_processing_failed", error=str(e), fact=fact.fact)

        # Update source status
        await self._source_repo.update_status(source_id, "extracted")

        return PipelineResult(
            source_id=source_id,
            extractions_created=extractions_created,
            extractions_deduplicated=extractions_deduplicated,
            entities_extracted=entities_extracted,
            entities_deduplicated=entities_deduplicated,
            errors=errors,
        )

    async def process_batch(
        self,
        source_ids: list[UUID],
        project_id: UUID,
        profile_name: str = "general",
    ) -> BatchPipelineResult:
        """Process multiple sources."""
        results = []
        sources_failed = 0

        for source_id in source_ids:
            result = await self.process_source(source_id, project_id, profile_name)
            results.append(result)
            if result.errors:
                sources_failed += 1

        total_extractions = sum(r.extractions_created for r in results)
        total_deduplicated = sum(r.extractions_deduplicated for r in results)
        total_entities = sum(r.entities_extracted for r in results)

        return BatchPipelineResult(
            sources_processed=len(source_ids),
            sources_failed=sources_failed,
            total_extractions=total_extractions,
            total_deduplicated=total_deduplicated,
            total_entities=total_entities,
            results=results,
        )

    async def process_project_pending(
        self,
        project_id: UUID,
        profile_name: str = "general",
    ) -> BatchPipelineResult:
        """Process all pending sources for a project."""
        # Query for pending sources
        pending_sources = await self._source_repo.get_by_project_and_status(
            project_id, "pending"
        )

        # Extract source IDs
        source_ids = [source.id for source in pending_sources]

        # Process batch
        return await self.process_batch(source_ids, project_id, profile_name)


class SchemaExtractionPipeline:
    """Runs schema extraction on sources and stores results."""

    def __init__(
        self,
        orchestrator,  # SchemaExtractionOrchestrator
        db_session,
    ):
        self._orchestrator = orchestrator
        self._db = db_session

    async def extract_source(
        self,
        source,  # Source ORM object
        company_name: str,
    ) -> list:  # list[Extraction]
        """Extract all field groups from a source.

        Args:
            source: Source ORM object with markdown content.
            company_name: Company name (source_group).

        Returns:
            List of created Extraction objects.
        """
        from orm_models import Extraction

        if not source.content:
            logger.warning("source_has_no_content", source_id=str(source.id))
            return []

        # Run extraction for all field groups
        results = await self._orchestrator.extract_all_groups(
            source_id=source.id,
            markdown=source.content,
            company_name=company_name,
        )

        # Store each result as an extraction
        extractions = []
        for result in results:
            extraction = Extraction(
                project_id=source.project_id,
                source_id=source.id,
                data=result["data"],
                extraction_type=result["extraction_type"],
                source_group=company_name,
                confidence=result.get("confidence"),
                profile_used="drivetrain_schema",
            )
            self._db.add(extraction)
            extractions.append(extraction)

        self._db.flush()
        return extractions

    async def extract_project(
        self,
        project_id: UUID,
        source_groups: list[str] | None = None,
    ) -> dict:
        """Extract all sources in a project.

        Args:
            project_id: Project UUID.
            source_groups: Optional filter by company names.

        Returns:
            Summary dict with extraction counts.
        """
        from orm_models import Source
        from services.extraction.field_groups import ALL_FIELD_GROUPS

        # Include sources that are ready or already extracted (have content)
        query = self._db.query(Source).filter(
            Source.project_id == project_id,
            Source.status.in_(["ready", "extracted", "pending"]),
            Source.content.isnot(None),
        )

        if source_groups:
            query = query.filter(Source.source_group.in_(source_groups))

        sources = query.all()

        logger.info(
            "project_extraction_started",
            project_id=str(project_id),
            source_count=len(sources),
        )

        # Process sources in parallel with semaphore to limit concurrency
        semaphore = asyncio.Semaphore(4)  # Max 4 concurrent source extractions

        async def extract_with_limit(source) -> int:
            async with semaphore:
                extractions = await self.extract_source(
                    source=source,
                    company_name=source.source_group,
                )
                return len(extractions)

        extraction_counts = await asyncio.gather(
            *[extract_with_limit(s) for s in sources]
        )
        total_extractions = sum(extraction_counts)

        self._db.commit()

        return {
            "project_id": str(project_id),
            "sources_processed": len(sources),
            "extractions_created": total_extractions,
            "field_groups": len(ALL_FIELD_GROUPS),
        }
