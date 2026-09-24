"""Minimal OpenID Connect client for pretix customer-account SSO.

The ID token is received directly from the token endpoint over TLS, so per
OIDC Core 3.1.3.7 its signature does not need to be verified; we still check
iss, aud, exp and nonce.
"""

import base64
import hashlib
import json
import secrets
import time

import requests
from django.conf import settings
from django.core.cache import cache

TIMEOUT = 10


class OIDCError(Exception):
    pass


def get_config():
    conf = settings.PLUGIN_SETTINGS.get("pretalx_pretix_sso", {})
    return {
        "issuer": conf.get("issuer", "").rstrip("/"),
        "client_id": conf.get("client_id", ""),
        "client_secret": conf.get("client_secret", ""),
        "scope": conf.get("scope", "openid email profile"),
        "pretix_url": conf.get("pretix_url", ""),
        "organizer": conf.get("organizer", ""),
        "api_token": conf.get("api_token", ""),
        # "pretalx-slug=pretix-slug, other=other-pretix"
        "event_map": dict(
            (part.strip() for part in pair.split("=", 1))
            for pair in conf.get("event_map", "").split(",")
            if "=" in pair
        ),
    }


def is_configured():
    conf = get_config()
    return bool(conf["issuer"] and conf["client_id"] and conf["client_secret"])


def is_enabled(event):
    """SSO is only available for events that have the plugin switched on."""
    return "pretalx_pretix_sso" in event.plugin_list and is_configured()


def discovery():
    issuer = get_config()["issuer"]
    key = f"pretix_sso_discovery:{issuer}"
    data = cache.get(key)
    if data is None:
        response = requests.get(
            f"{issuer}/.well-known/openid-configuration", timeout=TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
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
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError) as e:
        raise OIDCError("Malformed ID token") from e


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
    tokens = response.json()

    claims = _decode_jwt_payload(tokens.get("id_token", ""))
    audience = claims.get("aud")
    audience = audience if isinstance(audience, list) else [audience]
    # Compare against the configured issuer, not whatever discovery returned
    if claims.get("iss", "").rstrip("/") != conf["issuer"]:
        raise OIDCError("ID token issuer mismatch")
    if conf["client_id"] not in audience:
        raise OIDCError("ID token audience mismatch")
    if claims.get("exp", 0) < time.time():
        raise OIDCError("ID token expired")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OIDCError("ID token nonce mismatch")

    response = requests.get(
        meta["userinfo_endpoint"],
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise OIDCError(f"Userinfo endpoint returned {response.status_code}")
    userinfo = response.json()
    if not claims.get("sub") or userinfo.get("sub") != claims.get("sub"):
        raise OIDCError("Userinfo subject mismatch")
    return userinfo
