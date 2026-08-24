FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY src src
COPY *.html ./

ENV PYTHONPATH=/app/backend
ENV PORT=10000

EXPOSE 10000

CMD ["python", "-m", "app.web"]
