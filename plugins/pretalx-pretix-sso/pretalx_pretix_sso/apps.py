from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PluginApp(AppConfig):
    name = "pretalx_pretix_sso"
    verbose_name = _("pretix SSO")

    class PretalxPluginMeta:
        name = _("pretix SSO")
        author = "Wildfire Retreat"
        version = "0.1.0"
        visible = True
        description = _(
            "Lets speakers log in to the CfP with their pretix customer account."
        )
        category = "INTEGRATION"

    def ready(self):
        from . import signals  # noqa: F401
        from .restrict import patch_views

        patch_views()
