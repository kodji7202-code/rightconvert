"""Offline license protocol: canonical claims, trust separation and time policy."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from rightconvert import licensing
from scripts.issue_license import _key_outside_checkout, _read_signer, issue_token


NOW = datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)
ISSUED = "2026-10-13T17:00:00Z"
KEYS_EXAMPLE = "ORDER-2026-10001"


@pytest.fixture
def signer() -> Ed25519PrivateKey:
    # In-memory ephemeral test key; never part of source, checkout or release.
    return Ed25519PrivateKey.generate()


def trusted(signer: Ed25519PrivateKey) -> dict[str, bytes]:
    public = signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {licensing.public_key_id(public): public}


def token_for(signer: Ed25519PrivateKey, **kwargs: str) -> str:
    args = {"tier": "pro", "license_id": KEYS_EXAMPLE, "issued_at": ISSUED}
    args.update(kwargs)
    return issue_token(signer, **args)


def check(signer: Ed25519PrivateKey, token: str, **kwargs: object) -> licensing.LicenseResult:
    return licensing.verify_license(
        token, trusted_public_keys=trusted(signer), current_version="0.2.0",
        now=NOW, **kwargs
    )


def replace_payload(token: str, **changes: object) -> str:
    prefix, payload, signature = token.split(".")
    source = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    source.update(changes)
    encoded = licensing._encode_b64(licensing._canonical_payload(source))
    return f"{prefix}.{encoded}.{signature}"


def test_valid_license_can_be_verified_offline_without_device_binding(
    signer: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("Network used"))
    token = token_for(signer, expires_at="2027-01-01T00:00:00Z", max_version="0.3.0")
    result = check(signer, token)
    assert result.valid and result.reason == "valid"
    assert result.claims is not None
    assert result.claims.product == "rightconvert"
    assert result.claims.tier == "pro"
    assert result.claims.license_id == KEYS_EXAMPLE
    assert result.claims.max_version == "0.3.0"
    assert "machine" not in token.lower() and "hardware" not in token.lower()


def test_unconfigured_never_activates(signer: Ed25519PrivateKey) -> None:
    token = token_for(signer)
    assert licensing.verify_license(token).reason == "unconfigured"
    assert licensing.verify_license(token, trusted_public_keys={}).valid is False


def test_key_rotation_and_key_separation(signer: Ed25519PrivateKey) -> None:
    other = Ed25519PrivateKey.generate()
    token = token_for(signer)
    second = token_for(other)
    assert check(signer, token).valid
    assert check(signer, second).reason == "unknown_key"
    mixed = trusted(other)
    mixed[licensing.public_key_id(signer.public_key())] = next(iter(trusted(other).values()))
    assert licensing.verify_license(token, trusted_public_keys=mixed, now=NOW).reason == "invalid_key_configuration"
    combined = {**trusted(signer), **trusted(other)}
    assert licensing.verify_license(token, trusted_public_keys=combined, now=NOW).valid
    assert licensing.verify_license(second, trusted_public_keys=combined, now=NOW).valid


@pytest.mark.parametrize("change", [
    {"tier": "enterprise"},
    {"product": "other"},
    {"expires_at": "2099-01-01T00:00:00Z"},
    {"max_version": "99.0.0"},
    {"license_id": "ORDER-2026-10002"},
])
def test_tampered_claims_fail_signature(
    signer: Ed25519PrivateKey, change: dict[str, str]
) -> None:
    token = token_for(signer, expires_at="2027-01-01T00:00:00Z", max_version="0.3.0")
    assert check(signer, replace_payload(token, **change)).reason == "invalid_signature"


def test_modified_signature_fails(signer: Ed25519PrivateKey) -> None:
    token = token_for(signer)
    header, payload, sig = token.split(".")
    raw = bytearray(licensing._decode_b64(sig, 64))
    raw[4] ^= 0x20
    assert check(signer, f"{header}.{payload}.{licensing._encode_b64(raw)}").reason == "invalid_signature"


@pytest.mark.parametrize("bad", [
    "",
    "RC1",
    "RC1.x.y.z",
    "RC1.!.x",
    "RC2.abc.xyz",
    "RC1.A.z",
    "RC1." + ("A" * 8200) + ".AA",
])
def test_corrupt_tokens_reject_without_raising(signer: Ed25519PrivateKey, bad: str) -> None:
    assert check(signer, bad).valid is False


def test_expires_at_boundary_and_nonexpiring(signer: Ed25519PrivateKey) -> None:
    expires = token_for(signer, expires_at="2026-10-13T18:00:00Z")
    assert check(signer, expires).reason == "expired"
    assert licensing.verify_license(
        expires, trusted_public_keys=trusted(signer),
        now=datetime(2026, 10, 13, 17, 59, 59, tzinfo=timezone.utc)
    ).valid
    assert check(signer, token_for(signer)).valid


def test_future_issuance_and_clock_validation(signer: Ed25519PrivateKey) -> None:
    future = token_for(signer, issued_at="2026-10-14T18:00:00Z")
    assert check(signer, future).reason == "not_yet_valid"
    assert licensing.verify_license(
        future, trusted_public_keys=trusted(signer), now=datetime(2026, 10, 13, 18, 0)
    ).reason == "invalid_clock"
    assert licensing.verify_license(
        future, trusted_public_keys=trusted(signer),
        now=datetime(2026, 10, 14, 17, 57, tzinfo=timezone.utc)
    ).valid


def test_versions_fail_closed(signer: Ed25519PrivateKey) -> None:
    token = token_for(signer, max_version="0.2.0")
    assert check(signer, token).valid
    assert licensing.verify_license(
        token, trusted_public_keys=trusted(signer), current_version="0.2.1", now=NOW
    ).reason == "version_exceeded"
    assert licensing.verify_license(
        token, trusted_public_keys=trusted(signer), now=NOW
    ).reason == "version_required"
    assert licensing.verify_license(
        token, trusted_public_keys=trusted(signer), current_version="release-2", now=NOW
    ).reason == "invalid_version"


def test_signed_product_mismatch_is_rejected(signer: Ed25519PrivateKey) -> None:
    token = token_for(signer, product="anotherproduct")
    assert check(signer, token).reason == "wrong_product"


def test_canonical_and_algorithm_confusion_are_rejected(signer: Ed25519PrivateKey) -> None:
    token = token_for(signer)
    header, payload, sig = token.split(".")
    obj = json.loads(licensing._decode_b64(payload, 4096))
    # Even a token freshly signed by a trusted issuer cannot add alg or key material.
    obj["alg"] = "none"
    raw = licensing._canonical_payload(obj)
    attack = f"{header}.{licensing._encode_b64(raw)}.{licensing._encode_b64(signer.sign(licensing._signature_message(raw)))}"
    assert check(signer, attack).valid is False
    assert check(signer, attack).reason == "malformed"
    # JSON may be semantically equivalent but it must be canonical byte-for-byte.
    padded = json.dumps(json.loads(licensing._decode_b64(payload, 4096)), indent=1).encode()
    raw_sig = signer.sign(licensing._signature_message(padded))
    assert check(signer, f"RC1.{licensing._encode_b64(padded)}.{licensing._encode_b64(raw_sig)}").reason == "malformed"
    # A made-up algorithm prefix is never accepted.
    assert check(signer, token.replace("RC1.", "none.", 1)).reason == "malformed"


def test_invalid_claims_and_duplicate_keys_rejected_at_issuance(
    signer: Ed25519PrivateKey
) -> None:
    with pytest.raises(ValueError):
        token_for(signer, expires_at="2026-10-01T00:00:00Z")
    with pytest.raises(ValueError):
        token_for(signer, max_version="latest")
    with pytest.raises(ValueError):
        token_for(signer, tier="pro with spaces")
    key = licensing.public_key_id(signer.public_key())
    duplicate = (f'{{"key_id":"{key}","key_id":"{key}","tier":"pro"}}').encode()
    assert pytest.raises(ValueError, licensing._parse_claims, duplicate)


def test_cli_issues_real_token_with_private_key_outside_checkout(
    signer: Ed25519PrivateKey, tmp_path: Path
) -> None:
    private = tmp_path / "external-test-key.pem"
    private.write_bytes(signer.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()
    ))
    script = Path(__file__).resolve().parents[1] / "scripts" / "issue_license.py"
    run = subprocess.run(
        [
            sys.executable, str(script), "--private-key", str(private),
            "--tier", "pro", "--license-id", KEYS_EXAMPLE,
            "--issued-at", ISSUED, "--max-version", "0.2.0",
            "--show-public-key",
        ],
        capture_output=True, text=True, check=False, timeout=15
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.startswith("RC1.")
    assert f"Public key ID: {licensing.public_key_id(signer.public_key())}" in run.stderr
    assert "Raw public key (base64url):" in run.stderr
    assert "BEGIN PRIVATE KEY" not in run.stderr
    assert private.read_text().startswith("-----BEGIN PRIVATE KEY-----")
    assert check(signer, run.stdout.strip()).valid


def test_public_key_decode_and_identifier(signer: Ed25519PrivateKey) -> None:
    encoded = licensing._encode_b64(
        signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    key = licensing.decode_public_key(encoded)
    assert key == next(iter(trusted(signer).values()))
    assert licensing.public_key_id(key) == licensing.public_key_id(signer.public_key())
    with pytest.raises(ValueError):
        licensing.decode_public_key("bad-key")


def test_checkout_signer_path_is_forbidden_without_creating_a_key() -> None:
    project = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError, match="outside"):
        _key_outside_checkout(project / "forbidden-test-only.pem")


def test_encrypted_external_private_key_prompts_instead_of_using_argv(
    signer: Ed25519PrivateKey, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "encrypted.pem"
    private.write_bytes(signer.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"temporary-test-passphrase"),
    ))
    monkeypatch.setattr(
        "scripts.issue_license.getpass.getpass",
        lambda message: "temporary-test-passphrase",
    )
    assert licensing.public_key_id(_read_signer(private).public_key()) == licensing.public_key_id(signer.public_key())
