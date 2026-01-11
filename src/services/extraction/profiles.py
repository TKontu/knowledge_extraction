"""Profile repository for managing extraction profiles."""

from sqlalchemy.orm import Session

from models import ExtractionProfile
from orm_models import Profile as ORMProfile


class ProfileRepository:
    """Repository for loading and managing extraction profiles."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy database session.
        """
        self._session = session

    def get_by_name(self, name: str) -> ExtractionProfile | None:
        """Get a profile by name.

        Args:
            name: Profile name to retrieve.

        Returns:
            ExtractionProfile if found, None otherwise.
        """
        orm_profile = (
            self._session.query(ORMProfile).filter(ORMProfile.name == name).first()
        )

        if orm_profile is None:
            return None

        return self._to_dataclass(orm_profile)

    def list_all(self) -> list[ExtractionProfile]:
        """List all profiles.

        Returns:
            List of all extraction profiles.
        """
        orm_profiles = self._session.query(ORMProfile).all()
        return [self._to_dataclass(p) for p in orm_profiles]

    def list_builtin(self) -> list[ExtractionProfile]:
        """List only built-in profiles.

        Returns:
            List of built-in extraction profiles.
        """
        orm_profiles = (
            self._session.query(ORMProfile).filter(ORMProfile.is_builtin == True).all()
        )
        return [self._to_dataclass(p) for p in orm_profiles]

    def exists(self, name: str) -> bool:
        """Check if a profile exists.

        Args:
            name: Profile name to check.

        Returns:
            True if profile exists, False otherwise.
        """
        count = self._session.query(ORMProfile).filter(ORMProfile.name == name).count()
        return count > 0

    def _to_dataclass(self, orm_profile: ORMProfile) -> ExtractionProfile:
        """Convert ORM model to dataclass.

        Args:
            orm_profile: ORM Profile model.

        Returns:
            ExtractionProfile dataclass.
        """
        return ExtractionProfile(
            name=orm_profile.name,
            categories=orm_profile.categories,
            prompt_focus=orm_profile.prompt_focus,
            depth=orm_profile.depth,
            custom_instructions=orm_profile.custom_instructions,
            is_builtin=orm_profile.is_builtin,
        )
