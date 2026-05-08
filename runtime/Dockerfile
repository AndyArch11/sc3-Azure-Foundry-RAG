FROM python:3.12-slim

WORKDIR /app

# Install dependencies in a separate layer so they are cached unless
# requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the ingestion package and shared logging bootstrap used at runtime.
COPY ingestion/ ./ingestion/
COPY credentials/ ./credentials/
COPY storage/ ./storage/
COPY log_config.py ./log_config.py
COPY trace_context.py ./trace_context.py
COPY outbound_instrumentation.py ./outbound_instrumentation.py
COPY outbound_metrics.py ./outbound_metrics.py

# Run as a non-root user.
RUN useradd -m -u 1000 appuser && mkdir -p /app/samples && chown appuser /app/samples
USER appuser

# ENTRYPOINT is the module; CMD provides default arguments that can be
# overridden at container runtime or by the Container App Job trigger.
ENTRYPOINT ["python", "-m", "ingestion.runner"]
CMD ["--help"]
