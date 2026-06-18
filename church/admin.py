from django import forms
from django.contrib import admin
from types import MethodType

from .models import (
    CalendarEventCache,
    CalendarFeed,
    Notification,
    NotificationPreference,
    Profile,
    PushSubscription,
    SermonSource,
    CateringDuty,
    KidsMinistryDuty,
    SundayDuty,
    SundayPlan,
    WorshipBandDuty,
)


class SundayDutyAdminForm(forms.ModelForm):
    duty_type = None

    class Meta:
        model = SundayDuty
        fields = ("date", "people", "notes")

    def clean_date(self):
        date = self.cleaned_data["date"]
        if not self.duty_type:
            return date
        queryset = SundayDuty.objects.filter(date=date, duty_type=self.duty_type)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            label = SundayDuty.DutyType(self.duty_type).label
            raise forms.ValidationError(f"A {label} roster already exists for this date. Edit that entry instead.")
        return date


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "phone", "updated_at")
    list_filter = ("role",)
    search_fields = ("user__first_name", "user__last_name", "user__email", "phone")


class SundayDutyAdmin(admin.ModelAdmin):
    form = SundayDutyAdminForm
    list_display = ("date", "duty_type", "assigned_people", "updated_at")
    list_filter = ("date",)
    filter_horizontal = ("people",)
    search_fields = ("notes", "people__first_name", "people__last_name", "people__email")
    fields = ("date", "people", "notes")

    duty_type = None

    def get_form(self, request, obj=None, change=False, **kwargs):
        form = super().get_form(request, obj, change, **kwargs)
        form.duty_type = self.duty_type
        return form

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if self.duty_type:
            queryset = queryset.filter(duty_type=self.duty_type)
        return queryset

    def save_model(self, request, obj, form, change):
        if self.duty_type:
            obj.duty_type = self.duty_type
        super().save_model(request, obj, form, change)

    @admin.display(description="Assigned people")
    def assigned_people(self, obj):
        return ", ".join(user.get_full_name() or user.username for user in obj.people.all()) or "TBC"


@admin.register(WorshipBandDuty)
class WorshipBandDutyAdmin(SundayDutyAdmin):
    duty_type = SundayDuty.DutyType.WORSHIP_BAND


@admin.register(CateringDuty)
class CateringDutyAdmin(SundayDutyAdmin):
    duty_type = SundayDuty.DutyType.CATERING


@admin.register(KidsMinistryDuty)
class KidsMinistryDutyAdmin(SundayDutyAdmin):
    duty_type = SundayDuty.DutyType.KIDS_MINISTRY


@admin.register(SundayPlan)
class SundayPlanAdmin(admin.ModelAdmin):
    list_display = ("date", "preaching_people", "hosting_people", "setup_people")
    list_filter = ("date",)
    filter_horizontal = ("preaching", "hosting", "setup")
    search_fields = (
        "notes",
        "preaching__first_name",
        "preaching__last_name",
        "hosting__first_name",
        "hosting__last_name",
        "setup__first_name",
        "setup__last_name",
    )

    @admin.display(description="Preaching")
    def preaching_people(self, obj):
        return ", ".join(user.get_full_name() or user.username for user in obj.preaching.all()) or "TBC"

    @admin.display(description="Hosting")
    def hosting_people(self, obj):
        return ", ".join(user.get_full_name() or user.username for user in obj.hosting.all()) or "TBC"

    @admin.display(description="Setup")
    def setup_people(self, obj):
        return ", ".join(user.get_full_name() or user.username for user in obj.setup.all()) or "TBC"


@admin.register(CalendarEventCache)
class CalendarEventCacheAdmin(admin.ModelAdmin):
    list_display = ("title", "starts_at", "location", "feed")
    list_filter = ("starts_at", "feed")
    search_fields = ("title", "location", "description", "external_id")


@admin.register(CalendarFeed)
class CalendarFeedAdmin(admin.ModelAdmin):
    list_display = ("name", "calendar_id", "is_active", "last_synced_at")
    list_filter = ("is_active",)
    search_fields = ("name", "calendar_id", "public_ical_url", "last_sync_error")
    readonly_fields = ("last_synced_at", "last_sync_error", "created_at", "updated_at")


@admin.register(SermonSource)
class SermonSourceAdmin(admin.ModelAdmin):
    list_display = ("title", "published_on", "speaker", "is_latest")
    list_filter = ("is_latest", "published_on")
    search_fields = ("title", "speaker")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "due_at", "read_at")
    list_filter = ("read_at", "due_at")
    search_fields = ("title", "body", "user__email")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "in_app_enabled", "friday_reminder_enabled", "future_push_enabled")
    list_filter = ("in_app_enabled", "friday_reminder_enabled", "future_push_enabled")


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "enabled", "updated_at", "short_endpoint")
    list_filter = ("enabled", "updated_at")
    search_fields = ("user__email", "user__first_name", "user__last_name", "endpoint")
    readonly_fields = ("user", "endpoint", "p256dh", "auth", "user_agent", "enabled", "created_at", "updated_at")

    @admin.display(description="Endpoint")
    def short_endpoint(self, obj):
        return f"{obj.endpoint[:64]}..." if len(obj.endpoint) > 64 else obj.endpoint


ADMIN_MENU_GROUPS = [
    (
        "Rosters",
        {
            "WorshipBandDuty",
            "CateringDuty",
            "KidsMinistryDuty",
            "SundayPlan",
        },
    ),
    (
        "System Settings",
        {
            "CalendarFeed",
            "CalendarEventCache",
            "SermonSource",
            "Notification",
            "NotificationPreference",
            "PushSubscription",
        },
    ),
    (
        "Auth and Users",
        {
            "User",
            "Group",
            "Profile",
        },
    ),
]


def grouped_admin_app_list(self, request, app_label=None):
    app_dict = self._build_app_dict(request, app_label)
    models = []
    for app in app_dict.values():
        models.extend(app["models"])

    grouped_apps = []
    used_model_names = set()
    for label, model_names in ADMIN_MENU_GROUPS:
        group_models = [model for model in models if model["object_name"] in model_names]
        if not group_models:
            continue
        group_models.sort(key=lambda model: model["name"])
        grouped_apps.append(
            {
                "name": label,
                "app_label": label.lower().replace(" ", "_"),
                "app_url": "",
                "has_module_perms": True,
                "models": group_models,
            }
        )
        used_model_names.update(model["object_name"] for model in group_models)

    remaining_models = [model for model in models if model["object_name"] not in used_model_names]
    if remaining_models:
        remaining_models.sort(key=lambda model: model["name"])
        grouped_apps.append(
            {
                "name": "Other",
                "app_label": "other",
                "app_url": "",
                "has_module_perms": True,
                "models": remaining_models,
            }
        )

    return grouped_apps


admin.site.get_app_list = MethodType(grouped_admin_app_list, admin.site)
