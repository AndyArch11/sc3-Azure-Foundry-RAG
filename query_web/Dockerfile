FROM python:3.12-slim

WORKDIR /app

ENV PYTHONPATH=/app

COPY query_web/requirements.txt ./query_web-requirements.txt
COPY runtime/requirements.txt ./runtime-requirements.txt
RUN pip install --no-cache-dir -r query_web-requirements.txt -r runtime-requirements.txt

COPY query_web/app.py ./app.py
COPY query_web/prompt_injection_guard.py ./prompt_injection_guard.py
COPY query_web/templates ./templates
COPY query_web/static ./static
COPY query_web/policies /app/policies
COPY runtime ./runtime

RUN useradd -m -u 1000 appuser
USER appuser

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
