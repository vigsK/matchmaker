# Custom application image: Python base + all DB / data dependencies baked in.
# Built by AWS CodeBuild and pushed to ECR; run on ECS Fargate by Step Functions.
# Base pulled from the ECR Public mirror (not Docker Hub) to avoid unauthenticated
# pull rate limits (429 toomanyrequests) during CodeBuild.
FROM public.ecr.aws/docker/library/python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/app

# Dependency layer first for better build caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY app ./app

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /opt/app
USER appuser

# Mode is selected via the MODE env var (or argv[1]) at runtime.
ENTRYPOINT ["python", "-m", "app.main"]
