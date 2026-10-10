# Path: tenders/integrations.py
"""
Third-party integration clients, all configured from IntegrationSettings:

  - MSGraphMailer   : send e-mail alerts & reports via Microsoft Graph API
  - WhatsAppClient  : send WhatsApp messages (WaSenderAPI first, extendable)
  - test_ldap_connection : verify LDAP server / service-account bind

Every send — successful or not — is recorded in NotificationLog via
log_notification(), the single logging chokepoint. Callers only pass a
`category` (and optionally `triggered_by`); the two send methods do the rest,
so every current and future caller is covered automatically.
"""
import logging

import requests

logger = logging.getLogger('tenders')


def log_notification(*, channel, category, recipient, subject, ok,
                     body='', is_html=False, error='', triggered_by=None):
    """
    Write one NotificationLog row. Best-effort by design: a failure to log
    must never stop (or mask the result of) an actual send, so every exception
    ends here.
    """
    try:
        from .models import NotificationLog
        if triggered_by is not None and not getattr(triggered_by, 'is_authenticated', False):
            triggered_by = None
        NotificationLog.objects.create(
            channel=channel,
            category=category or NotificationLog.Category.GENERAL,
            recipient=str(recipient)[:500],
            subject=str(subject)[:300],
            body=str(body)[:NotificationLog.BODY_MAX_CHARS],
            is_html=is_html,
            status=(NotificationLog.Status.SENT if ok else NotificationLog.Status.FAILED),
            error_message='' if ok else str(error)[:2000],
            triggered_by=triggered_by,
        )
    except Exception:                                        # pragma: no cover
        logger.exception('[notify-log] could not write NotificationLog row')


# ─────────────────────────────────────────────────────────────────────────────
# Microsoft Graph API — e-mail alerts & reports
# ─────────────────────────────────────────────────────────────────────────────

