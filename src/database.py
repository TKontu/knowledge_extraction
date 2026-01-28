"""Database connection and session management."""

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from config import settings

# Convert postgresql:// to postgresql+psycopg:// for psycopg3
database_url = settings.database_url.replace("postgresql://", "postgresql+psycopg://")

# Create SQLAlchemy engine
engine = create_engine(
    database_url,
    pool_pre_ping=True,  # Verify connections before using them
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    Dependency for getting database sessions.

    Yields:
        Database session that will be automatically closed.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database_connection() -> bool:
    """
    Check if database connection is working.

    Returns:
        True if connection succeeds, False otherwise.
    """
    try:
        with engine.connect() as conn:
            # Execute a simple query to verify connection
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
