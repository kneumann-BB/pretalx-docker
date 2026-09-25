"""Keep the needsTicket tag on the proposals whose speakers have no ticket."""

import logging

from django.db import transaction

from pretalx.common.models import ActivityLog
from pretalx.submission.models import Tag

from .models import TicketOverride

logger = logging.getLogger(__name__)
TICKET_TAG = "needsTicket"
SYNC_ACTION = "pretalx_pretix_sso.tag.synced"
SYNC_FAILED_ACTION = "pretalx_pretix_sso.tag.sync_failed"
# Why a sync failed, as stored in the activity log
FAILED_PRETIX = "pretix"  # pretix unreachable or returned unexpected data
FAILED_ERROR = "error"  # anything else; details are in the server log


def ticket_tag(event):
    """The event's needsTicket tag, created if missing (tag names are unique
    per event)."""
    tag, _created = Tag.objects.get_or_create(
        event=event, tag=TICKET_TAG, defaults={"color": "#b23e65"}
    )
    return tag


def overridden_user_ids(event):
    return set(
        TicketOverride.objects.filter(event=event).values_list("user_id", flat=True)
    )


def sync_ticket_tag(event, holders, user):
    """Tag every proposal without a covered speaker, untag the rest, and record
    the result in the activity log. Returns (added, removed)."""
    overridden = overridden_user_ids(event)
    added = removed = 0
    with transaction.atomic():
        tag = ticket_tag(event)
        tagged = set(tag.submissions.values_list("pk", flat=True))
        # Speakers are SpeakerProfiles; tickets and overrides belong to their user
        submissions = event.submissions.prefetch_related(
            "speakers__user__pretix_customer"
        )
        # Every proposal in any state and of any submission type (pretalx
        # already leaves out drafts and deleted ones) needs a ticket until
        # any of its speakers is covered
        for submission in submissions:
            needs_ticket = not any(
                holders.status(speaker.user, speaker.user_id in overridden)
                for speaker in submission.speakers.all()
            )
            if needs_ticket and submission.pk not in tagged:
                submission.tags.add(tag)
                added += 1
            elif not needs_ticket and submission.pk in tagged:
                submission.tags.remove(tag)
                removed += 1
    logger.info(
        "%s tag sync on event %s: added to %d, removed from %d proposals",
        TICKET_TAG,
        event.slug,
        added,
        removed,
    )
    event.log_action(
        SYNC_ACTION,
        person=user,
        orga=True,
        data={"tag": TICKET_TAG, "added": added, "removed": removed},
    )
    return added, removed


def record_sync_failure(event, user, reason):
    """Put a failed sync in the activity log, so organisers see it failed
    rather than just never finishing."""
    event.log_action(
        SYNC_FAILED_ACTION,
        person=user,
        orga=True,
        data={"tag": TICKET_TAG, "reason": reason},
    )


def last_sync(event):
    """The most recent tag sync's activity log entry, successful or failed."""
    return (
        ActivityLog.objects.filter(
            event=event, action_type__in=(SYNC_ACTION, SYNC_FAILED_ACTION)
        )
        .select_related("person")
        .order_by("-timestamp", "-pk")
        .first()
    )
