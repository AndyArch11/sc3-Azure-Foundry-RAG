FROM python:3.12-slim

WORKDIR /app

ENV PYTHONPATH=/app

COPY query_web/requirements.txt ./query_web-requirements.txt
COPY runtime/requirements.txt ./runtime-requirements.txt
RUN pip install --no-cache-dir -r query_web-requirements.txt -r runtime-requirements.txt

COPY query_web/ ./query_web/
COPY runtime/ ./runtime/
COPY query_web/policies/precedence_policy.json ./policies/precedence_policy.json

RUN useradd -m -u 1000 appuser
USER appuser

EXPOSE 8080
CMD ["uvicorn", "query_web.app:app", "--host", "0.0.0.0", "--port", "8080"]
