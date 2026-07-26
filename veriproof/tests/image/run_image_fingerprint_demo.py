"""원본 이미지 bytes의 SHA-256 값을 확인하는 매우 단순한 수동 테스트.

프로젝트 루트에서 실행:
    python veriproof/tests/image/run_image_fingerprint_demo.py
"""
from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path("/Users/gamdodo/workspace/VeriProof_hackerton")
VERIPROOF_ROOT = PROJECT_ROOT / "veriproof"

sys.path.insert(0, str(VERIPROOF_ROOT))

from services.image_fingerprint import FingerprintService  # noqa: E402


def main() -> None:
    original_bytes = make_original_image()

    # 사용자가 직접 저장/온체인 전송할 값: 원본 이미지 bytes의 SHA-256.
    expected_sha256 = hashlib.sha256(original_bytes).hexdigest()

    service = FingerprintService()
    fingerprint = service.create(original_bytes)

    print("original image hash check")
    print(f"  expected_sha256 {expected_sha256}")
    print(f"  service_sha256  {fingerprint.file_sha256}")
    print(f"  hash_length     {len(fingerprint.file_sha256)}")
    print(f"  matched         {fingerprint.file_sha256 == expected_sha256}")

    assert fingerprint.file_sha256 == expected_sha256


def make_original_image() -> bytes:
    # 파일 I/O 없이 테스트용 원본 이미지를 메모리에서 만든다.
    image = Image.new("RGB", (320, 240), (238, 239, 232))
    draw = ImageDraw.Draw(image)
    draw.rectangle((28, 32, 136, 160), fill=(213, 48, 52))
    draw.ellipse((172, 44, 286, 158), fill=(45, 112, 190))
    draw.line((0, 218, 320, 24), fill=(22, 22, 22), width=6)

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


if __name__ == "__main__":
    main()
