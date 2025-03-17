import json
import requests
import sys
import os
import threading
import time
import zipfile
import logging
import subprocess
import shutil
from datetime import datetime

maxthreads = 5
sema = threading.Semaphore(value=maxthreads)
threads = []
path = "naip_output/new"
processed_dir = os.path.join(path, "processed")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def run_command(command):
    start_time = time.time()
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        end_time = time.time()
        logging.info(f"Command completed in {end_time - start_time:.2f} seconds: {' '.join(command[:2])}...")
        return result
    except subprocess.CalledProcessError as e:
        end_time = time.time()
        logging.error(f"Command failed in {end_time - start_time:.2f} seconds: {' '.join(command[:2])}...")
        logging.error(f"Error output: {e.stderr}")
        return e


def process_downloaded_tifs(tif_files):
    os.makedirs(processed_dir, exist_ok=True)
    
    # Create VRT maintaining original resolution and projection
    logging.info("Creating VRT file...")
    vrt_file = os.path.join(processed_dir, "merged.vrt")
    gdalbuildvrt_command = [
        'gdalbuildvrt',
        '-overwrite',
        '-r', 'cubic',  # High-quality resampling
        '-resolution', 'highest',  # Preserve highest resolution
        vrt_file
    ] + tif_files

    if run_command(gdalbuildvrt_command).returncode != 0:
        return

    # Convert to EPSG:3857 while preserving original resolution
    logging.info("Running gdalwarp...")
    clipped_file = os.path.join(processed_dir, "warped.tif")
    gdalwarp_command = [
        'gdalwarp',
        '-r', 'cubic',  # Highest quality resampling
        '-of', 'GTiff',
        '-co', 'TILED=YES',
        '-co', 'BLOCKXSIZE=256',  # Standard tile size
        '-co', 'BLOCKYSIZE=256',  # Standard tile size
        '-co', 'COMPRESS=LZW',  # Lossless compression
        '-co', 'PREDICTOR=2',  # Horizontal differencing predictor for better compression
        '-co', 'BIGTIFF=YES',
        '-t_srs', 'EPSG:3857',
        '-tr', '2.39', '2.39',  # Resolution for zoom level 16
        '-tap',
        '-multi',
        '-wo', 'NUM_THREADS=ALL_CPUS',
        vrt_file,
        clipped_file
    ]

    if run_command(gdalwarp_command).returncode != 0:
        return

    # Convert to MBTiles with highest quality
    logging.info("Converting to MBTiles...")
    final_mbtiles = os.path.join(processed_dir, "final_merge_raster.mbtiles")
    gdal_translate_command = [
        'gdal_translate', 
        '-of', 'MBTILES',
        '-co', 'TILE_FORMAT=JPEG',
        '-co', 'RESAMPLING=CUBIC',
        '-co', 'QUALITY=100',
        clipped_file, 
        final_mbtiles
    ]

    if run_command(gdal_translate_command).returncode != 0:
        return

    # Add comprehensive overviews
    logging.info("Adding overviews...")
    gdaladdo_mbtiles_command = [
        'gdaladdo', 
        '-r', 'cubic',  # Highest quality overview resampling
        '--config', 'COMPRESS_OVERVIEW', 'LZW',
        final_mbtiles,
        '2', '4', '8', '16', '32', '64', '128', '256', '512', '1024', '2048', '4096', '8192', '16384'
    ]
    run_command(gdaladdo_mbtiles_command)

    # Ensure full zoom range in metadata
    logging.info("Updating zoom levels in metadata...")
    sqlite_command = [
        'sqlite3', 
        final_mbtiles, 
        "UPDATE metadata SET value='1' WHERE name='minzoom';" +
        "UPDATE metadata SET value='16' WHERE name='maxzoom';" +
        "VACUUM;"
    ]
    run_command(sqlite_command)

    logging.info(f"Process complete. Output files in {processed_dir}")

def sendRequest(url, data, apiKey=None):  
    headers = {'Content-Type': 'application/json'}
    if apiKey:
        headers['X-Auth-Token'] = apiKey
    
    response = requests.post(url, json=data, headers=headers)    
    output = response.json()
    
    if output['errorCode'] is not None:
        print(f"Error: {output['errorCode']} - {output['errorMessage']}")
        sys.exit(1)
    
    return output['data']

