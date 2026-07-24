import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from calendar import monthrange

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .calendar_sync import CalendarSyncError, sync_active_calendar_if_due
from .email import send_account_approved_email, send_sunday_roster_reminders
from .forms import FeedPostForm, PublicRegistrationForm, prepare_feed_image_upload, validate_feed_image_upload
from .models import Announcement, Assignment, CalendarEventCache, ContentBlock, FeedImage, FeedPost, Notification, NotificationPreference, PushSubscription, SermonSource, SundayDuty, SundayPlan
from .spotify_sync import SpotifySyncError, sync_spotify_sermon_if_due


@dataclass
class DisplayDuty:
    date: date
    label: str
    people: list
    url: str
    sort_order: int
    display_people: str = ""

    def get_duty_type_display(self):
        return self.label

    def get_absolute_url(self):
        return self.url


SUNDAY_PLAN_ROLE_FIELDS = [
    ("preaching", "Preaching", 10),
    ("hosting", "Hosting", 20),
    ("setup", "Setup", 30),
]
MAX_FEED_UPLOADS = 4


def _upcoming_sunday(today):
    return today + timedelta(days=(6 - today.weekday()) % 7)


def _add_months(value, months):
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def _month_start(value):
    return value.replace(day=1)


def _month_end(value):
    return value.replace(day=monthrange(value.year, value.month)[1])


def _sunday_duty_sort_order(duty):
    order = {
        SundayDuty.DutyType.WORSHIP_BAND: 40,
        SundayDuty.DutyType.CATERING: 50,
        SundayDuty.DutyType.KIDS_MINISTRY: 60,
    }
    return order.get(duty.duty_type, 90)


def _display_sunday_duty(duty):
    display_people = ""
    if duty.duty_type == SundayDuty.DutyType.CATERING and duty.church_catering:
        display_people = "Church catering"
    return DisplayDuty(
        date=duty.date,
        label=duty.get_duty_type_display(),
        people=list(duty.people.all()),
        url=duty.get_absolute_url(),
        sort_order=_sunday_duty_sort_order(duty),
        display_people=display_people,
    )


def _display_sunday_plan_roles(plans, user=None, include_empty=False):
    items = []
    user_pk = getattr(user, "pk", None)
    for plan in plans:
        for field, label, sort_order in SUNDAY_PLAN_ROLE_FIELDS:
            people = list(getattr(plan, field).all())
            if not include_empty and user_pk and not any(person.pk == user_pk for person in people):
                continue
            if not include_empty and not user_pk and not people:
                continue
            items.append(
                DisplayDuty(
                    date=plan.date,
                    label=label,
                    people=people,
                    url=plan.get_absolute_url(),
                    sort_order=sort_order,
                )
            )
    return items


def _sort_display_duties(items):
    return sorted(items, key=lambda item: (item.date, item.sort_order, item.label))


def _group_display_duties_by_date(items, limit=4):
    groups = []
    for duty in _sort_display_duties(items):
        if not groups or groups[-1]["date"] != duty.date:
            groups.append({"date": duty.date, "duties": [], "url": duty.get_absolute_url()})
        groups[-1]["duties"].append(duty)
    return groups[:limit] if limit else groups


def _sundays_between(start_date, end_date):
    days_until_sunday = (6 - start_date.weekday()) % 7
    current = start_date + timedelta(days=days_until_sunday)
    while current <= end_date:
        yield current
        current += timedelta(days=7)


def _is_superadmin(user):
    return user.is_authenticated and (user.is_superuser or getattr(getattr(user, "profile", None), "role", "") == "superadmin")


def _can_send_roster_reminders(user):
    role = getattr(getattr(user, "profile", None), "role", "")
    return user.is_authenticated and (user.is_superuser or role in {"superadmin", "ministry_leader"})


def _can_manage_feed(user):
    role = getattr(getattr(user, "profile", None), "role", "")
    return user.is_authenticated and (user.is_superuser or role in {"superadmin", "ministry_leader"})


def _can_edit_feed_post(user, post):
    return _is_superadmin(user) or post.author_id == user.pk


def _attach_feed_images(post, uploads):
    for upload in uploads[:MAX_FEED_UPLOADS]:
        processed_upload = prepare_feed_image_upload(upload)
        FeedImage.objects.create(post=post, image=processed_upload, original_filename=upload.name[:255])


