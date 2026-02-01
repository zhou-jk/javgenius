#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MGStage Video Downloader
Download videos from MGStage with multi-threading and HTTP proxy support
"""

import os
import re
import json
import time
import logging
import argparse
import requests
import threading
import subprocess
import xml.etree.ElementTree as ET
from tqdm import tqdm
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('mgstage_downloader.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Thread lock for writing failed IDs
failed_lock = threading.Lock()


@dataclass
class VideoInfo:
    """Video information"""
    cid: str  # Original ID like kit-012
    pid: str  # PID returned by API
    title: str
    manifest_url: str
    actress: List[str]
    genres: List[str]


class MGStageDownloader:
    """MGStage Downloader"""
    
    API_BASE = "https://mgsplayer-api.mgstage.jp/api/v1"
    
    # Default headers
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) mgsplayer/1.2.3 Chrome/140.0.7339.41 Electron/38.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "jp-JP",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "sec-ch-ua": '"Not=A?Brand";v="24", "Chromium";v="140"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "player-type": "1",
        "priority": "u=1, i"
    }
    
    DOWNLOAD_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) mgsplayer/1.2.3 Chrome/140.0.7339.41 Electron/38.0.0 Safari/537.36",
        "Accept-Encoding": "identity",
        "Accept-Language": "jp-JP",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Dest": "empty",
        "priority": "u=4, i"
    }
    
    def __init__(self, config: Dict):
        self.uid = config.get('uid', '')
        self.device_id = config.get('device_id', '')
        self.shop_id = config.get('shop_id', 'prestigebb')
        self.quality = config.get('quality', 'high')
        self.player_version = config.get('player_version', '1.2.3')
        self.proxy = config.get('proxy', '')
        self.proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        self.output_dir = Path(config.get('output_dir', 'downloaded'))
        self.output_dir.mkdir(exist_ok=True)
        self.decrypted_dir = Path(config.get('decrypted_dir', 'decrypted'))
        self.decrypted_dir.mkdir(exist_ok=True)
        self.temp_dir = Path(config.get('temp_dir', 'temp'))
        self.temp_dir.mkdir(exist_ok=True)
        self.download_threads = config.get('download_threads', 1)
        
        # jav-it config
        self.mgs_username = config.get('mgs_username', '')
        self.mgs_password = config.get('mgs_password', '')
        # Resolve jav-it path relative to script directory
        jav_it_config_path = config.get('jav_it_path', './jav-it.exe')
        if jav_it_config_path.startswith('./') or jav_it_config_path.startswith('.\\'):
            # Relative path - resolve from script directory
            script_dir = Path(__file__).parent.resolve()
            self.jav_it_path = script_dir / jav_it_config_path[2:]
        else:
            self.jav_it_path = Path(jav_it_config_path)
        
        # Create session
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        if self.proxies:
            self.session.proxies.update(self.proxies)
    
    def _get_last_update(self) -> str:
        """Get last-update timestamp"""
        return datetime.now().strftime('%Y%m%d%H%M%S')
    
    def _get_api_headers(self, extra_headers: Dict = None) -> Dict:
        """Get API headers"""
        headers = {
            "uid": self.uid,
            "device-id": self.device_id,
            "player-version": self.player_version,
            "last-update": self._get_last_update()
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers
    
    def search_video(self, cid: str) -> Optional[Dict]:
        """Search video by CID to get PID"""
        url = f"{self.API_BASE}/list/monthly/search"
        params = {
            "word": cid,
            "size": 100,
            "offset": 0,
            "sort": "new",
            "shop_id": self.shop_id
        }
        
        try:
            headers = self._get_api_headers()
            response = self.session.get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if data.get('hits', 0) > 0 and data.get('contents'):
                # Find exact match
                for content in data['contents']:
                    pid = content.get('pid', '')
                    title = content.get('title', '')
                    if pid:
                        logger.info(f"Found video: {cid} -> pid: {pid}")
                        return content
                
                # If no exact match, return first result
                content = data['contents'][0]
                logger.info(f"Found video: {cid} -> pid: {content.get('pid')}")
                return content
            else:
                logger.warning(f"Video not found: {cid}")
                return None
                
        except Exception as e:
            logger.error(f"Search failed {cid}: {e}")
            return None
    
    def get_play_info(self, pid: str) -> Optional[Dict]:
        """Get play info (including manifest_url)"""
        url = f"{self.API_BASE}/detail/play/monthly/content"
        
        try:
            headers = self._get_api_headers({
                "pid": pid,
                "quality": self.quality
            })
            response = self.session.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if data.get('manifest_url'):
                logger.info(f"Got play info: {pid}")
                return data
            else:
                logger.warning(f"No manifest_url found: {pid}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to get play info {pid}: {e}")
            return None
    
    def parse_manifest(self, manifest_url: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse MPD file to get video and audio URLs directly from BaseURL elements"""
        try:
            # Download MPD manifest
            download_session = requests.Session()
            download_session.headers.update(self.DOWNLOAD_HEADERS)
            if self.proxies:
                download_session.proxies.update(self.proxies)
            
            response = download_session.get(manifest_url, timeout=30)
            response.raise_for_status()
            mpd_content = response.text
            
            import re
            
            # Extract video URLs from BaseURL (pattern: _XXXX.mp4 where XXXX is bitrate)
            # BaseURL may contain CDATA: <BaseURL><![CDATA[url]]></BaseURL> or just <BaseURL>url</BaseURL>
            video_pattern = r'<BaseURL>(?:<!\[CDATA\[)?(https?://[^<\]]+_(\d+)\.mp4[^<\]]*)(?:\]\]>)?</BaseURL>'
            video_matches = re.findall(video_pattern, mpd_content)
            
            # Extract audio URL from BaseURL (pattern: _audio.mp4)
            audio_pattern = r'<BaseURL>(?:<!\[CDATA\[)?(https?://[^<\]]+_audio\.mp4[^<\]]*)(?:\]\]>)?</BaseURL>'
            audio_match = re.search(audio_pattern, mpd_content)
            
            if not video_matches:
                logger.error("No video URLs found in MPD manifest")
                return None, None
            
            # Find highest bitrate video URL
            # video_matches is list of tuples: (full_url, bitrate)
            best_video = max(video_matches, key=lambda x: int(x[1]))
            video_url = best_video[0].replace(' ', '')  # Remove any spaces in URL
            
            logger.info(f"Available bitrates: {sorted([int(m[1]) for m in video_matches], reverse=True)}, using: {best_video[1]}")
            
            audio_url = None
            if audio_match:
                audio_url = audio_match.group(1).replace(' ', '')  # Remove any spaces in URL
            
            return video_url, audio_url
            
        except Exception as e:
            logger.error(f"Failed to parse manifest: {e}")
            return None, None
    
    def download_file(self, url: str, output_path: Path, desc: str = "") -> bool:
        """Download file with tqdm progress bar"""
        try:
            logger.info(f"Starting download: {desc}")
            
            # Create download session
            download_session = requests.Session()
            download_session.headers.update(self.DOWNLOAD_HEADERS)
            if self.proxies:
                download_session.proxies.update(self.proxies)
            
            # Get file size
            response = download_session.head(url, timeout=30)
            total_size = int(response.headers.get('content-length', 0))
            
            # Check for resume support
            downloaded_size = 0
            if output_path.exists():
                downloaded_size = output_path.stat().st_size
                if downloaded_size >= total_size and total_size > 0:
                    logger.info(f"File already exists and complete: {output_path.name}")
                    return True
            
            # Set Range header for resume
            headers = {}
            if downloaded_size > 0:
                headers['Range'] = f'bytes={downloaded_size}-'
                logger.info(f"Resuming from {downloaded_size} bytes")
            
            # Download file
            response = download_session.get(url, headers=headers, stream=True, timeout=60)
            response.raise_for_status()
            
            # Get actual content length
            content_length = int(response.headers.get('content-length', 0))
            if downloaded_size > 0:
                total_size = downloaded_size + content_length
            else:
                total_size = content_length
            
            # Write file with tqdm progress bar
            mode = 'ab' if downloaded_size > 0 else 'wb'
            
            with open(output_path, mode) as f:
                with tqdm(
                    total=total_size,
                    initial=downloaded_size,
                    unit='B',
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=desc,
                    leave=True,
                    position=None,
                    dynamic_ncols=True
                ) as pbar:
                    for chunk in response.iter_content(chunk_size=8192 * 16):  # 128KB chunks
                        if chunk:
                            f.write(chunk)
                            pbar.update(len(chunk))
            
            logger.info(f"Download complete: {output_path.name}")
            return True
            
        except Exception as e:
            logger.error(f"Download failed {desc}: {e}")
            return False
    
    def download_video(self, cid: str) -> bool:
        """Download single video"""
        logger.info(f"Processing: {cid}")
        
        # 1. Search to get PID
        search_result = self.search_video(cid)
        if not search_result:
            return False
        
        pid = search_result.get('pid')
        if not pid:
            logger.error(f"No pid found: {cid}")
            return False
        
        # 2. Get play info
        play_info = self.get_play_info(pid)
        if not play_info:
            return False
        
        manifest_url = play_info.get('manifest_url')
        
        # 3. Parse manifest to get download URLs
        video_url, audio_url = self.parse_manifest(manifest_url)
        if not video_url or not audio_url:
            return False
        
        # 4. Download video and audio
        cid_upper = cid.upper()
        video_file = self.output_dir / f"{cid_upper}_video.mp4"
        audio_file = self.output_dir / f"{cid_upper}_audio.mp4"
        output_file = self.decrypted_dir / f"{cid_upper}.mkv"
        
        # Check if already decrypted
        if output_file.exists():
            logger.info(f"Decrypted file already exists, skipping: {cid}")
            return True
        
        # Download video (skip if already exists)
        if not video_file.exists():
            if not self.download_file(video_url, video_file, f"{cid} video"):
                return False
        else:
            logger.info(f"Video file already exists: {video_file.name}")
        
        # Download audio (skip if already exists)
        if not audio_file.exists():
            if not self.download_file(audio_url, audio_file, f"{cid} audio"):
                return False
        else:
            logger.info(f"Audio file already exists: {audio_file.name}")
        
        logger.info(f"Download complete: {cid}")
        
        # 5. Decrypt with jav-it
        if not self.decrypt_video(cid_upper, video_file):
            return False
        
        return True
    
    def decrypt_video(self, cid: str, video_file: Path) -> bool:
        """Decrypt video using jav-it"""
        try:
            import shutil
            
            final_output_file = self.decrypted_dir / f"{cid}.mkv"
            temp_output_file = self.temp_dir / f"{cid}.mkv"
            
            # Check if already decrypted
            if final_output_file.exists():
                logger.info(f"Decrypted file already exists: {final_output_file.name}")
                return True
            
            # Check if jav-it exists
            if not self.jav_it_path.exists():
                logger.error(f"jav-it not found: {self.jav_it_path}")
                return False
            
            # Clean up any existing temp file
            if temp_output_file.exists():
                temp_output_file.unlink()
            
            # Set environment variables
            env = os.environ.copy()
            if self.mgs_username:
                env['MGS_USERNAME'] = self.mgs_username
            if self.mgs_password:
                env['MGS_PASSWORD'] = self.mgs_password
            
            # Build command - decrypt to temp directory first
            cmd = [
                str(self.jav_it_path),
                'decrypt',
                '-i', str(video_file),
                '-o', str(temp_output_file),
                '-t', 'mgs',
                '-s', self.shop_id
            ]
            
            logger.info(f"Running jav-it decrypt: {cid}")
            logger.info(f"Command: {' '.join(cmd)}")
            
            # Run jav-it
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                cwd=str(self.jav_it_path.parent) if self.jav_it_path.parent != Path('.') else None
            )
            
            if result.returncode != 0:
                logger.error(f"jav-it decrypt failed: {cid}")
                logger.error(f"stdout: {result.stdout}")
                logger.error(f"stderr: {result.stderr}")
                # Clean up temp file on failure
                if temp_output_file.exists():
                    temp_output_file.unlink()
                return False
            
            # Move from temp to final destination
            try:
                shutil.move(str(temp_output_file), str(final_output_file))
                logger.info(f"Moved decrypted file to: {final_output_file.name}")
            except Exception as e:
                logger.error(f"Failed to move decrypted file: {e}")
                return False
            
            logger.info(f"Decrypt complete: {final_output_file.name}")
            
            # Delete source files after successful decrypt
            try:
                video_file.unlink()
                logger.info(f"Deleted source file: {video_file.name}")
                audio_file = video_file.with_name(video_file.name.replace('_video.mp4', '_audio.mp4'))
                if audio_file.exists():
                    audio_file.unlink()
                    logger.info(f"Deleted source file: {audio_file.name}")
            except Exception as e:
                logger.warning(f"Failed to delete source files: {e}")
            
            return True
            
        except Exception as e:
            logger.error(f"Decrypt failed {cid}: {e}")
            return False
    
    def process_id(self, cid: str, failed_file: Path, ids_file: Path) -> bool:
        """Process single ID, write to failed file on failure, remove from ids.txt on success"""
        try:
            success = self.download_video(cid)
            if not success:
                with failed_lock:
                    with open(failed_file, 'a', encoding='utf-8') as f:
                        f.write(f"{cid}\n")
            else:
                # Remove successfully processed ID from ids.txt
                self._remove_id_from_file(cid, ids_file)
            return success
        except Exception as e:
            logger.error(f"Processing failed {cid}: {e}")
            with failed_lock:
                with open(failed_file, 'a', encoding='utf-8') as f:
                    f.write(f"{cid}\n")
            return False
    
    def _remove_id_from_file(self, cid: str, ids_file: Path):
        """Remove a CID from ids.txt after successful processing"""
        try:
            with failed_lock:
                with open(ids_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                # Filter out the processed CID (case-insensitive)
                remaining = [line for line in lines if line.strip().lower() != cid.lower()]
                
                with open(ids_file, 'w', encoding='utf-8') as f:
                    f.writelines(remaining)
                
                logger.info(f"Removed {cid} from {ids_file.name}")
        except Exception as e:
            logger.error(f"Failed to remove {cid} from {ids_file.name}: {e}")
    
    def run(self, ids_file: str = "ids.txt", failed_file: str = "failed_ids.txt"):
        """Run downloader"""
        ids_path = Path(ids_file)
        failed_path = Path(failed_file)
        
        if not ids_path.exists():
            logger.error(f"ID file not found: {ids_file}")
            return
        
        # Read ID list
        with open(ids_path, 'r', encoding='utf-8') as f:
            ids = [line.strip() for line in f if line.strip()]
        
        if not ids:
            logger.error("ID list is empty")
            return
        
        logger.info(f"Total {len(ids)} videos to download")
        
        # Clear failed file
        failed_path.write_text('', encoding='utf-8')
        
        # Multi-threaded download
        success_count = 0
        fail_count = 0
        
        with ThreadPoolExecutor(max_workers=self.download_threads) as executor:
            futures = {executor.submit(self.process_id, cid, failed_path, ids_path): cid for cid in ids}
            
            for future in as_completed(futures):
                cid = futures[future]
                try:
                    if future.result():
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    logger.error(f"Task exception {cid}: {e}")
                    fail_count += 1
        
        logger.info(f"Download finished: Success {success_count}, Failed {fail_count}")
        
        if fail_count > 0:
            logger.info(f"Failed IDs saved to: {failed_file}")


def load_config(config_path: str = "config.json") -> Dict:
    """Load config file"""
    config_file = Path(config_path)
    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
    return {}


def main():
    parser = argparse.ArgumentParser(description='MGStage Video Downloader')
    parser.add_argument('-c', '--config', default='config.json', help='Config file path')
    parser.add_argument('-i', '--ids', default='ids.txt', help='ID list file')
    parser.add_argument('-f', '--failed', default='failed_ids.txt', help='Failed ID save file')
    parser.add_argument('-p', '--proxy', help='HTTP proxy (override config)')
    parser.add_argument('-t', '--threads', type=int, help='Download threads (override config)')
    parser.add_argument('-o', '--output', help='Output directory (override config)')
    args = parser.parse_args()
    
    # Load config
    config = load_config(args.config)
    
    # Command line args override config file
    if args.proxy:
        config['proxy'] = args.proxy
    if args.threads:
        config['download_threads'] = args.threads
    if args.output:
        config['output_dir'] = args.output
    
    # Check required config
    if not config.get('uid'):
        logger.error("Missing uid config")
        return
    if not config.get('device_id'):
        logger.error("Missing device_id config")
        return
    
    # Create downloader and run
    downloader = MGStageDownloader(config)
    
    logger.info("MGStage Downloader started")
    logger.info(f"UID: {config.get('uid')}")
    logger.info(f"Proxy: {config.get('proxy') or 'None'}")
    logger.info(f"Threads: {config.get('download_threads', 1)}")
    logger.info(f"Output dir: {config.get('output_dir', 'downloaded')}")
    
    downloader.run(args.ids, args.failed)
    
    logger.info("Download task completed")


if __name__ == "__main__":
    main()
