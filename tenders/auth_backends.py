# Path: tenders/auth_backends.py
"""
Microsoft LDAP / Active Directory authentication backend.

Enabled at runtime from IntegrationSettings (System Admins → Integrations page)
— no server restart needed. Falls through to Django's ModelBackend when
disabled or when the LDAP bind fails (so local accounts keep working).
"""
import logging

from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import User

logger = logging.getLogger('tenders')


class LDAPBackend(BaseBackend):
    """Authenticate against Microsoft LDAP/AD using ldap3."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        from .models import IntegrationSettings
        try:
            cfg = IntegrationSettings.get()
        except Exception:
            return None  # table may not exist yet (pre-migration)

        if not cfg.ldap_enabled or not cfg.ldap_server_uri:
            return None

        try:
            from ldap3 import Server, Connection, ALL, SUBTREE
        except ImportError:
            logger.error("[LDAP] 'ldap3' package not installed — pip install ldap3")
            return None

        try:
            server = Server(cfg.ldap_server_uri, use_ssl=cfg.ldap_use_ssl,
                            get_info=ALL, connect_timeout=10)

            # Bind identity: UPN (user@domain) when a domain is configured
            bind_user = f'{username}@{cfg.ldap_domain}' if cfg.ldap_domain else username

            conn = Connection(server, user=bind_user, password=password,
                              auto_bind=True, receive_timeout=10)

            # Optional attribute lookup (email / names)
            attrs = {}
            if cfg.ldap_user_search_base:
                flt = (cfg.ldap_user_filter or '(sAMAccountName={username})') \
                    .replace('{username}', username)
                conn.search(cfg.ldap_user_search_base, flt, SUBTREE,
                            attributes=['mail', 'givenName', 'sn', 'displayName'])
                if conn.entries:
                    e = conn.entries[0]
                    attrs = {
                        'email':      str(e.mail) if 'mail' in e else '',
                        'first_name': str(e.givenName) if 'givenName' in e else '',
                        'last_name':  str(e.sn) if 'sn' in e else '',
                    }
            conn.unbind()

        except Exception as exc:
            logger.info(f'[LDAP] bind failed for {username!r}: {exc}')
            return None

        # Bind succeeded — map to a Django user
        user = User.objects.filter(username__iexact=username).first()
        if user is None:
            if not cfg.ldap_auto_create_users:
                logger.info(f'[LDAP] {username!r} authenticated but auto-create is off.')
                return None
            user = User(username=username)
            user.set_unusable_password()  # password lives only in AD

        for field in ('email', 'first_name', 'last_name'):
            val = attrs.get(field)
            if val and val != 'None':
                setattr(user, field, val)
        user.is_active = True
        user.save()
        logger.info(f'[LDAP] login OK: {username}')
        return user

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
