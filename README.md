# MGStage Video Downloader

Download videos from MGStage with multi-threading, HTTP proxy support, and automatic decryption via jav-it.

## Requirements

- Python 3.8+
- jav-it.exe (place in the same directory)

```bash
pip install -r requirements.txt
```

## Configuration

Edit `config.json`:

```json
{
    "uid": "your-uid",
    "device_id": "your-device-id",
    "shop_id": "prestigebb",
    "quality": "high",
    "proxy": "http://127.0.0.1:7890",
    "download_threads": 1,
    "output_dir": "downloaded",
    "player_version": "1.2.3",
    "mgs_username": "your-mgs-username",
    "mgs_password": "your-mgs-password",
    "jav_it_path": "./jav-it.exe"
}
```

### Configuration Options

| Option | Description |
|--------|-------------|
| `uid` | User ID (capture from MGS Player client) |
| `device_id` | Device ID (capture from MGS Player client) |
| `shop_id` | Shop ID, default: prestigebb |
| `quality` | Video quality: high/medium/low |
| `proxy` | HTTP proxy address |
| `download_threads` | Number of download threads |
| `output_dir` | Download output directory |
| `player_version` | Player version string |
| `mgs_username` | MGStage username (for jav-it decryption) |
| `mgs_password` | MGStage password (for jav-it decryption) |
| `jav_it_path` | Path to jav-it.exe |

## How to Get UID and Device ID

1. Install and open the official MGS Player desktop application
2. Use a network capture tool (e.g., Fiddler, Charles, or browser DevTools)
3. Capture any API request to `mgsplayer-api.mgstage.jp`
4. Look for these headers in the request:
   - `uid`: Your user ID
   - `device-id`: Your device ID

## Usage

### 1. Prepare ID List

Create `ids.txt` file with one video ID per line:

```
ABF-249
KIT-012
PPX-033
```

### 2. Run Downloader

```bash
python mgstage_downloader.py
```

### Command Line Arguments

```bash
# Specify config file
python mgstage_downloader.py -c config.json

# Specify ID file
python mgstage_downloader.py -i ids.txt

# Specify failed ID output file
python mgstage_downloader.py -f failed_ids.txt

# Use proxy (override config)
python mgstage_downloader.py -p http://127.0.0.1:7890

# Set thread count (override config)
python mgstage_downloader.py -t 5

# Set output directory (override config)
python mgstage_downloader.py -o ./videos
```

## Output

- Downloaded files are saved to `downloaded` directory (or configured directory)
- File naming format:
  - Encrypted: `{CID}_video.mp4` and `{CID}_audio.mp4`
  - Decrypted: `{CID}.mkv`
- Failed IDs are saved to `failed_ids.txt`
- Successfully processed IDs are automatically removed from `ids.txt`

## Download Workflow

The downloader follows this process for each video ID:

### Step 1: Search for PID

```
GET https://mgsplayer-api.mgstage.jp/api/v1/list/monthly/search?word={CID}&size=100&offset=0&sort=new&shop_id=prestigebb

Headers:
  uid: {your-uid}
  device-id: {your-device-id}
  player-version: 1.2.3
  player-type: 1
```

Response contains the `pid` (product ID) needed for the next step.

### Step 2: Get Play Info

```
GET https://mgsplayer-api.mgstage.jp/api/v1/detail/play/monthly/content

Headers:
  uid: {your-uid}
  device-id: {your-device-id}
  pid: {pid-from-step-1}
  quality: high
```

Response contains:
- `manifest_url`: MPD manifest URL with signed CloudFront policy
- `title`: Video title
- `actress`: Actress names
- `genres`: Genre tags

### Step 3: Parse MPD Manifest

Download the MPD manifest file and extract video/audio URLs directly from `<BaseURL>` elements:

```xml
<Representation bandwidth="6000000">
  <BaseURL><![CDATA[https://dash-download.mgstage.com/.../xxx_6000.mp4?Policy=...]]></BaseURL>
</Representation>
```

The script automatically selects the highest available bitrate.

### Step 4: Download Files

Download video and audio files separately with:
- Resume support (Range header)
- Dynamic progress bar display
- Speed and ETA calculation

### Step 5: Decrypt with jav-it

After downloading, automatically decrypt using jav-it:

```bash
./jav-it.exe decrypt -i {CID}_video.mp4 -o {CID}.mkv -t mgs -s prestigebb
```

Environment variables `MGS_USERNAME` and `MGS_PASSWORD` are automatically set from config.

jav-it will:
- Detect the corresponding audio file (`{CID}_audio.mp4`)
- Decrypt both video and audio
- Merge them into a single MKV file

### Step 6: Cleanup

- Successfully processed IDs are removed from `ids.txt`
- Failed IDs are appended to `failed_ids.txt`

## Progress Display

The downloader shows a dynamic progress bar:

```
[ABF-249 video] |████████████░░░░░░░░░░░░░░░░░░| 40.5% 1.22GB/3.01GB 25.3MB/s ETA:72s
```

## Notes

1. Valid `uid` and `device_id` are required (capture from MGS Player client)
2. Valid `mgs_username` and `mgs_password` are required for jav-it decryption
3. The signed URLs have an expiration time, so download promptly
4. Proxy is recommended for users outside Japan
5. Keep thread count to 1 for stability (multi-threading may cause issues with jav-it)
6. Place `jav-it.exe` in the same directory as the script
