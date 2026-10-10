# tenders/feedback_notify.py
# Path: TenderRadar/tenders/feedback_notify.py
#
# E-mails selected users (admins with "Feedback E-mail Alerts" enabled on the
# User Permissions page) whenever any user submits feedback.
# Sends via Microsoft Graph (IntegrationSettings) in a background thread so
# the submitting user's request is never blocked.

import html
import logging
import threading

logger = logging.getLogger('tenders')

YELLOW = '#FCD700'
INK    = '#1A1A1A'
FONT   = "Arial, Helvetica, sans-serif"


def _recipients() -> list[str]:
    """E-mail addresses of users who opted in to feedback alerts."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return list(
        User.objects.filter(
            is_active=True,
            profile__notify_feedback_email=True,
        ).exclude(email='').values_list('email', flat=True)
    )


def _build_html(fb, site_url: str) -> str:
    author = fb.author.get_full_name() or fb.author.username
    body_excerpt = html.escape(fb.body[:600]) + ('…' if len(fb.body) > 600 else '')
    review_link = ''
    if site_url:
        url = f"{site_url.rstrip('/')}/manage/feedback/{fb.pk}/"
        review_link = (
            f'<tr><td style="padding:18px 24px 24px;">'
            f'<a href="{url}" style="display:inline-block;background:{YELLOW};'
            f'color:{INK};font-weight:bold;text-decoration:none;'
            f'padding:10px 22px;border-radius:6px;font-family:{FONT};'
            f'font-size:14px;">Review Feedback</a></td></tr>'
        )
    attachment_row = ''
    if fb.attachment:
        attachment_row = (
            f'<tr><td style="padding:0 24px 8px;font-family:{FONT};font-size:13px;'
            f'color:#555;">📎 Attachment included</td></tr>'
        )
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f4f4f4;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:24px 0;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #e5e5e5;">
  <tr><td style="background:{INK};padding:18px 24px;">
    <span style="font-family:{FONT};font-size:18px;font-weight:bold;color:#ffffff;">
      Tender<span style="color:{YELLOW};">Radar</span></span>
    <span style="font-family:{FONT};font-size:12px;color:#bbbbbb;float:right;padding-top:4px;">
      New Feedback Submitted</span>
  </td></tr>
  <tr><td style="background:{YELLOW};height:4px;font-size:0;line-height:0;">&nbsp;</td></tr>
  <tr><td style="padding:24px 24px 8px;font-family:{FONT};">
    <div style="font-size:12px;font-weight:bold;color:#888;text-transform:uppercase;
                letter-spacing:.08em;">{html.escape(fb.get_feedback_type_display())}</div>
    <div style="font-size:18px;font-weight:bold;color:{INK};padding-top:4px;">
      {html.escape(fb.title)}</div>
    <div style="font-size:13px;color:#777;padding-top:6px;">
      Submitted by <strong>{html.escape(author)}</strong>
      ({html.escape(fb.author.username)})</div>
  </td></tr>
  <tr><td style="padding:14px 24px 8px;">
    <div style="font-family:{FONT};font-size:14px;color:#333;line-height:1.55;
                background:#fafafa;border-left:4px solid {YELLOW};
                padding:12px 16px;border-radius:0 6px 6px 0;">{body_excerpt}</div>
  </td></tr>
  {attachment_row}
  {review_link}
  <tr><td style="background:#fafafa;border-top:1px solid #eee;padding:14px 24px;
                 font-family:{FONT};font-size:11px;color:#999;">
    You receive this because Feedback E-mail Alerts are enabled for your account
    (Manage → User Permissions). TenderRadar · AJi Group
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


def _send(fb_pk: int):
    try:
        from .models import UserFeedback, IntegrationSettings
        from .integrations import send_alert_email

        fb = UserFeedback.objects.select_related('author').get(pk=fb_pk)
        recipients = _recipients()
        # Never notify the author about their own submission
        if fb.author.email:
            recipients = [r for r in recipients if r.lower() != fb.author.email.lower()]
        if not recipients:
            logger.info('[feedback-notify] No alert recipients configured — skipped.')
            return

        site_url = ''
        try:
            site_url = (IntegrationSettings.get().digest_site_url or '').strip()
        except Exception:
            pass

        subject = f'[TenderRadar] New Feedback: {fb.title[:80]}'
        ok = send_alert_email(subject, _build_html(fb, site_url), to=recipients,
                              category='FEEDBACK', triggered_by=fb.author)
        logger.info(f'[feedback-notify] Feedback #{fb.pk} → {len(recipients)} '
                    f'recipient(s), sent={ok}')
    except Exception as exc:
        logger.exception(f'[feedback-notify] Failed for feedback #{fb_pk}: {exc}')


def notify_feedback_submitted(fb):
    """Fire-and-forget e-mail alert to opted-in admins. Never blocks or raises."""
    threading.Thread(
        target=_send, args=(fb.pk,), name=f'feedback-notify-{fb.pk}', daemon=True,
    ).start()
