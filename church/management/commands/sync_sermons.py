from django.core.management.base import BaseCommand, CommandError

from church.spotify_sync import sync_spotify_sermon


class Command(BaseCommand):
    help = "Sync the latest sermon episode from the configured Spotify show."

    def handle(self, *args, **options):
        try:
            sermon = sync_spotify_sermon()
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Synced latest sermon: {sermon.title}"))
