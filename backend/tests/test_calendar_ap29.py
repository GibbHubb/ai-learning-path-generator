"""AP29 — calendar reminders (VALARM) + optional weekly study blocks (RRULE).

Exercises the pure `weekly_study_blocks` helper and parses the exported .ics
to assert VALARMs are always present and the RRULE study block appears only
when opted in.
"""
import os
import sys
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from database import Base, engine, SessionLocal  # noqa: E402
from main import app  # noqa: E402
from models import LearningPath, Milestone  # noqa: E402
from scheduling import weekly_study_blocks, DEFAULT_HOURS_PER_WEEK  # noqa: E402
from icalendar import Calendar  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _make_path(hours="10 hours/week", n=3):
    db = SessionLocal()
    try:
        p = LearningPath(
            title="Test Path", description="d", experience_level="beginner",
            time_commitment=hours, language="en",
        )
        db.add(p)
        db.flush()
        for i in range(n):
            db.add(Milestone(
                learning_path_id=p.id, title=f"M{i}", description="x",
                order=i, estimated_hours=5, resources="[]", completed=False,
            ))
        db.commit()
        return p.id
    finally:
        db.close()


# ---- pure helper -----------------------------------------------------------

def test_weekly_study_blocks_parses_hours():
    sb = weekly_study_blocks("10 hours/week", date(2026, 1, 1), date(2026, 3, 1))
    assert sb["hours_per_week"] == 10
    assert sb["dtstart"] == date(2026, 1, 1)
    assert sb["until"] == date(2026, 3, 1)
    assert sb["weekday"] == date(2026, 1, 1).weekday()


def test_weekly_study_blocks_empty_falls_back():
    sb = weekly_study_blocks("", date(2026, 1, 1), date(2026, 2, 1))
    assert sb["hours_per_week"] == DEFAULT_HOURS_PER_WEEK


def test_weekly_study_blocks_until_never_before_start():
    sb = weekly_study_blocks("5h", date(2026, 5, 1), date(2026, 1, 1))
    assert sb["until"] == date(2026, 5, 1)


# ---- route -----------------------------------------------------------------

def test_default_calendar_has_valarm_and_no_rrule(client):
    pid = _make_path()
    res = client.get(f"/api/paths/{pid}/calendar.ics")
    assert res.status_code == 200
    cal = Calendar.from_ical(res.content)
    vevents = [c for c in cal.walk("VEVENT")]
    assert len(vevents) == 3  # milestones only, no study block
    for ev in vevents:
        alarms = [c for c in ev.walk("VALARM")]
        assert len(alarms) == 1
        assert alarms[0].get("trigger").dt == timedelta(days=-1)
        assert "RRULE" not in ev  # no recurrence by default


def test_study_blocks_adds_one_rrule_weekly_event(client):
    pid = _make_path()
    res = client.get(f"/api/paths/{pid}/calendar.ics?study_blocks=1")
    assert res.status_code == 200
    cal = Calendar.from_ical(res.content)
    vevents = [c for c in cal.walk("VEVENT")]
    rrule_events = [ev for ev in vevents if "RRULE" in ev]
    assert len(rrule_events) == 1
    rr = rrule_events[0]["RRULE"]
    assert rr["FREQ"] == ["WEEKLY"]
    assert "UNTIL" in rr
    # milestones (3) + 1 study block
    assert len(vevents) == 4


def test_custom_reminder_days(client):
    pid = _make_path()
    res = client.get(f"/api/paths/{pid}/calendar.ics?reminder_days=3")
    cal = Calendar.from_ical(res.content)
    ev = next(iter(cal.walk("VEVENT")))
    alarm = next(iter(ev.walk("VALARM")))
    assert alarm.get("trigger").dt == timedelta(days=-3)


def test_calendar_404_for_missing_path(client):
    res = client.get("/api/paths/999999/calendar.ics")
    assert res.status_code == 404
