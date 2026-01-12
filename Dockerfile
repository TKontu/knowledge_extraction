FROM python:3.12-slim

WORKDIR /app

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

# Add src to Python path for imports
ENV PYTHONPATH=/app/src:/app

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/src"]
