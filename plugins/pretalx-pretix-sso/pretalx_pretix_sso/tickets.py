"""Look up paid pretix orders for a pretalx event via the pretix REST API."""

import hashlib
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests
from django.core.cache import cache

from .oidc import TIMEOUT, get_config

logger = logging.getLogger(__name__)
CACHE_SECONDS = 3600 #Cache for an hour

# How a speaker is covered, strongest match first
PAID_ACCOUNT = "account"  # paid order placed with their linked pretix account
PAID_EMAIL = "email"  # paid order whose order/attendee email matches theirs
OVERRIDE = "override"  # an organiser marked them as covered


def email_hash(email):
    """SHA-256 of the normalised address, so attendee emails are never stored
    (the cache holds only these). The prefix is fixed rather than derived from
    SECRET_KEY so that every pretalx container computes the same hashes."""
    normalised = email.strip().lower()
    return hashlib.sha256(f"pretalx-pretix-sso:{normalised}".encode()).hexdigest()


@dataclass(frozen=True)
class TicketHolders:
    """Who holds a paid ticket for one pretix event."""

    email_hashes: frozenset
    customers: frozenset

    @classmethod
    def from_emails(cls, emails=(), customers=()):
        return cls(frozenset(email_hash(e) for e in emails), frozenset(customers))

    def has_email(self, email):
        return email_hash(email) in self.email_hashes

    def status(self, user, overridden=False):
        """Return how ``user`` is covered, or None if they are not."""
        if customer_identifier(user) in self.customers:
            return PAID_ACCOUNT
        if self.has_email(user.email):
            return PAID_EMAIL
        if overridden:
            return OVERRIDE
        return None


def customer_identifier(user):
    try:
        return user.pretix_customer.identifier
    except AttributeError:  # no linked account (RelatedObjectDoesNotExist)
        return None


def api_config():
    conf = get_config()
    issuer = urlsplit(conf["issuer"])
    # issuer is https://<pretix host>/<organizer>
    default_org = issuer.path.strip("/").split("/")[-1] if issuer.path else ""
    return {
        "base_url": (
            conf["pretix_url"]
            or (f"{issuer.scheme}://{issuer.netloc}" if issuer.netloc else "")
        ).rstrip("/"),
        "organizer": conf["organizer"] or default_org,
        "token": conf["api_token"],
        "event_map": conf["event_map"],
    }


def missing_settings():
    """Names of the settings the ticket check still needs."""
    conf = api_config()
    missing = []
    if not conf["token"]:
        missing.append("api_token")
    if not conf["base_url"]:  # no pretix_url and no issuer to derive it from
        missing.append("pretix_url")
    if not conf["organizer"]:  # issuer has no /<organizer> path, e.g. a custom domain
        missing.append("organizer")
    return missing


def is_configured():
    return not missing_settings()


def pretix_event_slug(event):
    return api_config()["event_map"].get(event.slug, event.slug)


class TicketLookupError(Exception):
    """pretix answered, but not with the data we expected."""


# Everything the ticket lookup can raise when pretix is down or misbehaving
LOOKUP_ERRORS = (requests.RequestException, TicketLookupError)


def _get_all(url, params):
    """Yield every result of a paginated pretix API list."""
    headers = {"Authorization": f"Token {api_config()['token']}"}
    while url:
        response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        try:
            data = response.json()
            results = data["results"]
        except (ValueError, KeyError, TypeError) as e:
            raise TicketLookupError(f"Unexpected response from {url}") from e
        yield from results
        url = data.get("next")
        params = None  # "next" already carries the query string


def _fetch_ticket_holders(event_slug):
    conf = api_config()
    base = (
        f"{conf['base_url']}/api/v1/organizers/{conf['organizer']}"
        f"/events/{event_slug}"
    )
    # Only products that grant entry count, not add-ons, merchandise or parking
    admission_items = {
        item["id"] for item in _get_all(f"{base}/items/", {"admission": "true"})
    }
    emails = set()
    customers = set()
    order_count = 0
    orders = _get_all(f"{base}/orders/", {"status": "p", "testmode": "false"})
    for order in orders:
        order_count += 1
        buyer_email = (order.get("email") or "").strip().lower()
        for position in order.get("positions", []):
            if position.get("item") not in admission_items:
                continue
            # The ticket belongs to the named attendee; the buyer only holds it
            # when no one else is named on it
            attendee_email = (position.get("attendee_email") or "").strip().lower()
            if attendee_email:
                emails.add(attendee_email)
            if not attendee_email or attendee_email == buyer_email:
                if buyer_email:
                    emails.add(buyer_email)
                if order.get("customer"):
                    customers.add(order["customer"])
    holders = TicketHolders.from_emails(emails, customers)
    logger.info(
        "pretix event %s: %d paid orders, %d admission products, %d ticket emails, "
        "%d pretix accounts",
        event_slug,
        order_count,
        len(admission_items),
        len(holders.email_hashes),
        len(holders.customers),
    )
    return holders


def _from_cache(key):
    # Only plain lists are cached, so a changed TicketHolders class can never
    # make old entries unreadable; anything unexpected just counts as a miss.
    try:
        data = cache.get(key)
        if data is None:
            return None
        return TicketHolders(
            email_hashes=frozenset(data["email_hashes"]),
            customers=frozenset(data["customers"]),
        )
    except Exception:
        logger.warning(
            "Ignoring unreadable pretix ticket cache entry %s", key, exc_info=True
        )
        return None


def _to_cache(key, holders):
    data = {
        "email_hashes": sorted(holders.email_hashes),
        "customers": sorted(holders.customers),
    }
    try:
        cache.set(key, data, CACHE_SECONDS)
    except Exception:
        logger.warning("Could not cache pretix ticket holders", exc_info=True)


def ticket_holders(event, refresh=False):
    """Return the paid ticket holders of the pretix event linked to ``event``."""
    slug = pretix_event_slug(event)
    key = f"pretix_sso_tickets:v5:{api_config()['organizer']}:{slug}"
    holders = None if refresh else _from_cache(key)
    if holders is not None:
        logger.debug("pretix tickets for event %s served from cache", event.slug)
        return holders
    logger.info(
        "Fetching pretix tickets for event %s from pretix event %s (%s)",
        event.slug,
        slug,
        "refresh requested" if refresh else "not cached",
    )
    started = time.monotonic()
    try:
        holders = _fetch_ticket_holders(slug)
    except LOOKUP_ERRORS:
        logger.warning(
            "Fetching pretix tickets for event %s failed after %.1fs",
            event.slug,
            time.monotonic() - started,
        )
        raise
    logger.info(
        "Fetched pretix tickets for event %s in %.1fs",
        event.slug,
        time.monotonic() - started,
    )
    _to_cache(key, holders)
    return holders
