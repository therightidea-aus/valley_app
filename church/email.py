import logging
from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives, send_mail
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import SundayDuty, SundayPlan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RosterReminderResult:
    sent_count: int
    recipient_count: int
    sunday: date
    roles: list


def _upcoming_sunday(today):
    days_until_sunday = (6 - today.weekday()) % 7
    return today + timedelta(days=days_until_sunday)


def _name(user):
    return user.get_full_name() or user.first_name or user.email or user.username


def _people_for(queryset):
    return list(queryset.order_by("first_name", "last_name", "email"))


def build_sunday_roster_context(sunday):
    roles = []
    volunteer_ids = set()
    plan = SundayPlan.objects.filter(date=sunday).prefetch_related("preaching", "hosting", "setup").first()

    if plan:
        for label, people in (
            ("Preaching", _people_for(plan.preaching.all())),
            ("Hosting", _people_for(plan.hosting.all())),
            ("Setup", _people_for(plan.setup.all())),
        ):
            roles.append(
                {
                    "label": label,
                    "people": people,
                    "people_names": ", ".join(_name(person) for person in people) or "TBC",
                }
            )
            volunteer_ids.update(person.pk for person in people)
    else:
        for label in ("Preaching", "Hosting", "Setup"):
            roles.append({"label": label, "people": [], "people_names": "TBC"})

    for duty in SundayDuty.objects.filter(date=sunday).prefetch_related("people").order_by("duty_type"):
        people = _people_for(duty.people.all())
        roles.append(
            {
                "label": duty.get_duty_type_display(),
                "people": people,
                "people_names": ", ".join(_name(person) for person in people) or "TBC",
            }
        )
        volunteer_ids.update(person.pk for person in people)

    User = get_user_model()
    recipients = (
        User.objects.filter(pk__in=volunteer_ids, is_active=True)
        .exclude(email="")
        .filter(Q(notification_preference__friday_reminder_enabled=True) | Q(notification_preference__isnull=True))
        .distinct()
        .order_by("first_name", "last_name", "email")
    )
    return {"sunday": sunday, "roles": roles, "recipients": list(recipients)}


def send_sunday_roster_reminders(sunday=None, dry_run=False):
    sunday = sunday or _upcoming_sunday(timezone.localdate())
    context = build_sunday_roster_context(sunday)
    roles = context["roles"]
    recipients = context["recipients"]

    if dry_run:
        return RosterReminderResult(sent_count=0, recipient_count=len(recipients), sunday=sunday, roles=roles)

    sent_count = 0
    subject = f"Your Valley roster for {sunday:%A} {sunday.day} {sunday:%B}"
    for recipient in recipients:
        message_context = {**context, "recipient": recipient}
        text_body = render_to_string("church/emails/sunday_roster_reminder.txt", message_context)
        html_body = render_to_string("church/emails/sunday_roster_reminder.html", message_context)
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient.email],
        )
        email.attach_alternative(html_body, "text/html")
        try:
            email.send(fail_silently=False)
        except Exception:
            logger.exception("Could not send Sunday roster reminder to user %s", recipient.pk)
            continue
        sent_count += 1

    return RosterReminderResult(sent_count=sent_count, recipient_count=len(recipients), sunday=sunday, roles=roles)


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
