# StarMaker Phone Tracker

A phone-friendly tracker for estimating the most-sung and fastest-rising songs on StarMaker using **public web pages**.

## What this does

- Scrapes a configurable list of public StarMaker pages/playlists
- Stores timestamped snapshots in `data/snapshots.json`
- Builds `data/latest.json` and `data/top_movers.json`
- Publishes a mobile-friendly dashboard in `index.html`
- Can run automatically with **GitHub Actions** every 15 minutes
- Can be opened from your phone through **GitHub Pages**

## Important limits

This is **not** an official StarMaker admin tool. It estimates popularity from public pages only.

Because it relies on public pages:
- accuracy depends on what StarMaker exposes publicly
- page structure changes can break the scraper
- “near accurate” is realistic; “perfect live stats” is not
- GitHub Actions schedules are not exact to the minute

## Best phone setup

Use this stack:
1. Put this repo on GitHub
2. Enable GitHub Pages
3. Let GitHub Actions refresh data every 15 minutes
4. Open the Pages URL from your phone and add it to your home screen

That gives you a simple app-like dashboard you can check any time.

## Files

- `index.html` — mobile dashboard
- `scripts/update_tracker.py` — scraper + report generator
- `.github/workflows/update.yml` — automation
- `requirements.txt` — Python deps
- `data/*.json` — generated data files

## Quick setup

### 1) Create a GitHub repo
Create a new repo, then upload these files.

### 2) Enable GitHub Pages
In GitHub:
- Settings
- Pages
- Source: **Deploy from a branch**
- Branch: `main`
- Folder: `/ (root)`

### 3) Enable GitHub Actions
Actions are enabled by default on most repos. The workflow runs every 15 minutes and also supports manual runs.

### 4) Edit tracked URLs
Open `scripts/update_tracker.py` and update `TRACKED_URLS` if needed.

### 5) Visit your dashboard
Your URL will look like:

`https://YOUR_GITHUB_USERNAME.github.io/YOUR_REPO_NAME/`

Open it on your phone and add it to your home screen.

## Optional improvements

- add more public playlists by country or genre
- send yourself a Telegram or email alert for top movers
- move storage to a small database if the snapshot file gets large
- deploy the scraper on Render, Railway, or a VPS for tighter scheduling

## Local run

```bash
pip install -r requirements.txt
python scripts/update_tracker.py
```

Then open `index.html` in a browser.

## If the scraper stops working

StarMaker may have changed its page structure. Start by:
- checking the public page still shows recording counts
- inspecting the HTML text for title / artist / recordings patterns
- adjusting the parser heuristics in `scripts/update_tracker.py`
