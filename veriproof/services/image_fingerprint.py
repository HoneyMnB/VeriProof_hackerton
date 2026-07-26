"""Image fingerprinting and original-image comparison.

This module keeps proof-oriented byte hashes separate from perceptual hashes
used for visual similarity checks. It performs no I/O and does not depend on
database state, so callers can use it before deciding whether to persist data.
"""
from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageOps

CANONICAL_MAX_SIDE = 512
PHASH_SIZE = 32
HASH_SIDE = 8

T_IDENTICAL_VISUAL = 2
T_DERIVED_VISUAL = 10
T_TILE = 8
MIN_TILE_HITS = 2

ImageMatchVerdict = Literal[
    "exact_file",
    "same_pixels",
    "visually_similar",
    "possible_partial",
    "different",
]


@dataclass(frozen=True)
class ImageFingerprint:
    """Stable hash bundle extracted from one image."""

    file_sha256: str
    canonical_sha256: str
    width: int
    height: int
    image_format: str
    ahash: str
    dhash: str
    phash: str
    whash: str
    tile_phash: tuple[str, ...]


@dataclass(frozen=True)
class ImageFingerprintMatch:
    """Comparison result for one candidate against one original image."""

    verdict: ImageMatchVerdict
    file_sha256_match: bool
    canonical_sha256_match: bool
    phash_distance: int
    dhash_distance: int
    whash_distance: int
    tile_hits: int
    orientation_index: int | None

    @property
    def is_match(self) -> bool:
        return self.verdict != "different"


class FingerprintService:
    """Compute generic SHA-256 values and image-specific fingerprints."""

    def sha256(self, content: bytes) -> str:
        """Return the hex SHA-256 of any bytes payload."""
        return hashlib.sha256(content).hexdigest()

    def upload_sha256(self, upload) -> str:
        """Return SHA-256 for a Django upload without consuming it for callers."""
        hasher = hashlib.sha256()
        for chunk in upload.chunks():
            hasher.update(chunk)
        upload.seek(0)
        return hasher.hexdigest()

    def upload_manifest_sha256(self, uploads) -> str:
        """Return one proof hash for an ordered set of uploaded work files."""
        if not isinstance(uploads, (list, tuple)):
            uploads = (uploads,)
        file_hashes = [self.upload_sha256(upload) for upload in uploads]
        return self.manifest_sha256(file_hashes)

    def content_manifest_sha256(self, contents) -> str:
        """Return one proof hash for an ordered set of byte payloads."""
        content_hashes = [self.sha256(content) for content in contents]
        return self.manifest_sha256(content_hashes)

    @staticmethod
    def manifest_sha256(file_hashes) -> str:
        """Collapse ordered file SHA-256 values into the persisted work hash."""
        hashes = tuple(file_hashes)
        if not hashes:
            return hashlib.sha256(b"").hexdigest()
        if len(hashes) == 1:
            return hashes[0]
        return hashlib.sha256("\n".join(hashes).encode("ascii")).hexdigest()

    def create(self, image_bytes: bytes) -> ImageFingerprint:
        image = _load_image(image_bytes)
        return ImageFingerprint(
            file_sha256=self.sha256(image_bytes),
            canonical_sha256=hashlib.sha256(_canonical_bytes(image)).hexdigest(),
            width=image.width,
            height=image.height,
            image_format=image.format or "RAW",
            ahash=_hex64(_ahash(image)),
            dhash=_hex64(_dhash(image)),
            phash=_hex64(_phash(image)),
            whash=_hex64(_whash(image)),
            tile_phash=tuple(_hex64(value) for value in _tile_phash(image)),
        )

    def compare(
        self,
        original: ImageFingerprint,
        candidate: ImageFingerprint,
        *,
        candidate_image_bytes: bytes | None = None,
    ) -> ImageFingerprintMatch:
        """Compare two fingerprints.

        ``candidate_image_bytes`` enables rotation/mirror-aware pHash matching.
        Without it, comparison still works against the candidate's stored pHash.
        """
        file_match = candidate.file_sha256 == original.file_sha256
        canonical_match = candidate.canonical_sha256 == original.canonical_sha256
        if file_match:
            return _match("exact_file", file_match, canonical_match, 0, 0, 0, 0, 0)
        if canonical_match:
            return _match("same_pixels", file_match, canonical_match, 0, 0, 0, 0, 0)

        original_phash = int(original.phash, 16)
        orientation_hashes = [int(candidate.phash, 16)]
        if candidate_image_bytes is not None:
            orientation_hashes = _orientation_phashes(_load_image(candidate_image_bytes))
        phash_distance, orientation = min(
            (_hamming(value, original_phash), index)
            for index, value in enumerate(orientation_hashes)
        )
        dhash_distance = _hamming(int(candidate.dhash, 16), int(original.dhash, 16))
        whash_distance = _hamming(int(candidate.whash, 16), int(original.whash, 16))
        tile_hits = _tile_hits(candidate.tile_phash, original.tile_phash)

        if phash_distance <= T_IDENTICAL_VISUAL:
            verdict: ImageMatchVerdict = (
                "same_pixels" if orientation == 0 else "visually_similar"
            )
        elif phash_distance <= T_DERIVED_VISUAL:
            verdict = "visually_similar"
        elif tile_hits >= MIN_TILE_HITS:
            verdict = "possible_partial"
        else:
            verdict = "different"

        return _match(
            verdict,
            file_match,
            canonical_match,
            phash_distance,
            dhash_distance,
            whash_distance,
            tile_hits,
            orientation,
        )

    def compare_images(
        self, original_image_bytes: bytes, candidate_image_bytes: bytes
    ) -> ImageFingerprintMatch:
        original = self.create(original_image_bytes)
        candidate = self.create(candidate_image_bytes)
        return self.compare(
            original,
            candidate,
            candidate_image_bytes=candidate_image_bytes,
        )


