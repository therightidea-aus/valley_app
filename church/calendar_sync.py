from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone

from .models import CalendarEventCache, CalendarFeed


@dataclass
class ParsedEvent:
    uid: str
    title: str
    starts_at: datetime
    ends_at: datetime | None
    location: str = ""
    description: str = ""


class CalendarSyncError(Exception):
    pass


def ensure_default_calendar_feed() -> CalendarFeed:
    feed, _ = CalendarFeed.objects.get_or_create(
        calendar_id=settings.GOOGLE_CALENDAR_ID,
        defaults={"name": "Valley Google Calendar", "is_active": True},
    )
    return feed


def sync_due(feed: CalendarFeed) -> bool:
    if not feed.last_synced_at:
        return True
    return timezone.now() - feed.last_synced_at > timedelta(minutes=settings.GOOGLE_CALENDAR_SYNC_MINUTES)


def sync_active_calendar_if_due(force: bool = False) -> CalendarFeed | None:
    feed = CalendarFeed.objects.filter(is_active=True).first() or ensure_default_calendar_feed()
    if not force and not sync_due(feed):
        return feed
    sync_calendar_feed(feed)
    return feed


def sync_calendar_feed(feed: CalendarFeed) -> int:
    try:
        raw_calendar = fetch_calendar(feed.feed_url)
        now = timezone.now()
        events = parse_ical_events(raw_calendar, now - timedelta(days=30), now + timedelta(days=365))
        count = cache_events(feed, events)
    except Exception as exc:
        feed.last_sync_error = str(exc)
        feed.last_synced_at = timezone.now()
        feed.save(update_fields=["last_sync_error", "last_synced_at", "updated_at"])
        raise

    feed.last_sync_error = ""
    feed.last_synced_at = timezone.now()
    feed.save(update_fields=["last_sync_error", "last_synced_at", "updated_at"])
    return count


def fetch_calendar(url: str) -> str:
    request = Request(url, headers={"User-Agent": "ValleyChurchApp/1.0"})
    try:
        with urlopen(request, timeout=12) as response:
            if response.status >= 400:
                raise CalendarSyncError(f"Google Calendar returned HTTP {response.status}.")
            return response.read().decode("utf-8", errors="replace")
    except URLError as exc:
        raise CalendarSyncError(
            "Could not reach the Google Calendar feed. Confirm the calendar is public or provide a public iCal URL."
        ) from exc


