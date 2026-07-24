import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from urllib.parse import quote
from PIL import Image, ImageOps


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
    church_catering = models.BooleanField(default=False)
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


class Announcement(TimeStampedModel):
    title = models.CharField(max_length=160)
    body = models.TextField()
    archived = models.BooleanField(default=False)
    email_sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="announcements_created",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class ContentBlock(TimeStampedModel):
    class Key(models.TextChoices):
        BIBLE_STUDY_ZOOM = "bible_study_zoom", "Bible Study Zoom Details"

    key = models.CharField(max_length=64, choices=Key.choices, unique=True)
    title = models.CharField(max_length=160)
    body = models.TextField()
    button_label = models.CharField(max_length=80, blank=True)
    button_url = models.URLField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


def feed_image_upload_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower()
    return f"feed/{instance.post_id or 'new'}/{uuid.uuid4().hex}.{extension}"


class FeedPost(TimeStampedModel):
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="feed_posts")
    body = models.TextField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Feed post by {self.author} on {self.created_at:%d %b %Y}"


class FeedImage(TimeStampedModel):
    MAX_SIZE = 2000

    post = models.ForeignKey(FeedPost, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to=feed_image_upload_path)
    original_filename = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at"]

    def clean(self):
        if not self.image:
            return
        extension = self.image.name.rsplit(".", 1)[-1].lower()
        if extension not in {"jpg", "jpeg", "png", "webp"}:
            raise ValidationError("Feed images must be JPG, PNG, or WebP files.")

    def save(self, *args, **kwargs):
        if self.image and not self.original_filename:
            self.original_filename = self.image.name.rsplit("/", 1)[-1]
        super().save(*args, **kwargs)
        self.resize_image()

    def resize_image(self):
        if not self.image or not hasattr(self.image, "path"):
            return
        with Image.open(self.image.path) as uploaded:
            image = ImageOps.exif_transpose(uploaded)
            if max(image.size) <= self.MAX_SIZE:
                return
            image.thumbnail((self.MAX_SIZE, self.MAX_SIZE), Image.Resampling.LANCZOS)
            save_kwargs = {}
            if image.format == "JPEG" or self.image.name.lower().endswith((".jpg", ".jpeg")):
                save_kwargs = {"quality": 86, "optimize": True}
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
            image.save(self.image.path, **save_kwargs)

    def __str__(self):
        return self.original_filename or self.image.name


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


class RosterReminderCopy(TimeStampedModel):
    volunteer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="roster_reminder_copies_from",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="roster_reminder_copies_to",
    )
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["volunteer__last_name", "volunteer__first_name", "recipient__last_name", "recipient__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["volunteer", "recipient"], name="unique_roster_reminder_copy")
        ]
        verbose_name = "Roster reminder copy"
        verbose_name_plural = "Roster reminder copies"

    def __str__(self):
        volunteer = self.volunteer.get_full_name() or self.volunteer.email or self.volunteer.username
        recipient = self.recipient.get_full_name() or self.recipient.email or self.recipient.username
        return f"{recipient} receives reminders for {volunteer}"


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