class MSGraphMailer:
    """
    Sends mail through Microsoft Graph. Two OAuth 2.0 modes:

    APP (client credentials — default):
      1. POST /oauth2/v2.0/token  grant_type=client_credentials
      2. POST /v1.0/users/{sender}/sendMail
      Requires Mail.Send *application* permission WITH ADMIN CONSENT.
      No mailbox password is ever asked — the admin consent replaces it.
      Without consent, sendMail returns 403 ErrorAccessDenied.

    DELEGATED (mailbox sign-in / ROPC):
      1. POST /oauth2/v2.0/token  grant_type=password
         with username = sender mailbox, password = mailbox password
      2. POST /v1.0/me/sendMail
      Requires Mail.Send *delegated* permission, "Allow public client flows"
      enabled on the app registration, and NO MFA on the mailbox account.

    The token is acquired FIRST and cached on IntegrationSettings with its
    expiry (~1 h), reused until 5 min before expiry, then auto-refreshed.
    A 401 during sendMail triggers one forced refresh + retry.
    """
    TOKEN_URL = 'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
    GRAPH_URL = 'https://graph.microsoft.com/v1.0'
    SCOPE     = 'https://graph.microsoft.com/.default'

    def __init__(self, cfg=None):
        from .models import IntegrationSettings
        self.cfg = cfg or IntegrationSettings.get()
        self.last_error = ''

    @property
    def delegated(self) -> bool:
        return self.cfg.graph_auth_mode == 'DELEGATED'

    @property
    def configured(self) -> bool:
        """Credentials present (regardless of the enable switch)."""
        c = self.cfg
        base = bool(c.graph_tenant_id and c.graph_client_id and c.graph_sender_email)
        if self.delegated:
            return base and bool(c.graph_mailbox_password)
        return base and bool(c.graph_client_secret)

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.graph_enabled and self.configured)

    # ── Token handling ───────────────────────────────────────────────────────

    def acquire_token(self, force: bool = False) -> tuple[bool, str]:
        """
        Step 1 of the integration: get an OAuth 2.0 access token from the
        Microsoft identity platform and cache it on IntegrationSettings.

        Returns (ok, detail). Reuses the cached token unless `force` or it
        is missing / about to expire.
        """
        if not self.configured:
            if self.delegated:
                return False, ('Delegated mode needs Tenant ID, Client ID, the sender '
                               'mailbox and its password. Fill them in, save, then get the token.')
            return False, ('Fill in Tenant ID, Client ID, Client Secret and the '
                           'sender mailbox, save, then get the token.')

        if not force and self.cfg.graph_token_is_valid():
            exp = self.cfg.graph_token_expires_at
            return True, f'Using cached token (valid until {exp:%Y-%m-%d %H:%M} UTC).'

        if self.delegated:
            data = {
                'client_id':  self.cfg.graph_client_id,
                'scope':      self.SCOPE,
                'grant_type': 'password',
                'username':   self.cfg.graph_sender_email,
                'password':   self.cfg.graph_mailbox_password,
            }
            # Confidential clients must still present their secret with ROPC.
            if self.cfg.graph_client_secret:
                data['client_secret'] = self.cfg.graph_client_secret
        else:
            data = {
                'client_id':     self.cfg.graph_client_id,
                'client_secret': self.cfg.graph_client_secret,
                'scope':         self.SCOPE,
                'grant_type':    'client_credentials',
            }

        try:
            resp = requests.post(
                self.TOKEN_URL.format(tenant=self.cfg.graph_tenant_id),
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                data=data,
                timeout=20,
            )
        except requests.RequestException as exc:
            return False, f'Could not reach login.microsoftonline.com: {exc}'

        if resp.status_code != 200:
            # Surface Microsoft's own error description when available.
            try:
                err = resp.json()
                detail = err.get('error_description') or err.get('error') or resp.text[:300]
            except ValueError:
                detail = resp.text[:300]
            if self.delegated and 'AADSTS' in str(detail):
                if 'AADSTS50126' in detail:
                    detail = ('Sign-in failed — wrong mailbox e-mail or password. ' + detail[:200])
                elif 'AADSTS50076' in detail or 'AADSTS50079' in detail:
                    detail = ('This mailbox requires MFA, which the delegated (password) flow '
                              'cannot satisfy. Disable MFA for this account or use '
                              'Application mode with admin consent. ' + detail[:200])
                elif 'AADSTS7000218' in detail:
                    detail = ('Enable "Allow public client flows" on the app registration '
                              '(Authentication blade), or add the client secret. ' + detail[:200])
            logger.error(f'[MSGraph] token request failed {resp.status_code}: {detail}')
            return False, f'Token request failed (HTTP {resp.status_code}): {detail}'

        data = resp.json()
        token = data.get('access_token')
        if not token:
            return False, 'Token endpoint answered 200 but no access_token was returned.'

        self.cfg.store_graph_token(token, data.get('expires_in', 3599))
        mode = 'delegated sign-in' if self.delegated else 'client credentials'
        logger.info(f'[MSGraph] access token acquired via {mode} '
                    f'(expires {self.cfg.graph_token_expires_at:%Y-%m-%d %H:%M} UTC).')
        return True, (f'Access token acquired ({mode}) — valid until '
                      f'{self.cfg.graph_token_expires_at:%Y-%m-%d %H:%M} UTC.')

    def _get_token(self) -> str | None:
        """Return a valid token — cached if fresh, otherwise re-acquired."""
        if self.cfg.graph_token_is_valid():
            return self.cfg.graph_access_token
        ok, detail = self.acquire_token(force=True)
        if not ok:
            logger.error(f'[MSGraph] token refresh failed: {detail}')
            return None
        return self.cfg.graph_access_token

    # ── Sending ──────────────────────────────────────────────────────────────

    def send_mail(self, subject: str, body_html: str, to: list[str],
                  cc: list[str] | None = None, *,
                  category: str = '', triggered_by=None,
                  attachments: list[tuple[str, bytes, str]] | None = None) -> bool:
        """
        Send an HTML e-mail. Returns True on success (detail in self.last_error).
        Every attempt — including ones refused before reaching Graph — is
        recorded in NotificationLog under `category`.
        `attachments`: optional list of (filename, content_bytes, mime_type),
        e.g. the daily News Brief PDF.
        """
        ok = self._send_mail(subject, body_html, to, cc, attachments)
        log_notification(
            channel='EMAIL', category=category,
            recipient=', '.join(to or []), subject=subject,
            body=body_html, is_html=True,
            ok=ok, error=self.last_error, triggered_by=triggered_by,
        )
        return ok

    def _send_mail(self, subject: str, body_html: str, to: list[str],
                   cc: list[str] | None = None,
                   attachments: list[tuple[str, bytes, str]] | None = None) -> bool:
        self.last_error = ''
        if not self.enabled:
            self.last_error = 'Integration disabled or not fully configured.'
            logger.warning('[MSGraph] send_mail skipped — integration disabled/incomplete.')
            return False

        token = self._get_token()
        if not token:
            self.last_error = ('No access token — open Integrations and click '
                               '"Get Access Token".')
            logger.error('[MSGraph] send_mail aborted — no access token.')
            return False

        payload = {
            'message': {
                'subject': subject,
                'body': {'contentType': 'HTML', 'content': body_html},
                'toRecipients': [{'emailAddress': {'address': a}} for a in to],
            },
            'saveToSentItems': True,
        }
        if cc:
            payload['message']['ccRecipients'] = [
                {'emailAddress': {'address': a}} for a in cc
            ]
        if attachments:
            import base64
            payload['message']['attachments'] = [
                {
                    '@odata.type': '#microsoft.graph.fileAttachment',
                    'name': name,
                    'contentType': mime or 'application/octet-stream',
                    'contentBytes': base64.b64encode(content).decode('ascii'),
                }
                for (name, content, mime) in attachments
            ]

        try:
            resp = self._post_sendmail(token, payload)
            # Token revoked / expired server-side → one forced refresh + retry.
            if resp.status_code == 401:
                logger.info('[MSGraph] 401 from sendMail — refreshing token and retrying.')
                ok, detail = self.acquire_token(force=True)
                if not ok:
                    self.last_error = detail
                    return False
                resp = self._post_sendmail(self.cfg.graph_access_token, payload)

            if resp.status_code == 202:
                logger.info(f'[MSGraph] mail sent: {subject!r} → {to}')
                return True

            if resp.status_code == 403:
                if self.delegated:
                    self.last_error = (
                        'Access denied (403). The signed-in mailbox lacks the Mail.Send '
                        '*delegated* permission — add it on the app registration '
                        '(API permissions → Microsoft Graph → Delegated → Mail.Send).'
                    )
                else:
                    self.last_error = (
                        'Access denied (403). The token is valid, but the app registration '
                        'is missing the Mail.Send *application* permission with ADMIN CONSENT '
                        '(Azure Portal → App registrations → API permissions → Add Microsoft '
                        'Graph → Application permissions → Mail.Send → "Grant admin consent"). '
                        'If you cannot get admin consent, switch the Authentication mode to '
                        '"Delegated" and enter the mailbox password instead.'
                    )
                logger.error(f'[MSGraph] sendMail 403: {self.last_error} '
                             f'| raw: {resp.text[:300]}')
                return False

            self.last_error = f'sendMail failed (HTTP {resp.status_code}): {resp.text[:300]}'
            logger.error(f'[MSGraph] sendMail failed {resp.status_code}: {resp.text[:400]}')
            return False
        except Exception as exc:
            self.last_error = str(exc)
            logger.exception(f'[MSGraph] send_mail error: {exc}')
            return False

    def _post_sendmail(self, token: str, payload: dict) -> requests.Response:
        # Delegated tokens act as the signed-in user → /me/sendMail.
        # Application tokens must target the mailbox → /users/{sender}/sendMail.
        endpoint = ('me' if self.delegated
                    else f'users/{self.cfg.graph_sender_email}')
        return requests.post(
            f'{self.GRAPH_URL}/{endpoint}/sendMail',
            headers={'Authorization': f'Bearer {token}',
                     'Content-Type': 'application/json'},
            json=payload, timeout=30,
        )

    # ── Diagnostics ──────────────────────────────────────────────────────────

    def test_connection(self) -> tuple[bool, str]:
        """Forces a fresh token request — verifies tenant / client / secret."""
        return self.acquire_token(force=True)