def _is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _form_errors(form):
    return [error for errors in form.errors.values() for error in errors] + list(form.non_field_errors())


def _superadmin_users():
    User = get_user_model()
    return User.objects.filter(Q(is_superuser=True) | Q(profile__role="superadmin"), is_active=True).distinct()


def _notify_superadmins_about_registration(user):
    target_url = reverse("profile")
    body = f"{user.get_full_name() or user.email} has requested access."
    for superadmin in _superadmin_users():
        Notification.objects.create(
            user=superadmin,
            title="New user registration",
            body=body,
            target_url=target_url,
        )


def register(request):
    if request.method == "POST":
        form = PublicRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            _notify_superadmins_about_registration(user)
            return redirect("register_done")
    else:
        form = PublicRegistrationForm()
    return render(request, "registration/register.html", {"form": form})


def register_done(request):
    return render(request, "registration/register_done.html")


@login_required
def dashboard(request):
    today = timezone.localdate()
    sunday = _upcoming_sunday(today)

    sunday_duty_items = [
        _display_sunday_duty(duty)
        for duty in SundayDuty.objects.upcoming(today).for_user(request.user).prefetch_related("people")
    ]
    sunday_plan_items = _display_sunday_plan_roles(
        SundayPlan.objects.filter(date__gte=today)
        .filter(Q(preaching=request.user) | Q(hosting=request.user) | Q(setup=request.user))
        .prefetch_related("preaching", "hosting", "setup")
        .distinct(),
        user=request.user,
    )
    my_assignment_groups = _group_display_duties_by_date(sunday_duty_items + sunday_plan_items)
    sunday_plan = SundayPlan.objects.filter(date__gte=today).prefetch_related("preaching", "hosting", "setup").order_by("date").first()
    sunday_duties = []
    if sunday_plan:
        sunday_duties = list(SundayDuty.objects.filter(date=sunday_plan.date).prefetch_related("people").order_by("duty_type"))
    events = CalendarEventCache.objects.filter(starts_at__date__gte=today).order_by("starts_at")[:3]
    try:
        latest_sermon = sync_spotify_sermon_if_due()
    except SpotifySyncError:
        latest_sermon = None
    if latest_sermon is None:
        latest_sermon = SermonSource.objects.filter(Q(is_latest=True) | Q(published_on__lte=today)).order_by(
            "-is_latest", "-published_on"
        ).first()
    notifications = Notification.objects.filter(user=request.user, read_at__isnull=True)[:3]
    announcements = Announcement.objects.filter(archived=False)[:3]

    return render(
        request,
        "church/dashboard.html",
        {
            "today": today,
            "upcoming_sunday": sunday,
            "my_assignment_groups": my_assignment_groups,
            "sunday_plan": sunday_plan,
            "sunday_duties": sunday_duties,
            "events": events,
            "latest_sermon": latest_sermon,
            "notifications": notifications,
            "announcements": announcements,
            "active_nav": "home",
        },
    )


@login_required
def my_schedule(request):
    today = timezone.localdate()
    try:
        requested_months = int(request.GET.get("months", 2))
    except ValueError:
        requested_months = 2
    month_count = min(max(requested_months, 2), 12)
    first_month = _month_start(today)
    final_month = _add_months(first_month, month_count - 1)
    end_date = _month_end(final_month)

    sunday_duty_items = [
        _display_sunday_duty(duty)
        for duty in (
            SundayDuty.objects.upcoming(today)
            .for_user(request.user)
            .filter(date__lte=end_date)
            .prefetch_related("people")
            .order_by("date", "duty_type")
        )
    ]
    sunday_plan_items = _display_sunday_plan_roles(
        SundayPlan.objects.filter(date__gte=today, date__lte=end_date)
        .filter(Q(preaching=request.user) | Q(hosting=request.user) | Q(setup=request.user))
        .prefetch_related("preaching", "hosting", "setup")
        .distinct(),
        user=request.user,
    )
    assignments = _sort_display_duties(sunday_duty_items + sunday_plan_items)
    schedule_groups = []
    for index in range(month_count):
        month = _add_months(first_month, index)
        month_duties = [assignment for assignment in assignments if assignment.date.year == month.year and assignment.date.month == month.month]
        schedule_groups.append(
            {
                "month": month,
                "label": "This month" if index == 0 else month.strftime("%B %Y"),
                "duties": month_duties,
                "date_groups": _group_display_duties_by_date(month_duties, limit=None),
            }
        )

    has_later_duties = (
        SundayDuty.objects.upcoming(today)
        .for_user(request.user)
        .filter(date__gt=end_date)
        .exists()
        or SundayPlan.objects.filter(date__gt=end_date)
        .filter(Q(preaching=request.user) | Q(hosting=request.user) | Q(setup=request.user))
        .exists()
    )
    return render(
        request,
        "church/my_schedule.html",
        {
            "assignments": assignments,
            "schedule_groups": schedule_groups,
            "today": today,
            "month_count": month_count,
            "next_month_count": min(month_count + 2, 12),
            "can_load_more": month_count < 12 and has_later_duties,
            "active_nav": "schedule",
        },
    )


