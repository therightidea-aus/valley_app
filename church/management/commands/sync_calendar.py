from django.core.management.base import BaseCommand, CommandError

from church.calendar_sync import ensure_default_calendar_feed, sync_calendar_feed


class Command(BaseCommand):
    help = "Sync the active Google Calendar public iCal feed into the local event cache."

    def handle(self, *args, **options):
        feed = ensure_default_calendar_feed()
        try:
            count = sync_calendar_feed(feed)
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Synced {count} calendar event(s) from {feed.name}."))
