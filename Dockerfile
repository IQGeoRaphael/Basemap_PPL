# Dockerfile
FROM python:3.12-slim

# Install system dependencies
# Add sqlite3 to the apt-get install line
RUN apt-get update && apt-get install -y \
    gdal-bin \
    python3-gdal \
    git \
    build-essential \
    libsqlite3-dev \
    sqlite3 \  
    zlib1g-dev \
    libspatialite-dev \
    libgeos-dev \
    libproj-dev \
    proj-bin \
    libgdal-dev \
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

# Install tippecanoe
RUN git clone https://github.com/mapbox/tippecanoe.git && \
    cd tippecanoe && \
    make -j$(nproc) && \
    make install && \
    cd .. && \
    rm -rf tippecanoe

# Install GDAL Python bindings
RUN pip install --no-cache-dir \
    GDAL==$(gdal-config --version) \
    rasterio \
    numpy

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy script
COPY basemap_generator.py .

# Create directory for output
RUN mkdir -p /app/output

# Set environment variables
ENV OUTPUT_DIR=/app/output

# Run script
CMD ["python", "basemap_generator.py"]