@login_required
def catering(request):
    today = timezone.localdate()
    first_month = _month_start(today)
    final_month = _add_months(first_month, 2)
    end_date = _month_end(final_month)
    duties_by_date = {
        duty.date: duty
        for duty in SundayDuty.objects.filter(
            date__gte=today,
            date__lte=end_date,
            duty_type=SundayDuty.DutyType.CATERING,
        ).prefetch_related("people")
    }

    catering_groups = []
    for index in range(3):
        month = _add_months(first_month, index)
        sundays = []
        for sunday in _sundays_between(max(today, month), _month_end(month)):
            duty = duties_by_date.get(sunday)
            people = list(duty.people.all()) if duty else []
            sundays.append(
                {
                    "date": sunday,
                    "duty": duty,
                    "is_church_catering": bool(duty and duty.church_catering),
                    "people": people,
                    "is_claimed_by_user": any(person.pk == request.user.pk for person in people),
                }
            )
        catering_groups.append(
            {
                "month": month,
                "label": "This month" if index == 0 else month.strftime("%B %Y"),
                "sundays": sundays,
            }
        )

    return render(
        request,
        "church/catering.html",
        {
            "catering_groups": catering_groups,
            "active_nav": "catering",
        },
    )


@login_required
def feed(request):
    can_manage_feed = _can_manage_feed(request.user)
    form = FeedPostForm()
    if request.method == "POST":
        if not can_manage_feed:
            return HttpResponseForbidden("You do not have permission to post to the feed.")
        form = FeedPostForm(request.POST)
        uploads = request.FILES.getlist("images")
        if len(uploads) > MAX_FEED_UPLOADS:
            form.add_error(None, f"Please upload no more than {MAX_FEED_UPLOADS} photos at a time.")
        try:
            for upload in uploads:
                validate_feed_image_upload(upload)
        except ValidationError as exc:
            form.add_error(None, exc)
        if form.is_valid():
            try:
                with transaction.atomic():
                    post = form.save(commit=False)
                    post.author = request.user
                    post.save()
                    _attach_feed_images(post, uploads)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Feed post shared.")
                if _is_ajax(request):
                    return JsonResponse({"ok": True, "redirect_url": reverse("feed")})
                return redirect("feed")

        if _is_ajax(request):
            return JsonResponse({"ok": False, "errors": _form_errors(form)}, status=400)

    posts = FeedPost.objects.select_related("author").prefetch_related("images").order_by("-created_at")
    return render(
        request,
        "church/feed.html",
        {
            "posts": posts,
            "form": form,
            "can_manage_feed": can_manage_feed,
            "active_nav": "feed",
        },
    )


