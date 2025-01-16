# Dockerfile
FROM python:3.12-slim

# Install only the necessary system dependencies
RUN apt-get update && apt-get install -y \
    gdal-bin \
    python3-gdal \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install setuptools first
RUN pip install --no-cache-dir setuptools

# Install mbutil
RUN git clone --depth=1 https://github.com/mapbox/mbutil.git && \
    cd mbutil && \
    python setup.py install && \
    cd .. && \
    rm -rf mbutil

# Copy and install requirements first (better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy script last as it changes most often
COPY basemap_generator.py .

# Create directory for output
RUN mkdir -p /app/output

# Run script
CMD ["python", "basemap_generator.py"]