def parse_ical_events(
    raw_calendar: str,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[ParsedEvent]:
    now = timezone.now()
    window_start = window_start or now - timedelta(days=30)
    window_end = window_end or now + timedelta(days=365)
    events = []
    for block in _event_blocks(_unfold_ical_lines(raw_calendar)):
        events.extend(_parse_event_block(block, window_start, window_end))
    return events


def cache_events(feed: CalendarFeed, events: list[ParsedEvent]) -> int:
    count = 0
    cutoff = timezone.now() - timedelta(days=30)
    seen_ids = set()
    for event in events:
        if event.starts_at < cutoff:
            continue
        external_id = f"{event.uid}:{event.starts_at.isoformat()}"
        seen_ids.add(external_id)
        CalendarEventCache.objects.update_or_create(
            external_id=external_id,
            defaults={
                "feed": feed,
                "title": event.title,
                "starts_at": event.starts_at,
                "ends_at": event.ends_at,
                "location": event.location,
                "description": event.description,
            },
        )
        count += 1
    CalendarEventCache.objects.filter(feed=feed).exclude(external_id__in=seen_ids).delete()
    return count


def _unfold_ical_lines(raw_calendar: str) -> list[str]:
    lines = raw_calendar.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded = []
    for line in lines:
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _event_blocks(lines: list[str]) -> list[list[str]]:
    blocks = []
    current = []
    in_event = False
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = []
            in_event = True
            continue
        if line == "END:VEVENT" and in_event:
            blocks.append(current)
            in_event = False
            continue
        if in_event:
            current.append(line)
    return blocks


def _parse_event_block(lines: list[str], window_start: datetime, window_end: datetime) -> list[ParsedEvent]:
    fields = {}
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        name = key.split(";", 1)[0].upper()
        fields[name] = (key, _clean_ical_text(value))

    uid = fields.get("UID", ("", ""))[1]
    title = fields.get("SUMMARY", ("", "Untitled event"))[1] or "Untitled event"
    dtstart = fields.get("DTSTART")
    if not uid or not dtstart:
        return []

    starts_at = _parse_ical_datetime(*dtstart)
    ends_at = _parse_ical_datetime(*fields["DTEND"]) if "DTEND" in fields else None
    location = fields.get("LOCATION", ("", ""))[1]
    description = fields.get("DESCRIPTION", ("", ""))[1]
    base_event = ParsedEvent(
        uid=uid,
        title=title,
        starts_at=starts_at,
        ends_at=ends_at,
        location=location,
        description=description,
    )
    if "RRULE" not in fields:
        return [base_event]
    return _expand_recurring_event(base_event, fields["RRULE"][1], window_start, window_end)


def _expand_recurring_event(
    event: ParsedEvent,
    rule: str,
    window_start: datetime,
    window_end: datetime,
) -> list[ParsedEvent]:
    parts = _parse_rrule(rule)
    frequency = parts.get("FREQ")
    interval = int(parts.get("INTERVAL", "1"))
    until = _parse_rrule_until(parts.get("UNTIL"), event.starts_at.tzinfo) if parts.get("UNTIL") else window_end
    series_end = min(until, window_end)
    if series_end < window_start:
        return []

    if frequency == "WEEKLY":
        return _expand_weekly(event, parts, interval, window_start, series_end)
    if frequency == "MONTHLY":
        return _expand_monthly(event, parts, interval, window_start, series_end)
    return [event] if window_start <= event.starts_at <= series_end else []


def _expand_weekly(
    event: ParsedEvent,
    parts: dict[str, str],
    interval: int,
    window_start: datetime,
    window_end: datetime,
) -> list[ParsedEvent]:
    bydays = _byday_values(parts.get("BYDAY")) or [event.starts_at.strftime("%a").upper()[:2]]
    duration = (event.ends_at - event.starts_at) if event.ends_at else None
    cursor_date = max(event.starts_at.date(), window_start.date())
    end_date = window_end.date()
    instances = []
    while cursor_date <= end_date:
        candidate = datetime.combine(cursor_date, event.starts_at.timetz())
        if timezone.is_naive(candidate):
            candidate = timezone.make_aware(candidate, event.starts_at.tzinfo)
        weeks_since_start = (cursor_date - event.starts_at.date()).days // 7
        if (
            cursor_date >= event.starts_at.date()
            and candidate.strftime("%a").upper()[:2] in bydays
            and weeks_since_start % interval == 0
            and window_start <= candidate <= window_end
        ):
            instances.append(_event_instance(event, candidate, duration))
        cursor_date += timedelta(days=1)
    return instances


def _expand_monthly(
    event: ParsedEvent,
    parts: dict[str, str],
    interval: int,
    window_start: datetime,
    window_end: datetime,
) -> list[ParsedEvent]:
    byday = (parts.get("BYDAY") or "").split(",")[0]
    duration = (event.ends_at - event.starts_at) if event.ends_at else None
    cursor = date_month_start(event.starts_at.date())
    end_month = date_month_start(window_end.date())
    instances = []
    while cursor <= end_month:
        months_since_start = (cursor.year - event.starts_at.year) * 12 + cursor.month - event.starts_at.month
        if months_since_start >= 0 and months_since_start % interval == 0:
            candidate_date = _monthly_candidate_date(cursor, byday, event.starts_at.day)
            if candidate_date:
                candidate = datetime.combine(candidate_date, event.starts_at.timetz())
                if timezone.is_naive(candidate):
                    candidate = timezone.make_aware(candidate, event.starts_at.tzinfo)
                if window_start <= candidate <= window_end:
                    instances.append(_event_instance(event, candidate, duration))
        cursor = _add_month(cursor)
    return instances


def _event_instance(event: ParsedEvent, starts_at: datetime, duration: timedelta | None) -> ParsedEvent:
    ends_at = starts_at + duration if duration else None
    return ParsedEvent(
        uid=event.uid,
        title=event.title,
        starts_at=starts_at,
        ends_at=ends_at,
        location=event.location,
        description=event.description,
    )


def _parse_rrule(rule: str) -> dict[str, str]:
    parts = {}
    for part in rule.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            parts[key.upper()] = value
    return parts


def _parse_rrule_until(value: str, tzinfo) -> datetime:
    if value.endswith("Z"):
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ")
        return timezone.make_aware(parsed, ZoneInfo("UTC")).astimezone(tzinfo)
    if "T" in value:
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
        return timezone.make_aware(parsed, tzinfo)
    parsed_date = datetime.strptime(value, "%Y%m%d").date()
    return timezone.make_aware(datetime.combine(parsed_date, time.max), tzinfo)


def _byday_values(value: str | None) -> list[str]:
    if not value:
        return []
    return [item[-2:] for item in value.split(",")]


def date_month_start(value):
    return value.replace(day=1)


def _add_month(value):
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1)
    return value.replace(month=value.month + 1)


def _monthly_candidate_date(month_start, byday: str, fallback_day: int):
    if byday:
        day_code = byday[-2:]
        ordinal = byday[:-2]
        if ordinal:
            return _nth_weekday_of_month(month_start, int(ordinal), day_code)
    try:
        return month_start.replace(day=fallback_day)
    except ValueError:
        return None


def _nth_weekday_of_month(month_start, ordinal: int, day_code: str):
    target = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"].index(day_code)
    if ordinal > 0:
        cursor = month_start
        count = 0
        while cursor.month == month_start.month:
            if cursor.weekday() == target:
                count += 1
                if count == ordinal:
                    return cursor
            cursor += timedelta(days=1)
    return None


def _parse_ical_datetime(key: str, value: str) -> datetime:
    local_zone = ZoneInfo(settings.TIME_ZONE)
    if "VALUE=DATE" in key:
        parsed = datetime.strptime(value, "%Y%m%d").date()
        return timezone.make_aware(datetime.combine(parsed, time.min), local_zone)
    if value.endswith("Z"):
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ")
        return timezone.make_aware(parsed, ZoneInfo("UTC")).astimezone(local_zone)
    parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
    return timezone.make_aware(parsed, local_zone)


def _clean_ical_text(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
        .strip()
    )