@login_required
def edit_feed_post(request, pk):
    post = get_object_or_404(FeedPost.objects.prefetch_related("images"), pk=pk)
    if not _can_edit_feed_post(request.user, post):
        return HttpResponseForbidden("You do not have permission to edit this post.")

    if request.method == "POST":
        form = FeedPostForm(request.POST, instance=post)
        uploads = request.FILES.getlist("images")
        if len(uploads) > MAX_FEED_UPLOADS:
            form.add_error(None, f"Please upload no more than {MAX_FEED_UPLOADS} photos at a time.")
        try:
            for upload in uploads:
                validate_feed_image_upload(upload)
        except ValidationError as exc:
            form.add_error(None, exc)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    delete_ids = request.POST.getlist("delete_images")
                    if delete_ids:
                        post.images.filter(pk__in=delete_ids).delete()
                    _attach_feed_images(post, uploads)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Feed post updated.")
                if _is_ajax(request):
                    return JsonResponse({"ok": True, "redirect_url": reverse("feed")})
                return redirect("feed")

        if _is_ajax(request):
            return JsonResponse({"ok": False, "errors": _form_errors(form)}, status=400)
    else:
        form = FeedPostForm(instance=post)

    return render(request, "church/feed_form.html", {"post": post, "form": form, "active_nav": "feed"})


@login_required
@require_POST
def delete_feed_post(request, pk):
    post = get_object_or_404(FeedPost, pk=pk)
    if not _can_edit_feed_post(request.user, post):
        return HttpResponseForbidden("You do not have permission to delete this post.")
    post.delete()
    messages.success(request, "Feed post deleted.")
    return redirect("feed")


@login_required
def rosters(request):
    today = timezone.localdate()
    try:
        requested_months = int(request.GET.get("months", 2))
    except ValueError:
        requested_months = 2
    month_count = min(max(requested_months, 2), 12)
    first_month = _month_start(today)
    final_month = _add_months(first_month, month_count - 1)
    end_date = _month_end(final_month)

    sunday_duty_items = [
        _display_sunday_duty(duty)
        for duty in (
            SundayDuty.objects.upcoming(today)
            .filter(date__lte=end_date)
            .prefetch_related("people")
            .order_by("date", "duty_type")
        )
    ]
    sunday_plan_items = _display_sunday_plan_roles(
        SundayPlan.objects.filter(date__gte=today, date__lte=end_date)
        .prefetch_related("preaching", "hosting", "setup"),
        include_empty=True,
    )
    duties = _sort_display_duties(sunday_duty_items + sunday_plan_items)
    roster_groups = []
    for index in range(month_count):
        month = _add_months(first_month, index)
        month_duties = [duty for duty in duties if duty.date.year == month.year and duty.date.month == month.month]
        date_groups = []
        for duty in month_duties:
            if not date_groups or date_groups[-1]["date"] != duty.date:
                date_groups.append({"date": duty.date, "duties": []})
            date_groups[-1]["duties"].append(duty)
        roster_groups.append(
            {
                "month": month,
                "label": "This month" if index == 0 else month.strftime("%B %Y"),
                "date_groups": date_groups,
            }
        )

    has_later_duties = (
        SundayDuty.objects.upcoming(today).filter(date__gt=end_date).exists()
        or SundayPlan.objects.filter(date__gt=end_date).exists()
    )
    return render(
        request,
        "church/rosters.html",
        {
            "roster_groups": roster_groups,
            "month_count": month_count,
            "next_month_count": min(month_count + 2, 12),
            "can_load_more": month_count < 12 and has_later_duties,
            "active_nav": "rosters",
        },
    )


@login_required
def calendar(request):
    today = timezone.localdate()
    try:
        requested_days = int(request.GET.get("days", 14))
    except ValueError:
        requested_days = 14
    range_days = min(max(requested_days, 14), 84)
    end_date = today + timedelta(days=range_days)
    calendar_feed = None
    sync_error = ""
    try:
        calendar_feed = sync_active_calendar_if_due()
    except CalendarSyncError as exc:
        sync_error = str(exc)
        calendar_feed = getattr(exc, "feed", None)
    except Exception as exc:
        sync_error = "Calendar sync is currently unavailable."

    future_events = CalendarEventCache.objects.filter(starts_at__date__gte=today)
    events = future_events.filter(starts_at__date__lte=end_date).order_by("starts_at")
    has_later_events = future_events.filter(starts_at__date__gt=end_date).exists()
    next_days = min(range_days + 14, 84)
    return render(
        request,
        "church/calendar.html",
        {
            "events": events,
            "today": today,
            "end_date": end_date,
            "range_days": range_days,
            "next_days": next_days,
            "can_load_more": range_days < 84 and has_later_events,
            "has_future_events": future_events.exists(),
            "calendar_feed": calendar_feed,
            "sync_error": sync_error,
            "active_nav": "calendar",
        },
    )


