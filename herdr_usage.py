#!/usr/bin/env python3
"""herdr plugin entrypoint. Commands: once (print), watch (interactive pane), open (open the pane via herdr)."""

from __future__ import annotations

import argparse
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import termios
import time
import tty
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from providers import PROVIDERS, ProviderError, UsageReport, UsageWindow

# The Claude usage endpoint rate limits after a handful of calls in a few minutes, so poll slowly and reuse cache.
DEFAULT_REFRESH_SECONDS = 300
MIN_REFRESH_SECONDS = 30
CACHE_REUSE_SECONDS = 60
PANE_ENTRYPOINT = "usage"
BAR_MIN, BAR_MAX = 8, 40
QUIT_KEYS = {b"q", b"Q", b"\x1b", b"\x03"}
REFRESH_KEYS = {b"r", b"R"}


@dataclass
class Config:
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS
    providers: List[str] = field(default_factory=lambda: list(PROVIDERS))


def load_config() -> Config:
    """Read optional config.json from the plugin config dir; anything invalid falls back to defaults."""
    config = Config()
    config_dir = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if not config_dir:
        return config
    path = Path(config_dir) / "config.json"
    if not path.is_file():
        return config
    try:
        user = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"warning: ignoring {path}: {exc}", file=sys.stderr)
        return config
    if not isinstance(user, dict):
        return config
    refresh = user.get("refresh_seconds")
    if isinstance(refresh, (int, float)) and refresh >= MIN_REFRESH_SECONDS:
        config.refresh_seconds = int(refresh)
    providers = user.get("providers")
    if isinstance(providers, list):
        unknown = [p for p in providers if p not in PROVIDERS]
        if unknown:
            print(f"warning: unknown providers in {path}: {', '.join(map(str, unknown))}", file=sys.stderr)
        config.providers = [p for p in providers if p in PROVIDERS]
    return config


class Cache:
    """Last successful report per provider, so a failed refresh can still show stale numbers."""

    def __init__(self) -> None:
        state_dir = os.environ.get("HERDR_PLUGIN_STATE_DIR")
        self.path = Path(state_dir) / "cache.json" if state_dir else None
        self.reports: Dict[str, UsageReport] = {}
        if self.path and self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.reports = {k: UsageReport.from_json(v) for k, v in raw.items() if isinstance(v, dict)}
            except (OSError, ValueError, TypeError):
                self.reports = {}

    def get(self, provider_id: str) -> Optional[UsageReport]:
        return self.reports.get(provider_id)

    def fresh(self, provider_id: str, max_age: float) -> Optional[UsageReport]:
        report = self.reports.get(provider_id)
        if report is None or report.fetched_at is None:
            return None
        age = (datetime.now(timezone.utc) - report.fetched_at).total_seconds()
        return report if 0 <= age < max_age else None

    def put(self, report: UsageReport) -> None:
        self.reports[report.provider] = report
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {k: v.to_json() for k, v in self.reports.items()}
            self.path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass


@dataclass
class Entry:
    provider_id: str
    report: Optional[UsageReport]
    error: Optional[str] = None

    @property
    def stale(self) -> bool:
        return self.error is not None and self.report is not None


def collect(provider_ids: List[str], cache: Cache, force: bool = False) -> List[Entry]:
    entries = []
    for provider_id in provider_ids:
        if not force:
            cached = cache.fresh(provider_id, CACHE_REUSE_SECONDS)
            if cached is not None:
                entries.append(Entry(provider_id, cached))
                continue
        provider = PROVIDERS[provider_id]()
        try:
            report = provider.fetch()
        except ProviderError as exc:
            entries.append(Entry(provider_id, cache.get(provider_id), str(exc)))
            continue
        except Exception as exc:  # a provider bug should not take the pane down
            entries.append(Entry(provider_id, cache.get(provider_id), f"{type(exc).__name__}: {exc}"))
            continue
        cache.put(report)
        entries.append(Entry(provider_id, report))
    return entries


