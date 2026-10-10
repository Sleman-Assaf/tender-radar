# tenders/context_processors.py

def active_countries(request):
    """No longer used in sidebar — returns empty dict."""
    return {}


def notification_context(request):
    """Adds unread notification counts to every template."""
    if request.user.is_authenticated:
        from .models import NoteNotification, FeedbackNotification
        note_count = NoteNotification.objects.filter(
            recipient=request.user, is_read=False
        ).count()
        feedback_count = FeedbackNotification.objects.filter(
            feedback__author=request.user, is_read=False
        ).count()
        total = note_count + feedback_count
        return {
            'unread_notifications':      total,
            'unread_note_notifications': note_count,
            'unread_feedback_responses': feedback_count,
        }
    return {
        'unread_notifications':      0,
        'unread_note_notifications': 0,
        'unread_feedback_responses': 0,
    }


def rbac_context(request):
    """
    Exposes the caller's role to every template so navigation can hide what
    they may not use. Hiding is cosmetic only — the views enforce the same
    rules server-side via tenders.permissions.
    """
    from .permissions import can_administer, get_role, get_role_display, is_super_admin

    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {
            'user_role':          None,
            'user_role_display':  '',
            'is_super_admin':     False,
            'can_administer':     False,
        }
    return {
        'user_role':          get_role(user),
        'user_role_display':  get_role_display(user),
        'is_super_admin':     is_super_admin(user),
        'can_administer':     can_administer(user),
    }


def bd_context(request):
    """Exposes BD-module access so the navigation can show/hide its entry."""
    try:
        from bd.permissions import is_bd
        user = getattr(request, 'user', None)
        return {'is_bd_user': is_bd(user)}
    except Exception:
        return {'is_bd_user': False}
