"""첨부 GIF/MP4/MP3를 모바일 재생용 파일로 변환합니다. FFmpeg는 게시 작업에서만 사용합니다."""
from __future__ import annotations
import hashlib
import io
import json
import math
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from notice_photos import ImageRedirects, MAX_DOWNLOAD, allowed_host


def probe(path: Path) -> dict:
    result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_format', '-show_streams', '-of', 'json', str(path)],
        capture_output=True, timeout=20, check=True)
    return json.loads(result.stdout)


def convert_media(data: bytes, directory: Path, stem: str) -> dict:
    if not data or len(data) > MAX_DOWNLOAD:
        raise ValueError('첨부 파일은 20MB 이하로 올려주세요.')
    gif = data[:6] in {b'GIF87a', b'GIF89a'}
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / 'source'
        source.write_bytes(data)
        info = probe(source)
        duration = float(info.get('format', {}).get('duration', 0))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('재생 길이를 확인할 수 없는 파일입니다.')
        video = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
        audio = next((s for s in info.get('streams', []) if s.get('codec_type') == 'audio'), None)
        is_mp4 = b'ftyp' == data[4:8]
        is_mp3 = audio is not None and audio.get('codec_name') == 'mp3'
        if not (gif or is_mp4 or is_mp3):
            raise ValueError('사진, GIF, MP4, MP3 파일만 올려주세요.')
        kind = 'gif' if gif else 'video' if video is not None and is_mp4 else 'audio'
        if kind != 'audio' and video is None:
            raise ValueError('영상 트랙이 없습니다.')
        if kind == 'audio' and not is_mp3:
            raise ValueError('오디오는 MP3로 올려주세요.')
        if duration > (120 if kind == 'audio' else 60):
            raise ValueError('영상/GIF는 60초, MP3는 120초 이하로 올려주세요.')
        extension = 'mp3' if kind == 'audio' else 'mp4'
        target = directory / (stem + '.' + extension)
        command = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe', '-i', str(source), '-map_metadata', '-1']
        if kind == 'audio':
            command += ['-map', '0:a:0', '-vn', '-ac', '2', '-ar', '44100', '-c:a', 'libmp3lame', '-b:a', '128k']
        else:
            width, height = int(video.get('width', 0)), int(video.get('height', 0))
            if width <= 0 or height <= 0 or width * height > 40_000_000:
                raise ValueError('영상 해상도가 올바르지 않습니다.')
            command += ['-map', '0:v:0', '-vf', "scale=w='min(720,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2:out_color_matrix=bt709,pad=ceil(iw/2)*2:ceil(ih/2)*2", '-r', '24',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '25', '-maxrate', '1200k', '-bufsize', '2400k', '-pix_fmt', 'yuv420p', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-movflags', '+faststart']
            command += ['-an'] if gif else ['-map', '0:a:0?', '-c:a', 'aac', '-b:a', '96k', '-ac', '2']
        try:
            subprocess.run(command + [str(target)], capture_output=True, timeout=120, check=True)
            if target.stat().st_size > 12 * 1024 * 1024:
                raise ValueError('변환된 파일이 너무 큽니다. 더 짧은 파일로 올려주세요.')
            output = probe(target)
            stream = next((s for s in output['streams'] if s['codec_type'] == 'video'), {})
            return {'kind': kind, 'width': stream.get('width', 0), 'height': stream.get('height', 0), 'duration': duration, 'file': target.name}
        except Exception:
            target.unlink(missing_ok=True)
            raise


def publish_attachment(url: str, repository: str, number: int, index: int, folder: Path | None = None) -> tuple[str, dict | str]:
    if not allowed_host(url):
        raise ValueError('첨부 파일 주소를 확인해주세요.')
    directory = folder or Path('images/generated')
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"media-v1-{number}-{index}-{hashlib.sha256(url.encode()).hexdigest()[:16]}"
    photo_cache = next((p for p in directory.glob(stem + '.*') if p.suffix in {'.png', '.jpg'}), None)
    if photo_cache is not None:
        return 'image', f'https://raw.githubusercontent.com/{repository}/main/images/generated/{photo_cache.name}'
    manifest = directory / (stem + '.json')
    if manifest.exists():
        result = json.loads(manifest.read_text())
        if (directory / result['file']).exists():
            result['url'] = f"https://raw.githubusercontent.com/{repository}/main/images/generated/{result.pop('file')}"
            return result['kind'], result
    opener = urllib.request.build_opener(ImageRedirects())
    request = urllib.request.Request(url, headers={'User-Agent': 'WhereWhale-NoticePublisher'})
    with opener.open(request, timeout=30) as response:
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError('첨부 파일은 20MB 이하로 올려주세요.')
    try:
        with Image.open(io.BytesIO(data)) as picture:
            photo = picture.format in {'PNG', 'JPEG', 'WEBP'}
    except (UnidentifiedImageError, OSError):
        photo = False
    if photo:
        # 기존 사진 캐시 경로와 변환 방식을 유지합니다.
        from notice_photos import optimize
        output, extension = optimize(data)
        target = directory / (stem + '.' + extension)
        target.write_bytes(output)
        return 'image', f'https://raw.githubusercontent.com/{repository}/main/images/generated/{target.name}'
    result = convert_media(data, directory, stem)
    manifest.write_text(json.dumps(result), encoding='utf-8')
    result['url'] = f"https://raw.githubusercontent.com/{repository}/main/images/generated/{result.pop('file')}"
    return result['kind'], result