def get_fingerprint_service() -> FingerprintService:
    return FingerprintService()


def _match(
    verdict: ImageMatchVerdict,
    file_match: bool,
    canonical_match: bool,
    phash_distance: int,
    dhash_distance: int,
    whash_distance: int,
    tile_hits: int,
    orientation: int | None,
) -> ImageFingerprintMatch:
    return ImageFingerprintMatch(
        verdict=verdict,
        file_sha256_match=file_match,
        canonical_sha256_match=canonical_match,
        phash_distance=phash_distance,
        dhash_distance=dhash_distance,
        whash_distance=whash_distance,
        tile_hits=tile_hits,
        orientation_index=orientation,
    )


def _load_image(image_bytes: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(image_bytes))
    image.load()
    return ImageOps.exif_transpose(image)


def _canonical_bytes(image: Image.Image) -> bytes:
    rgb = image.convert("RGB")
    width, height = rgb.size
    scale = CANONICAL_MAX_SIDE / max(width, height)
    if scale < 1:
        rgb = rgb.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )
    return (
        rgb.width.to_bytes(4, "big")
        + rgb.height.to_bytes(4, "big")
        + rgb.tobytes()
    )


def _gray_values(image: Image.Image, size: tuple[int, int]) -> list[float]:
    gray = image.convert("L").resize(size, Image.Resampling.LANCZOS)
    return [float(value) for value in gray.getdata()]


def _bits_to_int(bits: list[bool]) -> int:
    out = 0
    for bit in bits:
        out = (out << 1) | int(bit)
    return out


def _hex64(value: int) -> str:
    return f"{value:016x}"


def _ahash(image: Image.Image) -> int:
    values = _gray_values(image, (HASH_SIDE, HASH_SIDE))
    avg = sum(values) / len(values)
    return _bits_to_int([value > avg for value in values])


