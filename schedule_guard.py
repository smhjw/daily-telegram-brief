#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, time
from typing import Iterable, Optional
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_MORNING_BRIEF_TIME = "09:30"
DEFAULT_EVENING_BRIEF_TIME = "20:30"
DEFAULT_WORKFLOW_FILENAME = "daily-telegram-brief.yml"
USER_AGENT = "daily-telegram-brief-guard/1.0"


@dataclass(frozen=True)
class GuardDecision:
    should_send: bool
    slot: str
    reason: str


def parse_iso_utc(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def parse_hhmm(raw: str, default: str) -> time:
    value = (raw or default).strip()
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def classify_slot(now_local: datetime, morning_brief_time: time, evening_brief_time: time) -> Optional[str]:
    current_time = now_local.time()
    if current_time < morning_brief_time:
        return None
    if current_time < evening_brief_time:
        return "morning"
    return "evening"


def find_duplicate_run(
    runs: Iterable[dict],
    *,
    current_run_id: int,
    now_local: datetime,
    timezone: ZoneInfo,
    slot: str,
    morning_brief_time: time,
    evening_brief_time: time,
) -> Optional[dict]:
    for run in runs:
        if int(run.get("id", 0)) == current_run_id:
            continue
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            continue

        started_at = run.get("run_started_at")
        if not started_at:
            continue

        started_local = parse_iso_utc(started_at).astimezone(timezone)
        if started_local.date() != now_local.date():
            continue
        if classify_slot(started_local, morning_brief_time, evening_brief_time) != slot:
            continue
        return run

    return None


def evaluate_schedule_guard(
    *,
    event_name: str,
    now_local: datetime,
    current_run_id: int,
    runs: Iterable[dict],
    morning_brief_time: time,
    evening_brief_time: time,
    timezone: ZoneInfo,
) -> GuardDecision:
    if morning_brief_time >= evening_brief_time:
        raise ValueError("MORNING_BRIEF_TIME must be earlier than EVENING_BRIEF_TIME")

    if event_name != "schedule":
        return GuardDecision(should_send=True, slot="manual", reason="manual trigger")

    slot = classify_slot(now_local, morning_brief_time, evening_brief_time)
    if slot is None:
        return GuardDecision(
            should_send=False,
            slot="waiting",
            reason=f"today's morning brief time {morning_brief_time.strftime('%H:%M')} has not arrived yet",
        )

    duplicate_run = find_duplicate_run(
        runs,
        current_run_id=current_run_id,
        now_local=now_local,
        timezone=timezone,
        slot=slot,
        morning_brief_time=morning_brief_time,
        evening_brief_time=evening_brief_time,
    )
    if duplicate_run is not None:
        return GuardDecision(
            should_send=False,
            slot=slot,
            reason=(
                f"already sent in {slot} slot by run "
                f"#{duplicate_run.get('run_number')} at {duplicate_run.get('run_started_at')}"
            ),
        )

    return GuardDecision(should_send=True, slot=slot, reason=f"{slot} slot not sent yet today")


def fetch_schedule_runs(repo: str, token: str, workflow_filename: str = DEFAULT_WORKFLOW_FILENAME) -> list[dict]:
    req = Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_filename}/runs?event=schedule&per_page=100",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    with urlopen(req, timeout=20) as resp:
        payload = json.load(resp)
    return payload.get("workflow_runs", [])


def write_github_output(output_path: str, decision: GuardDecision) -> None:
    with open(output_path, "a", encoding="utf-8") as out:
        out.write(f"should_send={'true' if decision.should_send else 'false'}\n")
        out.write(f"slot={decision.slot}\n")
        out.write(f"reason={decision.reason.replace(chr(10), ' ')}\n")


def main() -> int:
    event_name = os.environ.get("EVENT_NAME", "")
    repo = os.environ.get("REPO", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    run_id = int((os.environ.get("RUN_ID", "") or "0").strip())
    tz_name = os.environ.get("TZ_NAME", "").strip() or DEFAULT_TIMEZONE
    morning_brief_time = parse_hhmm(os.environ.get("MORNING_BRIEF_TIME", ""), DEFAULT_MORNING_BRIEF_TIME)
    evening_brief_time = parse_hhmm(os.environ.get("EVENING_BRIEF_TIME", ""), DEFAULT_EVENING_BRIEF_TIME)
    timezone = ZoneInfo(tz_name)
    now_local = datetime.now(timezone)

    runs: list[dict] = []
    if event_name == "schedule":
        if not repo:
            raise ValueError("Missing REPO environment variable")
        if not token:
            raise ValueError("Missing GITHUB_TOKEN environment variable")
        runs = fetch_schedule_runs(repo, token)

    decision = evaluate_schedule_guard(
        event_name=event_name,
        now_local=now_local,
        current_run_id=run_id,
        runs=runs,
        morning_brief_time=morning_brief_time,
        evening_brief_time=evening_brief_time,
        timezone=timezone,
    )

    print(f"guard: should_send={decision.should_send} slot={decision.slot} reason={decision.reason}")

    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if output_path:
        write_github_output(output_path, decision)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
