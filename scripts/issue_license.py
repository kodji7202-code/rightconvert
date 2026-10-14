"""Operator-only Ed25519 RightConvert offline license issuer.

Usage: python scripts/issue_license.py --private-key X.pem --tier pro
       --license-id ORDER-2026-0001 [--expires-at YYYY-MM-DDTHH:MM:SSZ]

The key must have been generated, stored and protected OUTSIDE this checkout.
This script never generates keys and prints only a signed license token to
stdout. The private key is never embedded in the output token.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
from pathlib import Path
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from rightconvert.licensing import (  # noqa: E402
    _canonical_payload,
    _encode_b64,
    _parse_claims,
    _signature_message,
    public_key_id,
)


def _key_outside_checkout(path: Path) -> Path:
    candidate = path.expanduser().absolute()
    if candidate.is_relative_to(_ROOT) or candidate.resolve().is_relative_to(_ROOT):
        raise ValueError("The private signing key must be outside the RightConvert project")
    if not candidate.is_file():
        raise ValueError("Private key file does not exist")
    return candidate


def _read_signer(path: Path) -> Ed25519PrivateKey:
    pem = path.read_bytes()
    try:
        signer = serialization.load_pem_private_key(pem, password=None)
    except TypeError:
        # Encrypted PKCS8 keys prompt interactively. Never accept secrets via argv.
        password = getpass.getpass("Private key passphrase: ").encode("utf-8")
        signer = serialization.load_pem_private_key(pem, password=password)
    if not isinstance(signer, Ed25519PrivateKey):
        raise ValueError("Private key must be PEM-encoded Ed25519")
    return signer


def issue_token(
    signer: Ed25519PrivateKey,
    *,
    tier: str,
    license_id: str,
    issued_at: str,
    product: str = "rightconvert",
    expires_at: str | None = None,
    max_version: str | None = None,
) -> str:
    """Produce a deterministic-format token with a randomized external key."""
    kid = public_key_id(signer.public_key())
    payload: dict[str, str] = {
        "product": product,
        "tier": tier,
        "issued_at": issued_at,
        "license_id": license_id,
        "key_id": kid,
    }
    if expires_at is not None:
        payload["expires_at"] = expires_at
    if max_version is not None:
        payload["max_version"] = max_version
    payload_bytes = _canonical_payload(payload)
    _parse_claims(payload_bytes)  # reject malformed or internally inconsistent issuance
    signature = signer.sign(_signature_message(payload_bytes))
    return f"RC1.{_encode_b64(payload_bytes)}.{_encode_b64(signature)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Issue an offline signed RightConvert license")
    parser.add_argument("--private-key", type=Path, required=True, help="External PEM Ed25519 key path")
    parser.add_argument("--product", default="rightconvert", help="Exact application product ID")
    parser.add_argument("--tier", required=True, help="Tier agreed with product owner (e.g. pro)")
    parser.add_argument("--license-id", required=True, help="Unique non-sensitive license reference")
    parser.add_argument("--issued-at", help="UTC timestamp YYYY-MM-DDTHH:MM:SSZ (defaults to now)")
    parser.add_argument("--expires-at", help="Optional UTC timestamp YYYY-MM-DDTHH:MM:SSZ")
    parser.add_argument("--max-version", help="Optional numeric MAJOR.MINOR.PATCH")
    parser.add_argument(
        "--show-public-key", action="store_true",
        help="Print non-secret public key ID and base64url raw key to stderr",
    )
    args = parser.parse_args(argv)
    try:
        key_file = _key_outside_checkout(args.private_key)
        signer = _read_signer(key_file)
        timestamp = args.issued_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        token = issue_token(
            signer,
            tier=args.tier,
            license_id=args.license_id,
            issued_at=timestamp,
            product=args.product,
            expires_at=args.expires_at,
            max_version=args.max_version,
        )
    except (OSError, ValueError, TypeError) as exc:
        parser.error(f"Could not issue license: {type(exc).__name__}")
    if args.show_public_key:
        raw = signer.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        print(f"Public key ID: {public_key_id(raw)}", file=sys.stderr)
        print(f"Raw public key (base64url): {_encode_b64(raw)}", file=sys.stderr)
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
