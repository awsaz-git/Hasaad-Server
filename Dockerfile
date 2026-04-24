# ── Base image ────────────────────────────────────────────────────────────────
FROM python:3.9-slim

# ── Install Java (required for PySpark/MLeap) ─────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        default-jdk \
        procps \
        curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Java environment variables ────────────────────────────────────────────────
ENV JAVA_HOME=/usr/lib/jvm/default-java
ENV PATH=$JAVA_HOME/bin:$PATH

# ── PySpark environment variables ─────────────────────────────────────────────
ENV PYSPARK_PYTHON=python3
ENV PYSPARK_DRIVER_PYTHON=python3

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /app

# ── Install Python dependencies ───────────────────────────────────────────────
# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Copy application code ─────────────────────────────────────────────────────
COPY . .

# ── Expose port ───────────────────────────────────────────────────────────────
EXPOSE 8000

# ── Start the app ─────────────────────────────────────────────────────────────
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
