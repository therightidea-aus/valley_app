from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from re import DOTALL, search, sub
from urllib.error import URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from django.conf import settings
from django.utils import timezone

from .models import SermonSource


@dataclass
class SpotifyEpisode:
    episode_id: str
    title: str
    published_on: date
    spotify_url: str
    artwork_url: str = ""
    description: str = ""


class SpotifySyncError(Exception):
    pass


def sync_spotify_sermon_if_due(force: bool = False) -> SermonSource | None:
    latest = SermonSource.objects.filter(is_latest=True, spotify_url__contains="/episode/").first()
    if latest and not force:
        age = timezone.now() - latest.updated_at
        if age.total_seconds() < settings.SPOTIFY_SERMON_SYNC_MINUTES * 60:
            return latest
    return sync_spotify_sermon()


def sync_spotify_sermon() -> SermonSource:
    episode = fetch_latest_podcast_episode()
    SermonSource.objects.update(is_latest=False)
    sermon, _ = SermonSource.objects.update_or_create(
        spotify_url=episode.spotify_url,
        defaults={
            "title": episode.title,
            "published_on": episode.published_on,
            "artwork_url": episode.artwork_url,
            "speaker": "Valley Community Church",
            "is_latest": True,
        },
    )
    return sermon


def fetch_latest_podcast_episode() -> SpotifyEpisode:
    try:
        feed_url = settings.SPOTIFY_SERMON_RSS_URL or fetch_apple_podcast_feed_url(settings.APPLE_PODCAST_ID)
        raw_feed = fetch_url(feed_url)
        return parse_latest_rss_episode(raw_feed)
    except SpotifySyncError:
        show_id = settings.SPOTIFY_SERMON_SHOW_ID
        raw_page = fetch_spotify_show(show_id)
        return parse_latest_episode(raw_page)


def fetch_apple_podcast_feed_url(podcast_id: str) -> str:
    if not podcast_id:
        raise SpotifySyncError("No Apple podcast ID has been configured.")
    raw_lookup = fetch_url(f"https://itunes.apple.com/lookup?id={podcast_id}")
    try:
        payload = json.loads(raw_lookup)
    except json.JSONDecodeError as exc:
        raise SpotifySyncError("Apple Podcasts lookup did not return valid JSON.") from exc
    feed_url = (payload.get("results") or [{}])[0].get("feedUrl", "")
    if not feed_url:
        raise SpotifySyncError("Apple Podcasts lookup did not include an RSS feed URL.")
    return feed_url


def fetch_url(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 ValleyChurchApp/1.0"})
    try:
        with urlopen(request, timeout=15) as response:
            if response.status >= 400:
                raise SpotifySyncError(f"Feed returned HTTP {response.status}.")
            return response.read().decode("utf-8", errors="replace")
    except URLError as exc:
        raise SpotifySyncError("Could not reach the podcast feed.") from exc


def fetch_spotify_show(show_id: str) -> str:
    url = f"https://open.spotify.com/show/{show_id}"
    try:
        return fetch_url(url)
    except SpotifySyncError as exc:
        raise SpotifySyncError("Could not reach the Spotify show page.") from exc


def parse_latest_rss_episode(raw_feed: str) -> SpotifyEpisode:
    try:
        root = ElementTree.fromstring(raw_feed)
    except ElementTree.ParseError as exc:
        raise SpotifySyncError("Podcast RSS feed could not be parsed.") from exc

    item = root.find("./channel/item")
    if item is None:
        raise SpotifySyncError("Podcast RSS feed did not include any episodes.")

    title = _rss_text(item, "title")
    if not title:
        raise SpotifySyncError("Podcast RSS feed did not include an episode title.")
    published_text = _rss_text(item, "pubDate")
    published_on = _parse_rss_date(published_text)
    link = _rss_text(item, "link") or _rss_text(item, "guid")
    episode_url = link if link.startswith("http") else ""
    guid = _rss_text(item, "guid") or episode_url or title
    artwork_url = _rss_image_url(item, root)
    description = _clean_html(_rss_text(item, "description"))

    return SpotifyEpisode(
        episode_id=guid.rstrip("/").split("/")[-1],
        title=title,
        published_on=published_on,
        spotify_url=episode_url,
        artwork_url=artwork_url,
        description=description,
    )


def _rss_text(item, tag: str) -> str:
    value = item.findtext(tag, default="")
    return unescape(value or "").strip()


def _rss_image_url(item, root) -> str:
    for node in item.iter():
        if node.tag.endswith("image") and node.attrib.get("href"):
            return node.attrib["href"]
    channel_image = root.find("./channel/image/url")
    return channel_image.text.strip() if channel_image is not None and channel_image.text else ""


def _parse_rss_date(value: str) -> date:
    if not value:
        return timezone.localdate()
    parsed = parsedate_to_datetime(value)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return timezone.localtime(parsed).date()


def parse_latest_episode(raw_page: str) -> SpotifyEpisode:
    block = _episode_block(raw_page)
    href = _extract_required(r'href="(/episode/[^"]+)"', block, "episode link")
    title = _clean_html(_extract_required(r'data-testid="episodeTitle">(.+?)</h4>', block, "episode title"))
    artwork_url = unescape(_extract_optional(r'<img[^>]+src="([^"]+)"', block))
    published_text = _clean_html(_extract_required(r'<p[^>]*>([A-Z][a-z]{2} \d{1,2})</p>', block, "publish date"))
    description = _clean_html(_extract_optional(r'<div class="QMwkp8ATH8kFiN2r"><p[^>]*>(.+?)</p></div>', block))
    episode_id = href.rstrip("/").split("/")[-1]
    return SpotifyEpisode(
        episode_id=episode_id,
        title=title,
        published_on=_parse_spotify_date(published_text),
        spotify_url=f"https://open.spotify.com{href}",
        artwork_url=artwork_url,
        description=description,
    )


def _episode_block(raw_page: str) -> str:
    start_match = search(r'data-testid="episode-0"', raw_page)
    if not start_match:
        raise SpotifySyncError("Spotify show page did not include a latest episode block.")
    end_match = search(r'data-testid="episode-1"', raw_page[start_match.end() :])
    end = start_match.end() + end_match.start() if end_match else len(raw_page)
    return raw_page[start_match.start() : end]


def _extract_required(pattern: str, value: str, label: str) -> str:
    match = search(pattern, value, DOTALL)
    if not match:
        raise SpotifySyncError(f"Spotify show page did not include {label}.")
    return match.group(1)


def _extract_optional(pattern: str, value: str) -> str:
    match = search(pattern, value, DOTALL)
    return match.group(1) if match else ""


def _clean_html(value: str) -> str:
    return sub(r"\s+", " ", sub(r"<[^>]+>", "", unescape(value))).strip()


def _parse_spotify_date(value: str) -> date:
    today = timezone.localdate()
    parsed = datetime.strptime(f"{value} {today.year}", "%b %d %Y").date()
    if parsed > today:
        parsed = parsed.replace(year=today.year - 1)
    return parsed
