import json
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .calendar_sync import parse_ical_events
from .models import Assignment, CalendarEventCache, CalendarFeed, Ministry, Notification, Profile, PushSubscription, Roster, SundayDuty
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
        next_month_date = today + timedelta(days=35)
        next_month = SundayDuty.objects.create(date=next_month_date, duty_type=SundayDuty.DutyType.CATERING)
        next_month.people.add(self.user)
        later_date = today + timedelta(days=100)
        later = SundayDuty.objects.create(date=later_date, duty_type=SundayDuty.DutyType.KIDS_MINISTRY)
        later.people.add(self.user)

    def test_my_schedule_groups_current_and_next_month_by_default(self):
        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.get(reverse("my_schedule"))
        self.assertContains(response, "Worship Band")
        self.assertContains(response, "Catering")
        self.assertNotContains(response, "Kids Ministry")
        self.assertContains(response, "Load more duties")

    def test_my_schedule_load_more_extends_month_range(self):
        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.get(f"{reverse('my_schedule')}?months=5")
        self.assertContains(response, "Kids Ministry")


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
        later = SundayDuty.objects.create(date=today + timedelta(days=100), duty_type=SundayDuty.DutyType.KIDS_MINISTRY)
        later.people.set([self.tom])

    def test_rosters_show_month_date_and_inline_people(self):
        self.client.login(username="roger@example.com", password="valley-demo")
        response = self.client.get(reverse("rosters"))
        self.assertContains(response, "This month")
        self.assertContains(response, "Worship Band:")
        self.assertContains(response, "Roger, Cath, Tom")
        self.assertContains(response, "Catering:")
        self.assertContains(response, "Jill")
        self.assertContains(response, "Load more rosters")


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
        self.assertContains(response, "System Settings")
        self.assertContains(response, "Auth and Users")
        self.assertNotContains(response, "Ministries")
        self.assertNotContains(response, "Assignments")

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
        self.assertNotContains(response, "Auth and Users")
        self.assertNotContains(response, "System Settings")

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
