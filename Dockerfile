# Stage 1: Build
FROM python:3.13-slim AS build

WORKDIR /app

# Install build-time system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY ./app /app/app

# Stage 2: Production
FROM python:3.13-slim AS production

WORKDIR /app

# curl is needed at runtime for the docker-compose healthchecks
# (curl -f http://localhost:<port>/health); gcc from the build stage
# never makes it into this image
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /usr/local /usr/local
COPY --from=build /app/app /app/app

RUN curl -fsSL -o /app/global-bundle.pem \
    https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem

# Expose port
EXPOSE 8080

# Run the application with 2 workers
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
