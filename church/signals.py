from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from .models import CateringDuty, KidsMinistryDuty, Notification, NotificationPreference, Profile, SundayDuty, SundayPlan, WorshipBandDuty
from .push import send_notification_push


MINISTRY_LEADER_GROUP = "Ministry Leaders"


def ensure_ministry_leader_group():
    group, _ = Group.objects.get_or_create(name=MINISTRY_LEADER_GROUP)
    models = [WorshipBandDuty, CateringDuty, KidsMinistryDuty, SundayPlan]
    permissions = []
    for model in models:
        content_type = ContentType.objects.get_for_model(model, for_concrete_model=False)
        for action in ["add", "change", "delete", "view"]:
            permissions.extend(
                Permission.objects.filter(
                    content_type=content_type,
                    codename=f"{action}_{model._meta.model_name}",
                )
            )
    group.permissions.set(permissions)
    return group


def sync_user_access_from_profile(profile):
    user = profile.user
    ministry_leader_group = ensure_ministry_leader_group()

    if profile.role == Profile.Role.SUPERADMIN:
        user.is_staff = True
        user.is_superuser = True
        user.groups.remove(ministry_leader_group)
    elif profile.role == Profile.Role.MINISTRY_LEADER:
        user.is_staff = True
        user.is_superuser = False
        user.groups.add(ministry_leader_group)
    else:
        user.is_staff = False
        user.is_superuser = False
        user.groups.remove(ministry_leader_group)

    user.save(update_fields=["is_staff", "is_superuser"])


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_defaults(sender, instance, created, **kwargs):
    if not created:
        return
    if instance.is_superuser:
        role = Profile.Role.SUPERADMIN
    elif instance.is_staff:
        role = Profile.Role.MINISTRY_LEADER
    else:
        role = Profile.Role.REGULAR
    Profile.objects.get_or_create(user=instance, defaults={"role": role})
    NotificationPreference.objects.get_or_create(user=instance)


@receiver(post_save, sender=Profile)
def sync_profile_role_to_user_access(sender, instance, **kwargs):
    sync_user_access_from_profile(instance)


@receiver(m2m_changed, sender=SundayDuty.people.through)
def notify_people_added_to_sunday_duty(sender, instance, action, pk_set, **kwargs):
    if action != "post_add" or not pk_set:
        return

    duty_name = instance.get_duty_type_display()
    title = f"You've been rostered for {duty_name}"
    body = f"{instance.date:%A} {instance.date.day} {instance.date:%B}"

    for user_id in pk_set:
        Notification.objects.create(
            user_id=user_id,
            title=title,
            body=body,
        )


@receiver(post_save, sender=Notification)
def send_push_for_new_notification(sender, instance, created, **kwargs):
    if created:
        send_notification_push(instance)
