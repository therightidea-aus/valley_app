from django.core.management.base import BaseCommand

from church.models import Profile
from church.signals import ensure_ministry_leader_group, sync_user_access_from_profile


class Command(BaseCommand):
    help = "Sync profile roles to Django staff/superuser flags and permission groups."

    def handle(self, *args, **options):
        ensure_ministry_leader_group()
        for profile in Profile.objects.select_related("user"):
            sync_user_access_from_profile(profile)
        self.stdout.write(self.style.SUCCESS(f"Synced {Profile.objects.count()} profile role(s)."))
