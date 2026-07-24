"""Registration certificate PDF rendering from persisted proof records only."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any

from django.conf import settings
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas


class RegistrationCertificateDocumentService:
    """Render an owner-visible registration certificate without original content."""

    _BACKGROUND = Path(settings.BASE_DIR) / "static" / "images" / "certificate-security-paper-v1.png"

    def render(self, asset: Any, explorer_url: str) -> bytes:
        """Return a one-page PDF containing only persisted registration proof."""
        page_width, page_height = landscape(A4)
        buffer = BytesIO()
        document = canvas.Canvas(buffer, pagesize=(page_width, page_height))
        document.setTitle(f"VeriProof registration certificate — {asset.id}")
        document.setAuthor("VeriProof AI")
        document.setSubject("On-chain intellectual property registration record")
        document.drawImage(ImageReader(str(self._BACKGROUND)), 0, 0, page_width, page_height)

        korean_font = "HYSMyeongJo-Medium"
        pdfmetrics.registerFont(UnicodeCIDFont(korean_font))
        self._draw_heading(document, korean_font, page_width, page_height)
        self._draw_content(document, korean_font, asset, explorer_url, page_width, page_height)
        document.showPage()
        document.save()
        return buffer.getvalue()

    @staticmethod
    def _draw_heading(document, korean_font: str, page_width: float, page_height: float) -> None:
        """인증서 상단의 헤더(영문 라벨, 제목, 구분선)를 그린다."""
        document.setFillColor(HexColor("#29483a"))
        document.setFont("Helvetica-Bold", 10)
        document.drawCentredString(page_width / 2, page_height - 81, "VERIPROOF AI  ·  ON-CHAIN REGISTRATION RECORD")
        document.setFillColor(HexColor("#26362d"))
        document.setFont(korean_font, 27)
        document.drawCentredString(page_width / 2, page_height - 117, "저작물 등록 인증서")
        document.setStrokeColor(HexColor("#9d834b"))
        document.line(180, page_height - 130, page_width - 180, page_height - 130)

    def _draw_content(self, document, korean_font: str, asset: Any, explorer_url: str, page_width: float, page_height: float) -> None:
        """등록 지문·창작자·트랜잭션 필드와 하단 검증 블록을 본문 영역에 배치한다."""
        registration_tx = asset.registration_certificate_tx_sig
        issued_at = asset.created_at.strftime("%Y-%m-%d %H:%M:%S %Z") if asset.created_at else ""
        verification_ref = sha256(f"{asset.id}:{registration_tx}".encode()).hexdigest()[:24].upper()
        left = 155
        content_top = page_height - 170

        document.setFillColor(HexColor("#38473d"))
        document.setFont(korean_font, 11)
        document.drawString(left, content_top, "본 문서는 아래 저작물의 등록 지문과 창작자 기록을 인증합니다.")
        document.setFont(korean_font, 18)
        document.setFillColor(HexColor("#1f3027"))
        document.drawString(left, content_top - 33, asset.title or "제목 미지정 작품")

        fields = (
            ("등록 일시", issued_at),
            ("창작자 지갑", asset.creator.wallet_address),
            ("자산 ID", str(asset.id)),
            ("콘텐츠 SHA-256", asset.image_sha256),
            ("앵커 트랜잭션", asset.anchor_tx_sig or "미발급"),
            ("등록 인증 트랜잭션", registration_tx),
        )
        y = content_top - 72
        for label, value in fields:
            document.setFillColor(HexColor("#6a725f"))
            document.setFont(korean_font, 9)
            document.drawString(left, y, label)
            document.setFillColor(HexColor("#26362d"))
            document.setFont("Helvetica", 8.5)
            document.drawString(left + 122, y, str(value))
            document.setStrokeColor(HexColor("#d3c7a7"))
            document.line(left, y - 8, page_width - 178, y - 8)
            y -= 31

        self._draw_verification(document, korean_font, explorer_url, verification_ref, page_width, 136)
        document.setFillColor(HexColor("#667062"))
        document.setFont(korean_font, 8)
        document.drawCentredString(page_width / 2, 71, "QR 코드 또는 앵커 트랜잭션을 Solana Explorer에서 대조해 진위를 확인하십시오.")
        document.setFont("Helvetica-Bold", 8)
        document.drawCentredString(page_width / 2, 55, f"Verification reference: {verification_ref}")

    @staticmethod
    def _draw_verification(document, korean_font: str, explorer_url: str, verification_ref: str, page_width: float, y: float) -> None:
        """우측 상단의 QR 코드와 검증 참조번호를 그려 온체인 대조를 안내한다."""
        qr = QrCodeWidget(explorer_url)
        bounds = qr.getBounds()
        size = 82
        drawing = Drawing(size, size, transform=[size / (bounds[2] - bounds[0]), 0, 0, size / (bounds[3] - bounds[1]), 0, 0])
        drawing.add(qr)
        renderPDF.draw(drawing, document, page_width - 162, y)
        document.setFillColor(HexColor("#29483a"))
        document.setFont(korean_font, 10)
        document.drawRightString(page_width - 177, y + 64, "온체인 검증")
        document.setFont("Helvetica", 7.5)
        document.drawRightString(page_width - 177, y + 46, verification_ref)
        document.setFillColor(HexColor("#6a725f"))
        document.setFont(korean_font, 8)
        document.drawRightString(page_width - 177, y + 26, "보안 배경 · QR 검증 · 지문 대조")


def get_registration_certificate_document_service() -> RegistrationCertificateDocumentService:
    """등록 인증서 문서 서비스 인스턴스를 생성한다."""
    return RegistrationCertificateDocumentService()
