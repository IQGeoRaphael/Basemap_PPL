import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from tqdm import tqdm
import json
import os
import subprocess
from datetime import datetime
import time
import threading
import signal
import sys
import sqlite3
import re
import traceback

import json
from datetime import datetime
import requests

def get_tif_urls():
    """Get direct NAIP TIF URLs for a specific area in Kentucky"""
    stac_search_url = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
    
    # Your GeoJSON polygon
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

    search_params = {
        "collections": ["naip"],
        "intersects": kentucky_polygon,
        "limit": 100,
        "query": {
            "datetime": {"gte": "2018-01-01"},
            "naip:state": {"eq": "KY"}
        }
    }

    try:
        response = session.post(stac_search_url, json=search_params)
        response.raise_for_status()
        
        features = response.json().get('features', [])
        if features:
            sorted_features = sorted(features, key=lambda x: x['properties']['datetime'], reverse=True)

            if latest_year is None:
                latest_year = datetime.strptime(sorted_features[0]['properties']['datetime'], '%Y-%m-%dT%H:%M:%SZ').year

            region_features = [f for f in sorted_features 
                           if datetime.strptime(f['properties']['datetime'], '%Y-%m-%dT%H:%M:%SZ').year == latest_year]

            for feature in region_features:
                url = feature['assets']['image']['href']
                if url not in all_urls:
                    all_urls.append(url)
                    
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")

    return all_urls
class DownloadTimeout(Exception):
    pass

def download_with_timeout(session, url, filename, timeout=300, chunk_size=1024*1024):
    """Download with timeout for each chunk"""
    try:
        response = session.get(url, stream=True)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        
        progress_bar = tqdm(
            total=total_size,
            unit='iB',
            unit_scale=True,
            unit_divisor=1024,
        )

        downloaded = 0
        last_update = time.time()
        
        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    current_time = time.time()
                    # Check if we've gone too long without progress
                    if current_time - last_update > timeout:
                        raise DownloadTimeout(f"No progress for {timeout} seconds")
                    
                    f.write(chunk)
                    size = len(chunk)
                    downloaded += size
                    progress_bar.update(size)
                    last_update = current_time
                    
        progress_bar.close()
        return True
        
    except Exception as e:
        progress_bar.close()
        if os.path.exists(filename):
            os.remove(filename)
        raise e

def create_retry_session(retries=3, backoff_factor=0.3, timeout=300):
    """Create a requests session with retry capability and timeouts"""
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=(500, 502, 503, 504),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.timeout = timeout  # Set timeout for the entire session
    return session

def download_with_progress(url, filename, max_retries=3, timeout=300):
    """Enhanced download function with timeout and retry logic"""
    for attempt in range(max_retries):
        try:
            session = create_retry_session(timeout=timeout)
            print(f"\nAttempt {attempt + 1}/{max_retries} for {filename}")
            
            download_with_timeout(session, url, filename, timeout=timeout)
            return True
            
        except DownloadTimeout as e:
            print(f"\nDownload timed out: {e}")
            if os.path.exists(filename):
                os.remove(filename)
            
        except requests.exceptions.RequestException as e:
            print(f"\nDownload attempt {attempt + 1} failed: {e}")
            if os.path.exists(filename):
                os.remove(filename)
                
        except Exception as e:
            print(f"\nUnexpected error during download: {e}")
            if os.path.exists(filename):
                os.remove(filename)
            
        if attempt < max_retries - 1:
            wait_time = (attempt + 1) * 15  # Progressive delay: 15s, 30s, 45s
            print(f"Waiting {wait_time} seconds before retry...")
            time.sleep(wait_time)
        else:
            print("Max retries reached. Moving to next file.")
            return False

def get_signed_url(url):
    """Get signed URL with retry capability"""
    session = create_retry_session()
    sign_url = f"https://planetarycomputer.microsoft.com/api/sas/v1/sign?href={url}"
    
    try:
        response = session.get(sign_url)
        response.raise_for_status()
        return response.json()['href']
    except requests.exceptions.RequestException as e:
        print(f"Error getting signed URL: {e}")
        return None

class ProcessTracker:
    def __init__(self, output_dir):
        self.progress_file = os.path.join(output_dir, 'download_progress.json')
        self.lock_file = os.path.join(output_dir, '.processing.lock')
        self.completed_urls = self.load_progress()

    def load_progress(self):
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, 'r') as f:
                    return set(json.load(f))
            except json.JSONDecodeError:
                print("Warning: Progress file corrupted, starting fresh")
                return set()
        return set()

    def save_progress(self):
        temp_file = self.progress_file + '.tmp'
        with open(temp_file, 'w') as f:
            json.dump(list(self.completed_urls), f)
        os.replace(temp_file, self.progress_file)  # Atomic update

    def mark_completed(self, url):
        self.completed_urls.add(url)
        self.save_progress()

    def is_completed(self, url):
        return url in self.completed_urls

