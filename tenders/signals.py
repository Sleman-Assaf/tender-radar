# tenders/signals.py

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    from .models import UserLoginLog
    ip = request.META.get('HTTP_X_FORWARDED_FOR')
    if ip:
        ip = ip.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    UserLoginLog.objects.create(
        user=user,
        ip_address=ip,
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
    )
