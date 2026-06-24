FROM python:3.12-slim AS deps

WORKDIR /build

ARG RUNTIME_REQUIREMENTS_FILE=requirements/ingestion.txt

# Install dependencies into an isolated prefix that can be copied into the runtime image.
COPY requirements/ ./requirements/
RUN pip install --no-cache-dir --prefix=/install -r "${RUNTIME_REQUIREMENTS_FILE}"

FROM python:3.12-slim

WORKDIR /app

ENV PATH="/install/bin:${PATH}"
ENV PYTHONPATH="/app:/install/lib/python3.12/site-packages"

COPY --from=deps /install /install

# Copy the ingestion package and shared logging bootstrap used at runtime.
COPY ingestion/ ./ingestion/
COPY credentials/ ./credentials/
COPY storage/ ./storage/
COPY provider_core/ ./provider_core/
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
