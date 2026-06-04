FROM python:3.12-slim

WORKDIR /app

# Install system dependencies:
# - build-essential, gcc: for compiling C extensions (lap, numpy)
# - libgl1, libglib2.0-0: OpenCV headless display dependencies
# - curl: used by run.sh to health-check the API before starting
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libgl1-mesa-glx \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies before copying source (layer cache optimization)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all pipeline source files
COPY . .

# Make entrypoint script executable
RUN chmod +x run.sh

# Default: run the simulator (overridden to video_processor by EXECUTION_MODE=LIVE)
CMD ["/bin/bash", "run.sh"]
