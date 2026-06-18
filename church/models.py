from django.conf import settings
from django.db import models
from django.urls import reverse
from urllib.parse import quote


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Profile(TimeStampedModel):
    class Role(models.TextChoices):
        SUPERADMIN = "superadmin", "Superadmin"
        MINISTRY_LEADER = "ministry_leader", "Ministry leader"
        REGULAR = "regular", "Regular user"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.REGULAR)
    phone = models.CharField(max_length=32, blank=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.get_role_display()})"


class Ministry(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "ministries"

    def __str__(self):
        return self.name


class AgeGroup(TimeStampedModel):
    name = models.CharField(max_length=120)
    order = models.PositiveIntegerField(default=0)
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class Roster(TimeStampedModel):
    title = models.CharField(max_length=160)
    starts_on = models.DateField()
    ends_on = models.DateField()
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["starts_on"]

    def __str__(self):
        return self.title


class Assignment(TimeStampedModel):
    date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    ministry = models.ForeignKey(Ministry, on_delete=models.PROTECT)
    roster = models.ForeignKey(Roster, on_delete=models.CASCADE, related_name="assignments")
    role_name = models.CharField(max_length=120)
    people = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="assignments", blank=True)
    age_group = models.ForeignKey(AgeGroup, null=True, blank=True, on_delete=models.SET_NULL)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["date", "start_time", "ministry__name", "role_name"]

    def __str__(self):
        return f"{self.date:%d %b} - {self.ministry}: {self.role_name}"

    def get_absolute_url(self):
        return reverse("assignment_detail", kwargs={"pk": self.pk})


class SundayDutyQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(people=user)

    def upcoming(self, today):
        return self.filter(date__gte=today)


class SundayDuty(TimeStampedModel):
    class DutyType(models.TextChoices):
        WORSHIP_BAND = "worship_band", "Worship Band"
        CATERING = "catering", "Catering"
        KIDS_MINISTRY = "kids_ministry", "Kids Ministry"

    date = models.DateField()
    duty_type = models.CharField(max_length=32, choices=DutyType.choices)
    people = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="sunday_duties", blank=True)
    notes = models.TextField(blank=True)

    objects = SundayDutyQuerySet.as_manager()

    class Meta:
        ordering = ["date", "duty_type"]
        constraints = [
            models.UniqueConstraint(fields=["date", "duty_type"], name="unique_sunday_duty_per_date_type")
        ]
        verbose_name_plural = "Sunday duties"

    def __str__(self):
        return f"{self.get_duty_type_display()} - {self.date:%d %b %Y}"

    def get_absolute_url(self):
        return reverse("sunday_duty_detail", kwargs={"pk": self.pk})


class WorshipBandDuty(SundayDuty):
    class Meta:
        proxy = True
        verbose_name = "Worship Band roster"
        verbose_name_plural = "Worship Band roster"


class CateringDuty(SundayDuty):
    class Meta:
        proxy = True
        verbose_name = "Catering roster"
        verbose_name_plural = "Catering roster"


class KidsMinistryDuty(SundayDuty):
    class Meta:
        proxy = True
        verbose_name = "Kids Ministry roster"
        verbose_name_plural = "Kids Ministry roster"


class SundayPlan(TimeStampedModel):
    date = models.DateField(unique=True)
    preaching = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="preaching_plans", blank=True)
    hosting = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="hosting_plans", blank=True)
    setup = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="setup_plans", blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"Sunday plan - {self.date:%d %b %Y}"

    def get_absolute_url(self):
        return reverse("sunday_plan_detail", kwargs={"pk": self.pk})


class CalendarFeed(TimeStampedModel):
    name = models.CharField(max_length=120, default="Valley Google Calendar")
    calendar_id = models.CharField(max_length=255, unique=True)
    public_ical_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_error = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def feed_url(self):
        if self.public_ical_url:
            return self.public_ical_url
        return f"https://calendar.google.com/calendar/ical/{quote(self.calendar_id, safe='')}/public/basic.ics"


class CalendarEventCache(TimeStampedModel):
    feed = models.ForeignKey(CalendarFeed, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    external_id = models.CharField(max_length=255, unique=True)
    title = models.CharField(max_length=200)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    location = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["starts_at"]

    def __str__(self):
        return self.title


class SermonSource(TimeStampedModel):
    title = models.CharField(max_length=200)
    published_on = models.DateField()
    spotify_url = models.URLField(blank=True)
    artwork_url = models.URLField(blank=True)
    speaker = models.CharField(max_length=120, blank=True)
    is_latest = models.BooleanField(default=False)

    class Meta:
        ordering = ["-published_on"]

    def __str__(self):
        return self.title


class Notification(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=160)
    body = models.TextField()
    target_url = models.CharField(max_length=255, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["read_at", "-created_at"]

    def __str__(self):
        return self.title


class NotificationPreference(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preference",
    )
    in_app_enabled = models.BooleanField(default=True)
    friday_reminder_enabled = models.BooleanField(default=True)
    future_push_enabled = models.BooleanField(default=False)

    def __str__(self):
        return f"Notification preferences for {self.user}"


class PushSubscription(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(unique=True, max_length=1000)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=255, blank=True)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["user", "-updated_at"]

    def __str__(self):
        return f"Push subscription for {self.user}"

    @property
    def subscription_info(self):
        return {
            "endpoint": self.endpoint,
            "keys": {
                "p256dh": self.p256dh,
                "auth": self.auth,
            },
        }
