"""Lantmäteriet credentials loading and OAuth2 token fetching.

Reads either an env file (KEY=VALUE) or process environment. Returns an
Authorization header value, or None if the user wants anonymous access
(only useful for dry-run searches against unauthenticated endpoints).
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

import requests

USER_AGENT = "gis_layers/0.1"
DEFAULT_STAC_URL = "https://api.lantmateriet.se/stac-hojd/v1"

ENV_KEYS = (
    "LANTMATERIET_STAC_URL",
    "LANTMATERIET_STAC_USERNAME",
    "LANTMATERIET_STAC_PASSWORD",
    "LANTMATERIET_AUTH_METHOD",
    "LANTMATERIET_OAUTH_CLIENT_ID",
    "LANTMATERIET_OAUTH_CLIENT_SECRET",
    "LANTMATERIET_OAUTH_TOKEN_URL",
    "LANTMATERIET_OAUTH_SCOPE",
    "LANTMATERIET_OAUTH_AUTH_STYLE",
)


@dataclass(frozen=True)
class Auth:
    header: str  # full Authorization header value (e.g. "Bearer eyJ...")


def load_settings(env_path: Path | None) -> dict[str, str]:
    """Merge env-file values with os.environ. os.environ wins."""
    settings: dict[str, str] = {}
    if env_path is not None and env_path.exists():
        for lineno, raw in enumerate(env_path.read_text("utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                raise ValueError(f"{env_path}:{lineno}: expected KEY=VALUE")
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            if not key:
                raise ValueError(f"{env_path}:{lineno}: empty key")
            settings[key] = value
    for key in ENV_KEYS:
        if key in os.environ:
            settings[key] = os.environ[key]
    return settings


def auth_from_settings(settings: dict[str, str], timeout: float = 30.0) -> Auth | None:
    method = settings.get("LANTMATERIET_AUTH_METHOD", "").strip().lower()
    has_oauth = bool(
        settings.get("LANTMATERIET_OAUTH_CLIENT_ID")
        and settings.get("LANTMATERIET_OAUTH_CLIENT_SECRET")
        and settings.get("LANTMATERIET_OAUTH_TOKEN_URL")
    )
    has_basic = bool(
        settings.get("LANTMATERIET_STAC_USERNAME")
        and settings.get("LANTMATERIET_STAC_PASSWORD")
    )

    if method in {"oauth", "oauth2", "client_credentials"} or (not method and has_oauth):
        return _oauth_auth(settings, timeout)
    if method in {"basic", "basic_auth"} or (not method and has_basic):
        return _basic_auth(settings)
    if method:
        raise ValueError(f"unsupported LANTMATERIET_AUTH_METHOD={method!r}")
    return None


def _basic_auth(settings: dict[str, str]) -> Auth:
    user = settings["LANTMATERIET_STAC_USERNAME"]
    pwd = settings["LANTMATERIET_STAC_PASSWORD"]
    cred = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
    return Auth(header=f"Basic {cred}")


def _oauth_auth(settings: dict[str, str], timeout: float) -> Auth:
    client_id = settings["LANTMATERIET_OAUTH_CLIENT_ID"]
    client_secret = settings["LANTMATERIET_OAUTH_CLIENT_SECRET"]
    token_url = settings["LANTMATERIET_OAUTH_TOKEN_URL"]
    scope = settings.get("LANTMATERIET_OAUTH_SCOPE", "")
    style = settings.get("LANTMATERIET_OAUTH_AUTH_STYLE", "basic").lower()

    if not _is_lantmateriet_host(token_url):
        raise ValueError("LANTMATERIET_OAUTH_TOKEN_URL must be on lantmateriet.se")

    body = {"grant_type": "client_credentials"}
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if scope:
        body["scope"] = scope
    if style == "basic":
        cred = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {cred}"
    elif style == "post":
        body["client_id"] = client_id
        body["client_secret"] = client_secret
    else:
        raise ValueError("LANTMATERIET_OAUTH_AUTH_STYLE must be 'basic' or 'post'")

    response = requests.post(token_url, data=body, headers=headers, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("OAuth2 token response missing access_token")
    return Auth(header=f"Bearer {token}")


def basic_auth_available(settings: dict[str, str]) -> bool:
    return bool(
        settings.get("LANTMATERIET_STAC_USERNAME")
        and settings.get("LANTMATERIET_STAC_PASSWORD")
    )


def auth_for_asset_download(settings: dict[str, str], stac_auth: Auth, href: str) -> Auth:
    """Tile files on dl*.lantmateriet.se require Geotorget basic auth, not OAuth bearer."""
    from urllib.parse import urlparse

    host = (urlparse(href).hostname or "").lower()
    if host.startswith("dl") and host.endswith(".lantmateriet.se") and basic_auth_available(settings):
        return _basic_auth(settings)
    return stac_auth


def _is_lantmateriet_host(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host == "lantmateriet.se" or host.endswith(".lantmateriet.se")
