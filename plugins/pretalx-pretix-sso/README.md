# pretalx-pretix-sso

Adds a "Log in with pretix" button to the CfP login page. Speakers sign in with
their pretix customer account via OpenID Connect. Organisers, reviewers and
admins (anyone on a team) are refused and must use their password.

## Setup

1. In pretix: Organizer → Customer accounts → SSO clients → add a client
   (confidential, authorization code) with redirect URI
   `https://<pretalx host>/p/pretix-sso/callback/`.
2. Configure pretalx (`pretalx.cfg`, or the `pretix_sso_*` OpenTofu variables):

   ```ini
   [plugin:pretalx_pretix_sso]
   issuer = https://pretix.eu/<organizer>
   client_id = ...
   client_secret = ...
   ```

3. Enable the "pretix SSO" plugin in each event's settings.

New users are created from the pretix email and name; existing speaker accounts
are matched by email.

In the submission wizard, pretix is the only sign-in option: the password login
and registration form is hidden and refused. The regular login page keeps
password login for organisers, but its registration form is hidden and refused,
so new accounts can only be created through pretix.

## Ticket check (organisers)

The organiser sidebar gets a "pretix tickets" page listing speakers and
whether they hold a paid order in the pretix event with the same slug. Speakers
are matched first by the pretix account they last logged in with (linked on
each SSO login), then by order or attendee email. Organisers can mark a speaker
as covered (e.g. a complimentary ticket), and sync a `needTicket` tag onto
accepted/confirmed proposals where no speaker is covered. Configure in the same
section:

```ini
api_token = <pretix team API token with "Can view orders">
# optional, when slugs differ: pretalx-slug=pretix-slug, ...
event_map = conf2026=conference-2026
# optional, derived from issuer by default
pretix_url = https://pretix.eu
organizer = myorg
```
