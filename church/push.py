import json
import logging

from django.conf import settings
from django.urls import reverse

from .models import PushSubscription

logger = logging.getLogger(__name__)


def push_is_configured():
    return bool(settings.VAPID_PUBLIC_KEY and (settings.VAPID_PRIVATE_KEY_PATH or settings.VAPID_PRIVATE_KEY))


def send_notification_push(notification):
    if not push_is_configured():
        return

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("pywebpush is not installed; push notification skipped.")
        return

    payload = json.dumps(
        {
            "title": notification.title,
            "body": notification.body,
            "url": reverse("dashboard"),
            "icon": "/static/church/img/valley-icon-192.png",
            "badge": "/static/church/img/valley-icon-192.png",
        }
    )
    vapid_key = settings.VAPID_PRIVATE_KEY_PATH or settings.VAPID_PRIVATE_KEY
    subscriptions = PushSubscription.objects.filter(user=notification.user, enabled=True)

    for subscription in subscriptions:
        try:
            webpush(
                subscription_info=subscription.subscription_info,
                data=payload,
                vapid_private_key=vapid_key,
                vapid_claims={"sub": settings.VAPID_SUBJECT},
            )
        except WebPushException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in {404, 410}:
                subscription.enabled = False
                subscription.save(update_fields=["enabled", "updated_at"])
            else:
                logger.warning("Push notification failed for subscription %s: %s", subscription.pk, exc)
