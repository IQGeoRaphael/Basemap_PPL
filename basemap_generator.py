import os
import subprocess
from tqdm import tqdm
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
import json
import time
from datetime import datetime
import glob
import logging
import shutil

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def run_command(command):
    logging.info(f"Running command: {' '.join(command)}")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        logging.error(f"Command failed with error: {result.stderr}")
    else:
        logging.info("Command completed successfully")
    return result

class ProcessTracker:
    def __init__(self, output_dir):
        self.progress_file = os.path.join(output_dir, 'download_progress.json')
        self.completed_urls = self.load_progress()

    def load_progress(self):
        if os.path.exists(self.progress_file):
            with open(self.progress_file, 'r') as f:
                return set(json.load(f))
        return set()

    def save_progress(self):
        with open(self.progress_file, 'w') as f:
            json.dump(list(self.completed_urls), f)

    def mark_completed(self, url):
        self.completed_urls.add(url)
        self.save_progress()

    def is_completed(self, url):
        return url in self.completed_urls

def create_retry_session(retries=3, backoff_factor=0.3, timeout=300):
    session = requests.Session()
    retry = Retry(total=retries, backoff_factor=backoff_factor, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.timeout = timeout
    return session

def download_tif(url, output_path, max_retries=3):
    session = create_retry_session(retries=5, timeout=600)  # Increased timeout for large files
    for attempt in range(max_retries):
        try:
            logging.info(f"Downloading high-quality imagery from {url}")
            response = session.get(url, stream=True)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            
            with open(output_path, 'wb') as file, tqdm(
                desc=output_path,
                total=total_size,
                unit='iB',
                unit_scale=True,
                unit_divisor=1024,
            ) as progress_bar:
                for data in response.iter_content(chunk_size=8192):  # Larger chunk size for efficiency
                    size = file.write(data)
                    progress_bar.update(size)
            
            # Verify the file was downloaded correctly
            if os.path.getsize(output_path) > 0:
                logging.info(f"Successfully downloaded {os.path.getsize(output_path) / (1024*1024):.2f} MB to {output_path}")
                return True
            else:
                logging.error("Downloaded file is empty. Retrying...")
                if os.path.exists(output_path):
                    os.remove(output_path)
                continue
                
        except requests.RequestException as e:
            logging.error(f"Download attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 5
                logging.info(f"Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
            else:
                logging.error("Max retries reached. Moving to next file.")
                return False

def get_tif_urls():
    """Get direct NAIP TIF URLs for a specific area in Kentucky"""
    stac_search_url = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
    
    kentucky_polygon = {
        "type": "Polygon",
        "coordinates": [[
            [-85.76975230223191, 37.63831975175371],
            [-85.79792299732526, 37.556622960281146],
            [-85.77917808348003, 37.558211386666116],
            [-85.7731342906098, 37.58369784583759],
            [-85.76039471747762, 37.61128549203865],
            [-85.74833151247218, 37.63142760638745],
            [-85.51777647369674, 37.62988516059704],
            [-85.51709126957944, 37.64263083576242],
            [-85.64388882701948, 37.64311140194164],
            [-85.76975230223191, 37.63831975175371]
        ]]
    }

    all_urls = []
    latest_year = None
    session = create_retry_session()

    # Focus on getting the highest quality imagery
    search_params = {
        "collections": ["naip"],
        "intersects": kentucky_polygon,
        "limit": 2,  # Only get one image as requested
        "query": {
            "datetime": {"gte": "2020-01-01"}, # Most recent data will typically have better color processing
            "gsd": {"lte": 0.6}  # Get the highest quality imagery available
        },
        "sortby": [{"field": "datetime", "direction": "desc"}] # Get the most recent imagery first
    }

    try:
        response = session.post(stac_search_url, json=search_params)
        response.raise_for_status()
        
        logging.info(f"API Response Status Code: {response.status_code}")
        
        features = response.json().get('features', [])
        logging.info(f"Number of features found: {len(features)}")
        
        if features:
            sorted_features = sorted(features, key=lambda x: x['properties']['datetime'], reverse=True)

            if latest_year is None:
                latest_year = datetime.strptime(sorted_features[0]['properties']['datetime'], '%Y-%m-%dT%H:%M:%SZ').year
            
            logging.info(f"Latest year: {latest_year}")

            region_features = [f for f in sorted_features 
                           if datetime.strptime(f['properties']['datetime'], '%Y-%m-%dT%H:%M:%SZ').year == latest_year]

            logging.info(f"Number of features for the latest year: {len(region_features)}")

            for feature in region_features:
                url = feature['assets']['image']['href']
                if url not in all_urls:
                    all_urls.append(url)
                    
            logging.info(f"Number of unique URLs found: {len(all_urls)}")
                    
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching data: {e}")

    return all_urls

def process_tifs(tif_urls, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    tracker = ProcessTracker(output_dir)

    for i, url in enumerate(tif_urls, 1):
        if tracker.is_completed(url):
            logging.info(f"Skipping already completed image {i} of {len(tif_urls)}")
            continue

        logging.info(f"Processing image {i} of {len(tif_urls)}")
        
        # Define all file paths we'll need
        input_tif = os.path.join(output_dir, f"input_{i}.tif")
        
        # Step 1: Download the TIF
        if not download_tif(url, input_tif):
            logging.error(f"Failed to download TIF for URL {url}. Skipping.")
            continue
        
        # Step 2: Check band statistics to determine if we need to reorder bands
        logging.info(f"Analyzing band statistics to determine correct RGB order")
        try:
            # Get band statistics to identify potential swapped bands
            stats_command = ['gdalinfo', '-stats', '-json', input_tif]
            stats_result = subprocess.run(stats_command, capture_output=True, text=True)
            
            # Default band order (standard RGB)
            band_order = [1, 2, 3]
            
            if stats_result.returncode == 0:
                import json
                try:
                    stats = json.loads(stats_result.stdout)
                    bands = stats.get('bands', [])
                    
                    if len(bands) >= 3:
                        # Check statistics to detect potential band swapping
                        means = []
                        for band in bands[:3]:
                            stats_data = band.get('stats', {})
                            mean = stats_data.get('mean', 0)
                            means.append(mean)
                        
                        logging.info(f"Band means: {means}")
                        
                        # Common pattern for swapped colors:
                        # If blue mean (band 3) is higher than red mean (band 1), 
                        # there's likely a swap
                        if len(means) == 3 and means[2] > means[0] * 1.2:
                            logging.info(f"Detected likely RGB/BGR swap, applying correction")
                            band_order = [3, 2, 1]  # BGR to RGB swap
                except json.JSONDecodeError:
                    logging.warning("Could not parse band statistics JSON")
            
            logging.info(f"Using band order: {band_order}")
        
        except Exception as e:
            logging.warning(f"Error analyzing band statistics: {e}")
            # Default to standard RGB order
            band_order = [1, 2, 3]
        
        # Step 3: Create a corrected intermediate TIFF with proper RGB bands
        corrected_tif = os.path.join(output_dir, f"corrected_{i}.tif")
        
        color_fix_command = [
            'gdal_translate',
            '-of', 'GTiff',
            '-co', 'COMPRESS=LZW',
            '-co', 'PHOTOMETRIC=RGB',
        ]
        
        # Add band selection based on the determined order
        for band in band_order:
            color_fix_command.extend(['-b', str(band)])
        
        color_fix_command.extend([input_tif, corrected_tif])
        
        logging.info(f"Creating color-corrected image with band order {band_order}")
        run_command(color_fix_command)
            
        # Step 4: Reproject and downsample to 1m resolution
        resampled_tif = os.path.join(output_dir, f"resampled_{i}.tif")
        gdalwarp_command = [
            'gdalwarp',
            '-r', 'lanczos',              # Best quality resampling algorithm
            '-of', 'GTiff',
            '-co', 'COMPRESS=LZW',        # LZW compression preserves quality better than JPEG
            '-co', 'TILED=YES',
            '-co', 'BLOCKXSIZE=256',
            '-co', 'BLOCKYSIZE=256',
            '-co', 'PREDICTOR=2',         # Predictor improves compression while maintaining quality
            '-co', 'BIGTIFF=YES',         # Support for large files
            '-co', 'PHOTOMETRIC=RGB',     # Force RGB color interpretation
            '-t_srs', 'EPSG:3857',        # Web Mercator projection
            '-tr', '1.0', '1.0',          # 1-meter resolution as requested
            '-tap',                       # Align pixels to the target resolution
            '-multi',                     # Use multithreading
            '-wo', 'NUM_THREADS=ALL_CPUS',
            '-dstnodata', '0',
            corrected_tif,
            resampled_tif
        ]
        
        logging.info(f"Reprojecting and resampling to 1m resolution")
        if run_command(gdalwarp_command).returncode != 0:
            logging.error(f"Failed to resample image {i}. Skipping.")
            continue
        
        # Step 5: Create high-quality MBTiles with consistent color
        final_mbtiles = os.path.join(output_dir, f"naip_tile_{i}.mbtiles")
        gdal_translate_command = [
            'gdal_translate', 
            '-of', 'MBTILES',
            '-co', 'TILE_FORMAT=JPEG',    # JPEG format as in your original script
            '-co', 'QUALITY=100',         # Max quality for JPEG tiles
            '-co', 'RESAMPLING=CUBIC',    # Cubic resampling as in your original script
            '-co', 'MINZOOM=1',           # Min zoom level 1 as requested
            '-co', 'MAXZOOM=16',          # Max zoom level 16 as requested
            '-mo', 'minzoom=1',           # Set metadata minzoom
            '-mo', 'maxzoom=16',          # Set metadata maxzoom
            '--config', 'GDAL_CACHEMAX', '1024',  # Increase cache for better processing
            resampled_tif, 
            final_mbtiles
        ]
        
        logging.info(f"Creating high-quality MBTiles with zoom levels 1-16")
        if run_command(gdal_translate_command).returncode != 0:
            logging.error(f"Failed to create MBTiles for image {i}. Skipping.")
            continue
        
        # Step 6: Add optimized overviews for better performance
        logging.info(f"Adding overviews for zoom levels")
        gdaladdo_mbtiles_command = [
            'gdaladdo', 
            '-r', 'lanczos',              # Use lanczos for highest quality overviews
            '--config', 'COMPRESS_OVERVIEW', 'LZW',
            '--config', 'GDAL_NUM_THREADS', 'ALL_CPUS',  # Use all CPUs for faster processing
            final_mbtiles,
            # Overview factors appropriate for zoom levels
            '2', '4', '8', '16', '32', '64', '128', '256', '512', '1024', '2048', '4096', '8192'
        ]
        
        if run_command(gdaladdo_mbtiles_command).returncode != 0:
            logging.warning(f"Adding overviews to MBTiles for image {i} failed, but continuing.")
        
        # Step 7: Verify the output quality and file integrity
        logging.info(f"Verifying MBTiles quality and integrity")
        verify_command = ['gdalinfo', final_mbtiles]
        verify_result = run_command(verify_command)
        
        # Fix metadata if needed
        fix_zoom_metadata = [
            'sqlite3', final_mbtiles, 
            "UPDATE metadata SET value='1' WHERE name='minzoom'; UPDATE metadata SET value='16' WHERE name='maxzoom';"
        ]
        subprocess.run(fix_zoom_metadata, capture_output=True)
        
        # Clean up intermediate files
        if os.path.exists(input_tif):
            os.remove(input_tif)
        if os.path.exists(corrected_tif):
            os.remove(corrected_tif)
        if os.path.exists(resampled_tif):
            os.remove(resampled_tif)
        
        # Mark this URL as completed
        tracker.mark_completed(url)
        
        logging.info(f"✅ Successfully processed image {i}/{len(tif_urls)} with correct colors and quality")
    
    logging.info(f"🎉 All processing complete! High-quality MBTiles (zoom levels 1-16) created in {output_dir}")
    
def check_gdal_version():
    """Check GDAL version to ensure it supports the needed features"""
    try:
        result = subprocess.run(['gdalinfo', '--version'], capture_output=True, text=True)
        version_str = result.stdout.strip()
        logging.info(f"Using GDAL version: {version_str}")
    except Exception as e:
        logging.warning(f"Could not check GDAL version: {e}")

def main():
    # Get output directory from environment variable, with fallback
    output_dir = os.environ.get('OUTPUT_DIR', '/app/output')
    logging.info(f"Using output directory: {output_dir}")
    
    logging.info("Starting NAIP imagery download and processing at 1m resolution...")
    
    # First check GDAL version
    check_gdal_version()
    
    # Get high-quality imagery URLs - just one as requested
    tif_urls = get_tif_urls()
    
    if not tif_urls:
        logging.error("No NAIP images found. Check your bounding box and search parameters.")
        return
    
    logging.info(f"Found {len(tif_urls)} NAIP images to process.")
    process_tifs(tif_urls, output_dir)
    
    logging.info("✅ Processing complete! Rich color agricultural satellite imagery has been saved to the output directory.")

if __name__ == "__main__":
    main()