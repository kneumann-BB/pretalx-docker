from unittest import mock

import pytest

from pretalx_pretix_sso import tickets
from pretalx_pretix_sso.models import PretixCustomer

ITEMS = {"results": [{"id": 1, "admission": True}], "next": None}
ORDERS_PAGE_1 = {
    "results": [
        # add-on/merch only: not a ticket
        {"email": "merch@example.invalid", "customer": "MERCH",
         "positions": [{"item": 99, "attendee_email": None}]},
        # bought for someone else: the attendee holds it, not the buyer
        {"email": "Buyer@example.invalid", "customer": "BUYER",
         "positions": [{"item": 1, "attendee_email": "Friend@example.invalid"}]},
    ],
    "next": "https://pretix.example.invalid/orders/?page=2",
}
ORDERS_PAGE_2 = {
    "results": [
        # no attendee named: the buyer holds it
        {"email": "self@example.invalid", "customer": "SELF",
         "positions": [{"item": 1, "attendee_email": None}, {"item": 99}]},
        # attendee is the buyer: account counts too
        {"email": "named@example.invalid", "customer": "NAMED",
         "positions": [{"item": 1, "attendee_email": "NAMED@example.invalid"}]},
    ],
    "next": None,
}


def _response(data=None, bad_json=False):
    response = mock.Mock()
    response.raise_for_status = lambda: None
    if bad_json:
        response.json.side_effect = ValueError("not json")
    else:
        response.json.return_value = data
    return response


def _fake_get(url, params=None, headers=None, timeout=None):
    assert headers == {"Authorization": "Token test-token"}
    if "/items/" in url:
        assert params == {"admission": "true"}
        return _response(ITEMS)
    if "page=2" in url:
        assert params is None  # "next" already carries the query
        return _response(ORDERS_PAGE_2)
    assert params == {"status": "p", "testmode": "false"}
    return _response(ORDERS_PAGE_1)


@pytest.fixture
def holders():
    with mock.patch.object(tickets.requests, "get", side_effect=_fake_get) as get:
        result = tickets._fetch_ticket_holders("test")
    assert get.call_args_list[0].args[0] == (
        "https://pretix.example.invalid/api/v1/organizers/org/events/test/items/"
    )
    return result


def test_only_admission_items_count(holders):
    assert "merch@example.invalid" not in holders.emails
    assert "MERCH" not in holders.customers


def test_named_attendee_holds_ticket_not_buyer(holders):
    assert "friend@example.invalid" in holders.emails
    assert "buyer@example.invalid" not in holders.emails
    assert "BUYER" not in holders.customers


def test_unnamed_ticket_belongs_to_buyer_across_pages(holders):
    assert "self@example.invalid" in holders.emails
    assert "SELF" in holders.customers


def test_attendee_equal_to_buyer_counts_account(holders):
    assert "named@example.invalid" in holders.emails
    assert "NAMED" in holders.customers


@pytest.mark.parametrize("response", [_response(bad_json=True), _response({"detail": "?"})])
def test_malformed_response_raises_lookup_error(response):
    with mock.patch.object(tickets.requests, "get", return_value=response):
        with pytest.raises(tickets.TicketLookupError):
            tickets._fetch_ticket_holders("test")


def test_status_priority(make_speaker):
    linked = make_speaker("linked@example.invalid")
    PretixCustomer.objects.create(user=linked, identifier="CUST1")
    by_email = make_speaker("paid@example.invalid")
    nobody = make_speaker("nobody@example.invalid")
    holders = tickets.TicketHolders(
        emails=frozenset({"paid@example.invalid", "linked@example.invalid"}),
        customers=frozenset({"CUST1"}),
    )
    linked.refresh_from_db()
    assert holders.status(linked) == tickets.PAID_ACCOUNT
    assert holders.status(by_email, overridden=True) == tickets.PAID_EMAIL
    assert holders.status(nobody, overridden=True) == tickets.OVERRIDE
    assert holders.status(nobody) is None
    assert tickets.customer_identifier(nobody) is None


def test_cached_until_refresh(event):
    first = tickets.TicketHolders(frozenset({"a@example.invalid"}), frozenset())
    second = tickets.TicketHolders(frozenset({"b@example.invalid"}), frozenset())
    with mock.patch.object(tickets, "_fetch_ticket_holders", side_effect=[first, second]):
        assert tickets.ticket_holders(event) == first
        assert tickets.ticket_holders(event) == first
        assert tickets.ticket_holders(event, refresh=True) == second


def test_event_map_selects_pretix_event(event, monkeypatch):
    assert tickets.pretix_event_slug(event) == "test"
    monkeypatch.setenv("PRETALX_PRETIX_SSO_EVENT_MAP", "test=pretix-test")
    assert tickets.pretix_event_slug(event) == "pretix-test"


def test_organizer_and_base_url_derived_from_issuer():
    conf = tickets.api_config()
    assert conf["base_url"] == "https://pretix.example.invalid"
    assert conf["organizer"] == "org"
