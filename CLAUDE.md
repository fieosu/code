# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A web-monitoring tool that periodically screenshots one or more target webpages, runs two-layer threshold detection (keywords + report count), and pushes an alert (with screenshot) to a WeCom (企业微信) bot webhook. All behavior is configured through `config.py` — no other file needs editing to adapt it to a new target.

**Note:** code comments, `README_RUN.md`, and config are written in Chinese.

## Commands

```powershell
# Setup (one-time): pip deps + Patchright's Chromium browser
pip install -r requirements.txt
patchright install chromium
# or: setup.bat

# Single check, then exit (debugging)
python monitor.py --once

# Continuous monitoring loop (runs at config.CHECK_INTERVAL)
python monitor.py
```

There are no tests, linters, or formatters configured.

## Architecture

A single check (`monitor.py` → `run_once()`) is a three-stage pipeline:

1. **Capture** (`screenshot.py`): `capture_with_text()` returns `(screenshot_path, page_text)` — both the PNG and the page's visible text are captured together.
2. **Detect** (`detector.py`): `detect()` runs two layers in order and returns abnormal if **any** fires:
   - **Layer 1** — keyword match against `config.ERROR_KEYWORDS` in the page text (fast, precise).
   - **Layer 2** — report count (人数) from the chart tooltip exceeds `config.REPORTS_THRESHOLD`. The count only renders on hover: after capture, `screenshot.py` scans the chart (`config.CHART_SELECTOR`, `.recharts-wrapper`) from its right edge (data points shift as data updates, so a fixed hover position is unreliable) and selects the tooltip with the latest timestamp, so the Recharts tooltip's `Reports: N` lands in the extracted text; `check_by_reports()` parses it with `config.REPORTS_PATTERN`. Set `CHART_SELECTOR` empty (or `REPORTS_THRESHOLD` non-positive) to disable this layer.
3. **Notify** (`notifier.py`): `notify()` dispatches to a sender via the `_SENDERS` dict keyed by `config.WEBHOOK_TYPE` (only `wecom` is implemented).

`utils.py` provides the shared `logger` (console + `monitor.log`) and a `retry` decorator that reads retry counts from config. `monitor.py` entry points: `--once` runs a single check, default is the infinite loop.

## Cloudflare handling (the non-obvious part)

`config.py` targets a Cloudflare-protected site, and `screenshot.py` handles the challenge via **Patchright** (a patched fork of Playwright — `import patchright`, never `playwright`) plus a **persistent browser profile**:

- Measured (2026-08-12): the site uses a **managed (non-interactive) challenge**. `cf_clearance` is fingerprint-bound — headless can't use the cookie cached by headed Chromium, so headless is essentially always blocked for this site; headed real Chromium auto-passes in seconds with zero interaction. Don't try to "beat" it with synthetic clicks.
- Capture strategy (`_capture()`): by default it goes **straight to an off-screen headed window** (`--window-position=-32000,-32000`, gated by `CF_HEADED_OFFSCREEN`) — real Chromium auto-passes the managed challenge invisibly: no window is ever visible, no interaction needed. Headless remains available only as a debug path via `CF_FORCE_HEADLESS = True`.
- The persistent context at `.browser_profile/` caches the `cf_clearance` cookie: while valid, Cloudflare lets the page load straight through (faster); when expired, the same off-screen headed run refreshes it — a silent self-renewing loop.
- **Never delete `.browser_profile/`** — it resets the tool to "first visit" and forces a fresh challenge on the next run.
- Do **not** re-add `playwright-stealth` — Patchright is already patched, and stacking stealth leaves detectable traces.

## Runtime artifacts

| Path | Purpose | Notes |
|---|---|---|
| `.browser_profile/` | CF cookie / localStorage cache | Don't delete |
| `capture_<name>.png` | Current-round screenshot per site (e.g. `capture_amazon.png`) | Deleted after a normal round; kept when abnormal, when page text was empty (alerted as suspicious), or when the push failed |
| `monitor.log` | Run log | Safe to delete |

To change what's monitored or alerted, edit `config.py` (`TARGETS` target list, check interval, `REPORTS_THRESHOLD`, keywords, webhook credentials) — nothing else. `TARGETS` empty falls back to single-site `TARGET_URL`.
