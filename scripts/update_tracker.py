from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SNAPSHOTS_PATH = DATA_DIR / "snapshots.json"
LATEST_PATH = DATA_DIR / "latest.json"
MOVERS_PATH = DATA_DIR / "top_movers.json"
STATUS_PATH = DATA_DIR / "status.json"

TRACKED_URLS = [
    "https://www.starmakerstudios.com/en/playlist/starmaker-top-songs/294",
    "https://www.starmakerstudios.com/en/songs",
    "https://www.starmakerstudios.com/playlist/%E6%9C%AC%E5%91%A8us%E7%86%B1%E9%96%80100/1357",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StarMakerPhoneTracker/1.0; +https://github.com/)"
}
TIMEOUT = 25
MAX_SNAPSHOTS = 120  # keep recent runs only to control file size


@dataclass
class SongSnapshot:
    scraped_at: str
    source_url: str
    source_name: str
    song_title: str
    artist: str | None
    recordings_raw: str | None
    recordings_num: int | None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_recordings(text: str | None) -> int | None:
    if not text:
        return None
    cleaned = text.strip().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*([KMB]?)\s*(?:recordings?)?", cleaned, flags=re.I)
    if not m:
        return None
    value = float(m.group(1))
    suffix = m.group(2).upper()
    mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
    return int(value * mult)


def normalize_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def source_name_from_url(url: str) -> str:
    if "/playlist/" in url:
        return "playlist"
    if url.rstrip("/").endswith("/songs"):
        return "songs"
    return "page"


def fetch_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.text


RECORDING_RE = re.compile(r"\b\d+(?:\.\d+)?\s*[KMB]?\s*recordings?\b", flags=re.I)


def parse_with_text_heuristics(html: str, url: str) -> list[SongSnapshot]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [normalize_text(line) for line in text.splitlines() if normalize_text(line)]

    snapshots: list[SongSnapshot] = []
    scraped_at = now_iso()
    source_name = source_name_from_url(url)

    for i, line in enumerate(lines):
        if not RECORDING_RE.search(line):
            continue

        recordings_raw = RECORDING_RE.search(line).group(0)
        recordings_num = parse_recordings(recordings_raw)
        if recordings_num is None:
            continue

        # Look slightly above the recordings line for likely title/artist candidates.
        window = lines[max(0, i - 4): i]
        title = None
        artist = None

        # Prefer lines that are not generic UI text.
        filtered = [
            w for w in window
            if len(w) <= 120
            and not re.search(r"^(download app|upload tracks|recharge|home|songbook|blog)$", w, flags=re.I)
            and "recordings" not in w.lower()
        ]

        if filtered:
            title = filtered[-1]
        if len(filtered) >= 2:
            artist = filtered[-2]

        if not title:
            continue

        snapshots.append(SongSnapshot(
            scraped_at=scraped_at,
            source_url=url,
            source_name=source_name,
            song_title=title[:200],
            artist=(artist[:200] if artist else None),
            recordings_raw=recordings_raw,
            recordings_num=recordings_num,
        ))

    return dedupe_snapshots(snapshots)


JSON_CANDIDATE_RE = re.compile(r"\{.*?\}", flags=re.S)


def parse_from_embedded_json(html: str, url: str) -> list[SongSnapshot]:
    """
    Fallback parser that scans scripts for song-ish objects.
    This is intentionally broad because StarMaker's front-end structure may vary.
    """
    soup = BeautifulSoup(html, "html.parser")
    scripts = "\n".join(script.get_text("\n", strip=True) for script in soup.find_all("script"))
    title_patterns = ["songTitle", "title", "song_name", "songName"]
    artist_patterns = ["artist", "singer", "author"]
    recordings_patterns = ["recordings", "recordCount", "singCount", "record_count"]

    found: list[SongSnapshot] = []
    scraped_at = now_iso()
    source_name = source_name_from_url(url)

    for match in re.finditer(r"[^\n]{0,300}(?:songTitle|song_name|songName|recordCount|recordings|singCount)[^\n]{0,300}", scripts, flags=re.I):
        chunk = match.group(0)
        title = None
        artist = None
        recordings_num = None
        recordings_raw = None

        for key in title_patterns:
            m = re.search(rf'{key}"?\s*[:=]\s*"([^"]+)"', chunk, flags=re.I)
            if m:
                title = normalize_text(m.group(1))
                break
        for key in artist_patterns:
            m = re.search(rf'{key}"?\s*[:=]\s*"([^"]+)"', chunk, flags=re.I)
            if m:
                artist = normalize_text(m.group(1))
                break
        for key in recordings_patterns:
            m = re.search(rf'{key}"?\s*[:=]\s*"?([\d.,]+[KMB]?)"?', chunk, flags=re.I)
            if m:
                recordings_raw = normalize_text(m.group(1))
                recordings_num = parse_recordings(recordings_raw)
                break

        if title and recordings_num is not None:
            found.append(SongSnapshot(
                scraped_at=scraped_at,
                source_url=url,
                source_name=source_name,
                song_title=title[:200],
                artist=(artist[:200] if artist else None),
                recordings_raw=recordings_raw,
                recordings_num=recordings_num,
            ))

    return dedupe_snapshots(found)


