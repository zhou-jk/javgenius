# Nanairo Video Downloader

从 nanairo.co 下载视频的工具，使用 N_m3u8DL-CLI 进行 m3u8 流下载。

## 功能特点

- 支持自定义 Cookie 认证
- 支持 HTTP 代理
- 支持 ID 范围下载
- 支持从文件读取 ID 列表
- 自动选择最高码率视频
- 多线程下载支持
- 自动提取视频标题作为文件名

## 依赖项

### Python 依赖

```bash
pip install -r requirements.txt
```

### 外部工具

需要下载 [N_m3u8DL-CLI](https://github.com/nilaoda/N_m3u8DL-CLI/releases)，放置到脚本同目录或配置文件中指定路径。

## 配置

编辑 `config.json`：

```json
{
    "cookie": "your_cookie_here",
    "proxy": "http://127.0.0.1:7890",
    "output_dir": "downloaded",
    "download_threads": 1,
    "language": "ja",
    "n_m3u8dl_path": "./N_m3u8DL-CLI.exe",
    "n_m3u8dl_args": []
}
```

### 配置说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `cookie` | 网站 Cookie，从浏览器中复制 | 必填 |
| `proxy` | HTTP 代理地址 | 空（不使用代理） |
| `output_dir` | 下载输出目录 | `downloaded` |
| `download_threads` | 下载线程数 | `1` |
| `language` | 网站语言 (ja/en) | `ja` |
| `n_m3u8dl_path` | N_m3u8DL-CLI 路径 | `./N_m3u8DL-CLI.exe` |
| `n_m3u8dl_args` | N_m3u8DL-CLI 额外参数 | `[]` |

### 获取 Cookie

1. 在浏览器中登录 nanairo.co
2. 打开开发者工具 (F12)
3. 切换到 Network 标签
4. 刷新页面，找到任意请求
5. 在请求头中复制完整的 Cookie 值

## 使用方法

### 按 ID 范围下载

```bash
# 下载 ID 5950 到 5960 的视频
python nanairo_downloader.py -s 5950 -e 5960

# 使用代理
python nanairo_downloader.py -s 5950 -e 5960 -p http://127.0.0.1:7890

# 指定输出目录
python nanairo_downloader.py -s 5950 -e 5960 -o /path/to/output
```

### 从文件读取 ID 列表

创建 `ids.txt` 文件，每行一个 ID：

```
5950
5951
5952
```

然后运行：

```bash
python nanairo_downloader.py -i ids.txt
```

### 命令行参数

| 参数 | 说明 |
|------|------|
| `-c, --config` | 配置文件路径，默认 `config.json` |
| `-s, --start` | 起始视频 ID |
| `-e, --end` | 结束视频 ID |
| `-i, --ids` | ID 列表文件路径 |
| `-f, --failed` | 失败 ID 保存文件，默认 `failed_ids.txt` |
| `-p, --proxy` | HTTP 代理（覆盖配置文件） |
| `-t, --threads` | 下载线程数（覆盖配置文件） |
| `-o, --output` | 输出目录（覆盖配置文件） |
| `--cookie` | Cookie 字符串（覆盖配置文件） |

## 输出

- 视频文件保存在 `output_dir` 目录
- 下载日志保存在 `nanairo_downloader.log`
- 失败的 ID 保存在 `failed_ids.txt`

## 注意事项

1. 需要有效的网站账号和 Cookie
2. Cookie 会过期，需要定期更新
3. 建议使用代理以获得更好的下载速度
4. N_m3u8DL-CLI 会自动选择最高码率的视频流
