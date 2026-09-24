import logging

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from pretalx.common.views.mixins import EventPermissionRequired
from pretalx.orga.views.speaker import get_speaker_profiles_for_user
from pretalx.submission.models import SubmissionStates, Tag

from . import tickets
from .models import TicketOverride

logger = logging.getLogger(__name__)
TICKET_TAG = "needTicket"


def _ticket_tag(event):
    """The event's needTicket tag, created if missing. pretalx does not enforce
    unique tag names, so if duplicates exist the oldest one is used."""
    tags = list(Tag.objects.filter(event=event, tag=TICKET_TAG).order_by("pk"))
    if len(tags) > 1:
        logger.warning(
            "Event %s has %d %r tags; syncing the oldest", event.slug, len(tags), TICKET_TAG
        )
    return tags[0] if tags else Tag.objects.create(
        event=event, tag=TICKET_TAG, color="#b23e65"
    )


def _overridden_user_ids(event):
    return set(
        TicketOverride.objects.filter(event=event).values_list("user_id", flat=True)
    )


class TicketCheckView(EventPermissionRequired, TemplateView):
    # The page renders immediately with a loading indicator; tickets.js then
    # fetches the table (?partial=1), which is the slow part that calls pretix.
    template_name = "pretalx_pretix_sso/tickets.html"
    partial_template_name = "pretalx_pretix_sso/_tickets_table.html"
    permission_required = "orga.view_speakers"

    @property
    def is_partial(self):
        return self.request.GET.get("partial") == "1"

    def get_template_names(self):
        return [self.partial_template_name if self.is_partial else self.template_name]

    def dispatch(self, request, *args, **kwargs):
        if "pretalx_pretix_sso" not in request.event.plugin_list:
            raise Http404()
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm("orga.change_submissions", request.event):
            raise Http404()
        action = request.POST.get("action")
        if action == "sync_tag":
            self._sync_tag(request)
        elif action in ("override_on", "override_off"):
            self._set_override(request, action == "override_on")
        else:
            raise Http404()
        # Back to the page, but without ?refresh=1: the action already fetched
        # fresh data where needed, and reloads should use the cache again
        query = request.GET.copy()
        query.pop("refresh", None)
        return redirect(f"{request.path}?{query.urlencode()}" if query else request.path)

    def _set_override(self, request, enabled):
        event = request.event
        profile = (
            get_speaker_profiles_for_user(request.user, event)
            .filter(user__code=request.POST.get("user"))
            .select_related("user")
            .first()
        )
        if not profile:
            raise Http404()
        user = profile.user
        if enabled:
            TicketOverride.objects.get_or_create(
                event=event, user=user, defaults={"created_by": request.user}
            )
        else:
            TicketOverride.objects.filter(event=event, user=user).delete()
        logger.info(
            "User %s %s the ticket override for speaker %s on event %s",
            request.user.code,
            "set" if enabled else "removed",
            user.code,
            event.slug,
        )
        event.log_action(
            "pretalx_pretix_sso.override." + ("set" if enabled else "removed"),
            person=request.user,
            orga=True,
            data={"speaker": user.code},
        )

    def _sync_tag(self, request):
        event = request.event
        if not tickets.is_configured():
            logger.info(
                "Tag sync on event %s skipped: ticket check not configured", event.slug
            )
            return
        logger.info(
            "User %s started the %s tag sync on event %s",
            request.user.code,
            TICKET_TAG,
            event.slug,
        )
        try:
            holders = tickets.ticket_holders(event, refresh=True)
        except tickets.LOOKUP_ERRORS:
            logger.exception("pretix ticket lookup failed")
            messages.error(request, _("Could not load orders from pretix."))
            return

        overridden = _overridden_user_ids(event)
        added = removed = 0
        with transaction.atomic():
            tag = _ticket_tag(event)
            tagged = set(tag.submissions.values_list("pk", flat=True))
            submissions = event.submissions.prefetch_related(
                "speakers__pretix_customer"
            )
            # Every proposal in any state and of any submission type (pretalx
            # already leaves out drafts and deleted ones) needs a ticket until
            # any of its speakers is covered
            for submission in submissions:
                needs_ticket = not any(
                    holders.status(speaker, speaker.pk in overridden)
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
            "pretalx_pretix_sso.tag.synced",
            person=request.user,
            orga=True,
            data={"tag": TICKET_TAG, "added": added, "removed": removed},
        )
        messages.success(
            request,
            _(
                'Tag "{tag}" updated: added to {added}, removed from {removed} proposals.'
            ).format(tag=TICKET_TAG, added=added, removed=removed),
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        ctx["missing_settings"] = tickets.missing_settings()
        ctx["configured"] = not ctx["missing_settings"]
        ctx["pretix_event"] = tickets.pretix_event_slug(event)
        ctx["ticket_tag"] = TICKET_TAG
        ctx["can_edit"] = self.request.user.has_perm("orga.change_submissions", event)
        ctx["only_accepted"] = self.request.GET.get("accepted") == "1"
        ctx["rows"] = []
        if not ctx["configured"]:
            return ctx
        if not self.is_partial:
            query = self.request.GET.copy()
            query["partial"] = "1"
            ctx["table_url"] = f"{self.request.path}?{query.urlencode()}"
            return ctx
        try:
            holders = tickets.ticket_holders(
                event, refresh="refresh" in self.request.GET
            )
        except tickets.LOOKUP_ERRORS:
            logger.exception("pretix ticket lookup failed")
            ctx["load_error"] = True
            return ctx

        overridden = _overridden_user_ids(event)
        profiles = (
            get_speaker_profiles_for_user(self.request.user, event)
            .select_related("user", "user__pretix_customer")
            .annotate(
                accepted_count=Count(
                    "user__submissions",
                    filter=Q(user__submissions__event=event)
                    & Q(user__submissions__state__in=SubmissionStates.accepted_states),
                    distinct=True,
                )
            )
            .order_by("-accepted_count", "user__name")
        )
        for profile in profiles:
            if ctx["only_accepted"] and not profile.accepted_count:
                continue
            ctx["rows"].append(
                {
                    "profile": profile,
                    "status": holders.status(
                        profile.user, profile.user_id in overridden
                    ),
                    "overridden": profile.user_id in overridden,
                    "linked": tickets.customer_identifier(profile.user) is not None,
                }
            )
        # Only speakers who are actually on the programme need a ticket
        ctx["missing_count"] = sum(
            1
            for row in ctx["rows"]
            if not row["status"] and row["profile"].accepted_count
        )
        return ctx
