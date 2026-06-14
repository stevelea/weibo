# Pipeline Dockerfile — Python application container
FROM python:3.12-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

# Set timezone
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Install Python dependencies
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir hatchling && \
    pip install --no-cache-dir .

# Copy application code
COPY src/ /app/src/
COPY config/ /app/config/

# Create data and output directories
RUN mkdir -p /app/data /output/content /output/public

# Run the pipeline scheduler
CMD ["python", "-m", "src.main"]
