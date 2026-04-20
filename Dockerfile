# Sponsio API — Cloud Run deployment (backend only)
# AI Studio frontend calls this API via CORS.

FROM python:3.12-slim

WORKDIR /app

# System deps for potential native packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml ./
COPY sponsio/ ./sponsio/
RUN pip install --no-cache-dir ".[all]" && \
    pip install --no-cache-dir langgraph langchain-google-genai

# Copy API code
COPY api/ ./api/

# Copy demo examples (needed for Playground Real LLM mode)
COPY examples/ ./examples/

# Create data directory for SQLite
RUN mkdir -p /app/data

# Expose port
EXPOSE 8080

# Start FastAPI
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
