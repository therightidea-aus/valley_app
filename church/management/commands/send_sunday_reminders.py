from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from church.email import send_sunday_roster_reminders


class Command(BaseCommand):
    help = "Send Friday roster reminder emails to everyone rostered for a Sunday."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            help="Sunday date to send reminders for, in YYYY-MM-DD format. Defaults to the upcoming Sunday.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many emails would be sent without sending anything.",
        )

    def handle(self, *args, **options):
        sunday = None
        if options["date"]:
            try:
                sunday = datetime.strptime(options["date"], "%Y-%m-%d").date()
            except ValueError as exc:
                raise CommandError("Date must be in YYYY-MM-DD format.") from exc
            if sunday.weekday() != 6:
                raise CommandError("The reminder date must be a Sunday.")

        result = send_sunday_roster_reminders(sunday=sunday, dry_run=options["dry_run"])
        action = "Would send" if options["dry_run"] else "Sent"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {result.sent_count if not options['dry_run'] else result.recipient_count} reminder email(s) "
                f"for {result.sunday:%Y-%m-%d}."
            )
        )
