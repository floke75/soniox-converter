FROM python:3.11-slim

WORKDIR /app

# Install curl for health checks
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip/setuptools and install supervisor
RUN pip install --no-cache-dir --upgrade pip setuptools wheel supervisor

# Copy project files and install
COPY pyproject.toml .
COPY soniox_converter/ soniox_converter/
COPY format_captions/ format_captions/
RUN pip install --no-cache-dir .

# Copy tests and test assets (for in-container test runs)
COPY tests/ tests/
COPY test-assets/ test-assets/
COPY PremierePro_transcript_format_spec.json .
RUN pip install --no-cache-dir pytest

# Copy supervisor config
COPY supervisord.conf /etc/supervisord.conf

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

CMD ["supervisord", "-c", "/etc/supervisord.conf"]
