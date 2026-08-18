"""Convert a Solana keypair JSON file to its Base58 secret key."""

import json
from pathlib import Path


KEYPAIR_JSON_PATH = Path("/Users/gamdodo/.config/solana/veriproof-buyer-devnet.json")
BASE58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    encoded = bytearray()
    while number:
        number, remainder = divmod(number, 58)
        encoded.append(BASE58_ALPHABET[remainder])
    return (b"1" * (len(data) - len(data.lstrip(b"\0"))) + encoded[::-1]).decode()


def main() -> None:
    keypair = json.loads(KEYPAIR_JSON_PATH.read_text())
    if not isinstance(keypair, list) or len(keypair) != 64 or any(
        not isinstance(value, int) or not 0 <= value <= 255 for value in keypair
    ):
        raise ValueError("Keypair JSON must be an array of exactly 64 byte values.")
    print(b58encode(bytes(keypair)))


if __name__ == "__main__":
    main()
