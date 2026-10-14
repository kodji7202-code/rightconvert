"""Offline RightConvert license verification (no network, telemetry or device ID).

This module holds *no* trusted production key. A distributor must explicitly
provide a key-id -> raw Ed25519 public key mapping; an unconfigured verifier
always rejects activation. The application currently has no license gates.

Token format: RC1.base64url(canonical-UTF8-JSON).base64url(Ed25519-signature).
The signed message is domain-separated and contains the exact canonical
payload. No algorithm field, public key, or URL is accepted from the token.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


_PREFIX = "RC1"
_DOMAIN = b"RightConvert offline license v1\x00"
_MAX_TOKEN_LENGTH = 8192
_MAX_PAYLOAD_LENGTH = 4096
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_LICENSE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
_KEY_ID = re.compile(r"^ed25519-[0-9a-f]{32}$")
_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_REQUIRED = frozenset({"product", "tier", "issued_at", "license_id", "key_id"})
_OPTIONAL = frozenset({"expires_at", "max_version"})


@dataclass(frozen=True)
class LicenseClaims:
    product: str
    tier: str
    issued_at: datetime
    license_id: str
    key_id: str
    expires_at: datetime | None = None
    max_version: str | None = None


@dataclass(frozen=True)
class LicenseResult:
    valid: bool
    reason: str
    claims: LicenseClaims | None = None


def _canonical_payload(payload: Mapping[str, object]) -> bytes:
    """Serialize a signed payload with one deterministic JSON representation."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def _signature_message(payload_bytes: bytes) -> bytes:
    return _DOMAIN + payload_bytes


