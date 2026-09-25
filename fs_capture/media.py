"""Image inspection through the already-required FFmpeg tools, no Python dependencies."""
import json
from pathlib import Path
import subprocess
from .core import CaptureError, capture, tool


def image_pixels(path, ffmpeg='ffmpeg', ffprobe='ffprobe', mask=False):
    path = Path(path)
    info = json.loads(capture([tool(ffprobe), '-v', 'error', '-show_streams', '-of', 'json', str(path)]))
    streams = info.get('streams', [])
    if len(streams) != 1 or streams[0].get('codec_type') != 'video':
        raise CaptureError(f'画像を読み取れません: {path.name}')
    stream = streams[0]
    width, height = stream['width'], stream['height']
    if not 0 < width * height <= 64_000_000 or stream.get('nb_frames', '1') not in ('1', 'N/A'):
        raise CaptureError('画像サイズまたはフレーム数が未対応です。')
    result = subprocess.run([tool(ffmpeg), '-nostdin', '-v', 'error', '-xerror', '-err_detect', 'explode',
                             '-noautorotate', '-i', str(path), '-frames:v', '1', '-f', 'rawvideo',
                             '-pix_fmt', 'rgba', 'pipe:1'], capture_output=True, timeout=120)
    pixels = result.stdout
    if result.returncode or len(pixels) != width * height * 4:
        raise CaptureError(f'画像本体のデコードに失敗: {path.name}')
    data = {'width': width, 'height': height}
    if mask:
        if stream.get('codec_name') != 'png':
            raise CaptureError('取り込みマスクはPNG専用です。')
        red, green, blue, alpha = (pixels[i::4] for i in range(4))
        if red != green or red != blue or set(alpha) != {255} or not set(red).issubset({0, 255}):
            raise CaptureError('マスクは不透明な白黒二値PNGが必要です。色付き・中間値・透過alphaは拒否します。')
        excluded = red.count(0) / len(red)
        data.update(excludedFraction=excluded, warnings=['除外率が極端です。白黒の反転・対象の欠落を目視してください。']
                    if excluded < .01 or excluded > .95 else [])
    return data


def thumbnail(path, ffmpeg='ffmpeg', mask=False):
    argv = [tool(ffmpeg), '-nostdin', '-v', 'error', '-i', str(path), '-frames:v', '1',
            '-vf', 'scale=320:-1:flags=neighbor' if mask else 'scale=320:-1',
            '-f', 'image2pipe', '-c:v', 'png' if mask else 'mjpeg', 'pipe:1']
    result = subprocess.run(argv, capture_output=True, timeout=60)
    if result.returncode or not result.stdout:
        raise CaptureError(f'サムネイル生成失敗: {Path(path).name}')
    return result.stdout
