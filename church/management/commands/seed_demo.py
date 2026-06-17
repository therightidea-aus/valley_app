from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from church.models import (
    AgeGroup,
    Assignment,
    CalendarEventCache,
    CalendarFeed,
    Ministry,
    Notification,
    Profile,
    Roster,
    SermonSource,
    SundayDuty,
    SundayPlan,
)


class Command(BaseCommand):
    help = "Create demo users, rosters, Sunday plans, calendar events, and a sermon."

    def handle(self, *args, **options):
        User = get_user_model()
        people = [
            ("Roger", "James", "roger@example.com", Profile.Role.SUPERADMIN),
            ("Sarah", "Lee", "sarah@example.com", Profile.Role.MINISTRY_LEADER),
            ("Andrew", "Morris", "andrew@example.com", Profile.Role.MINISTRY_LEADER),
            ("Chris", "Nguyen", "chris@example.com", Profile.Role.REGULAR),
            ("Mia", "Taylor", "mia@example.com", Profile.Role.REGULAR),
            ("Jo", "Wright", "jo@example.com", Profile.Role.REGULAR),
            ("Daniel", "Kim", "daniel@example.com", Profile.Role.REGULAR),
            ("May", "Chen", "may@example.com", Profile.Role.REGULAR),
            ("Lee", "Patel", "lee@example.com", Profile.Role.REGULAR),
            ("Grace", "Wilson", "grace@example.com", Profile.Role.REGULAR),
        ]
        users = []
        for first, last, email, role in people:
            user, created = User.objects.get_or_create(
                username=email,
                defaults={"email": email, "first_name": first, "last_name": last},
            )
            if created:
                user.set_password("valley-demo")
            user.email = email
            user.first_name = first
            user.last_name = last
            user.is_staff = role in {Profile.Role.SUPERADMIN, Profile.Role.MINISTRY_LEADER}
            user.is_superuser = role == Profile.Role.SUPERADMIN
            user.save()
            user.profile.role = role
            user.profile.save()
            users.append(user)

        ministries = {}
        for name, slug in [
            ("Worship", "worship"),
            ("Catering", "catering"),
            ("Sunday Kids", "sunday-kids"),
            ("Valley Kids", "valley-kids"),
            ("Setup", "setup"),
        ]:
            ministries[slug], _ = Ministry.objects.get_or_create(name=name, slug=slug)

        prep, _ = AgeGroup.objects.get_or_create(name="Prep-Grade 2", defaults={"order": 1})
        grade3, _ = AgeGroup.objects.get_or_create(name="Grade 3-5", defaults={"order": 2})

        today = timezone.localdate()
        sunday = today + timedelta(days=(6 - today.weekday()) % 7)

        feed, _ = CalendarFeed.objects.get_or_create(
            calendar_id="9f6af90bfb33be5add874af50f1ec796dc39086f6f73044ba4561248666e6eab@group.calendar.google.com",
            defaults={"name": "Valley Google Calendar", "is_active": True},
        )

        for week in range(4):
            start = sunday + timedelta(days=week * 7)
            roster, _ = Roster.objects.get_or_create(
                starts_on=start,
                defaults={"title": f"Week of {start:%d %B}", "ends_on": start + timedelta(days=6)},
            )
            entries = [
                (ministries["worship"], "Worship Leader", [users[(3 + week) % len(users)]], None, time(9, 45), start),
                (ministries["worship"], "Sound", [users[(6 + week) % len(users)]], None, time(9, 45), start),
                (ministries["worship"], "Visuals", [users[0] if week == 0 else users[(7 + week) % len(users)]], None, time(9, 45), start),
                (ministries["sunday-kids"], "Leader", [users[(4 + week) % len(users)]], prep, time(10, 0), start),
                (ministries["sunday-kids"], "Leader", [users[(5 + week) % len(users)]], grade3, time(10, 0), start),
                (ministries["catering"], "Assigned People", [users[(8 + week) % len(users)], users[(9 + week) % len(users)]], None, time(11, 45), start),
                (ministries["setup"], "Assigned People", [users[(2 + week) % len(users)]], None, time(8, 30), start),
                (ministries["valley-kids"], "Leader", [users[0] if week == 0 else users[(1 + week) % len(users)]], prep, time(18, 30), start + timedelta(days=5)),
            ]
            for ministry, role_name, assigned, age_group, start_time, date in entries:
                assignment, _ = Assignment.objects.get_or_create(
                    roster=roster,
                    date=date,
                    ministry=ministry,
                    role_name=role_name,
                    age_group=age_group,
                    defaults={"start_time": start_time},
                )
                assignment.people.set(assigned)

            plan, _ = SundayPlan.objects.get_or_create(date=start)
            plan.notes = "Welcome team to confirm communion setup and kids sign-in before the service."
            plan.save()
            plan.preaching.set([users[(2 + week) % len(users)]])
            plan.hosting.set([users[(1 + week) % len(users)]])
            plan.setup.set([users[(2 + week) % len(users)]])

            sunday_duties = [
                (SundayDuty.DutyType.WORSHIP_BAND, [users[0] if week == 0 else users[(3 + week) % len(users)], users[(9 + week) % len(users)]]),
                (SundayDuty.DutyType.CATERING, [users[(8 + week) % len(users)], users[(9 + week) % len(users)]]),
                (SundayDuty.DutyType.KIDS_MINISTRY, [users[(4 + week) % len(users)], users[(5 + week) % len(users)]]),
            ]
            for duty_type, assigned_people in sunday_duties:
                duty, _ = SundayDuty.objects.get_or_create(date=start, duty_type=duty_type)
                duty.people.set(assigned_people)

        CalendarEventCache.objects.filter(external_id__startswith="demo-").delete()

        SermonSource.objects.update(is_latest=False)
        SermonSource.objects.get_or_create(
            title="Faithfulness in Ordinary Weeks",
            defaults={
                "published_on": today - timedelta(days=2),
                "speaker": "Andrew Morris",
                "spotify_url": "https://open.spotify.com/",
                "is_latest": True,
            },
        )

        Notification.objects.get_or_create(
            user=users[0],
            title="You're rostered this Sunday",
            defaults={"body": "You are serving on visuals at 9:45 AM."},
        )

        self.stdout.write(self.style.SUCCESS("Demo data ready. Login with roger@example.com / valley-demo"))
