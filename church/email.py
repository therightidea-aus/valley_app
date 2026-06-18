import logging

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

logger = logging.getLogger(__name__)


def send_account_approved_email(user, request):
    if not user.email:
        return False

    login_url = request.build_absolute_uri(reverse("login"))
    subject = "Your Valley app account is active"
    message = (
        f"Hi {user.first_name or user.get_full_name() or 'there'},\n\n"
        "Your Valley Community Church app account has been approved and is now active.\n\n"
        f"You can sign in here:\n{login_url}\n\n"
        "Valley Community Church"
    )

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Could not send account approval email to user %s", user.pk)
        return False
    return True
