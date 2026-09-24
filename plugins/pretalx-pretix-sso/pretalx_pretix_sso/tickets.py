"""Look up paid pretix orders for a pretalx event via the pretix REST API."""

from dataclasses import dataclass
from urllib.parse import urlsplit

import requests
from django.core.cache import cache

from .oidc import TIMEOUT, get_config

CACHE_SECONDS = 3600 #Cache for an hour

# How a speaker is covered, strongest match first
PAID_ACCOUNT = "account"  # paid order placed with their linked pretix account
PAID_EMAIL = "email"  # paid order whose order/attendee email matches theirs
OVERRIDE = "override"  # an organiser marked them as covered


@dataclass(frozen=True)
class TicketHolders:
    """Who holds a paid ticket for one pretix event."""

    emails: frozenset
    customers: frozenset

    def status(self, user, overridden=False):
        """Return how ``user`` is covered, or None if they are not."""
        if customer_identifier(user) in self.customers:
            return PAID_ACCOUNT
        if user.email.lower() in self.emails:
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
            conf["pretix_url"] or f"{issuer.scheme}://{issuer.netloc}"
        ).rstrip("/"),
        "organizer": conf["organizer"] or default_org,
        "token": conf["api_token"],
        "event_map": conf["event_map"],
    }


def is_configured():
    conf = api_config()
    return bool(conf["base_url"] and conf["organizer"] and conf["token"])


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
    orders = _get_all(f"{base}/orders/", {"status": "p", "testmode": "false"})
    for order in orders:
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
    return TicketHolders(emails=frozenset(emails), customers=frozenset(customers))


def ticket_holders(event, refresh=False):
    """Return the paid ticket holders of the pretix event linked to ``event``."""
    slug = pretix_event_slug(event)
    key = f"pretix_sso_tickets:v3:{api_config()['organizer']}:{slug}"
    holders = None if refresh else cache.get(key)
    if holders is None:
        holders = _fetch_ticket_holders(slug)
        cache.set(key, holders, CACHE_SECONDS)
    return holders
