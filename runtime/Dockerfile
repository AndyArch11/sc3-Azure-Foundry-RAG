FROM python:3.12-slim

WORKDIR /app

# Install dependencies in a separate layer so they are cached unless
# requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the ingestion package.
COPY ingestion/ ./ingestion/

# Run as a non-root user.
RUN useradd -m -u 1000 appuser
USER appuser

# ENTRYPOINT is the module; CMD provides default arguments that can be
# overridden at container runtime or by the Container App Job trigger.
ENTRYPOINT ["python", "-m", "ingestion.runner"]
CMD ["--help"]