def _encode_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode_b64(value: str, max_bytes: int) -> bytes:
    if not isinstance(value, str) or not value or not _B64URL.fullmatch(value):
        raise ValueError("Invalid base64url alphabet")
    if len(value) > (max_bytes * 4 // 3 + 4):
        raise ValueError("Oversized base64url value")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64url encoding") from exc
    if len(decoded) > max_bytes or _encode_b64(decoded) != value:
        raise ValueError("Noncanonical base64url value")
    return decoded


def _utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        raise ValueError("Timestamp must be YYYY-MM-DDTHH:MM:SSZ")
    result = datetime.fromisoformat(value[:-1] + "+00:00")
    if result.utcoffset() != timedelta(0):
        raise ValueError("Timestamp is not UTC")
    return result


def _version_tuple(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError("Version must be numeric MAJOR.MINOR.PATCH")
    return tuple(int(v) for v in value.split("."))  # type: ignore[return-value]


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _parse_claims(payload_bytes: bytes) -> LicenseClaims:
    if not payload_bytes or len(payload_bytes) > _MAX_PAYLOAD_LENGTH:
        raise ValueError("Invalid payload size")
    payload = json.loads(
        payload_bytes.decode("utf-8"),
        object_pairs_hook=_unique_pairs,
        parse_constant=_invalid_constant,
    )
    if not isinstance(payload, dict) or set(payload) - (_REQUIRED | _OPTIONAL):
        raise ValueError("Unknown or invalid payload fields")
    if not _REQUIRED.issubset(payload) or _canonical_payload(payload) != payload_bytes:
        raise ValueError("Missing fields or noncanonical JSON")
    for field in ("product", "tier"):
        if not isinstance(payload[field], str) or not _IDENTIFIER.fullmatch(payload[field]):
            raise ValueError(f"Invalid {field}")
    if not isinstance(payload["license_id"], str) or not _LICENSE_ID.fullmatch(payload["license_id"]):
        raise ValueError("Invalid license ID")
    if not isinstance(payload["key_id"], str) or not _KEY_ID.fullmatch(payload["key_id"]):
        raise ValueError("Invalid key ID")
    issued_at = _utc_timestamp(payload["issued_at"])
    expires_at = _utc_timestamp(payload["expires_at"]) if "expires_at" in payload else None
    if expires_at is not None and expires_at <= issued_at:
        raise ValueError("Expiration must be after issue time")
    max_version = payload.get("max_version")
    if "max_version" in payload:
        _version_tuple(max_version)
    return LicenseClaims(
        product=payload["product"],
        tier=payload["tier"],
        issued_at=issued_at,
        license_id=payload["license_id"],
        key_id=payload["key_id"],
        expires_at=expires_at,
        max_version=max_version,
    )


def public_key_id(public_key: bytes | Ed25519PublicKey) -> str:
    """Stable key identifier for key rotation; never a trust decision by itself."""
    if isinstance(public_key, Ed25519PublicKey):
        raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    elif isinstance(public_key, bytes) and len(public_key) == 32:
        raw = public_key
    else:
        raise ValueError("Expected a 32-byte raw Ed25519 public key")
    return "ed25519-" + hashlib.sha256(raw).hexdigest()[:32]


def decode_public_key(encoded: str) -> bytes:
    """Decode an explicitly configured base64url raw Ed25519 public key."""
    raw = _decode_b64(encoded, 32)
    if len(raw) != 32:
        raise ValueError("Expected a 32-byte raw Ed25519 public key")
    return raw


def verify_license(
    token: str,
    *,
    trusted_public_keys: Mapping[str, bytes | Ed25519PublicKey] | None = None,
    expected_product: str = "rightconvert",
    current_version: str | None = None,
    now: datetime | None = None,
) -> LicenseResult:
    """Validate an offline token against caller-provisioned trusted keys.

    A successful verification asserts signature and claims validity, *not*
    feature eligibility: product-tier feature gates are not implemented.
    Timestamps use the local wall clock, which can be changed by the user.
    """
    if not trusted_public_keys:
        return LicenseResult(False, "unconfigured")
    try:
        if not isinstance(token, str) or len(token) > _MAX_TOKEN_LENGTH:
            return LicenseResult(False, "malformed")
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != _PREFIX:
            return LicenseResult(False, "malformed")
        payload_bytes = _decode_b64(parts[1], _MAX_PAYLOAD_LENGTH)
        signature = _decode_b64(parts[2], 64)
        if len(signature) != 64:
            return LicenseResult(False, "malformed")
        claims = _parse_claims(payload_bytes)
        key = trusted_public_keys.get(claims.key_id)
        if key is None:
            return LicenseResult(False, "unknown_key")
        if isinstance(key, bytes):
            if len(key) != 32:
                return LicenseResult(False, "invalid_key_configuration")
            key = Ed25519PublicKey.from_public_bytes(key)
        if not isinstance(key, Ed25519PublicKey) or public_key_id(key) != claims.key_id:
            return LicenseResult(False, "invalid_key_configuration")
        try:
            key.verify(signature, _signature_message(payload_bytes))
        except InvalidSignature:
            return LicenseResult(False, "invalid_signature")
        if claims.product != expected_product:
            return LicenseResult(False, "wrong_product")
        instant = now if now is not None else datetime.now(timezone.utc)
        if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
            return LicenseResult(False, "invalid_clock")
        instant = instant.astimezone(timezone.utc)
        if instant + timedelta(minutes=5) < claims.issued_at:
            return LicenseResult(False, "not_yet_valid")
        if claims.expires_at is not None and instant >= claims.expires_at:
            return LicenseResult(False, "expired")
        if claims.max_version is not None:
            if current_version is None:
                return LicenseResult(False, "version_required")
            try:
                version = _version_tuple(current_version)
            except ValueError:
                return LicenseResult(False, "invalid_version")
            if version > _version_tuple(claims.max_version):
                return LicenseResult(False, "version_exceeded")
        return LicenseResult(True, "valid", claims)
    except (UnicodeDecodeError, ValueError, TypeError, OverflowError, KeyError, json.JSONDecodeError):
        return LicenseResult(False, "malformed")
