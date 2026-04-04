FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./app.py
COPY prompt_injection_guard.py ./prompt_injection_guard.py
COPY templates ./templates
COPY policies ./policies

RUN useradd -m -u 1000 appuser
USER appuser

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
