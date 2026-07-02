import json
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .calendar_sync import parse_ical_events
from .email import send_announcement_email, send_sunday_roster_reminders
from .models import Announcement, Assignment, CalendarEventCache, CalendarFeed, Ministry, Notification, Profile, PushSubscription, Roster, SundayDuty, SundayPlan
from .spotify_sync import parse_latest_episode


class DashboardTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="roger@example.com",
            email="roger@example.com",
            password="valley-demo",
            first_name="Roger",
        )
        self.user.profile.role = Profile.Role.REGULAR
        self.user.profile.save()
        ministry = Ministry.objects.create(name="Worship", slug="worship")
        roster = Roster.objects.create(title="Demo roster", starts_on=date.today(), ends_on=date.today())
        assignment = Assignment.objects.create(
            roster=roster,
            date=date.today(),
            start_time=time(9, 45),
            ministry=ministry,
            role_name="Visuals",
        )
        assignment.people.add(self.user)
        duty = SundayDuty.objects.create(date=date.today(), duty_type=SundayDuty.DutyType.WORSHIP_BAND)
        duty.people.add(self.user)
        plan = SundayPlan.objects.create(date=date.today())
        plan.hosting.add(self.user)

    @patch("church.views.sync_spotify_sermon_if_due")
    def test_dashboard_requires_login(self, sync_mock):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
        sync_mock.assert_not_called()

    @patch("church.views.sync_spotify_sermon_if_due")
    def test_email_login_renders_dashboard(self, sync_mock):
        sync_mock.return_value = None
        logged_in = self.client.login(username="roger@example.com", password="valley-demo")
        self.assertTrue(logged_in)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Good morning, Roger")
        self.assertContains(response, "Worship Band")
        self.assertContains(response, "Hosting")
        self.assertContains(response, f"{date.today():%a} {date.today().day} {date.today():%b}")
        self.assertNotContains(response, f"<h3>{date.today():%A} {date.today().day} {date.today():%B}</h3>")
        self.assertContains(response, 'class="item"', count=1)
        self.assertContains(response, reverse("profile"))

    @patch("church.views.sync_spotify_sermon_if_due")
    def test_dashboard_shows_active_announcements_only(self, sync_mock):
        sync_mock.return_value = None
        Announcement.objects.create(title="Members meeting", body="There is a meeting after church.")
        Announcement.objects.create(title="Old update", body="Archived text", archived=True)

        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Announcements")
        self.assertContains(response, "Members meeting")
        self.assertContains(response, "There is a meeting after church.")
        self.assertNotContains(response, "Old update")

    @patch("church.views.sync_spotify_sermon_if_due")
    def test_dashboard_this_sunday_shows_church_catering(self, sync_mock):
        sync_mock.return_value = None
        SundayDuty.objects.create(
            date=date.today(),
            duty_type=SundayDuty.DutyType.CATERING,
            church_catering=True,
        )

        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Catering")
        self.assertContains(response, "Church catering")


class CalendarTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="roger@example.com",
            email="roger@example.com",
            password="valley-demo",
            first_name="Roger",
        )
        self.feed = CalendarFeed.objects.create(name="Valley", calendar_id="calendar@example.com")
        now = timezone.now()
        CalendarEventCache.objects.create(
            feed=self.feed,
            external_id="soon",
            title="Prayer night",
            starts_at=now + timedelta(days=3),
            location="Church hall",
            description="Details at https://example.com/prayer",
        )
        CalendarEventCache.objects.create(
            feed=self.feed,
            external_id="later",
            title="Members lunch",
            starts_at=now + timedelta(days=20),
            location="Main auditorium",
        )

    @patch("church.views.sync_active_calendar_if_due")
    def test_calendar_shows_two_weeks_by_default(self, sync_mock):
        sync_mock.return_value = self.feed
        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.get(reverse("calendar"))
        self.assertContains(response, "Prayer night")
        self.assertContains(response, '<a href="https://example.com/prayer"')
        self.assertNotContains(response, "Members lunch")
        self.assertContains(response, "Load more events")

    @patch("church.views.sync_active_calendar_if_due")
    def test_calendar_load_more_extends_window(self, sync_mock):
        sync_mock.return_value = self.feed
        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.get(f"{reverse('calendar')}?days=28")
        self.assertContains(response, "Prayer night")
        self.assertContains(response, "Members lunch")

    @patch("church.views.sync_active_calendar_if_due")
    def test_calendar_hides_load_more_when_no_later_events_exist(self, sync_mock):
        sync_mock.return_value = self.feed
        CalendarEventCache.objects.filter(external_id="later").delete()
        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.get(reverse("calendar"))
        self.assertContains(response, "Prayer night")
        self.assertNotContains(response, "Load more events")


