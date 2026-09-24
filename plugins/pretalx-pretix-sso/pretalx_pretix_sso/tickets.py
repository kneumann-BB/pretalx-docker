"""Look up paid pretix orders for a pretalx event via the pretix REST API."""

from dataclasses import dataclass
from urllib.parse import urlsplit

import requests
from django.core.cache import cache

from .oidc import TIMEOUT, get_config

CACHE_SECONDS = 300

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


def _fetch_ticket_holders(event_slug):
    conf = api_config()
    url = (
        f"{conf['base_url']}/api/v1/organizers/{conf['organizer']}"
        f"/events/{event_slug}/orders/"
    )
    params = {"status": "p", "testmode": "false"}
    headers = {"Authorization": f"Token {conf['token']}"}
    emails = set()
    customers = set()
    while url:
        response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        for order in data["results"]:
            if order.get("customer"):
                customers.add(order["customer"])
            if order.get("email"):
                emails.add(order["email"].strip().lower())
            for position in order.get("positions", []):
                if position.get("attendee_email"):
                    emails.add(position["attendee_email"].strip().lower())
        url = data.get("next")
        params = None  # "next" already carries the query string
    return TicketHolders(emails=frozenset(emails), customers=frozenset(customers))


def ticket_holders(event, refresh=False):
    """Return the paid ticket holders of the pretix event linked to ``event``."""
    slug = pretix_event_slug(event)
    key = f"pretix_sso_tickets:v2:{api_config()['organizer']}:{slug}"
    holders = None if refresh else cache.get(key)
    if holders is None:
        holders = _fetch_ticket_holders(slug)
        cache.set(key, holders, CACHE_SECONDS)
    return holders
