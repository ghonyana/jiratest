# ============================================================================
# Error Triage → Jira Upserter Service - Multi-Stage Docker Build
# ============================================================================
# This Dockerfile implements a production-ready multi-stage build for the
# Flask-based webhook processing service that integrates Vercel and GCP error
# events with Jira issue tracking.
#
# Build Strategy:
# - Stage 1 (Builder): Compile Python dependencies with native extensions
# - Stage 2 (Runtime): Minimal production image with only runtime dependencies
#
# Security Features:
# - Non-root user execution (nobody, UID 65534)
# - No secrets baked into image layers
# - Minimal attack surface (slim base image, no build tools in runtime)
# - Read-only root filesystem compatible (tmpfs for /tmp in ECS)
#
# Performance Optimizations:
# - Multi-stage build reduces final image size by 30-50%
# - Layer caching optimized (dependencies before application code)
# - Pip and apt package caches purged to minimize image size
#
# Per Technical Specification Section 8.4.2 and 8.4.3
# ============================================================================

# ============================================================================
# Stage 1: Builder - Dependency Compilation
# ============================================================================
# Purpose: Install build dependencies and compile Python packages with native
# extensions (cryptography, pymongo, redis). This stage includes compilers
# and development headers that are excluded from the final runtime image.
# ============================================================================

FROM python:3.11-slim AS builder

# Install build dependencies required for compiling Python packages
# - gcc: C compiler for native extensions
# - python3-dev: Python development headers
# - libpq-dev: PostgreSQL client library headers (for psycopg2 if needed)
# - build-essential: Common build tools (make, g++, etc.)
# --no-install-recommends: Minimize unnecessary packages
# rm -rf /var/lib/apt/lists/*: Clean up apt cache to reduce layer size
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libpq-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set working directory for dependency installation
WORKDIR /app

# Copy only requirements.txt first to leverage Docker layer caching
# If requirements.txt hasn't changed, Docker reuses the cached layer
# from the pip install step, significantly speeding up builds
COPY requirements.txt .

# Install Python dependencies to user site-packages directory
# --user: Install to /root/.local for easy copying to runtime stage
# --no-cache-dir: Prevent pip cache accumulation, reducing image size by ~50MB
# This step takes 30-60 seconds on first build but is cached for subsequent builds
RUN pip install --user --no-cache-dir -r requirements.txt

# ============================================================================
# Stage 2: Runtime - Production Image
# ============================================================================
# Purpose: Create minimal production container with only runtime dependencies
# and application code. Build tools and compilers are excluded to reduce
# attack surface and image size.
# ============================================================================

FROM python:3.11-slim

# Install only runtime dependencies (no compilers or development tools)
# - libpq5: PostgreSQL client library (runtime dependency for psycopg2)
# - curl: HTTP client for health check endpoint verification
# - ca-certificates: TLS certificate validation for HTTPS connections
# These packages are ~30MB total vs. ~200MB for build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled Python packages from builder stage
# /root/.local contains all pip-installed packages with native extensions
# This avoids recompiling packages in the runtime image
COPY --from=builder /root/.local /root/.local

# Set working directory for application code
WORKDIR /app

# Copy application source code and configuration files
# Separate COPY commands allow independent layer caching
# (source code changes more frequently than config files)
COPY src/ ./src/
COPY config/ ./config/

# Configure environment variables for Python runtime optimization
# PATH: Add user site-packages bin directory for installed executables (gunicorn)
# PYTHONUNBUFFERED: Disable output buffering for real-time log streaming to CloudWatch
# PYTHONDONTWRITEBYTECODE: Prevent .pyc file generation (read-only filesystem compatible)
ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user for security hardening
# nobody user (UID 65534) is a standard unprivileged system user present in all Linux distributions
# This prevents container breakout privilege escalation attacks
# All application code runs with minimal OS permissions
USER nobody

# Health check configuration for container orchestration
# ECS uses this to determine container health and trigger automatic replacement
# --interval=30s: Check health every 30 seconds (aligned with ALB health checks)
# --timeout=5s: Maximum 5 seconds for health check response
# --start-period=60s: Grace period for application initialization (config loading, dependency connections)
# --retries=2: Mark unhealthy after 2 consecutive failures (60s total = fast failure detection)
# curl -f: Fail on HTTP error status codes (4xx, 5xx)
# /healthz endpoint: Flask route validating Redis, MongoDB, and Jira connectivity
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=2 \
    CMD curl -f http://localhost:8080/healthz || exit 1

