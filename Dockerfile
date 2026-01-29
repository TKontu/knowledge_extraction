FROM python:3.12-slim

# Cache buster - change this to force rebuild
ARG CACHE_BUST=2026-01-29-205830

# Version information
ARG APP_VERSION=v1.3.1
ARG GIT_COMMIT=unknown

# Set as environment variables
ENV APP_VERSION=${APP_VERSION}
ENV GIT_COMMIT=${GIT_COMMIT}

WORKDIR /app

# Prevent Python from writing pyc files
ENV PYTHONDONTWRITEBYTECODE=1

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    pandoc \
    texlive-xetex \
    texlive-fonts-recommended \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code - preserving src/ package structure for imports
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY docker-entrypoint.sh .

# Make entrypoint executable
RUN chmod +x docker-entrypoint.sh

# Add src to Python path for imports
ENV PYTHONPATH=/app/src:/app

# Expose port
EXPOSE 8000

# Run migrations then start the application
CMD ["./docker-entrypoint.sh"]
