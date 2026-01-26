#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nanairo Video Downloader
Download videos from nanairo.co with N_m3u8DL-CLI
Supports custom cookie, ID range, and HTTP proxy
"""

import os
import re
import json
import logging
import argparse
import requests
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('nanairo_downloader.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Thread lock for writing failed IDs
failed_lock = threading.Lock()


@dataclass
class VideoStream:
    """Video stream information"""
    bandwidth: int
    resolution: str
    video_url: str
    audio_url: str
    codecs: str


class NanairoDownloader:
    """Nanairo Video Downloader"""
    
    BASE_URL = "https://nanairo.co"
    
    # Default headers
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,zh-TW;q=0.8",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "DNT": "1",
        "Priority": "u=1, i"
    }
    
    def __init__(self, config: Dict):
        self.cookie = config.get('cookie', '')
        self.proxy = config.get('proxy', '')
        self.proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        self.output_dir = Path(config.get('output_dir', 'downloaded'))
        self.output_dir.mkdir(exist_ok=True)
        self.download_threads = config.get('download_threads', 1)
        self.language = config.get('language', 'ja')  # ja or en
        
        # N_m3u8DL-RE config
        n_m3u8dl_config_path = config.get('n_m3u8dl_path', './N_m3u8DL-RE.exe')
        if n_m3u8dl_config_path.startswith('./') or n_m3u8dl_config_path.startswith('.\\'):
            script_dir = Path(__file__).parent.resolve()
            self.n_m3u8dl_path = script_dir / n_m3u8dl_config_path[2:]
        else:
            self.n_m3u8dl_path = Path(n_m3u8dl_config_path)
        
        # Additional N_m3u8DL-RE options
        self.n_m3u8dl_args = config.get('n_m3u8dl_args', [])
        
        # Create session
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        if self.cookie:
            self.session.headers['Cookie'] = self.cookie
        if self.proxies:
            self.session.proxies.update(self.proxies)
    
    def get_video_page(self, video_id: int) -> Optional[str]:
        """Get video page HTML to extract title and other info"""
        url = f"{self.BASE_URL}/{self.language}/videos/{video_id}"
        
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"Video not found: {video_id}")
            else:
                logger.error(f"Failed to get video page {video_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to get video page {video_id}: {e}")
            return None
    
    def extract_video_title(self, html: str, video_id: int) -> str:
        """Extract video title from HTML"""
        try:
            # Try to find title in <title> tag
            title_match = re.search(r'<title>([^<]+)</title>', html)
            if title_match:
                title = title_match.group(1).strip()
                # Clean up title - remove site name suffix
                title = re.sub(r'\s*[-|]\s*nanairo.*$', '', title, flags=re.IGNORECASE)
                title = re.sub(r'\s*[-|]\s*ナナイロ.*$', '', title)
                if title:
                    # Sanitize filename
                    title = self._sanitize_filename(title)
                    return title
        except Exception as e:
            logger.warning(f"Failed to extract title: {e}")
        
        return str(video_id)
    
    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename by removing invalid characters"""
        # Remove invalid characters for Windows filenames
        invalid_chars = r'<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        # Remove leading/trailing spaces and dots
        filename = filename.strip(' .')
        # Limit length
        if len(filename) > 200:
            filename = filename[:200]
        return filename
    
    def start_player(self, video_id: int) -> Optional[Dict]:
        """Call PUT /player/{id}/start to get video play info including m3u8 URL"""
        url = f"{self.BASE_URL}/player/{video_id}/start"
        
        try:
            headers = self.HEADERS.copy()
            headers['Referer'] = f"{self.BASE_URL}/{self.language}/videos/{video_id}"
            headers['Origin'] = self.BASE_URL
            headers['Content-Type'] = 'application/json'
            if self.cookie:
                headers['Cookie'] = self.cookie
            
            response = self.session.put(url, headers=headers, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            logger.debug(f"Player start response: {data}")
            return data
            
        except Exception as e:
            logger.error(f"Failed to start player for video {video_id}: {e}")
            return None
    
    def get_master_m3u8_url(self, video_id: int) -> Optional[str]:
        """Get master m3u8 URL by calling player start API"""
        try:
            # Call player start API
            player_data = self.start_player(video_id)
            if not player_data:
                return None
            
            # Check if request was successful
            if not player_data.get('success'):
                logger.error(f"Player start API returned error: {player_data}")
                return None
            
            data = player_data.get('data', {})
            
            # Extract segmentToken (UUID) from response
            segment_token = data.get('segmentToken')
            if segment_token:
                # Construct m3u8 URL using the segment token
                m3u8_url = f"{self.BASE_URL}/videos/{video_id}/cmaf/sdr/{segment_token}/index.m3u8"
                logger.info(f"Found m3u8 URL: {m3u8_url}")
                return m3u8_url
            
            logger.warning(f"No segmentToken found in player response: {player_data}")
            return None
            
        except Exception as e:
            logger.error(f"Failed to get m3u8 URL: {e}")
            return None
    
    def parse_master_m3u8(self, m3u8_url: str, video_id: int) -> Optional[VideoStream]:
        """Parse master m3u8 to get highest quality stream"""
        try:
            headers = self.HEADERS.copy()
            headers['Referer'] = f"{self.BASE_URL}/{self.language}/videos/{video_id}"
            if self.cookie:
                headers['Cookie'] = self.cookie
            
            response = self.session.get(m3u8_url, headers=headers, timeout=30)
            response.raise_for_status()
            m3u8_content = response.text
            
            logger.debug(f"M3U8 content:\n{m3u8_content}")
            
            streams = []
            lines = m3u8_content.strip().split('\n')
            
            # Parse audio tracks
            audio_tracks = {}
            for line in lines:
                if line.startswith('#EXT-X-MEDIA:') and 'TYPE=AUDIO' in line:
                    group_match = re.search(r'GROUP-ID="([^"]+)"', line)
                    uri_match = re.search(r'URI="([^"]+)"', line)
                    if group_match and uri_match:
                        audio_tracks[group_match.group(1)] = uri_match.group(1)
            
            # Parse video streams
            i = 0
            while i < len(lines):
                if lines[i].startswith('#EXT-X-STREAM-INF:'):
                    stream_info = lines[i]
                    video_url = lines[i + 1] if i + 1 < len(lines) else None
                    
                    if video_url and not video_url.startswith('#'):
                        # Parse stream attributes
                        bandwidth_match = re.search(r'BANDWIDTH=(\d+)', stream_info)
                        resolution_match = re.search(r'RESOLUTION=(\d+x\d+)', stream_info)
                        codecs_match = re.search(r'CODECS="([^"]+)"', stream_info)
                        audio_match = re.search(r'AUDIO="([^"]+)"', stream_info)
                        
                        bandwidth = int(bandwidth_match.group(1)) if bandwidth_match else 0
                        resolution = resolution_match.group(1) if resolution_match else "unknown"
                        codecs = codecs_match.group(1) if codecs_match else "unknown"
                        audio_group = audio_match.group(1) if audio_match else None
                        
                        audio_url = audio_tracks.get(audio_group, '') if audio_group else ''
                        
                        streams.append(VideoStream(
                            bandwidth=bandwidth,
                            resolution=resolution,
                            video_url=video_url,
                            audio_url=audio_url,
                            codecs=codecs
                        ))
                i += 1
            
            if not streams:
                logger.error(f"No streams found in m3u8 for video {video_id}")
                return None
            
            # Sort by bandwidth (highest first)
            streams.sort(key=lambda x: x.bandwidth, reverse=True)
            
            best_stream = streams[0]
            logger.info(f"Selected stream: {best_stream.resolution}, bandwidth: {best_stream.bandwidth}, codecs: {best_stream.codecs}")
            
            return best_stream
            
        except Exception as e:
            logger.error(f"Failed to parse m3u8: {e}")
            return None
    
    def download_with_n_m3u8dl(self, m3u8_url: str, video_id: int, title: str) -> bool:
        """Download video using N_m3u8DL-RE"""
        try:
            output_name = f"{video_id}_{title}" if title != str(video_id) else str(video_id)
            
            # Check if file already exists
            for ext in ['.mp4', '.mkv', '.ts']:
                if (self.output_dir / f"{output_name}{ext}").exists():
                    logger.info(f"File already exists: {output_name}{ext}")
                    return True
            
            # Check if N_m3u8DL-RE exists
            if not self.n_m3u8dl_path.exists():
                logger.error(f"N_m3u8DL-RE not found: {self.n_m3u8dl_path}")
                return False
            
            # Build command for N_m3u8DL-RE
            cmd = [
                str(self.n_m3u8dl_path),
                m3u8_url,
                "--save-dir", str(self.output_dir),
                "--save-name", output_name,
                "--auto-select",              # 自动选择最佳轨道
                "-M", "format=mp4",           # 混流为 mp4 格式
                "--no-log",                   # 关闭日志文件输出
                "-mt"                         # 并发下载音视频
            ]
            
            # Add headers
            if self.cookie:
                cmd.extend(["-H", f"Cookie: {self.cookie}"])
            cmd.extend(["-H", f"Referer: {self.BASE_URL}/{self.language}/videos/{video_id}"])
            cmd.extend(["-H", f"User-Agent: {self.HEADERS['User-Agent']}"])
            
            # Add proxy if configured
            if self.proxy:
                cmd.extend(["--custom-proxy", self.proxy])
            
            # Add additional arguments from config
            if self.n_m3u8dl_args:
                cmd.extend(self.n_m3u8dl_args)
            
            logger.info(f"Running N_m3u8DL-RE for video {video_id}")
            logger.debug(f"Command: {' '.join(cmd)}")
            
            # Run N_m3u8DL-RE with real-time output display
            process = subprocess.Popen(
                cmd,
                stdout=None,   # 直接输出到控制台
                stderr=None    # 直接输出到控制台
            )
            
            # Wait for process to complete
            process.wait()
            
            if process.returncode != 0:
                logger.error(f"N_m3u8DL-RE failed for video {video_id}")
                return False
            
            logger.info(f"Download complete: {video_id}")
            return True
            
        except Exception as e:
            logger.error(f"Download failed for video {video_id}: {e}")
            return False
    
    def download_video(self, video_id: int) -> bool:
        """Download single video"""
        logger.info(f"Processing video: {video_id}")
        
        # 1. Get video page
        html = self.get_video_page(video_id)
        if not html:
            return False
        
        # 2. Extract title
        title = self.extract_video_title(html, video_id)
        logger.info(f"Video title: {title}")
        
        # 3. Get master m3u8 URL via player start API
        m3u8_url = self.get_master_m3u8_url(video_id)
        if not m3u8_url:
            return False
        
        # 4. Download with N_m3u8DL-CLI (it will auto-select best quality)
        return self.download_with_n_m3u8dl(m3u8_url, video_id, title)
    
    def process_id(self, video_id: int, failed_file: Path) -> bool:
        """Process single ID, write to failed file on failure"""
        try:
            success = self.download_video(video_id)
            if not success:
                with failed_lock:
                    with open(failed_file, 'a', encoding='utf-8') as f:
                        f.write(f"{video_id}\n")
            return success
        except Exception as e:
            logger.error(f"Processing failed {video_id}: {e}")
            with failed_lock:
                with open(failed_file, 'a', encoding='utf-8') as f:
                    f.write(f"{video_id}\n")
            return False
    
    def run(self, start_id: int, end_id: int, failed_file: str = "failed_ids.txt"):
        """Run downloader for ID range"""
        failed_path = Path(failed_file)
        
        ids = list(range(start_id, end_id + 1))
        
        if not ids:
            logger.error("ID range is empty")
            return
        
        logger.info(f"Total {len(ids)} videos to download (ID {start_id} - {end_id})")
        
        # Clear failed file
        failed_path.write_text('', encoding='utf-8')
        
        # Process downloads
        success_count = 0
        fail_count = 0
        
        if self.download_threads > 1:
            # Multi-threaded download
            with ThreadPoolExecutor(max_workers=self.download_threads) as executor:
                futures = {executor.submit(self.process_id, vid, failed_path): vid for vid in ids}
                
                for future in as_completed(futures):
                    vid = futures[future]
                    try:
                        if future.result():
                            success_count += 1
                        else:
                            fail_count += 1
                    except Exception as e:
                        logger.error(f"Task exception {vid}: {e}")
                        fail_count += 1
        else:
            # Single-threaded download
            for vid in ids:
                if self.process_id(vid, failed_path):
                    success_count += 1
                else:
                    fail_count += 1
        
        logger.info(f"Download finished: Success {success_count}, Failed {fail_count}")
        
        if fail_count > 0:
            logger.info(f"Failed IDs saved to: {failed_file}")
    
    def run_from_list(self, ids: List[int], failed_file: str = "failed_ids.txt"):
        """Run downloader from ID list"""
        failed_path = Path(failed_file)
        
        if not ids:
            logger.error("ID list is empty")
            return
        
        logger.info(f"Total {len(ids)} videos to download from config")
        
        # Clear failed file
        failed_path.write_text('', encoding='utf-8')
        
        # Process downloads
        success_count = 0
        fail_count = 0
        
        if self.download_threads > 1:
            with ThreadPoolExecutor(max_workers=self.download_threads) as executor:
                futures = {executor.submit(self.process_id, vid, failed_path): vid for vid in ids}
                
                for future in as_completed(futures):
                    vid = futures[future]
                    try:
                        if future.result():
                            success_count += 1
                        else:
                            fail_count += 1
                    except Exception as e:
                        logger.error(f"Task exception {vid}: {e}")
                        fail_count += 1
        else:
            for vid in ids:
                if self.process_id(vid, failed_path):
                    success_count += 1
                else:
                    fail_count += 1
        
        logger.info(f"Download finished: Success {success_count}, Failed {fail_count}")
        
        if fail_count > 0:
            logger.info(f"Failed IDs saved to: {failed_file}")
    
    def run_from_file(self, ids_file: str = "ids.txt", failed_file: str = "failed_ids.txt"):
        """Run downloader from ID file"""
        ids_path = Path(ids_file)
        failed_path = Path(failed_file)
        
        if not ids_path.exists():
            logger.error(f"ID file not found: {ids_file}")
            return
        
        # Read ID list
        with open(ids_path, 'r', encoding='utf-8') as f:
            ids = []
            for line in f:
                line = line.strip()
                if line and line.isdigit():
                    ids.append(int(line))
        
        if not ids:
            logger.error("ID list is empty")
            return
        
        logger.info(f"Total {len(ids)} videos to download from file")
        
        # Clear failed file
        failed_path.write_text('', encoding='utf-8')
        
        # Process downloads
        success_count = 0
        fail_count = 0
        
        if self.download_threads > 1:
            with ThreadPoolExecutor(max_workers=self.download_threads) as executor:
                futures = {executor.submit(self.process_id, vid, failed_path): vid for vid in ids}
                
                for future in as_completed(futures):
                    vid = futures[future]
                    try:
                        if future.result():
                            success_count += 1
                        else:
                            fail_count += 1
                    except Exception as e:
                        logger.error(f"Task exception {vid}: {e}")
                        fail_count += 1
        else:
            for vid in ids:
                if self.process_id(vid, failed_path):
                    success_count += 1
                else:
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
    parser = argparse.ArgumentParser(description='Nanairo Video Downloader')
    parser.add_argument('-c', '--config', default='config.json', help='Config file path')
    parser.add_argument('-s', '--start', type=int, help='Start video ID')
    parser.add_argument('-e', '--end', type=int, help='End video ID')
    parser.add_argument('-i', '--ids', help='ID list file (alternative to range)')
    parser.add_argument('-f', '--failed', default='failed_ids.txt', help='Failed ID save file')
    parser.add_argument('-p', '--proxy', help='HTTP proxy (override config)')
    parser.add_argument('-t', '--threads', type=int, help='Download threads (override config)')
    parser.add_argument('-o', '--output', help='Output directory (override config)')
    parser.add_argument('--cookie', help='Cookie string (override config)')
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
    if args.cookie:
        config['cookie'] = args.cookie
    
    # Check required config
    if not config.get('cookie'):
        logger.warning("No cookie configured - some videos may not be accessible")
    
    # Create downloader
    downloader = NanairoDownloader(config)
    
    logger.info("Nanairo Downloader started")
    logger.info(f"Proxy: {config.get('proxy') or 'None'}")
    logger.info(f"Threads: {config.get('download_threads', 1)}")
    logger.info(f"Output dir: {config.get('output_dir', 'downloaded')}")
    
    # Run downloader
    # Priority: command line args > config file
    start_id = args.start if args.start is not None else config.get('start_id')
    end_id = args.end if args.end is not None else config.get('end_id')
    ids_list = config.get('ids', [])
    
    if args.ids:
        downloader.run_from_file(args.ids, args.failed)
    elif start_id is not None and end_id is not None:
        downloader.run(start_id, end_id, args.failed)
    elif ids_list:
        downloader.run_from_list(ids_list, args.failed)
    else:
        logger.error("Please specify either --start and --end, or --ids file, or configure in config.json")
        parser.print_help()
        return
    
    logger.info("Download task completed")


if __name__ == "__main__":
    main()
