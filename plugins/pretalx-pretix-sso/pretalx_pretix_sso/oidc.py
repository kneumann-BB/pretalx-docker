"""Minimal OpenID Connect client for pretix customer-account SSO.

The ID token is received directly from the token endpoint over TLS, so per
OIDC Core 3.1.3.7 its signature does not need to be verified; we still check
iss, aud, exp and nonce.
"""

import base64
import hashlib
import json
import os
import secrets
import time

import requests
from django.conf import settings
from django.core.cache import cache

TIMEOUT = 10


class OIDCError(Exception):
    pass


ENV_PREFIX = "PRETALX_PRETIX_SSO_"


def _setting(conf, key, default=""):
    """Read a setting from the environment (PRETALX_PRETIX_SSO_<KEY>), falling
    back to the [plugin:pretalx_pretix_sso] section of pretalx.cfg. Keeps secrets
    such as client_secret and api_token out of config files."""
    return os.environ.get(ENV_PREFIX + key.upper()) or conf.get(key, default)


def get_config():
    conf = settings.PLUGIN_SETTINGS.get("pretalx_pretix_sso", {})
    return {
        "issuer": _setting(conf, "issuer").rstrip("/"),
        "client_id": _setting(conf, "client_id"),
        "client_secret": _setting(conf, "client_secret"),
        "scope": _setting(conf, "scope", "openid email profile"),
        "pretix_url": _setting(conf, "pretix_url"),
        "organizer": _setting(conf, "organizer"),
        "api_token": _setting(conf, "api_token"),
        # "pretalx-slug=pretix-slug, other=other-pretix"
        "event_map": dict(
            (part.strip() for part in pair.split("=", 1))
            for pair in _setting(conf, "event_map").split(",")
            if "=" in pair
        ),
    }


def is_configured():
    conf = get_config()
    return bool(conf["issuer"] and conf["client_id"] and conf["client_secret"])


def is_enabled(event):
    """SSO is only available for events that have the plugin switched on."""
    return "pretalx_pretix_sso" in event.plugin_list and is_configured()


def _json_object(response, what):
    """Parse a JSON object from pretix, or raise OIDCError."""
    try:
        data = response.json()
    except ValueError as e:
        raise OIDCError(f"{what} did not return JSON") from e
    if not isinstance(data, dict):
        raise OIDCError(f"{what} did not return a JSON object")
    return data


DISCOVERY_KEYS = ("authorization_endpoint", "token_endpoint", "userinfo_endpoint")


def discovery():
    issuer = get_config()["issuer"]
    key = f"pretix_sso_discovery:{issuer}"
    data = cache.get(key)
    if data is None:
        response = requests.get(
            f"{issuer}/.well-known/openid-configuration", timeout=TIMEOUT
        )
        response.raise_for_status()
        data = _json_object(response, "Discovery")
        if not all(isinstance(data.get(k), str) for k in DISCOVERY_KEYS):
            raise OIDCError("Discovery document is missing endpoints")
        cache.set(key, data, 3600)
    return data


def new_pkce_pair():
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def authorization_url(redirect_uri, state, nonce, code_challenge):
    conf = get_config()
    request = requests.Request(
        "GET",
        discovery()["authorization_endpoint"],
        params={
            "response_type": "code",
            "client_id": conf["client_id"],
            "redirect_uri": redirect_uri,
            "scope": conf["scope"],
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        },
    )
    return request.prepare().url


def _decode_jwt_payload(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (AttributeError, IndexError, ValueError) as e:
        raise OIDCError("Malformed ID token") from e
    if not isinstance(claims, dict):
        raise OIDCError("Malformed ID token")
    return claims


def fetch_userinfo(code, redirect_uri, nonce, code_verifier):
    conf = get_config()
    meta = discovery()
    response = requests.post(
        meta["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            # client_secret_post: the Authorization header does not reliably
            # reach pretix through its reverse proxy.
            "client_id": conf["client_id"],
            "client_secret": conf["client_secret"],
        },
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise OIDCError(f"Token endpoint returned {response.status_code}")
    tokens = _json_object(response, "Token endpoint")

    claims = _decode_jwt_payload(tokens.get("id_token", ""))
    audience = claims.get("aud")
    audience = audience if isinstance(audience, list) else [audience]
    # Compare against the configured issuer, not whatever discovery returned
    if claims.get("iss", "").rstrip("/") != conf["issuer"]:
        raise OIDCError("ID token issuer mismatch")
    if conf["client_id"] not in audience:
        raise OIDCError("ID token audience mismatch")
    try:
        expires = float(claims.get("exp", 0))
    except (TypeError, ValueError) as e:
        raise OIDCError("ID token has an invalid expiry") from e
    if expires < time.time():
        raise OIDCError("ID token expired")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OIDCError("ID token nonce mismatch")

    if not isinstance(tokens.get("access_token"), str):
        raise OIDCError("Token endpoint returned no access token")
    response = requests.get(
        meta["userinfo_endpoint"],
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise OIDCError(f"Userinfo endpoint returned {response.status_code}")
    userinfo = _json_object(response, "Userinfo endpoint")
    if not claims.get("sub") or userinfo.get("sub") != claims.get("sub"):
        raise OIDCError("Userinfo subject mismatch")
    return userinfo
