from django.dispatch import receiver
from django.templatetags.static import static
from django.urls import resolve, reverse
from django.utils.html import format_html
from django.utils.http import urlencode
from django.utils.translation import gettext

from pretalx.cfp.signals import html_head
from pretalx.orga.signals import nav_event

from .oidc import is_configured

LOGIN_URL_NAMES = {"event.login"}


@receiver(html_head, dispatch_uid="pretix_sso_html_head")
def add_login_button(sender, request, **kwargs):
    match = getattr(request, "resolver_match", None)
    if not match or "cfp" not in match.namespaces:
        return ""
    # The wizard rewrites resolver_match.kwargs["step"] to the next step while
    # rendering, so read the current step from the URL itself.
    in_wizard = (
        match.url_name == "event.submit"
        and resolve(request.path_info).kwargs.get("step") == "user"
    )
    if not (match.url_name in LOGIN_URL_NAMES or in_wizard) or not is_configured():
        return ""
    if request.user.is_authenticated:
        return ""
    login_url = reverse(
        "plugins:pretalx_pretix_sso:login", kwargs={"event": sender.slug}
    )
    # In the submission wizard, come back to the same step after logging in
    next_url = request.path if in_wizard else request.GET.get("next")
    if next_url:
        login_url += "?" + urlencode({"next": next_url})
    return format_html(
        '<meta name="pretix-sso-login" content="{}" data-label="{}" data-or="{}"'
        ' data-exclusive="{}">'
        '<script defer src="{}"></script>',
        login_url,
        gettext("Log in with pretix"),
        gettext("or"),
        # In the wizard, pretix SSO replaces password login and registration;
        # on the login page it replaces registration only.
        "all" if in_wizard else "register",
        static("pretalx_pretix_sso/login.js"),
    )


@receiver(nav_event, dispatch_uid="pretix_sso_nav_event")
def add_tickets_nav(sender, request, **kwargs):
    if not request.user.has_perm("orga.view_speakers", sender):
        return []
    url = reverse("plugins:pretalx_pretix_sso:tickets", kwargs={"event": sender.slug})
    return [
        {
            "label": gettext("pretix tickets"),
            "url": url,
            "active": request.path.startswith(url),
            "icon": "ticket",
        }
    ]

