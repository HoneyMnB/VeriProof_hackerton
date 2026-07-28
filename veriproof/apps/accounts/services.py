"""계정 시드처럼 HTTP와 분리해야 하는 계정 유스케이스."""
from __future__ import annotations

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth.models import User

DEVELOPER_EMAIL = "admin@test.com"
DEVELOPER_PASSWORD = "a123456789?"
BASE58_ALPHABET = frozenset("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


class WalletSigningError(ValueError):
    """Raised when an authenticated account cannot authorize a wallet signature."""


def active_wallet_address(user: User) -> str:
    """Return the authenticated account's active public wallet address."""
    from .models import WalletConfiguration

    wallet = WalletConfiguration.objects.filter(user=user, is_active=True).first()
    if wallet is None or not wallet.address:
        raise WalletSigningError("An active wallet is required.")
    return wallet.address


def ensure_developer_account() -> User:
    """반복 실행해도 같은 로컬 개발자 계정을 반환한다."""
    user, created = User.objects.get_or_create(
        username=DEVELOPER_EMAIL,
        defaults={"email": DEVELOPER_EMAIL, "is_staff": True, "is_superuser": True},
    )
    changed = created or not user.check_password(DEVELOPER_PASSWORD) or not user.is_staff
    if changed:
        user.email = DEVELOPER_EMAIL
        user.is_staff = True
        user.is_superuser = True
        user.set_password(DEVELOPER_PASSWORD)
        user.save()
    return user


def is_valid_solana_address(address: str) -> bool:
    """공개 Solana 주소로 사용할 수 있는 base58 문자열인지 확인한다."""
    return 32 <= len(address) <= 44 and all(char in BASE58_ALPHABET for char in address)


def _wallet_private_key_cipher() -> Fernet:
    """Return the configured encryption cipher without exposing its key."""
    encryption_key = settings.WALLET_PRIVATE_KEY_ENCRYPTION_KEY
    if not encryption_key:
        if not settings.DEBUG:
            raise RuntimeError("WALLET_PRIVATE_KEY_ENCRYPTION_KEY is required when DEBUG is false.")
        material = f"{settings.SECRET_KEY}:wallet-private-address:v1".encode()
        encryption_key = base64.urlsafe_b64encode(hashlib.sha256(material).digest()).decode()
    return Fernet(encryption_key.encode())


def encrypt_wallet_private_address(private_address: str) -> str:
    """Encrypt a wallet secret for storage; it must never be serialized to clients."""
    return _wallet_private_key_cipher().encrypt(private_address.encode()).decode()


def decrypt_wallet_private_address(encrypted_private_address: str) -> str:
    """Decrypt only in an explicitly authorized future signing flow."""
    try:
        return _wallet_private_key_cipher().decrypt(encrypted_private_address.encode()).decode()
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise ValueError("Stored wallet private key cannot be decrypted.") from exc


def private_address_matches_wallet(private_address: str, wallet_address: str) -> bool:
    """Accept Solana base58 or a 64-byte JSON keypair only when it owns the address."""
    if not private_address or len(private_address) > 512:
        return False
    try:
        from solders.keypair import Keypair

        if private_address.lstrip().startswith("["):
            raw_bytes = json.loads(private_address)
            if not isinstance(raw_bytes, list) or len(raw_bytes) != 64 or any(not isinstance(value, int) or not 0 <= value <= 255 for value in raw_bytes):
                return False
            keypair = Keypair.from_bytes(bytes(raw_bytes))
        else:
            keypair = Keypair.from_base58_string(private_address)
    except (ImportError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return str(keypair.pubkey()) == wallet_address


def active_wallet_signer(user: User) -> tuple[str, list[int]]:
    """Return only the active account wallet's verified signing bytes.

    This is deliberately the sole boundary that decrypts a user wallet key for
    registration signing.  No caller receives the encrypted database value.
    """
    from .models import WalletConfiguration

    wallet = WalletConfiguration.objects.filter(user=user, is_active=True).first()
    if wallet is None:
        raise WalletSigningError("An active wallet is required to register content.")
    if not wallet.private_address:
        raise WalletSigningError("The active wallet does not have a registered private key.")
    try:
        private_address = decrypt_wallet_private_address(wallet.private_address)
    except ValueError as exc:
        raise WalletSigningError("The active wallet private key is unavailable.") from exc
    if not private_address_matches_wallet(private_address, wallet.address):
        raise WalletSigningError("The active wallet private key does not match its public address.")
    try:
        from solders.keypair import Keypair

        if private_address.lstrip().startswith("["):
            secret_key = list(bytes(json.loads(private_address)))
        else:
            secret_key = list(bytes(Keypair.from_base58_string(private_address)))
    except (ImportError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WalletSigningError("The active wallet private key is invalid.") from exc
    return wallet.address, secret_key


def ensure_creator_wallet(wallet_address: str) -> None:
    """검증된 공개 지갑 주소를 IP 도메인의 창작자 식별자로 준비한다."""
    from apps.ip.models import Creator

    Creator.objects.get_or_create(wallet_address=wallet_address)