def downloadFile(url, dataset='naip'):
    sema.acquire()
    try:        
        response = requests.get(url, stream=True, timeout=60)
        content_disp = response.headers.get('content-disposition', '')
        if 'filename=' not in content_disp:
            sema.release()
            return
            
        filename = content_disp.split('filename=')[1].strip('"')
        
        # Download ZIP, JPEG, JP2, and TIF files
        allowed_extensions = ['.zip', '.tif'] # '.jpg', '.jpeg', '.jp2',
        if not any(filename.lower().endswith(ext) for ext in allowed_extensions):
            print(f"Skipping file: {filename}")
            sema.release()
            return
            
        os.makedirs(path, exist_ok=True)
        full_path = os.path.join(path, filename)
        
        print(f"Downloading file: {filename}")
        with open(full_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        print(f"Successfully downloaded file: {filename}")

        # Extract and handle different file types
        try:
            if filename.lower().endswith('.zip'):
                with zipfile.ZipFile(full_path, 'r') as zip_ref:
                    for file in zip_ref.namelist():
                        if any(file.lower().endswith(ext) for ext in ['.tif', '.jpg', '.jpeg', '.jp2']):
                            print(f"Extracting: {file}")
                            zip_ref.extract(file, path)
                os.remove(full_path)
                print(f"Extracted contents and removed ZIP: {filename}")
            elif filename.lower().endswith(('.jpg', '.jpeg', '.jp2', '.tif')):
                # For direct image downloads, keep the file
                print(f"Saved image file: {filename}")
        except Exception as ze:
            print(f"Error processing file {filename}: {str(ze)}")
        
        sema.release()
        
    except Exception as e:
        print(f"Error downloading {url}: {str(e)}")
        sema.release()

def runDownload(url):
    thread = threading.Thread(target=downloadFile, args=(url,))
    threads.append(thread)
    thread.start()

def main():
    # Create output directory if it doesn't exist
    os.makedirs(path, exist_ok=True)

    # Check for existing TIF files
    existing_tif_files = [os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith('.tif')]
    
    if existing_tif_files:
        print("\nExisting TIF files found:")
        for tif in existing_tif_files:
            print(f"- {os.path.basename(tif)}")
        
        print("\nSkipping download. Proceeding to GDAL processing...")
        process_downloaded_tifs(existing_tif_files)
        return

    # If no TIF files exist, proceed with download process
    username = ''
    token = ''
    serviceUrl = "https://m2m.cr.usgs.gov/api/api/json/stable/"
    
    print("Logging in...")
    apiKey = sendRequest(serviceUrl + "login-token", {'username': username, 'token': token})

    print("Getting scenes...")
    scenes = sendRequest(serviceUrl + "scene-search", {
        'datasetName': 'naip',
        'maxResults': 20,
        'sceneFilter': {
            'acquisitionFilter': {
                'start': '2022-01-01T00:00:00Z',
                'end': '2022-12-31T23:59:59Z'
            },
            'spatialFilter': {
                'filterType': 'geojson',
                'geoJson': {
                    'type': 'Polygon',
                    'coordinates': [[
                        [-89.080550458766368, 37.080322330226032],
                        [-88.955417979910038, 36.599550174620148],
                        [-88.593192383220668, 37.03422089064739],
                        [-86.920368718509778, 37.324001367998882],
                        [-85.67562984988632, 37.383274647457142],
                        [-84.812874337771646, 37.705984724507665],
                        [-84.667984099095889, 37.093494170105643],
                        [-85.089482975243527, 37.021049050767772],
                        [-85.135584414822162, 36.876158812092029],
                        [-84.635054499396858, 36.974947611189123],
                        [-83.607650988787015, 36.54686281510169],
                        [-82.145576762149943, 36.95518985136971],
                        [-82.902957555227701, 37.021049050767772],
                        [-84.114766824152127, 37.910148242641675],
                        [-83.403487470653005, 38.219686479812587],
                        [-83.798642667041406, 38.733388235117509],
                        [-84.378203621744404, 38.838762954154411],
                        [-85.063139295484291, 38.772903754756349],
                        [-85.405607132354234, 38.779489674696151],
                        [-85.405607132354234, 38.779489674696151],
                        [-87.328695754777783, 37.929906002461095],
                        [-87.993873668698257, 37.752086164086315],
                        [-87.993873668698257, 37.752086164086315],
                        [-87.993873668698257, 37.752086164086315],
                        [-89.080550458766368, 37.080322330226032]
                    ]]
                }
            }
        }
    }, apiKey)

    sceneIds = [result['entityId'] for result in scenes['results']]
    
    print("\nGetting download options...")
    downloadOptions = sendRequest(serviceUrl + "download-options", 
                                {
                                    'datasetName': 'naip', 
                                    'entityIds': sceneIds,
                                    # Add additional options if the API supports them
                                    'downloadFormat': ['GeoTIFF', 'JPEG2000', 'JPEG'],  # Request multiple formats
                                    'projection': 'EPSG:3857',  # Request Web Mercator if possible
                                    'resolution': 2.39  # Request specific resolution if possible
                                }, 
                                apiKey)

    downloads = []
    for option in downloadOptions:
        if option['available']:
            downloads.append({
                'entityId': option['entityId'],
                'productId': option['id']
            })

    if not downloads:
        print("No downloads available!")
        return

    print(f"\nRequesting downloads...")
    requestResults = sendRequest(serviceUrl + "download-request", {'downloads': downloads, 'label': 'naip_download'},apiKey)

    max_attempts = 6000
    attempt = 0
    while attempt < max_attempts:
        print(f"\nChecking download status (attempt {attempt + 1}/{max_attempts})...")
        downloadUrls = sendRequest(serviceUrl + "download-retrieve", {'label': 'naip_download'}, apiKey)
        
        if 'available' in downloadUrls and downloadUrls['available']:
            available_count = len(downloadUrls['available'])
            if available_count > 0:
                print(f"Found {available_count} available downloads!")
                for item in downloadUrls['available']:
                    runDownload(item['url'])
                break
        
        print("Downloads not ready yet, waiting 10 seconds...")
        time.sleep(10)
        attempt += 1

    print("\nWaiting for downloads to complete...")
    for thread in threads:
        thread.join()

    tif_files = [os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith('.tif')]
    tif_list_file = os.path.join(processed_dir, "input_tifs.txt")

    # Write TIF files to a text file
    with open(tif_list_file, 'w') as f:
        for tif in tif_files:
            f.write(f"{tif}\n")

    print(f"TIF files list written to {tif_list_file}")
    for tif in tif_files:
        print(f"- {os.path.basename(tif)}")
    
    if tif_files:
        print("\nStarting GDAL processing...")
        process_downloaded_tifs(tif_files)
    else:
        print("No TIF files found to process")

    sendRequest(serviceUrl + "logout", None, apiKey)

if __name__ == "__main__":
    main()