def send_alert_email(subject: str, body_html: str, to: list[str] | None = None,
                     *, category: str = 'ALERT', triggered_by=None) -> bool:
    """Convenience wrapper used by alerts / reports across the app."""
    from .models import IntegrationSettings
    cfg = IntegrationSettings.get()
    recipients = to or cfg.report_recipient_list()
    if not recipients:
        logger.warning('[MSGraph] no recipients configured — e-mail not sent.')
        return False
    return MSGraphMailer(cfg).send_mail(subject, body_html, recipients,
                                        category=category, triggered_by=triggered_by)


# ─────────────────────────────────────────────────────────────────────────────
# WhatsApp — provider-pluggable client (WaSenderAPI first)
# ─────────────────────────────────────────────────────────────────────────────

class WhatsAppClient:
    """
    Sends WhatsApp messages via the configured provider.

    WaSenderAPI (https://wasenderapi.com/api):
        POST {base}/send-message
        Authorization: Bearer <api key>
        JSON: {"to": "+9627xxxxxxx", "text": "hello"}
    """

    def __init__(self, cfg=None):
        from .models import IntegrationSettings
        self.cfg = cfg or IntegrationSettings.get()

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.wa_enabled and self.cfg.wa_api_url and self.cfg.wa_api_key)

    def send_message(self, to: str, text: str, *,
                     category: str = '', triggered_by=None) -> tuple[bool, str]:
        ok, detail = self._send(to, text)
        log_notification(
            channel='WHATSAPP', category=category,
            recipient=to, subject=text[:300],
            body=text, is_html=False,
            ok=ok, error=detail if not ok else '', triggered_by=triggered_by,
        )
        return ok, detail

    def _send(self, to: str, text: str) -> tuple[bool, str]:
        if not self.enabled:
            return False, 'WhatsApp integration is disabled or not configured.'
        provider = self.cfg.wa_provider
        try:
            if provider == 'WASENDER':
                return self._send_wasender(to, text)
            # CUSTOM providers assumed WaSender-compatible until adapters are added
            return self._send_wasender(to, text)
        except Exception as exc:
            logger.exception(f'[WhatsApp:{provider}] send error: {exc}')
            return False, str(exc)

    def _send_wasender(self, to: str, text: str) -> tuple[bool, str]:
        url = self.cfg.wa_api_url.rstrip('/') + '/send-message'
        resp = requests.post(
            url,
            headers={'Authorization': f'Bearer {self.cfg.wa_api_key}',
                     'Content-Type': 'application/json'},
            json={'to': to, 'text': text},
            timeout=30,
        )
        if resp.ok:
            logger.info(f'[WhatsApp:WASENDER] message sent → {to}')
            return True, f'Sent (HTTP {resp.status_code}).'
        logger.error(f'[WhatsApp:WASENDER] failed {resp.status_code}: {resp.text[:400]}')
        return False, f'HTTP {resp.status_code}: {resp.text[:300]}'

    def send_document(self, to: str, document_url: str, file_name: str,
                      caption: str = '', *,
                      category: str = '', triggered_by=None) -> tuple[bool, str]:
        """
        Send a document (e.g. the daily News Brief PDF) via WaSenderAPI's
        `documentUrl` message type. `document_url` must be reachable by the
        provider — build it from the public site URL + MEDIA path.
        """
        ok, detail = self._send_document(to, document_url, file_name, caption)
        log_notification(
            channel='WHATSAPP', category=category,
            recipient=to, subject=f'[document] {file_name}',
            body=f'{caption}\n{document_url}', is_html=False,
            ok=ok, error=detail if not ok else '', triggered_by=triggered_by,
        )
        return ok, detail

    def _send_document(self, to: str, document_url: str, file_name: str,
                       caption: str) -> tuple[bool, str]:
        if not self.enabled:
            return False, 'WhatsApp integration is disabled or not configured.'
        url = self.cfg.wa_api_url.rstrip('/') + '/send-message'
        payload = {'to': to, 'documentUrl': document_url, 'fileName': file_name}
        if caption:
            payload['text'] = caption
        try:
            resp = requests.post(
                url,
                headers={'Authorization': f'Bearer {self.cfg.wa_api_key}',
                         'Content-Type': 'application/json'},
                json=payload, timeout=30,
            )
        except Exception as exc:
            logger.exception(f'[WhatsApp] document send error: {exc}')
            return False, str(exc)
        if resp.ok:
            logger.info(f'[WhatsApp:WASENDER] document sent → {to} ({file_name})')
            return True, f'Sent (HTTP {resp.status_code}).'
        logger.error(f'[WhatsApp:WASENDER] document failed {resp.status_code}: '
                     f'{resp.text[:400]}')
        return False, f'HTTP {resp.status_code}: {resp.text[:300]}'

    def broadcast(self, text: str, recipients: list[str] | None = None, *,
                  category: str = '', triggered_by=None) -> dict:
        """Send to all default recipients. Returns {number: (ok, detail)}."""
        recipients = recipients or self.cfg.wa_recipient_list()
        return {num: self.send_message(num, text, category=category,
                                       triggered_by=triggered_by)
                for num in recipients}