@login_required
def more(request):
    zoom_defaults = {
        "title": "Bible Study Zoom Details",
        "body": "Meeting ID: 891 1754 6603\nPasscode: 12345",
        "button_label": "Open Zoom",
        "button_url": "https://us06web.zoom.us/j/89117546603?pwd=dmNLYnl4cWtBcmErRXA0VmtubTFUUT09",
    }
    zoom_block = (
        ContentBlock.objects.filter(key=ContentBlock.Key.BIBLE_STUDY_ZOOM, active=True)
        .values("title", "body", "button_label", "button_url")
        .first()
        or zoom_defaults
    )
    return render(request, "church/more.html", {"active_nav": "more", "zoom_block": zoom_block})


@login_required
def profile(request):
    unread_count = Notification.objects.filter(user=request.user, read_at__isnull=True).count()
    has_push_subscription = PushSubscription.objects.filter(user=request.user, enabled=True).exists()
    pending_users = []
    can_send_roster_reminders = _can_send_roster_reminders(request.user)
    roster_reminder_preview = None
    if can_send_roster_reminders:
        roster_reminder_preview = send_sunday_roster_reminders(dry_run=True)
    if _is_superadmin(request.user):
        User = get_user_model()
        pending_users = User.objects.filter(is_active=False).order_by("date_joined", "last_name", "first_name")
    return render(
        request,
        "church/profile.html",
        {
            "unread_count": unread_count,
            "active_nav": "profile",
            "push_public_key": settings.VAPID_PUBLIC_KEY,
            "has_push_subscription": has_push_subscription,
            "pending_users": pending_users,
            "can_review_users": _is_superadmin(request.user),
            "can_send_roster_reminders": can_send_roster_reminders,
            "roster_reminder_preview": roster_reminder_preview,
        },
    )


@login_required
def sunday_plan_detail(request, pk):
    plan = get_object_or_404(SundayPlan.objects.prefetch_related("preaching", "hosting", "setup"), pk=pk)
    duties = SundayDuty.objects.filter(date=plan.date).prefetch_related("people").order_by("duty_type")
    return render(request, "church/sunday_plan_detail.html", {"plan": plan, "duties": duties, "active_nav": "home"})


@login_required
def assignment_detail(request, pk):
    assignment = get_object_or_404(
        Assignment.objects.select_related("ministry", "age_group", "roster").prefetch_related("people"),
        pk=pk,
    )
    return render(request, "church/assignment_detail.html", {"assignment": assignment, "active_nav": "schedule"})


@login_required
def sunday_duty_detail(request, pk):
    duty = get_object_or_404(SundayDuty.objects.prefetch_related("people"), pk=pk)
    return render(request, "church/sunday_duty_detail.html", {"duty": duty, "active_nav": "schedule"})


@login_required
@require_POST
def dismiss_notification(request, pk):
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.read_at = timezone.now()
    notification.save(update_fields=["read_at", "updated_at"])
    return redirect(request.POST.get("next") or "dashboard")


@login_required
@require_POST
def claim_catering(request):
    requested_date = request.POST.get("date", "").strip()
    try:
        duty_date = datetime.strptime(requested_date, "%Y-%m-%d").date()
    except ValueError:
        messages.error(request, "Please choose a valid Sunday.")
        return redirect("catering")

    today = timezone.localdate()
    first_month = _month_start(today)
    end_date = _month_end(_add_months(first_month, 2))
    if duty_date < today or duty_date > end_date or duty_date.weekday() != 6:
        messages.error(request, "Please choose a Sunday in the next three months.")
        return redirect("catering")

    duty, _ = SundayDuty.objects.get_or_create(date=duty_date, duty_type=SundayDuty.DutyType.CATERING)
    if duty.church_catering:
        messages.error(request, f"Catering on {duty_date:%A} {duty_date.day} {duty_date:%B} is being handled by the church.")
        return redirect("catering")

    action = request.POST.get("action")
    if action == "remove":
        duty.people.remove(request.user)
        messages.success(request, f"You have been removed from Catering on {duty_date:%A} {duty_date.day} {duty_date:%B}.")
    elif duty.people.exclude(pk=request.user.pk).exists():
        messages.error(request, f"Catering on {duty_date:%A} {duty_date.day} {duty_date:%B} has already been claimed.")
    else:
        duty.people.add(request.user)
        messages.success(request, f"You have claimed Catering on {duty_date:%A} {duty_date.day} {duty_date:%B}.")
    return redirect("catering")