def dedupe_snapshots(items: Iterable[SongSnapshot]) -> list[SongSnapshot]:
    seen: dict[tuple[str, str, int | None, str], SongSnapshot] = {}
    for item in items:
        key = (
            normalize_text(item.song_title).casefold(),
            normalize_text(item.artist).casefold(),
            item.recordings_num,
            item.source_url,
        )
        seen[key] = item
    return list(seen.values())


def scrape_url(url: str) -> list[SongSnapshot]:
    html = fetch_html(url)
    parsed = parse_with_text_heuristics(html, url)
    if len(parsed) < 3:
        parsed = parse_from_embedded_json(html, url)
    return parsed


def append_snapshot_run(current_rows: list[dict]) -> list[dict]:
    all_runs = load_json(SNAPSHOTS_PATH, default=[])
    all_runs.append({
        "scraped_at": now_iso(),
        "rows": current_rows,
    })
    all_runs = all_runs[-MAX_SNAPSHOTS:]
    save_json(SNAPSHOTS_PATH, all_runs)
    return all_runs


def build_latest(rows: list[dict]) -> list[dict]:
    best: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (
            normalize_text(row["song_title"]).casefold(),
            normalize_text(row.get("artist")).casefold(),
        )
        prev = best.get(key)
        if prev is None or (row.get("recordings_num") or -1) > (prev.get("recordings_num") or -1):
            best[key] = row

    latest = sorted(
        best.values(),
        key=lambda r: (r.get("recordings_num") or -1),
        reverse=True,
    )
    return latest[:200]


def closest_prior_run(runs: list[dict], hours_ago: int) -> dict | None:
    if len(runs) < 2:
        return None
    target_seconds = hours_ago * 3600
    latest_dt = datetime.fromisoformat(runs[-1]["scraped_at"])
    best = None
    best_delta = None
    for run in runs[:-1]:
        dt = datetime.fromisoformat(run["scraped_at"])
        diff = (latest_dt - dt).total_seconds()
        if diff <= 0:
            continue
        delta = abs(diff - target_seconds)
        if best is None or delta < best_delta:
            best = run
            best_delta = delta
    return best


def make_lookup(rows: list[dict]) -> dict[tuple[str, str], dict]:
    out = {}
    for row in rows:
        key = (
            normalize_text(row["song_title"]).casefold(),
            normalize_text(row.get("artist")).casefold(),
        )
        if key not in out or (row.get("recordings_num") or -1) > (out[key].get("recordings_num") or -1):
            out[key] = row
    return out


def compute_movers(runs: list[dict], hours: int = 24, limit: int = 50) -> list[dict]:
    if len(runs) < 2:
        return []
    latest_run = runs[-1]
    prior = closest_prior_run(runs, hours)
    if not prior:
        return []

    latest_lookup = make_lookup(latest_run["rows"])
    prior_lookup = make_lookup(prior["rows"])
    movers = []

    for key, current in latest_lookup.items():
        old = prior_lookup.get(key)
        if not old:
            continue
        cur_num = current.get("recordings_num")
        old_num = old.get("recordings_num")
        if cur_num is None or old_num is None:
            continue
        growth = cur_num - old_num
        if growth <= 0:
            continue
        movers.append({
            "song_title": current["song_title"],
            "artist": current.get("artist"),
            "source_name": current.get("source_name"),
            "current_recordings": cur_num,
            "previous_recordings": old_num,
            "growth": growth,
            "hours_window": hours,
        })

    movers.sort(key=lambda x: x["growth"], reverse=True)
    return movers[:limit]


def build_status(rows: list[dict], runs: list[dict]) -> dict:
    last_run = runs[-1]["scraped_at"] if runs else None
    max_recordings = max((r.get("recordings_num") or 0 for r in rows), default=0)
    return {
        "generated_at": now_iso(),
        "last_scrape_at": last_run,
        "songs_found": len(rows),
        "tracked_urls": TRACKED_URLS,
        "max_recordings_found": max_recordings,
        "notes": [
            "Unofficial tracker based on public StarMaker pages.",
            "Stats are estimates and depend on what the public site exposes.",
            "If zero songs appear, the page structure likely changed and the parser needs adjustment.",
        ],
    }


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict] = []
    errors: list[dict] = []

    for url in TRACKED_URLS:
        try:
            items = scrape_url(url)
            all_rows.extend(asdict(item) for item in items)
        except Exception as exc:  # pragma: no cover
            errors.append({"url": url, "error": str(exc)})

    latest_rows = build_latest(all_rows)
    runs = append_snapshot_run(latest_rows)
    top_movers = compute_movers(runs, hours=24, limit=50)
    status = build_status(latest_rows, runs)
    if errors:
        status["errors"] = errors

    save_json(LATEST_PATH, latest_rows)
    save_json(MOVERS_PATH, top_movers)
    save_json(STATUS_PATH, status)

    print(f"Saved {len(latest_rows)} latest song rows")
    print(f"Saved {len(top_movers)} 24h movers")
    if errors:
        print(f"Encountered {len(errors)} scrape errors")


if __name__ == "__main__":
    main()
