# Use official lightweight Python image
FROM python:3.12-slim

# Install system dependencies (ffmpeg is required for merging audio/video)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependencies and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Ensure output directories exist
RUN mkdir -p output/images output/audio output/videos output/merged uploads

# Expose port
EXPOSE 8000

# Start FastAPI
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
