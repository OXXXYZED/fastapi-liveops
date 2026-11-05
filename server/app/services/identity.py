"""Verification of third-party sign-in tokens.

Per-environment config (``Environment.auth_providers``)::

    {
      "google": {"client_ids": ["123.apps.googleusercontent.com"]},
      "apple":  {"audiences": ["com.example.game"]},
      "steam":  {"app_id": 480, "web_api_key": "..."}
    }
"""

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import jwt
from jwt import PyJWK


class IdentityError(Exception):
    """The token is invalid, expired, or not for this game."""


class ProviderNotConfigured(Exception):
    pass


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    provider: str
    subject: str
    email: str | None = None


class Verifier(Protocol):
    async def verify(self, token: str, config: dict[str, Any]) -> VerifiedIdentity: ...


class JwksCache:
    """Fetches and caches a JWKS document; refetches on unknown kid (key rotation)."""

    def __init__(self, http: httpx.AsyncClient, url: str, ttl: float = 3600) -> None:
        self._http, self._url, self._ttl = http, url, ttl
        self._keys: dict[str, PyJWK] = {}
        self._fetched = 0.0

    async def _refresh(self) -> None:
        r = await self._http.get(self._url, timeout=10)
        r.raise_for_status()
        keys = {}
        for k in r.json().get("keys", []):
            try:
                keys[k["kid"]] = PyJWK(k)
            except (jwt.PyJWKError, KeyError):
                continue
        self._keys, self._fetched = keys, time.monotonic()

    async def get(self, kid: str) -> PyJWK:
        age = time.monotonic() - self._fetched
        # Unknown kid may mean key rotation, but refetch at most once a minute for garbage kids.
        if age > self._ttl or (kid not in self._keys and age > 60):
            try:
                await self._refresh()
            except httpx.HTTPError as exc:
                raise IdentityError("could not fetch provider keys") from exc
        if kid not in self._keys:
            raise IdentityError("unknown signing key")
        return self._keys[kid]


class OidcVerifier:
    """Google / Apple ID tokens: RS256 JWTs signed by the provider's JWKS."""

    def __init__(
        self, provider: str, jwks: JwksCache, issuers: set[str], audience_key: str
    ) -> None:
        self.provider, self._jwks, self._issuers, self._aud_key = (
            provider,
            jwks,
            issuers,
            audience_key,
        )

    async def verify(self, token: str, config: dict[str, Any]) -> VerifiedIdentity:
        audiences = config.get(self._aud_key) or []
        if not audiences:
            raise ProviderNotConfigured(self.provider)
        try:
            header = jwt.get_unverified_header(token)
            key = await self._jwks.get(str(header.get("kid", "")))
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=audiences,
                options={"require": ["iss", "sub", "aud", "exp"]},
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise IdentityError(str(exc)) from exc
        if claims["iss"] not in self._issuers:
            raise IdentityError("wrong issuer")
        email = claims.get("email") if claims.get("email_verified") in (True, "true") else None
        return VerifiedIdentity(self.provider, str(claims["sub"]), email)


class SteamVerifier:
    """Steam session tickets, checked with ISteamUserAuth/AuthenticateUserTicket."""

    URL = "https://partner.steam-api.com/ISteamUserAuth/AuthenticateUserTicket/v1/"

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def verify(self, token: str, config: dict[str, Any]) -> VerifiedIdentity:
        app_id, key = config.get("app_id"), config.get("web_api_key")
        if not app_id or not key:
            raise ProviderNotConfigured("steam")
        try:
            r = await self._http.get(
                self.URL, params={"key": key, "appid": app_id, "ticket": token}, timeout=10
            )
            r.raise_for_status()
            params = r.json()["response"]["params"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise IdentityError("steam rejected the ticket") from exc
        if params.get("result") != "OK" or params.get("vacbanned") or params.get("publisherbanned"):
            raise IdentityError("steam rejected the ticket")
        return VerifiedIdentity("steam", str(params["steamid"]))


PROVIDERS = ("google", "apple", "steam")


def default_verifiers(http: httpx.AsyncClient) -> dict[str, Verifier]:
    return {
        "google": OidcVerifier(
            "google",
            JwksCache(http, "https://www.googleapis.com/oauth2/v3/certs"),
            {"accounts.google.com", "https://accounts.google.com"},
            "client_ids",
        ),
        "apple": OidcVerifier(
            "apple",
            JwksCache(http, "https://appleid.apple.com/auth/keys"),
            {"https://appleid.apple.com"},
            "audiences",
        ),
        "steam": SteamVerifier(http),
    }
