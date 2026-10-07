"""
keys.py — Ed25519 signing key for /verify responses.

Generate once on the VPS (keep the PEM secret and backed up offline!):
    sudo -u beammods /opt/beammods/venv/bin/python -m bot.drm.keys generate /opt/beammods/keys/drm_ed25519.pem

Show the public key (this is what goes into every Lua bundle):
    python -m bot.drm.keys show /opt/beammods/keys/drm_ed25519.pem

If the private key leaks, anyone can forge "valid" licenses. If it is lost,
every bundle already sold must be rebuilt with a new public key.
"""
import os
import sys
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def generate(path: str) -> str:
    p = Path(path)
    if p.exists():
        raise SystemExit(f"Refusing to overwrite existing key: {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return public_hex(key)


def load_private_key(path: str) -> Ed25519PrivateKey:
    with open(path, "rb") as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Not an Ed25519 private key")
    return key


def public_hex(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()


@lru_cache(maxsize=1)
def get_signing_key() -> Ed25519PrivateKey:
    path = os.getenv("DRM_SIGNING_KEY_FILE", "")
    if not path:
        raise RuntimeError("DRM_SIGNING_KEY_FILE is not set")
    return load_private_key(path)


def get_public_hex() -> str:
    return public_hex(get_signing_key())


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("generate", "show"):
        raise SystemExit("usage: python -m bot.drm.keys generate|show <path.pem>")
    cmd, path = sys.argv[1], sys.argv[2]
    if cmd == "generate":
        print("Created", path)
        print("PUBLIC KEY:", generate(path))
    else:
        print("PUBLIC KEY:", public_hex(load_private_key(path)))
