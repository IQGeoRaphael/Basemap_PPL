# NAIP Basemap Download and Processing

This tool downloads NAIP (National Agriculture Imagery Program) imagery for Kentucky and converts it to MBTiles format. It includes robust error handling, progress tracking, and automatic retries for failed downloads.

## Prerequisites

- Docker installed and running
- Git (for cloning the repository)
- Sufficient disk space for the imagery

## Quick Start

1. Clone the repository:
```bash
git clone https://github.com/rzagha1/NAIP-Basemap-Download.git
cd NAIP-Basemap-Download
```

2. Build the Docker image:
```bash
docker build -t naip-processor .
```

3. Run the container:
```bash
docker run -v $(pwd)/output:/app/output naip-processor
```

## Docker Commands

### Basic Usage
- Build image: `docker build -t naip-processor .`
- Run container: `docker run -v $(pwd)/output:/app/output naip-processor`

### Advanced Usage (with resource limits)
```bash
docker run \
  --cpus=4 \
  --memory=6g \
  --memory-swap=8g \
  -v $(pwd)/output:/app/output \
  naip-processor
```

### Cleaning Up
The repository includes a `docker-clean` script for easy cleanup. Make it executable and use it:

```bash
chmod +x docker-clean
./docker-clean    # Cleans all Docker resources
```

## Output

- Processed files are saved in the `output` directory
- Each image is processed into an MBTiles file
- Progress is tracked in `output/download_progress.json`

## Features

- Automatic retry for failed downloads
- Progress tracking and resumption capability
- Timeout handling for stalled downloads
- Region-specific processing for Kentucky
- Robust error handling and cleanup

## Troubleshooting

If the container stops or fails:

1. Clean up Docker resources:
```bash
./docker-clean
```

2. Rebuild and run:
```bash
docker build -t naip-processor .
docker run -v $(pwd)/output:/app/output naip-processor
```

The script will automatically resume from where it left off.

## Notes

- Downloads that get stuck will automatically timeout after 5 minutes and retry
- The process tracks completion, so you can safely stop and restart
- MBTiles files can be viewed in QGIS or other compatible software

## Project Structure

```
.
├── Dockerfile           # Docker configuration
├── basemap_generator.py # Main processing script
├── docker-clean        # Cleanup utility script
├── output/             # Generated MBTiles (gitignored)
└── README.md           # This file
```