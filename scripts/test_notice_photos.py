import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from notice_photos import allowed_host, optimize, publish_photo


class PhotoTests(unittest.TestCase):
    @staticmethod
    def image_bytes(mode="RGB", size=(2400, 1200), format="JPEG", exif=None):
        output = io.BytesIO()
        image = Image.new(mode, size, (40, 120, 210, 128) if mode == "RGBA" else (40, 120, 210))
        image.save(output, format=format, **({"exif": exif} if exif else {}))
        return output.getvalue()

    def test_large_photo_is_resized(self):
        output, extension = optimize(self.image_bytes())
        with Image.open(io.BytesIO(output)) as image:
            self.assertEqual(image.size, (1600, 800))
        self.assertEqual(extension, "jpg")
        self.assertLess(len(output), 4 * 1024 * 1024)

    def test_alpha_is_preserved(self):
        output, extension = optimize(self.image_bytes("RGBA", (200, 100), "PNG"))
        with Image.open(io.BytesIO(output)) as image:
            self.assertEqual(image.mode, "RGBA")
        self.assertEqual(extension, "png")

    def test_small_photo_is_not_upscaled(self):
        output, _ = optimize(self.image_bytes(size=(64, 64)))
        with Image.open(io.BytesIO(output)) as image:
            self.assertEqual(image.size, (64, 64))

    def test_phone_orientation_is_corrected(self):
        exif = Image.Exif(); exif[274] = 6
        output, _ = optimize(self.image_bytes(size=(200, 100), exif=exif))
        with Image.open(io.BytesIO(output)) as image:
            self.assertEqual(image.size, (100, 200))
            self.assertNotIn(274, image.getexif())

    def test_invalid_photo_is_rejected(self):
        with self.assertRaises(ValueError):
            optimize(b"not an image")

    def test_oversized_upload_is_rejected(self):
        with self.assertRaises(ValueError):
            optimize(b"0" * (20 * 1024 * 1024 + 1))

    def test_redirect_destination_is_restricted(self):
        self.assertTrue(allowed_host("https://github.com/user-attachments/assets/1"))
        self.assertTrue(allowed_host("https://private-user-images.githubusercontent.com/image.png"))
        self.assertTrue(allowed_host("https://github-production-user-asset-6210df.s3.amazonaws.com/photo.png"))
        self.assertFalse(allowed_host("https://attacker.s3.amazonaws.com/photo.png"))
        self.assertFalse(allowed_host("https://github-production-user-asset-6210df.s3.amazonaws.com.attacker.com/photo.png"))
        self.assertFalse(allowed_host("http://github.com/photo.png"))
        self.assertFalse(allowed_host("https://example.com/photo.png"))
        self.assertFalse(allowed_host("https://githubusercontent.com.attacker.com/photo.png"))

    def test_existing_processed_photo_is_reused(self):
        url = "https://github.com/user-attachments/assets/1"
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"notice-3-1-{digest}.jpg"
            path.write_bytes(self.image_bytes(size=(64, 64)))
            result = publish_photo(url, "TopFiddlesticksGap/Where-Whale-Notices", 3, 1, Path(folder))
            self.assertTrue(result.endswith(path.name))


if __name__ == "__main__":
    unittest.main()