def send_whatsapp_alert(text: str, to: list[str] | None = None,
                        *, category: str = 'ALERT', triggered_by=None) -> dict:
    """Convenience wrapper for alerts across the app."""
    return WhatsAppClient().broadcast(text, to, category=category,
                                      triggered_by=triggered_by)


# ─────────────────────────────────────────────────────────────────────────────
# LDAP — connectivity test (auth backend lives in tenders/auth_backends.py)
# ─────────────────────────────────────────────────────────────────────────────

def test_ldap_connection(cfg=None) -> tuple[bool, str]:
    from .models import IntegrationSettings
    cfg = cfg or IntegrationSettings.get()
    if not cfg.ldap_server_uri:
        return False, 'LDAP server URI is not configured.'
    try:
        from ldap3 import Server, Connection, ALL
    except ImportError:
        return False, "python package 'ldap3' is not installed — run: pip install ldap3"
    try:
        server = Server(cfg.ldap_server_uri, use_ssl=cfg.ldap_use_ssl,
                        get_info=ALL, connect_timeout=10)
        if cfg.ldap_bind_dn:
            conn = Connection(server, user=cfg.ldap_bind_dn,
                              password=cfg.ldap_bind_password,
                              auto_bind=True, receive_timeout=10)
            conn.unbind()
            return True, 'Server reachable and service-account bind succeeded.'
        conn = Connection(server, auto_bind=True, receive_timeout=10)  # anonymous
        conn.unbind()
        return True, 'Server reachable (anonymous bind). Configure a service account for user lookup.'
    except Exception as exc:
        return False, f'LDAP connection failed: {exc}'
