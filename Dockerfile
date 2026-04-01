FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY static/ static/
COPY teams_manifest/ teams_manifest/

EXPOSE 8000

CMD ["python", "src/beetexting_webhook.py", "--port", "8000"]
