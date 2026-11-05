import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.models.core import ApiKeyKind

_KEY_PREFIX = "lo"


def generate_api_key(kind: ApiKeyKind) -> tuple[str, str, str]:
    """Return (plaintext, display_prefix, sha256_hash). Plaintext is shown once."""
    secret = secrets.token_urlsafe(32)
    plaintext = f"{_KEY_PREFIX}_{kind.value}_{secret}"
    return plaintext, plaintext[:16], hash_api_key(plaintext)


def hash_api_key(plaintext: str) -> str:
    # Keys are 256-bit random, so a fast unsalted hash is sufficient and allows indexed lookup.
    return hashlib.sha256(plaintext.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


@dataclass(frozen=True, slots=True)
class PlayerClaims:
    player_id: uuid.UUID
    environment_id: uuid.UUID


def issue_player_token(claims: PlayerClaims, secret: str, ttl_seconds: int) -> tuple[str, int]:
    now = datetime.now(UTC)
    payload = {
        "sub": str(claims.player_id),
        "env": str(claims.environment_id),
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
        "typ": "player",
    }
    return jwt.encode(payload, secret, algorithm="HS256"), ttl_seconds


def decode_player_token(token: str, secret: str) -> PlayerClaims:
    """Raises jwt.InvalidTokenError on any problem."""
    payload = jwt.decode(
        token, secret, algorithms=["HS256"], options={"require": ["sub", "env", "exp"]}
    )
    if payload.get("typ") != "player":
        raise jwt.InvalidTokenError("wrong token type")
    try:
        return PlayerClaims(uuid.UUID(payload["sub"]), uuid.UUID(payload["env"]))
    except (ValueError, TypeError) as exc:
        raise jwt.InvalidTokenError("malformed claims") from exc
