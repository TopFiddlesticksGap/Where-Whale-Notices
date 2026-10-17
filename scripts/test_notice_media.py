import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from notice_media import convert_media, probe


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class MediaTests(unittest.TestCase):
    def test_gif_becomes_loopable_video(self):
        output = io.BytesIO()
        Image.new('RGB', (121, 81), 'blue').save(output, format='GIF', save_all=True,
            append_images=[Image.new('RGB', (121, 81), 'red')], duration=200, loop=0)
        with tempfile.TemporaryDirectory() as folder:
            result = convert_media(output.getvalue(), Path(folder), 'test')
            self.assertEqual(result['kind'], 'gif')
            self.assertEqual(result['width'] % 2, 0)
            self.assertEqual(probe(Path(folder) / result['file'])['streams'][0]['codec_name'], 'h264')

    def test_mp4_and_mp3(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for extension, source in [('mp4', 'color=c=blue:s=800x400:d=0.3'), ('mp3', 'sine=frequency=440:duration=0.3')]:
                path = root / ('input.' + extension)
                subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i', source, str(path)], check=True)
                result = convert_media(path.read_bytes(), root, 'output-' + extension)
                self.assertEqual(result['kind'], 'video' if extension == 'mp4' else 'audio')
                self.assertLessEqual(result['width'], 720)

    def test_arbitrary_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(subprocess.CalledProcessError):
                convert_media(b'not a media file', Path(folder), 'bad')

    def test_long_audio_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'long.mp3'
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=601', str(path)], check=True)
            with self.assertRaises(ValueError):
                convert_media(path.read_bytes(), Path(folder), 'bad')