class Style:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        utf8 = (sys.stdout.encoding or "").lower().replace("-", "") == "utf8"
        self.fill, self.empty = ("█", "░") if utf8 else ("#", "-")

    def paint(self, text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.enabled and text else text

    def bold(self, text: str) -> str:
        return self.paint(text, "1")

    def dim(self, text: str) -> str:
        return self.paint(text, "2")

    def red(self, text: str) -> str:
        return self.paint(text, "31")

    def by_percent(self, text: str, percent: float) -> str:
        code = "31" if percent >= 80 else "33" if percent >= 50 else "32"
        return self.paint(text, code)


def format_reset(resets_at: Optional[datetime], now: datetime) -> str:
    if resets_at is None:
        return ""
    seconds = int((resets_at - now).total_seconds())
    if seconds <= 0:
        return "resets now"
    if seconds < 24 * 3600:
        hours, minutes = divmod(seconds // 60, 60)
        return f"resets in {hours}h {minutes:02d}m" if hours else f"resets in {minutes}m"
    rounded = resets_at + timedelta(seconds=30)
    return "resets " + rounded.astimezone().strftime("%a %H:%M")


def render_window(window: UsageWindow, label_width: int, bar_width: int, style: Style, now: datetime) -> str:
    percent = max(0.0, min(100.0, window.percent))
    filled = round(bar_width * percent / 100)
    bar = style.by_percent(style.fill * filled, percent) + style.dim(style.empty * (bar_width - filled))
    tail = window.detail or format_reset(window.resets_at, now)
    return f"  {window.label:<{label_width}} {bar} {style.by_percent(f'{percent:3.0f}%', percent)}  {style.dim(tail)}".rstrip()


def render_entry(entry: Entry, width: int, style: Style, now: datetime) -> List[str]:
    report = entry.report
    title = report.title if report else PROVIDERS[entry.provider_id].title
    header = style.bold(title)
    if report and report.subtitle:
        header += style.dim(f" · {report.subtitle}")
    if report and report.fetched_at:
        stamp = report.fetched_at.astimezone().strftime("%H:%M:%S")
        status = f"stale · fetched {stamp}" if entry.stale else f"fetched {stamp}"
        header += style.dim(f"  ({status})")
    lines = [header]
    if entry.error:
        lines.append(style.red(f"  ! {entry.error}"))
    if report is None:
        return lines
    if not report.windows:
        lines.append(style.dim("  no limits reported"))
        return lines
    label_width = min(24, max(len(w.label) for w in report.windows))
    bar_width = max(BAR_MIN, min(BAR_MAX, width - label_width - 30))
    lines.extend(render_window(w, label_width, bar_width, style, now) for w in report.windows)
    return lines


def render(entries: List[Entry], width: int, style: Style, now: datetime) -> List[str]:
    lines: List[str] = []
    for entry in entries:
        if lines:
            lines.append("")
        lines.extend(render_entry(entry, width, style, now))
    return lines


def terminal_size() -> "os.terminal_size":
    return shutil.get_terminal_size((80, 24))


def cmd_once(config: Config) -> int:
    style = Style(sys.stdout.isatty() and not os.environ.get("NO_COLOR"))
    entries = collect(config.providers, Cache())
    print("\n".join(render(entries, terminal_size().columns, style, datetime.now(timezone.utc))))
    return 1 if any(e.error for e in entries) else 0


def cmd_watch(config: Config) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return cmd_once(config)
    style = Style(not os.environ.get("NO_COLOR"))
    cache = Cache()
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    out = sys.stdout

    def draw(lines: List[str], footer: str) -> None:
        size = terminal_size()
        body = lines[: max(1, size.lines - 2)]
        out.write("\x1b[H")
        for line in body:
            out.write(line + "\x1b[K\n")
        out.write("\x1b[K\n" + style.dim(footer) + "\x1b[K\x1b[J")
        out.flush()

    def on_signal(signum, _frame):  # type: ignore[no-untyped-def]
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_signal)

    try:
        tty.setcbreak(fd)
        out.write("\x1b[?1049h\x1b[?25l")
        entries: List[Entry] = []
        needs_fetch = True
        force = False
        last_fetch = time.monotonic()
        while True:
            now = datetime.now(timezone.utc)
            width = terminal_size().columns
            if needs_fetch:
                draw(render(entries, width, style, now) or [style.dim("Fetching usage…")], "refreshing…")
                entries = collect(config.providers, cache, force=force)
                last_fetch = time.monotonic()
                needs_fetch = False
                force = False
                now = datetime.now(timezone.utc)
            remaining = max(0, int(config.refresh_seconds - (time.monotonic() - last_fetch)))
            draw(render(entries, width, style, now), f"r refresh · q quit · next refresh in {remaining}s")
            ready, _, _ = select.select([fd], [], [], 1.0)
            if ready:
                key = os.read(fd, 16)
                if key in QUIT_KEYS:
                    return 0
                if key in REFRESH_KEYS:
                    needs_fetch = True
                    force = True
            if time.monotonic() - last_fetch >= config.refresh_seconds:
                needs_fetch = True
    except KeyboardInterrupt:
        return 0
    finally:
        # The pty may already be gone when herdr closes the popup; restoring is best effort.
        try:
            out.write("\x1b[?25h\x1b[?1049l")
            out.flush()
        except OSError:
            pass
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        except termios.error:
            pass


def cmd_open() -> int:
    plugin_id = os.environ.get("HERDR_PLUGIN_ID")
    if not plugin_id:
        print("HERDR_PLUGIN_ID is not set; run this through herdr", file=sys.stderr)
        return 2
    herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
    result = subprocess.run([herdr, "plugin", "pane", "open", "--plugin", plugin_id, "--entrypoint", PANE_ENTRYPOINT], check=False)
    return result.returncode


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Show coding-agent subscription usage.")
    parser.add_argument("command", nargs="?", choices=["once", "watch", "open"], default="once")
    args = parser.parse_args(argv)
    if args.command == "open":
        return cmd_open()
    config = load_config()
    if not config.providers:
        print("no providers enabled", file=sys.stderr)
        return 2
    if args.command == "watch":
        return cmd_watch(config)
    return cmd_once(config)


if __name__ == "__main__":
    sys.exit(main())