def convert_to_mbtiles(input_tif, output_mbtiles):
    """Convert TIF to MBTiles with precise bounds and error handling"""
    tiles_dir = 'tiles_dir'

    def get_tif_bounds(tif_path):
        """Extract precise geographic bounds of the input TIF"""
        try:
            # Use gdalinfo to get precise bounds
            result = subprocess.check_output([
                'gdalinfo', 
                '-json', 
                tif_path
            ], universal_newlines=True)
            
            import json
            gdalinfo = json.loads(result)
            
            # Extract bounds from the WGS84 extent
            extent = gdalinfo.get('wgs84Extent', {}).get('coordinates', [[[0,0,0,0]]])[0]
            
            # Flatten coordinates and find min/max
            lons = [coord[0] for coord in extent]
            lats = [coord[1] for coord in extent]
            
            bounds = [
                max(-180.0, min(lons)),   # min lon, clamped
                max(-85.0511, min(lats)),   # min lat, clamped
                min(180.0, max(lons)),    # max lon, clamped
                min(85.0511, max(lats))   # max lat, clamped
            ]
            
            return bounds
        except Exception as e:
            print(f"Could not extract precise bounds: {e}")
            return [-180.0, -85.0511, 180.0, 85.0511]

    try:
        # Clean up any existing files
        if os.path.exists(tiles_dir):
            subprocess.run(['rm', '-rf', tiles_dir], check=True)
        if os.path.exists(output_mbtiles):
            os.remove(output_mbtiles)

        # Get precise bounds of the input TIF
        bounds = get_tif_bounds(input_tif)
        bounds_str = ','.join(map(str, bounds))

        # Generate tiles with precise bounds
        subprocess.run([
            'gdal2tiles.py',
            '-p', 'mercator',    # Mercator projection
            '-z', '0-18',        # Zoom levels 0 to 18
            '-w', 'none',        # No world file generation
            '--xyz',             # XYZ tile naming convention
            '--processes=4',     # Parallel processing
            '-r', 'bilinear',    # Bilinear resampling for smoother transitions
            '--webviewer=none',  # No web viewer
            '-b', bounds_str,    # Use precise bounds
            input_tif,
            tiles_dir
        ], check=True)

        # Create MBTiles
        subprocess.run([
            'mb-util',
            '--image_format=png',  # PNG format for lossless compression
            '--scheme=xyz',         # XYZ tile scheme
            tiles_dir,
            output_mbtiles
        ], check=True)

        # Add metadata
        conn = sqlite3.connect(output_mbtiles)
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS metadata 
                     (name text, value text)''')
        
        # Extract filename details for metadata
        filename = os.path.basename(input_tif)
        
        # Try to extract year from filename (adjust regex as needed)
        year_match = re.search(r'(\d{4})', filename)
        year = year_match.group(1) if year_match else 'Unknown'
        
        metadata = [
            ('name', filename),
            ('type', 'overlay'),
            ('version', '1.1'),
            ('format', 'png'),
            ('bounds', bounds_str),  # Use precise bounds
            ('minzoom', '0'),
            ('maxzoom', '18'),
            ('year', year)
        ]
        
        c.executemany('INSERT OR REPLACE INTO metadata VALUES (?, ?)', metadata)
        conn.commit()
        conn.close()

        return True

    except subprocess.CalledProcessError as e:
        print(f"Error during conversion: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error during conversion: {e}")
        traceback.print_exc()  # Add detailed error traceback
        return False
    finally:
        # Always cleanup
        if os.path.exists(tiles_dir):
            subprocess.run(['rm', '-rf', tiles_dir])

def main():
    # Setup signal handler for graceful shutdown
    def signal_handler(signum, frame):
        print("\nReceived shutdown signal. Cleaning up...")
        if os.path.exists('temp_tif'):
            os.remove('temp_tif')
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    output_dir = os.environ.get('OUTPUT_DIR', './output')
    os.makedirs(output_dir, exist_ok=True)

    tracker = ProcessTracker(output_dir)
    
    try:
        tif_urls = get_tif_urls()
        if not tif_urls:
            print("No NAIP images found.")
            return

        for i, url in enumerate(tif_urls, 1):
            if tracker.is_completed(url):
                print(f"\nSkipping already completed image {i} of {len(tif_urls)}")
                continue

            print(f"\nProcessing image {i} of {len(tif_urls)}")
            temp_tif = os.path.join(output_dir, f"temp_ky_{i}.tif")
            output_mbtiles = os.path.join(output_dir, f"kentucky_{i}.mbtiles")

            # Get signed URL with retries
            max_attempts = 3
            signed_url = None
            for attempt in range(max_attempts):
                signed_url = get_signed_url(url)
                if signed_url:
                    break
                if attempt < max_attempts - 1:
                    print(f"Failed to get signed URL, retrying in 10 seconds...")
                    time.sleep(10)

            if not signed_url:
                print(f"Could not get signed URL for image {i} after {max_attempts} attempts")
                continue

            # Download with timeout and progress tracking
            if download_with_progress(signed_url, temp_tif, max_retries=3, timeout=300):
                if os.path.exists(temp_tif):
                    print(f"Converting TIF {i} to MBTiles...")
                    if convert_to_mbtiles(temp_tif, output_mbtiles):
                        tracker.mark_completed(url)
                    #os.remove(temp_tif)

        print("\nProcessing complete. Output files:")
        print(os.listdir(output_dir))

    except Exception as e:
        print(f"Unexpected error in main process: {e}")
        if os.path.exists('temp_tif'):
            os.remove('temp_tif')
        raise

if __name__ == "__main__":
    main()