# Expose application port for webhook ingress
# Port 8080: Standard non-privileged port (< 1024 requires root)
# ALB target group forwards traffic to this port
EXPOSE 8080

# Start Gunicorn WSGI server with Flask application factory
# Gunicorn Configuration (per Section 8.4.3):
# --bind 0.0.0.0:8080: Listen on all network interfaces for ECS awsvpc networking
# --workers 4: 4 worker processes aligned to 0.5 vCPU ECS task allocation
# --threads 2: 2 threads per worker for I/O-bound concurrency (8 total concurrent requests)
# --worker-class sync: Synchronous worker model (simplest, lowest memory overhead)
# --timeout 30: 30-second request timeout to prevent hung requests
# --graceful-timeout 30: 30-second graceful shutdown for in-flight request completion
# --access-logfile -: Stream HTTP access logs to stdout for CloudWatch capture
# --error-logfile -: Stream application errors to stderr for CloudWatch capture
# --log-level info: INFO level logging (ERROR, WARNING, INFO; DEBUG excluded)
# src.app:create_app(): Flask application factory pattern from src/app/__init__.py
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "4", \
     "--threads", "2", \
     "--worker-class", "sync", \
     "--timeout", "30", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "src.app:create_app()"]

# ============================================================================
# Build Instructions:
# ============================================================================
# Local build:
#   docker build -t jiratest/error-triage:local .
#
# Build with commit SHA tag:
#   docker build -t jiratest/error-triage:$(git rev-parse --short HEAD) .
#
# Multi-architecture build for AWS Graviton (ARM64) and x86_64:
#   docker buildx build --platform linux/amd64,linux/arm64 -t jiratest/error-triage:latest .
#
# ============================================================================
# Security Scanning:
# ============================================================================
# Trivy vulnerability scan:
#   trivy image --severity CRITICAL,HIGH jiratest/error-triage:latest
#
# ECR scan (automatic on push):
#   aws ecr start-image-scan --repository-name jiratest/error-triage --image-id imageTag=latest
#
# ============================================================================
# Runtime Requirements:
# ============================================================================
# Environment variables (loaded from AWS Secrets Manager in ECS):
# - REDIS_HOST: ElastiCache Redis cluster endpoint
# - REDIS_PORT: Redis port (default: 6379)
# - MONGODB_URI: MongoDB Atlas connection string (optional, for audit logs)
# - JIRA_BASE_URL: Atlassian Jira Cloud instance URL
# - JIRA_API_TOKEN: Jira API authentication token
# - VERCEL_WEBHOOK_SECRET: Vercel webhook signature verification secret
# - GCP_OIDC_AUDIENCE: GCP Pub/Sub push subscription audience for JWT validation
# - AWS_REGION: AWS region for Secrets Manager and CloudWatch
#
# ECS Task Definition Requirements:
# - CPU: 0.25-0.5 vCPU (256-512 CPU units)
# - Memory: 512-1024 MB
# - Port mappings: Container port 8080 → Host port 8080
# - Health check: CMD-SHELL, curl -f http://localhost:8080/healthz
# - Secrets: Load from AWS Secrets Manager with task role permissions
# - Networking: awsvpc mode with security groups allowing HTTPS inbound
#
# ============================================================================
# Image Size Metrics:
# ============================================================================
# Expected final image size: ~180 MB compressed (~450 MB uncompressed)
# Breakdown:
# - Base python:3.11-slim: ~150 MB
# - Runtime dependencies (libpq5, curl, ca-certificates): ~20 MB
# - Python packages (Flask, Jira, Redis, boto3, pymongo, etc.): ~200 MB
# - Application code and config: ~10 MB
#
# Multi-stage build size reduction:
# - Single-stage build: ~400 MB (includes gcc, python3-dev, build-essential)
# - Multi-stage build: ~180 MB (excludes build tools from runtime)
# - Size reduction: ~55% (220 MB saved)
# ============================================================================
