import io
import unittest

from PIL import Image

from backend.errors import ImageError
from backend.image_validation import prepare_for_model, safe_filename, validate_image
from tests.helpers import png_bytes


class ImageValidationTests(unittest.TestCase):
    def test_accepts_png_jpeg_webp(self):
        for fmt, name, mime in (("PNG", "a.png", "image/png"), ("JPEG", "a.jpg", "image/jpeg"),
                                ("WEBP", "a.webp", "image/webp")):
            with self.subTest(fmt=fmt):
                info = validate_image(png_bytes(fmt=fmt), name, mime)
                self.assertEqual(info["format"], fmt)
                self.assertEqual((info["width"], info["height"]), (200, 200))

    def test_rejects_empty_file(self):
        with self.assertRaises(ImageError):
            validate_image(b"", "a.png", "image/png")

    def test_rejects_non_image_content_with_image_name(self):
        with self.assertRaises(ImageError) as ctx:
            validate_image(b"MZ\x90\x00 this is really an executable", "diagram.png", "image/png")
        self.assertIn("could not be read", ctx.exception.message)

    def test_rejects_wrong_mime_and_extension(self):
        with self.assertRaises(ImageError):
            validate_image(png_bytes(), "a.png", "application/pdf")
        with self.assertRaises(ImageError):
            validate_image(png_bytes(), "run.exe", "image/png")

    def test_rejects_unsupported_real_format_even_if_named_png(self):
        with self.assertRaises(ImageError):
            validate_image(png_bytes(fmt="GIF", mode="P"), "a.png", "image/png")

    def test_rejects_oversized_file(self):
        with self.assertRaises(ImageError) as ctx:
            validate_image(png_bytes(), "a.png", "image/png", max_mb=0.00001)
        self.assertIn("too large", ctx.exception.message)

    def test_rejects_tiny_image(self):
        with self.assertRaises(ImageError):
            validate_image(png_bytes(size=(10, 10)), "a.png", "image/png")

    def test_rejects_truncated_image(self):
        data = png_bytes(size=(400, 400))
        with self.assertRaises(ImageError):
            validate_image(data[: len(data) // 2], "a.png", "image/png")

    def test_safe_filename_strips_paths(self):
        self.assertEqual(safe_filename("../../etc/passwd.png"), "passwd.png")
        self.assertEqual(safe_filename("C:\\Users\\me\\flow chart.png"), "flow chart.png")
        self.assertEqual(safe_filename(None), "upload")
        self.assertLessEqual(len(safe_filename("x" * 500 + ".png")), 100)

    def test_prepare_downsizes_large_and_flattens_alpha(self):
        big = png_bytes(size=(3000, 1500))
        out = Image.open(io.BytesIO(prepare_for_model(big, 1280)))
        self.assertEqual(max(out.size), 1280)
        self.assertEqual(out.size, (1280, 640))
        rgba = png_bytes(size=(100, 100), color=(255, 0, 0, 0), mode="RGBA")
        flat = Image.open(io.BytesIO(prepare_for_model(rgba)))
        self.assertEqual(flat.mode, "RGB")
        self.assertEqual(flat.getpixel((5, 5)), (255, 255, 255))  # transparent -> white


if __name__ == "__main__":
    unittest.main()
