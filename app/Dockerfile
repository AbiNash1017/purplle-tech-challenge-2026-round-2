FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer cache optimization)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire app source into the container
# The WORKDIR is /app, and we copy the app/ directory contents here.
# At runtime: uvicorn app.main:app runs from /app/../ (project root),
# but since we place source at /app, we run as: uvicorn main:app --app-dir /app
COPY . .

# Create a non-root user for security
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Use app.main:app module path — compatible when running from /app directory
# where main.py imports from app.* subpackages
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
