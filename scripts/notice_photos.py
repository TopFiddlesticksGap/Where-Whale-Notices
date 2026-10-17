"""공지 첨부 사진을 다운로드·회전 보정·축소하여 게임에 맞는 공개 파일로 저장합니다."""
from __future__ import annotations

import hashlib
import io
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_DOWNLOAD = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
DISPLAY_SIDE = 1600
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def allowed_host(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and not parsed.username and not parsed.password and (
        host == "github.com" or host.endswith(".githubusercontent.com") or host.endswith(".blob.core.windows.net"))


class ImageRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        if not allowed_host(newurl):
            raise ValueError("첨부 사진의 다운로드 주소를 확인해주세요.")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def optimize(data: bytes) -> tuple[bytes, str]:
    if not data or len(data) > MAX_DOWNLOAD:
        raise ValueError("사진은 20MB 이하로 첨부해주세요.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("PNG, JPG, WEBP 사진을 사용해주세요.")
            if image.width * image.height > MAX_PIXELS:
                raise ValueError("사진 해상도가 너무 큽니다. 4천만 화소 이하로 준비해주세요.")
            picture = ImageOps.exif_transpose(image)
            picture.thumbnail((DISPLAY_SIDE, DISPLAY_SIDE), Image.Resampling.LANCZOS)
            alpha = picture.mode in {"RGBA", "LA"} or "transparency" in picture.info
            output = io.BytesIO()
            if alpha:
                picture.convert("RGBA").save(output, format="PNG", optimize=True)
                if output.tell() <= 4 * 1024 * 1024:
                    return output.getvalue(), "png"
                rgba = picture.convert("RGBA")
                background = Image.new("RGB", picture.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                picture = background
                output = io.BytesIO()
            picture.convert("RGB").save(output, format="JPEG", quality=88, optimize=True)
            if output.tell() > 4 * 1024 * 1024:
                raise ValueError("사진을 줄이지 못했습니다. 다른 사진을 첨부해주세요.")
            return output.getvalue(), "jpg"
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as error:
        raise ValueError("사진 파일을 읽지 못했습니다. 다른 PNG/JPG 사진을 첨부해주세요.") from error


def publish_photo(url: str, repository: str, number: int, index: int, folder: Path | None = None) -> str:
    if not allowed_host(url):
        raise ValueError("사진의 공개 주소를 확인해주세요.")
    directory = folder or Path("images/generated")
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    stem = f"notice-{number}-{index}-{digest}"
    cached = next((path for path in directory.glob(stem + ".*") if path.suffix in {".png", ".jpg"}), None)
    if cached is None:
        request = urllib.request.Request(url, headers={"User-Agent": "WhereWhale-NoticePublisher"})
        opener = urllib.request.build_opener(ImageRedirects())
        with opener.open(request, timeout=30) as response:
            payload = response.read(MAX_DOWNLOAD + 1)
        output, extension = optimize(payload)
        cached = directory / (stem + "." + extension)
        cached.write_bytes(output)
    return f"https://raw.githubusercontent.com/{repository}/main/images/generated/{cached.name}"