def _dhash(image: Image.Image) -> int:
    values = _gray_values(image, (HASH_SIDE + 1, HASH_SIDE))
    bits = []
    for row in range(HASH_SIDE):
        start = row * (HASH_SIDE + 1)
        for col in range(HASH_SIDE):
            bits.append(values[start + col + 1] > values[start + col])
    return _bits_to_int(bits)


def _phash(image: Image.Image) -> int:
    matrix = _gray_matrix(image, PHASH_SIZE)
    coeffs = _low_frequency_dct(matrix)
    flat = [
        coeffs[row][col]
        for row in range(HASH_SIDE)
        for col in range(HASH_SIDE)
    ]
    median = _median(flat[1:])
    bits = [value > median for value in flat]
    bits[0] = False
    return _bits_to_int(bits)


def _whash(image: Image.Image) -> int:
    values = _gray_matrix(image, 64)
    for _ in range(3):
        next_side = len(values) // 2
        values = [
            [
                (
                    values[row * 2][col * 2]
                    + values[row * 2][col * 2 + 1]
                    + values[row * 2 + 1][col * 2]
                    + values[row * 2 + 1][col * 2 + 1]
                )
                / 4
                for col in range(next_side)
            ]
            for row in range(next_side)
        ]
    flat = [value for row in values for value in row]
    return _bits_to_int([value > _median(flat) for value in flat])


def _tile_phash(image: Image.Image, grid: int = 3, overlap: float = 0.25) -> list[int]:
    rgb = image.convert("RGB")
    width, height = rgb.size
    tile_width = width / grid
    tile_height = height / grid
    overlap_width = tile_width * overlap
    overlap_height = tile_height * overlap
    hashes = []
    for row in range(grid):
        for col in range(grid):
            left = max(0, int(col * tile_width - overlap_width))
            top = max(0, int(row * tile_height - overlap_height))
            right = min(width, int((col + 1) * tile_width + overlap_width))
            bottom = min(height, int((row + 1) * tile_height + overlap_height))
            hashes.append(_phash(rgb.crop((left, top, right, bottom))))
    return hashes


def _orientation_phashes(image: Image.Image) -> list[int]:
    base = image.convert("L")
    hashes = []
    for flip in (False, True):
        variant = ImageOps.mirror(base) if flip else base
        for turns in range(4):
            hashes.append(_phash(variant.rotate(90 * turns, expand=True)))
    return hashes


def _tile_hits(candidate_tiles: tuple[str, ...], original_tiles: tuple[str, ...]) -> int:
    candidate = [int(value, 16) for value in candidate_tiles]
    original = [int(value, 16) for value in original_tiles]
    matched = {
        index
        for probe in candidate
        for index, stored in enumerate(original)
        if _hamming(probe, stored) <= T_TILE
    }
    return len(matched)


def _gray_matrix(image: Image.Image, side: int) -> list[list[float]]:
    values = _gray_values(image, (side, side))
    return [values[index : index + side] for index in range(0, len(values), side)]


def _dct_basis(k: int, n: int) -> list[float]:
    scale = math.sqrt(1 / n) if k == 0 else math.sqrt(2 / n)
    return [
        scale * math.cos(math.pi * (2 * index + 1) * k / (2 * n))
        for index in range(n)
    ]


_DCT32_LOW = tuple(tuple(_dct_basis(k, PHASH_SIZE)) for k in range(HASH_SIDE))


def _low_frequency_dct(matrix: list[list[float]]) -> list[list[float]]:
    temp = [
        [
            sum(_DCT32_LOW[row][i] * matrix[i][col] for i in range(PHASH_SIZE))
            for col in range(PHASH_SIZE)
        ]
        for row in range(HASH_SIDE)
    ]
    return [
        [
            sum(temp[row][j] * _DCT32_LOW[col][j] for j in range(PHASH_SIZE))
            for col in range(HASH_SIDE)
        ]
        for row in range(HASH_SIDE)
    ]


def _median(values: list[float]) -> float:
    sorted_values = sorted(values)
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()
