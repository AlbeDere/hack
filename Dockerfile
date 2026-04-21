FROM python:3.11-slim

WORKDIR /app

# Install system deps needed by PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# DB lives inside the container (persists as long as container runs)
ENV SQLITE_DB_PATH=/app/easels.db

# Initialise empty DB schema at build time
RUN python -m db.init_db

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
