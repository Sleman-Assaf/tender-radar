# tenders/permissions.py
"""
Role-Based Access Control for TenderRadar.

Single source of truth for "who may do what". Views must gate on the
decorators / predicates here rather than testing `is_superuser` or
`profile.role` directly, so the policy can change in one place.

Roles (tenders.models.SystemRole):

    SUPER_ADMIN   unrestricted — every module, every setting, all administration
    USER          the tender modules only

The System Administrator (IT) role was removed on 2026-08-24; the Super Admin
now does everything. The predicate layer is kept so a future role can be
reintroduced by changing this module alone.

Anonymous callers are redirected to the login page; authenticated callers
without the required role get HTTP 403 (never a redirect — a redirect would
look like "log in again" for someone who is already logged in).
"""

from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse


# ── Role resolution ───────────────────────────────────────────────────────────

def get_role(user) -> str | None:
    """
    The effective role of `user`, or None when they cannot act at all.

    `is_superuser` wins over the stored role so that a superuser created on the
    command line (`createsuperuser`) is never locked out of their own system
    before a profile exists.
    """
    from .models import SystemRole

    if not user or not user.is_authenticated or not user.is_active:
        return None
    if user.is_superuser:
        return SystemRole.SUPER_ADMIN

    profile = getattr(user, 'profile', None)
    if profile is None:
        return SystemRole.USER
    return profile.role or SystemRole.USER


def get_role_display(user) -> str:
    from .models import SystemRole
    role = get_role(user)
    return dict(SystemRole.choices).get(role, '—')


# ── Predicates ────────────────────────────────────────────────────────────────

def is_super_admin(user) -> bool:
    from .models import SystemRole
    return get_role(user) == SystemRole.SUPER_ADMIN


def can_administer(user) -> bool:
    """
    True for the role allowed into the System Dashboard and every module it
    fronts — the Super Admin.
    """
    return is_super_admin(user)


def can_manage_users(user) -> bool:
    return can_administer(user)


def can_manage_departments(user) -> bool:
    return can_administer(user)


def can_manage_backups(user) -> bool:
    return can_administer(user)


def can_manage_settings(user) -> bool:
    return can_administer(user)


# ── Teams ─────────────────────────────────────────────────────────────────────
# Teams predate this module and were gated inline on
# `groups.filter(name='Team Leaders') or is_superuser`, which this module exists
# to stop. These two predicates replace that check.

TEAM_LEADER_GROUP = 'Team Leaders'


def is_team_leader(user) -> bool:
    """A member of the Team Leaders group. Says nothing about admin rights."""
    if not user or not user.is_authenticated:
        return False
    return user.groups.filter(name=TEAM_LEADER_GROUP).exists()


def can_create_team(user) -> bool:
    """Team Leaders run their own teams; administrators may also create them."""
    return is_team_leader(user) or can_administer(user)


def can_administer_teams(user) -> bool:
    """
    May act on *any* team: see it, edit it, change its leader, delete it.

    Deliberately narrower than can_create_team() — a Team Leader is trusted with
    their own team, not with everybody else's.
    """
    return can_administer(user)


def can_edit_team(user, team) -> bool:
    """The team's own leader, or an administrator."""
    if not user or not user.is_authenticated or team is None:
        return False
    return team.leader_id == user.pk or can_administer_teams(user)


def can_view_department(user, department) -> bool:
    """
    Regular users see only their own department; administrators see all.
    """
    if can_administer(user):
        return True
    profile = getattr(user, 'profile', None)
    return bool(profile and department and profile.department_id == department.pk)


def assignable_roles(user) -> list[tuple[str, str]]:
    """
    Roles `user` is allowed to hand out. Only a Super Admin assigns roles at
    all — whoever can grant a role can also set that account's password, so
    role assignment is the same power as acting through the account.
    """
    from .models import SystemRole
    if is_super_admin(user):
        return list(SystemRole.choices)
    return []


def can_assign_role(user, role: str) -> bool:
    return role in dict(assignable_roles(user))


def can_edit_user(actor, target) -> bool:
    """
    Who may edit, deactivate, reset the password of, or delete `target`.

    Only a Super Admin administers accounts, and a Super Admin may administer
    everybody — including other Super Admins. The last-active-Super-Admin and
    self-deactivation guards live in the views (tenders/it_views.py).
    """
    return can_administer(actor)


# ── Decorators ────────────────────────────────────────────────────────────────

def _deny(request, message: str):
    """403 for browsers, JSON 403 for fetch()/XHR callers."""
    from .models import AdminAuditLog

    AdminAuditLog.record(
        request, AdminAuditLog.Action.ACCESS_DENIED,
        target=request.path, detail=message,
    )
    wants_json = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('Accept', '')
    )
    if wants_json:
        return JsonResponse({'error': 'forbidden', 'detail': message}, status=403)
    raise PermissionDenied(message)


def role_required(predicate, message='You do not have permission to access this page.'):
    """
    Build a view decorator from a predicate over the request user.

    Anonymous → login redirect. Authenticated but unauthorised → HTTP 403.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(
                    request.get_full_path(),
                    settings.LOGIN_URL,
                )
            if not user.is_active or not predicate(user):
                return _deny(request, message)
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


#: Super Admin only — role assignment to Super Admin, destructive settings.
super_admin_required = role_required(
    is_super_admin,
    'Super Admin access required.',
)

#: Super Admin — everything reached from the System Dashboard. Kept as its own
#: name (rather than aliasing super_admin_required) so a future non-super admin
#: role only needs can_administer() widened.
admin_required = role_required(
    can_administer,
    'System Administration access required.',
)
