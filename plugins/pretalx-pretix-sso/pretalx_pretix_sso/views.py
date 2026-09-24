import logging
import secrets

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views import View
from django_scopes import scopes_disabled

from pretalx.event.models import Event
from pretalx.person.models import User

from . import oidc
from .models import PretixCustomer

logger = logging.getLogger(__name__)
SESSION_KEY = "pretix_sso"


def _redirect_uri():
    # Built from the configured site URL, not the request: behind a TLS-terminating
    # proxy the request looks like plain http, and the Host header is client input.
    return settings.SITE_URL.rstrip("/") + reverse("plugins:pretalx_pretix_sso:callback")


class LoginStartView(View):
    def get(self, request, *args, **kwargs):
        event = request.event
        if not oidc.is_enabled(event):
            raise Http404()
        if request.user.is_authenticated:
            return redirect(event.urls.user_submissions)

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier, challenge = oidc.new_pkce_pair()
        next_url = request.GET.get("next", "")
        if not url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}
        ):
            next_url = ""
        request.session[SESSION_KEY] = {
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "event": event.slug,
            "next": next_url,
        }
        try:
            url = oidc.authorization_url(_redirect_uri(), state, nonce, challenge)
        except requests.RequestException:
            logger.exception("pretix SSO discovery failed")
            messages.error(request, _("Login with pretix is currently unavailable."))
            return redirect(event.urls.login)
        return redirect(url)


class CallbackView(View):
    def get(self, request, *args, **kwargs):
        data = request.session.pop(SESSION_KEY, None)
        if not data:
            raise Http404()
        with scopes_disabled():
            event = Event.objects.filter(slug=data["event"]).first()
        if not event or not oidc.is_enabled(event):
            raise Http404()

        def fail(message):
            messages.error(request, message)
            return redirect(event.urls.login)

        if request.GET.get("error"):
            return fail(_("Login with pretix was cancelled."))
        state = request.GET.get("state", "")
        code = request.GET.get("code", "")
        if not code or not secrets.compare_digest(state, data["state"]):
            return fail(_("Login with pretix failed, please try again."))

        try:
            userinfo = oidc.fetch_userinfo(
                code, _redirect_uri(), data["nonce"], data["verifier"]
            )
        except (oidc.OIDCError, requests.RequestException, ValueError, KeyError):
            logger.exception("pretix SSO token exchange failed")
            return fail(_("Login with pretix failed, please try again."))

        email = (userinfo.get("email") or "").strip().lower()
        # Accounts are matched by email, so only trust addresses pretix has verified
        if not email or userinfo.get("email_verified") is not True:
            return fail(_("Your pretix account has no verified email address."))

        # Prefer the pretix account link: it survives email changes on either side
        link = PretixCustomer.objects.filter(identifier=userinfo["sub"]).first()
        user = link.user if link else User.objects.filter(email__iexact=email).first()
        if not user:
            name = userinfo.get("name") or email.split("@")[0]
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        password=None,
                        email=email,
                        name=name[:120],
                        locale=getattr(request, "LANGUAGE_CODE", event.locale),
                        timezone=event.timezone,
                    )
            except IntegrityError:
                # A concurrent callback created the account first
                user = User.objects.filter(email__iexact=email).first()
                if not user:
                    raise

        # SSO is only for speakers: organisers and admins must use their password.
        if user.is_administrator or user.is_superuser or user.teams.exists():
            return fail(
                _(
                    "This account belongs to an organiser. Please log in with your password."
                )
            )
        if not user.is_active:
            return fail(_("This account has been deactivated."))

        # Remember the pretix account so its orders count as this speaker's tickets.
        # The latest login wins if the user switched pretix accounts.
        PretixCustomer.objects.update_or_create(
            user=user, defaults={"identifier": userinfo["sub"]}
        )

        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        if data["next"] and url_has_allowed_host_and_scheme(
            data["next"], allowed_hosts={request.get_host()}
        ):
            return redirect(data["next"])
        return redirect(event.urls.user_submissions)