@login_required
@require_POST
def send_roster_reminder(request):
    if not _can_send_roster_reminders(request.user):
        return redirect("profile")

    sunday = None
    requested_date = request.POST.get("date", "").strip()
    if requested_date:
        try:
            sunday = datetime.strptime(requested_date, "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, "Please enter the roster date in YYYY-MM-DD format.")
            return redirect("profile")
        if sunday.weekday() != 6:
            messages.error(request, "Roster reminder emails can only be sent for a Sunday.")
            return redirect("profile")

    dry_run = request.POST.get("mode") == "preview"
    result = send_sunday_roster_reminders(sunday=sunday, dry_run=dry_run)
    if dry_run:
        messages.info(
            request,
            f"{result.recipient_count} volunteer email{'' if result.recipient_count == 1 else 's'} would be sent for {result.sunday:%A} {result.sunday.day} {result.sunday:%B}.",
        )
    else:
        messages.success(
            request,
            f"Sent {result.sent_count} of {result.recipient_count} roster reminder email{'' if result.recipient_count == 1 else 's'} for {result.sunday:%A} {result.sunday.day} {result.sunday:%B}.",
        )
    return redirect("profile")


@login_required
@require_POST
def save_push_subscription(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponseBadRequest("Invalid subscription payload.")

    endpoint = payload.get("endpoint")
    keys = payload.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not endpoint or not p256dh or not auth:
        return HttpResponseBadRequest("Subscription endpoint and keys are required.")

    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "p256dh": p256dh,
            "auth": auth,
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:255],
            "enabled": True,
        },
    )
    preference, _ = NotificationPreference.objects.get_or_create(user=request.user)
    preference.future_push_enabled = True
    preference.save(update_fields=["future_push_enabled", "updated_at"])
    return JsonResponse({"ok": True})


@login_required
@require_POST
def remove_push_subscription(request):
    endpoint = ""
    if request.body:
        try:
            payload = json.loads(request.body.decode("utf-8"))
            endpoint = payload.get("endpoint", "")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return HttpResponseBadRequest("Invalid subscription payload.")

    queryset = PushSubscription.objects.filter(user=request.user, enabled=True)
    if endpoint:
        queryset = queryset.filter(endpoint=endpoint)
    queryset.update(enabled=False)

    if not PushSubscription.objects.filter(user=request.user, enabled=True).exists():
        preference, _ = NotificationPreference.objects.get_or_create(user=request.user)
        preference.future_push_enabled = False
        preference.save(update_fields=["future_push_enabled", "updated_at"])
    return JsonResponse({"ok": True})


@login_required
@require_POST
def approve_pending_user(request, pk):
    if not _is_superadmin(request.user):
        return redirect("profile")
    User = get_user_model()
    pending_user = get_object_or_404(User, pk=pk, is_active=False)
    pending_user.is_active = True
    pending_user.save(update_fields=["is_active"])
    name = pending_user.get_full_name() or pending_user.email
    if send_account_approved_email(pending_user, request):
        messages.success(request, f"{name} has been approved and emailed.")
    else:
        messages.warning(request, f"{name} has been approved, but the email could not be sent.")
    return redirect("profile")


@login_required
@require_POST
def dismiss_pending_user(request, pk):
    if not _is_superadmin(request.user):
        return redirect("profile")
    User = get_user_model()
    pending_user = get_object_or_404(User, pk=pk, is_active=False)
    name = pending_user.get_full_name() or pending_user.email
    pending_user.delete()
    messages.success(request, f"{name} has been dismissed.")
    return redirect("profile")


def service_worker(request):
    path = settings.BASE_DIR / "church" / "static" / "church" / "service-worker.js"
    response = FileResponse(open(path, "rb"), content_type="text/javascript")
    response["Cache-Control"] = "no-cache"
    return response
