"""VeriProof service layer (architecture 4).

Every external I/O boundary is isolated behind one of these classes. All service
bodies are implemented (SPEC-001 through SPEC-008); constructors never open live
connections, so the whole package imports offline. Cloud-only RPC/SDK code paths
are import-guarded and marked ``# pragma: no cover`` (excluded from the offline
coverage gate).

The ``get_*()`` factory helpers read Django settings lazily (import-time safe).
"""
from ._payment import resolve_pay_to
from ._types import (
    AnalysisResult,
    BatchQuote,
    NegotiationResult,
    PaymentVerification,
    SubmittedPayment,
    quantize_usdc,
)
from .bigquery_sink import BigQuerySink, get_bigquery_sink
from .event_recorder import EventRecorder, get_event_recorder
from .firestore_mirror import FirestoreMirror, get_firestore_mirror
from .gemini_service import GeminiService, get_gemini_service
from .image_fingerprint import (
    FingerprintService,
    ImageFingerprint,
    ImageFingerprintMatch,
    get_fingerprint_service,
)
from .image_processor import ImageProcessor, get_image_processor
from .kms_signer import KmsSigner, get_kms_signer
from .license_service import LicenseService, get_license_service
from .negotiation_engine import NegotiationEngine, get_negotiation_engine
from .pubsub_publisher import PubSubPublisher, get_pubsub_publisher
from .royalty_service import RoyaltyService, get_royalty_service
from .solana_adapter_factory import get_solana_service
from .solana_service import SolanaService
from .storage_service import StorageService, get_storage_service
from .x402_service import X402Service, get_x402_service

__all__ = [
    # Shared payment helpers
    "resolve_pay_to",
    # Result value-objects
    "AnalysisResult",
    "BatchQuote",
    "NegotiationResult",
    "PaymentVerification",
    "SubmittedPayment",
    "quantize_usdc",
    # Services + factories
    "GeminiService",
    "get_gemini_service",
    "SolanaService",
    "get_solana_service",
    "KmsSigner",
    "get_kms_signer",
    "StorageService",
    "get_storage_service",
    "ImageProcessor",
    "get_image_processor",
    "FingerprintService",
    "get_fingerprint_service",
    "ImageFingerprint",
    "ImageFingerprintMatch",
    "NegotiationEngine",
    "get_negotiation_engine",
    "X402Service",
    "get_x402_service",
    "LicenseService",
    "get_license_service",
    "RoyaltyService",
    "get_royalty_service",
    "EventRecorder",
    "get_event_recorder",
    "FirestoreMirror",
    "get_firestore_mirror",
    "BigQuerySink",
    "get_bigquery_sink",
    "PubSubPublisher",
    "get_pubsub_publisher",
]