class CalendarSyncTests(TestCase):
    def test_parse_google_ical_event(self):
        raw_calendar = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:event-1@example.com
DTSTART:20260705T093000
DTEND:20260705T110000
SUMMARY:Sunday Gathering
LOCATION:Valley Community Church
DESCRIPTION:Worship and teaching
END:VEVENT
END:VCALENDAR
"""
        events = parse_ical_events(raw_calendar)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].uid, "event-1@example.com")
        self.assertEqual(events[0].title, "Sunday Gathering")
        self.assertEqual(events[0].starts_at.date(), date(2026, 7, 5))

    def test_parse_weekly_recurring_event_expands_future_instances(self):
        raw_calendar = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:event-2@example.com
DTSTART:20260607T093000
DTEND:20260607T110000
RRULE:FREQ=WEEKLY;BYDAY=SU
SUMMARY:Sunday Meeting
END:VEVENT
END:VCALENDAR
"""
        start = timezone.make_aware(datetime(2026, 6, 17, 0, 0))
        end = timezone.make_aware(datetime(2026, 7, 8, 0, 0))
        events = parse_ical_events(raw_calendar, start, end)
        self.assertEqual([event.starts_at.date() for event in events], [date(2026, 6, 21), date(2026, 6, 28), date(2026, 7, 5)])


class AnnouncementTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="valley-demo",
            first_name="Admin",
        )
        User.objects.create_user(
            username="roger@example.com",
            email="roger@example.com",
            password="valley-demo",
            first_name="Roger",
        )
        User.objects.create_user(
            username="inactive@example.com",
            email="inactive@example.com",
            password="valley-demo",
            is_active=False,
        )

    def test_send_announcement_email_sends_to_active_users_only(self):
        announcement = Announcement.objects.create(title="Prayer night", body="Join us this Wednesday.")

        sent_count = send_announcement_email(announcement)

        self.assertEqual(sent_count, 2)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(sorted(message.to[0] for message in mail.outbox), ["admin@example.com", "roger@example.com"])
        self.assertIn("Valley update: Prayer night", mail.outbox[0].subject)
        self.assertIn("Join us this Wednesday.", mail.outbox[0].body)
        announcement.refresh_from_db()
        self.assertIsNotNone(announcement.email_sent_at)

    def test_admin_creation_toggle_emails_all_active_users(self):
        self.client.login(username="admin@example.com", password="valley-demo")

        response = self.client.post(
            reverse("admin:church_announcement_add"),
            {
                "title": "Church lunch",
                "body": "Please bring something to share.",
                "archived": "",
                "email_all_users": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        announcement = Announcement.objects.get(title="Church lunch")
        self.assertEqual(announcement.created_by, self.admin)
        self.assertIsNotNone(announcement.email_sent_at)
        self.assertEqual(len(mail.outbox), 2)


class SpotifySyncTests(TestCase):
    def test_parse_latest_episode_from_spotify_markup(self):
        raw_page = """
<div data-testid="episode-0">
  <img src="https://i.scdn.co/image/cover" alt="Church Explained | Week 6"/>
  <a href="/episode/6nlHrAoIQxjkux3zl3kDYY">
    <h4 data-testid="episodeTitle">Church Explained | Week 6</h4>
  </a>
  <div class="QMwkp8ATH8kFiN2r"><p>Episode description</p></div>
  <p>Jun 1</p>
</div>
<div data-testid="episode-1"></div>
"""
        episode = parse_latest_episode(raw_page)
        self.assertEqual(episode.title, "Church Explained | Week 6")
        self.assertEqual(episode.spotify_url, "https://open.spotify.com/episode/6nlHrAoIQxjkux3zl3kDYY")
        self.assertEqual(episode.artwork_url, "https://i.scdn.co/image/cover")


class MyScheduleTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="roger@example.com",
            email="roger@example.com",
            password="valley-demo",
            first_name="Roger",
        )
        today = timezone.localdate()
        current = SundayDuty.objects.create(date=today, duty_type=SundayDuty.DutyType.WORSHIP_BAND)
        current.people.add(self.user)
        plan = SundayPlan.objects.create(date=today)
        plan.preaching.add(self.user)
        next_month_date = today + timedelta(days=35)
        next_month = SundayDuty.objects.create(date=next_month_date, duty_type=SundayDuty.DutyType.CATERING)
        next_month.people.add(self.user)
        later_date = today + timedelta(days=100)
        later = SundayDuty.objects.create(date=later_date, duty_type=SundayDuty.DutyType.KIDS_MINISTRY)
        later.people.add(self.user)

    def test_my_schedule_groups_current_and_next_month_by_default(self):
        self.client.login(username="roger@example.com", password="valley-demo")
        today = timezone.localdate()
        response = self.client.get(reverse("my_schedule"))
        self.assertContains(response, "Worship Band")
        self.assertContains(response, "Preaching")
        self.assertContains(response, "Catering")
        self.assertContains(response, f"{today:%a} {today.day} {today:%b}")
        self.assertContains(response, 'class="item"', count=2)
        self.assertNotContains(response, "Kids Ministry")
        self.assertContains(response, "Load more duties")

    def test_my_schedule_load_more_extends_month_range(self):
        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.get(f"{reverse('my_schedule')}?months=5")
        self.assertContains(response, "Kids Ministry")


class CateringSelfServeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="roger@example.com",
            email="roger@example.com",
            password="valley-demo",
            first_name="Roger",
            last_name="Curran",
        )
        today = timezone.localdate()
        self.sunday = today + timedelta(days=(6 - today.weekday()) % 7)

    def test_catering_tab_replaces_schedule_tab_in_primary_nav(self):
        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, reverse("catering"))
        self.assertContains(response, "Catering")
        self.assertNotContains(response, ">My Schedule</a>")
        self.assertNotContains(response, "<span>Schedule</span>")
        self.assertContains(response, reverse("my_schedule"))

    def test_catering_page_lists_upcoming_sundays(self):
        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.get(reverse("catering"))

        self.assertContains(response, "Claim a Sunday.")
        self.assertContains(response, self.sunday.strftime("%Y-%m-%d"))
        self.assertContains(response, "Claim")

    @patch("church.signals.send_notification_push")
    def test_user_can_claim_available_catering_date(self, push_mock):
        self.client.login(username="roger@example.com", password="valley-demo")

        response = self.client.post(reverse("claim_catering"), {"date": self.sunday.isoformat(), "action": "claim"})

        self.assertRedirects(response, reverse("catering"))
        duty = SundayDuty.objects.get(date=self.sunday, duty_type=SundayDuty.DutyType.CATERING)
        self.assertIn(self.user, duty.people.all())

        response = self.client.get(reverse("catering"))
        self.assertContains(response, "Roger Curran")
        self.assertContains(response, "Remove yourself from catering")
        self.assertContains(response, f'name="date" value="{self.sunday.isoformat()}"')
        push_mock.assert_called()

        response = self.client.post(reverse("claim_catering"), {"date": self.sunday.isoformat(), "action": "remove"})

        self.assertRedirects(response, reverse("catering"))
        duty.refresh_from_db()
        self.assertNotIn(self.user, duty.people.all())

    @patch("church.signals.send_notification_push")
    def test_claimed_catering_date_cannot_be_claimed_by_someone_else(self, push_mock):
        User = get_user_model()
        other = User.objects.create_user(
            username="other@example.com",
            email="other@example.com",
            password="valley-demo",
            first_name="Other",
        )
        duty = SundayDuty.objects.create(date=self.sunday, duty_type=SundayDuty.DutyType.CATERING)
        duty.people.add(other)
        self.client.login(username="roger@example.com", password="valley-demo")

        response = self.client.post(reverse("claim_catering"), {"date": self.sunday.isoformat(), "action": "claim"})

        self.assertRedirects(response, reverse("catering"))
        duty.refresh_from_db()
        self.assertNotIn(self.user, duty.people.all())

        response = self.client.get(reverse("catering"))
        self.assertContains(response, "Other")
        self.assertNotContains(response, "Remove yourself from catering")
        self.assertNotContains(response, f'name="date" value="{self.sunday.isoformat()}"')

    def test_church_catering_date_cannot_be_claimed(self):
        SundayDuty.objects.create(
            date=self.sunday,
            duty_type=SundayDuty.DutyType.CATERING,
            church_catering=True,
        )
        self.client.login(username="roger@example.com", password="valley-demo")

        response = self.client.get(reverse("catering"))

        self.assertContains(response, "Church catering")
        self.assertNotContains(response, f'name="date" value="{self.sunday.isoformat()}"')

        response = self.client.post(reverse("claim_catering"), {"date": self.sunday.isoformat(), "action": "claim"})

        self.assertRedirects(response, reverse("catering"))
        duty = SundayDuty.objects.get(date=self.sunday, duty_type=SundayDuty.DutyType.CATERING)
        self.assertNotIn(self.user, duty.people.all())


class SundayReminderEmailTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.roger = User.objects.create_user(
            username="roger@example.com",
            email="roger@example.com",
            first_name="Roger",
        )
        self.cath = User.objects.create_user(
            username="cath@example.com",
            email="cath@example.com",
            first_name="Cath",
        )
        self.sunday = date(2026, 6, 28)
        plan = SundayPlan.objects.create(date=self.sunday)
        plan.preaching.add(self.roger)
        plan.hosting.add(self.cath)
        duty = SundayDuty.objects.create(date=self.sunday, duty_type=SundayDuty.DutyType.WORSHIP_BAND)
        duty.people.add(self.roger, self.cath)

    def test_sends_full_roster_to_each_unique_volunteer(self):
        result = send_sunday_roster_reminders(sunday=self.sunday)

        self.assertEqual(result.sent_count, 2)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(sorted(message.to[0] for message in mail.outbox), ["cath@example.com", "roger@example.com"])
        self.assertIn("Your Valley roster for Sunday 28 June", mail.outbox[0].subject)
        self.assertIn("Preaching: Roger", mail.outbox[0].body)
        self.assertIn("Hosting: Cath", mail.outbox[0].body)
        self.assertIn("Worship Band: Cath, Roger", mail.outbox[0].body)
        self.assertIn("<table", mail.outbox[0].alternatives[0][0])

    def test_dry_run_does_not_send_email(self):
        result = send_sunday_roster_reminders(sunday=self.sunday, dry_run=True)

        self.assertEqual(result.recipient_count, 2)
        self.assertEqual(result.sent_count, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_church_catering_flows_into_reminder_email(self):
        SundayDuty.objects.create(
            date=self.sunday,
            duty_type=SundayDuty.DutyType.CATERING,
            church_catering=True,
        )

        send_sunday_roster_reminders(sunday=self.sunday)

        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("Catering: Church catering", mail.outbox[0].body)

    def test_sunday_plan_notes_flow_into_reminder_email(self):
        plan = SundayPlan.objects.get(date=self.sunday)
        plan.notes = "Please arrive by 9:30am.\nBring your lanyard."
        plan.save(update_fields=["notes"])

        send_sunday_roster_reminders(sunday=self.sunday)

        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("Notes:", mail.outbox[0].body)
        self.assertIn("Please arrive by 9:30am.", mail.outbox[0].body)
        self.assertIn("Bring your lanyard.", mail.outbox[0].alternatives[0][0])

    def test_management_command_can_target_sunday(self):
        call_command("send_sunday_reminders", date=self.sunday.isoformat(), dry_run=True)

        self.assertEqual(len(mail.outbox), 0)


class SundayReminderProfileTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.leader = User.objects.create_user(
            username="leader@example.com",
            email="leader@example.com",
            password="valley-demo",
            first_name="Leader",
        )
        self.leader.profile.role = Profile.Role.MINISTRY_LEADER
        self.leader.profile.save()
        self.regular = User.objects.create_user(
            username="regular@example.com",
            email="regular@example.com",
            password="valley-demo",
            first_name="Regular",
        )
        self.sunday = date(2026, 6, 28)
        duty = SundayDuty.objects.create(date=self.sunday, duty_type=SundayDuty.DutyType.CATERING)
        duty.people.add(self.regular)

    def test_ministry_leader_sees_reminder_panel_on_profile(self):
        self.client.login(username="leader@example.com", password="valley-demo")
        response = self.client.get(reverse("profile"))

        self.assertContains(response, "Sunday reminder email")
        self.assertContains(response, reverse("send_roster_reminder"))
        self.assertContains(response, "Send emails")

    def test_regular_user_does_not_see_reminder_panel(self):
        self.client.login(username="regular@example.com", password="valley-demo")
        response = self.client.get(reverse("profile"))

        self.assertNotContains(response, "Sunday reminder email")
        self.assertNotContains(response, reverse("send_roster_reminder"))

    def test_ministry_leader_can_preview_without_sending(self):
        self.client.login(username="leader@example.com", password="valley-demo")
        response = self.client.post(
            reverse("send_roster_reminder"),
            {"date": self.sunday.isoformat(), "mode": "preview"},
        )

        self.assertRedirects(response, reverse("profile"))
        self.assertEqual(len(mail.outbox), 0)

    def test_ministry_leader_can_send_reminder_email(self):
        self.client.login(username="leader@example.com", password="valley-demo")
        response = self.client.post(
            reverse("send_roster_reminder"),
            {"date": self.sunday.isoformat(), "mode": "send"},
        )

        self.assertRedirects(response, reverse("profile"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["regular@example.com"])
        self.assertIn("Catering: Regular", mail.outbox[0].body)

    def test_regular_user_cannot_send_reminder_email(self):
        self.client.login(username="regular@example.com", password="valley-demo")
        response = self.client.post(
            reverse("send_roster_reminder"),
            {"date": self.sunday.isoformat(), "mode": "send"},
        )

        self.assertRedirects(response, reverse("profile"))
        self.assertEqual(len(mail.outbox), 0)


class NotificationTests(TestCase):
    @patch("church.signals.send_notification_push")
    def test_user_gets_notification_when_added_to_sunday_duty(self, push_mock):
        User = get_user_model()
        user = User.objects.create_user(
            username="roger@example.com",
            email="roger@example.com",
            password="valley-demo",
            first_name="Roger",
        )
        duty = SundayDuty.objects.create(date=date(2026, 6, 21), duty_type=SundayDuty.DutyType.CATERING)
        duty.people.add(user)

        notification = Notification.objects.get(user=user)
        self.assertEqual(notification.title, "You've been rostered for Catering")
        self.assertEqual(notification.body, "Sunday 21 June")
        push_mock.assert_called_once_with(notification)

    @patch("church.views.sync_spotify_sermon_if_due")
    def test_notifications_render_first_in_dashboard_right_column(self, sync_mock):
        sync_mock.return_value = None
        User = get_user_model()
        user = User.objects.create_user(
            username="roger@example.com",
            email="roger@example.com",
            password="valley-demo",
            first_name="Roger",
        )
        Notification.objects.create(user=user, title="Roster update", body="You are serving soon.")

        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.get(reverse("dashboard"))
        html = response.content.decode()
        self.assertLess(html.index("Notifications"), html.index("Upcoming church events"))

    def test_user_can_dismiss_own_notification(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="roger@example.com",
            email="roger@example.com",
            password="valley-demo",
        )
        notification = Notification.objects.create(user=user, title="Roster update", body="You are serving soon.")

        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.post(reverse("dismiss_notification", kwargs={"pk": notification.pk}))

        notification.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(notification.read_at)

    def test_user_cannot_dismiss_someone_elses_notification(self):
        User = get_user_model()
        user = User.objects.create_user(username="roger@example.com", email="roger@example.com", password="valley-demo")
        other = User.objects.create_user(username="other@example.com", email="other@example.com", password="valley-demo")
        notification = Notification.objects.create(user=other, title="Roster update", body="You are serving soon.")

        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.post(reverse("dismiss_notification", kwargs={"pk": notification.pk}))

        notification.refresh_from_db()
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(notification.read_at)

    def test_user_can_save_push_subscription(self):
        User = get_user_model()
        user = User.objects.create_user(username="roger@example.com", email="roger@example.com", password="valley-demo")
        self.client.login(username="roger@example.com", password="valley-demo")

        response = self.client.post(
            reverse("save_push_subscription"),
            data=json.dumps(
                {
                    "endpoint": "https://push.example.com/device-1",
                    "keys": {
                        "p256dh": "public-key",
                        "auth": "auth-secret",
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        subscription = PushSubscription.objects.get(user=user)
        self.assertEqual(subscription.endpoint, "https://push.example.com/device-1")
        self.assertTrue(subscription.enabled)
        user.notification_preference.refresh_from_db()
        self.assertTrue(user.notification_preference.future_push_enabled)

    def test_user_can_disable_push_subscription(self):
        User = get_user_model()
        user = User.objects.create_user(username="roger@example.com", email="roger@example.com", password="valley-demo")
        subscription = PushSubscription.objects.create(
            user=user,
            endpoint="https://push.example.com/device-1",
            p256dh="public-key",
            auth="auth-secret",
        )
        self.client.login(username="roger@example.com", password="valley-demo")

        response = self.client.post(
            reverse("remove_push_subscription"),
            data=json.dumps({"endpoint": subscription.endpoint}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        subscription.refresh_from_db()
        self.assertFalse(subscription.enabled)


class RegistrationApprovalTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superadmin = User.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="valley-demo",
            first_name="Admin",
        )

    @patch("church.signals.send_notification_push")
    def test_public_registration_creates_inactive_user_and_notifies_superadmin(self, push_mock):
        response = self.client.post(
            reverse("register"),
            {
                "first_name": "New",
                "last_name": "Person",
                "email": "new@example.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertRedirects(response, reverse("register_done"))
        User = get_user_model()
        user = User.objects.get(email="new@example.com")
        self.assertFalse(user.is_active)
        notification = Notification.objects.get(user=self.superadmin, title="New user registration")
        self.assertEqual(notification.target_url, reverse("profile"))
        push_mock.assert_called_once_with(notification)

    def test_superadmin_can_approve_pending_user_from_more(self):
        User = get_user_model()
        pending_user = User.objects.create_user(
            username="new@example.com",
            email="new@example.com",
            password="StrongPass123!",
            is_active=False,
        )

        self.client.login(username="admin@example.com", password="valley-demo")
        response = self.client.post(reverse("approve_pending_user", kwargs={"pk": pending_user.pk}))

        pending_user.refresh_from_db()
        self.assertRedirects(response, reverse("profile"))
        self.assertTrue(pending_user.is_active)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["new@example.com"])
        self.assertIn("account has been approved", mail.outbox[0].body)

    @patch("church.views.send_account_approved_email", return_value=False)
    def test_approval_still_activates_user_if_email_fails(self, email_mock):
        User = get_user_model()
        pending_user = User.objects.create_user(
            username="new@example.com",
            email="new@example.com",
            password="StrongPass123!",
            is_active=False,
        )

        self.client.login(username="admin@example.com", password="valley-demo")
        response = self.client.post(reverse("approve_pending_user", kwargs={"pk": pending_user.pk}))

        pending_user.refresh_from_db()
        self.assertRedirects(response, reverse("profile"))
        self.assertTrue(pending_user.is_active)
        email_mock.assert_called_once()

    def test_superadmin_can_dismiss_pending_user_from_more(self):
        User = get_user_model()
        pending_user = User.objects.create_user(
            username="new@example.com",
            email="new@example.com",
            password="StrongPass123!",
            is_active=False,
        )

        self.client.login(username="admin@example.com", password="valley-demo")
        response = self.client.post(reverse("dismiss_pending_user", kwargs={"pk": pending_user.pk}))

        self.assertRedirects(response, reverse("profile"))
        self.assertFalse(User.objects.filter(pk=pending_user.pk).exists())

    def test_superadmin_sees_pending_users_on_profile_page(self):
        User = get_user_model()
        User.objects.create_user(
            username="new@example.com",
            email="new@example.com",
            password="StrongPass123!",
            first_name="New",
            last_name="Person",
            is_active=False,
        )

        self.client.login(username="admin@example.com", password="valley-demo")
        response = self.client.get(reverse("profile"))

        self.assertContains(response, "New user requests")
        self.assertContains(response, "New Person")
        self.assertContains(response, "Approve")

    def test_more_tab_no_longer_shows_account_or_admin_panels(self):
        self.client.login(username="admin@example.com", password="valley-demo")
        response = self.client.get(reverse("more"))

        self.assertContains(response, "Helpful links")
        self.assertNotContains(response, "New user requests")
        self.assertNotContains(response, "Admin dashboard")


class PasswordResetTests(TestCase):
    def test_password_reset_sends_email_for_active_user(self):
        User = get_user_model()
        User.objects.create_user(
            username="roger@example.com",
            email="roger@example.com",
            password="old-password-123",
            first_name="Roger",
        )

        response = self.client.post(reverse("password_reset"), {"email": "roger@example.com"})

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["roger@example.com"])
        self.assertIn("Reset your Valley app password", mail.outbox[0].subject)
        self.assertIn("/password-reset/", mail.outbox[0].body)

    def test_password_reset_does_not_email_inactive_user(self):
        User = get_user_model()
        User.objects.create_user(
            username="pending@example.com",
            email="pending@example.com",
            password="old-password-123",
            is_active=False,
        )

        response = self.client.post(reverse("password_reset"), {"email": "pending@example.com"})

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)


class PasswordChangeTests(TestCase):
    def test_logged_in_user_can_change_password(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="roger@example.com",
            email="roger@example.com",
            password="old-password-123",
        )

        self.client.login(username="roger@example.com", password="old-password-123")
        response = self.client.post(
            reverse("password_change"),
            {
                "old_password": "old-password-123",
                "new_password1": "new-password-456",
                "new_password2": "new-password-456",
            },
        )

        self.assertRedirects(response, reverse("password_change_done"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("new-password-456"))


class RostersPageTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.roger = User.objects.create_user(
            username="roger@example.com",
            email="roger@example.com",
            password="valley-demo",
            first_name="Roger",
        )
        self.cath = User.objects.create_user(username="cath@example.com", email="cath@example.com", first_name="Cath")
        self.tom = User.objects.create_user(username="tom@example.com", email="tom@example.com", first_name="Tom")
        self.jill = User.objects.create_user(username="jill@example.com", email="jill@example.com", first_name="Jill")
        today = timezone.localdate()
        worship = SundayDuty.objects.create(date=today, duty_type=SundayDuty.DutyType.WORSHIP_BAND)
        worship.people.set([self.roger, self.cath, self.tom])
        catering = SundayDuty.objects.create(date=today, duty_type=SundayDuty.DutyType.CATERING)
        catering.people.set([self.jill])
        plan = SundayPlan.objects.create(date=today)
        plan.preaching.set([self.roger])
        plan.hosting.set([self.cath])
        plan.setup.set([self.tom])
        later = SundayDuty.objects.create(date=today + timedelta(days=100), duty_type=SundayDuty.DutyType.KIDS_MINISTRY)
        later.people.set([self.tom])

    def test_rosters_show_month_date_and_inline_people(self):
        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.get(reverse("rosters"))
        self.assertContains(response, "This month")
        self.assertContains(response, "Preaching:")
        self.assertContains(response, "Hosting:")
        self.assertContains(response, "Setup:")
        self.assertContains(response, "Worship Band:")
        self.assertContains(response, "Roger, Cath, Tom")
        self.assertContains(response, "Catering:")
        self.assertContains(response, "Jill")
        self.assertContains(response, "Load more rosters")

    def test_rosters_show_church_catering_label(self):
        today = timezone.localdate()
        SundayDuty.objects.filter(date=today, duty_type=SundayDuty.DutyType.CATERING).delete()
        SundayDuty.objects.create(date=today, duty_type=SundayDuty.DutyType.CATERING, church_catering=True)

        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.get(reverse("rosters"))

        self.assertContains(response, "Catering:")
        self.assertContains(response, "Church catering")


class SundayDutyAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="valley-demo",
        )
        self.volunteer = User.objects.create_user(
            username="volunteer@example.com",
            email="volunteer@example.com",
            password="valley-demo",
        )
        duty = SundayDuty.objects.create(date=date(2026, 6, 21), duty_type=SundayDuty.DutyType.WORSHIP_BAND)
        duty.people.add(self.volunteer)

    def test_duplicate_worship_band_date_returns_form_error(self):
        self.client.login(username="admin@example.com", password="valley-demo")
        response = self.client.post(
            "/admin/church/worshipbandduty/add/",
            {
                "date": "2026-06-21",
                "people": [str(self.volunteer.pk)],
                "notes": "",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A Worship Band roster already exists for this date")

    def test_admin_menu_is_grouped_by_workflow(self):
        self.client.login(username="admin@example.com", password="valley-demo")
        response = self.client.get("/admin/")
        self.assertContains(response, "Rosters")
        self.assertContains(response, "Sunday duties table")
        self.assertContains(response, "System Settings")
        self.assertContains(response, "Auth and Users")
        self.assertNotContains(response, "Ministries")
        self.assertNotContains(response, "Assignments")
        html = response.content.decode()
        self.assertLess(html.index("Worship Band roster"), html.index("Sunday duties table"))
        self.assertLess(html.index("Catering roster"), html.index("Sunday duties table"))
        self.assertLess(html.index("Kids Ministry roster"), html.index("Sunday duties table"))

    def test_sunday_duty_matrix_shows_roster_table(self):
        plan = SundayPlan.objects.create(date=date(2026, 6, 21))
        plan.preaching.add(self.volunteer)
        SundayDuty.objects.create(
            date=date(2026, 6, 28),
            duty_type=SundayDuty.DutyType.CATERING,
            church_catering=True,
        )

        self.client.login(username="admin@example.com", password="valley-demo")
        response = self.client.get("/admin/sunday-duty-matrix/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sunday duties table")
        self.assertContains(response, "Preaching")
        self.assertContains(response, "Worship Band")
        self.assertContains(response, "Catering")
        self.assertContains(response, "volunteer@example.com")
        self.assertContains(response, "Church catering")
        self.assertNotContains(response, "TBC")

    def test_ministry_leader_role_gets_roster_admin_only(self):
        User = get_user_model()
        leader = User.objects.create_user(
            username="leader@example.com",
            email="leader@example.com",
            password="valley-demo",
        )
        leader.profile.role = Profile.Role.MINISTRY_LEADER
        leader.profile.save()
        leader.refresh_from_db()

        self.assertTrue(leader.is_staff)
        self.assertFalse(leader.is_superuser)

        self.client.login(username="leader@example.com", password="valley-demo")
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rosters")
        self.assertContains(response, "Sunday duties table")
        self.assertNotContains(response, "Auth and Users")
        self.assertNotContains(response, "System Settings")

        matrix_response = self.client.get("/admin/sunday-duty-matrix/")
        self.assertEqual(matrix_response.status_code, 200)

    def test_regular_role_has_no_admin_access(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="regular@example.com",
            email="regular@example.com",
            password="valley-demo",
        )
        user.profile.role = Profile.Role.REGULAR
        user.profile.save()
        user.refresh_from_db()

        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

        self.client.login(username="regular@example.com", password="valley-demo")
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)
