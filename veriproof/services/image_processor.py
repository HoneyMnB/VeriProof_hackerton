"""ImageProcessor — Pillow-based thumbnail/watermark + SHA-256 hashing.

Architecture 4 contract. Pillow is the only hard image dependency (installed).
SPEC-001 implements all three methods.
"""
from __future__ import annotations

import hashlib
import io

from PIL import Image, ImageDraw, ImageFont

# SPEC-001 R5: maximum side of a thumbnail.
THUMBNAIL_MAX_SIDE = 512


class ImageProcessor:
    """Pure compute over image bytes (no I/O, fully deterministic)."""

    def __init__(self, default_thumbnail_size: tuple[int, int] = (512, 512)) -> None:
        self.default_thumbnail_size = default_thumbnail_size

    # --- Architecture 4 methods (SPEC-001) -----------------------------------
    def sha256(self, image_bytes: bytes) -> str:
        """Return the hex SHA-256 of the ORIGINAL image bytes. SPEC-001 R2.

        This hash is the permanent content fingerprint anchored on-chain.
        """
        return hashlib.sha256(image_bytes).hexdigest()

    def perceptual_hash(self, image_bytes: bytes) -> str:
        """이미지의 8×8 명암 지문을 만들어 검색 후보 선별에 사용한다.

        원본 SHA-256은 동일 파일 판정용이고, 이 값은 시각적으로 가까운 후보를
        빠르게 찾기 위한 보조 값이다. 디스크·네트워크 I/O 없이 결정적으로 계산한다.
        """
        image = Image.open(io.BytesIO(image_bytes)).convert("L").resize((8, 8))
        values = list(image.getdata())
        average = sum(values) / len(values)
        bits = "".join("1" if value >= average else "0" for value in values)
        return f"{int(bits, 2):016x}"

    def make_thumbnail(self, image_bytes: bytes, size: tuple[int, int]) -> bytes:
        """Generate a thumbnail PNG within ``size`` bounds. SPEC-001 R5.

        ``size`` is the (max_width, max_height) envelope; aspect ratio is
        preserved (Pillow ``thumbnail`` never upscales). RGBA input is
        composited onto a white background so the output PNG is safe for
        any downstream preview render.
        """
        img = Image.open(io.BytesIO(image_bytes))
        img = self._flatten_to_rgb(img)
        img.thumbnail(size)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def make_watermark(self, image_bytes: bytes, text: str) -> bytes:
        """Overlay a diagonal watermark text (the persistent free preview).

        SPEC-001 R5 / R15: the result MUST differ from the original bytes so
        the watermarked preview cannot substitute for the licensed original.
        """
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)
        # A watermark spanning the diagonal in a semi-transparent white box.
        font = self._load_font(size=max(12, img.size[0] // 16))
        text_bbox = draw.textbbox((0, 0), text, font=font)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        # Stamp the watermark repeatedly along the diagonal for coverage.
        step = max(text_w + 40, 80)
        for y in range(-text_h, img.size[1] + text_h, step):
            for x in range(-text_w, img.size[0] + text_w, step):
                draw.text((x, y), text, fill=(255, 255, 255, 90), font=font)
        watermarked = Image.alpha_composite(img, overlay).convert("RGB")
        buf = io.BytesIO()
        watermarked.save(buf, format="PNG")
        return buf.getvalue()

    # --- Internal helpers ----------------------------------------------------

    @staticmethod
    def _flatten_to_rgb(img: Image.Image) -> Image.Image:
        """Composite any mode onto a white RGB canvas (alpha-safe)."""
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            return Image.alpha_composite(background, rgba).convert("RGB")
        return img.convert("RGB")

    @staticmethod
    def _load_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
        """Load the default bitmap font, scaled when possible."""
        try:
            return ImageFont.load_default(size=size)  # Pillow >= 10.1
        except TypeError:  # pragma: no cover (Pillow < 10.1 fallback)
            return ImageFont.load_default()  # pragma: no cover


def get_image_processor() -> ImageProcessor:
    """Factory: build the default ImageProcessor."""
    return ImageProcessor()
