# bd/permissions.py
# Path: TenderRadar/bd/permissions.py

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

BD_GROUP        = 'Business Developers'
HEAD_UNIT_GROUP = 'Head of Unit'


def is_bd(user) -> bool:
    """Business Developers, Heads of Unit, and admins may use the BD module."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    return user.groups.filter(name__in=[BD_GROUP, HEAD_UNIT_GROUP]).exists()


def is_head_of_unit(user) -> bool:
    """Go/No-Go approvers (Phase 2)."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=HEAD_UNIT_GROUP).exists()


def bd_required(view):
    @wraps(view)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not is_bd(request.user):
            raise PermissionDenied('Business Development access required.')
        return view(request, *args, **kwargs)
    return _wrapped
