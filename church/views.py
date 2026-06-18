import json
from datetime import date, timedelta
from calendar import monthrange

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpResponseBadRequest, JsonResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .calendar_sync import CalendarSyncError, sync_active_calendar_if_due
from .forms import PublicRegistrationForm
from .models import Assignment, CalendarEventCache, Notification, NotificationPreference, PushSubscription, SermonSource, SundayDuty, SundayPlan
from .spotify_sync import SpotifySyncError, sync_spotify_sermon_if_due


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


def _is_superadmin(user):
    return user.is_authenticated and (user.is_superuser or getattr(getattr(user, "profile", None), "role", "") == "superadmin")


def _superadmin_users():
    User = get_user_model()
    return User.objects.filter(Q(is_superuser=True) | Q(profile__role="superadmin"), is_active=True).distinct()


def _notify_superadmins_about_registration(user):
    target_url = reverse("more")
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

    my_assignments = SundayDuty.objects.upcoming(today).for_user(request.user).prefetch_related("people")[:4]
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

    return render(
        request,
        "church/dashboard.html",
        {
            "today": today,
            "upcoming_sunday": sunday,
            "my_assignments": my_assignments,
            "sunday_plan": sunday_plan,
            "sunday_duties": sunday_duties,
            "events": events,
            "latest_sermon": latest_sermon,
            "notifications": notifications,
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

    assignments = list(
        SundayDuty.objects.upcoming(today)
        .for_user(request.user)
        .filter(date__lte=end_date)
        .prefetch_related("people")
        .order_by("date", "duty_type")
    )
    schedule_groups = []
    for index in range(month_count):
        month = _add_months(first_month, index)
        month_duties = [assignment for assignment in assignments if assignment.date.year == month.year and assignment.date.month == month.month]
        schedule_groups.append(
            {
                "month": month,
                "label": "This month" if index == 0 else month.strftime("%B %Y"),
                "duties": month_duties,
            }
        )

    has_later_duties = (
        SundayDuty.objects.upcoming(today)
        .for_user(request.user)
        .filter(date__gt=end_date)
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

    duties = list(
        SundayDuty.objects.upcoming(today)
        .filter(date__lte=end_date)
        .prefetch_related("people")
        .order_by("date", "duty_type")
    )
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

    has_later_duties = SundayDuty.objects.upcoming(today).filter(date__gt=end_date).exists()
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
    unread_count = Notification.objects.filter(user=request.user, read_at__isnull=True).count()
    has_push_subscription = PushSubscription.objects.filter(user=request.user, enabled=True).exists()
    pending_users = []
    if _is_superadmin(request.user):
        User = get_user_model()
        pending_users = User.objects.filter(is_active=False).order_by("date_joined", "last_name", "first_name")
    return render(
        request,
        "church/more.html",
        {
            "unread_count": unread_count,
            "active_nav": "more",
            "push_public_key": settings.VAPID_PUBLIC_KEY,
            "has_push_subscription": has_push_subscription,
            "pending_users": pending_users,
            "can_review_users": _is_superadmin(request.user),
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
        return redirect("more")
    User = get_user_model()
    pending_user = get_object_or_404(User, pk=pk, is_active=False)
    pending_user.is_active = True
    pending_user.save(update_fields=["is_active"])
    messages.success(request, f"{pending_user.get_full_name() or pending_user.email} has been approved.")
    return redirect("more")


@login_required
@require_POST
def dismiss_pending_user(request, pk):
    if not _is_superadmin(request.user):
        return redirect("more")
    User = get_user_model()
    pending_user = get_object_or_404(User, pk=pk, is_active=False)
    name = pending_user.get_full_name() or pending_user.email
    pending_user.delete()
    messages.success(request, f"{name} has been dismissed.")
    return redirect("more")


def service_worker(request):
    path = settings.BASE_DIR / "church" / "static" / "church" / "service-worker.js"
    response = FileResponse(open(path, "rb"), content_type="text/javascript")
    response["Cache-Control"] = "no-cache"
    return response
