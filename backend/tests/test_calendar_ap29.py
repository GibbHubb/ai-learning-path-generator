"""AP29 — calendar reminders (VALARM) + optional weekly study blocks (RRULE).

Exercises the pure `weekly_study_blocks` helper and parses the exported .ics
to assert VALARMs are always present and the RRULE study block appears only
when opted in.
"""
import os
import sys
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from database import Base, engine, SessionLocal  # noqa: E402
from main import app  # noqa: E402
from models import LearningPath, Milestone  # noqa: E402
from scheduling import weekly_study_blocks, DEFAULT_HOURS_PER_WEEK, MAX_BLOCK_HOURS  # noqa: E402
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


# ---- AP29-fu1: timed study blocks ------------------------------------------

def test_timed_block_helper_returns_datetime_and_duration():
    sb = weekly_study_blocks("6 hours/week", date(2026, 1, 1), date(2026, 3, 1), hour=18)
    assert sb["dtstart_dt"] == datetime(2026, 1, 1, 18, 0)
    assert sb["duration"] == timedelta(hours=6)
    assert sb["block_hours"] == 6
    # the all-day keys are still there, so existing callers are unaffected
    assert sb["dtstart"] == date(2026, 1, 1)


def test_timed_block_helper_honours_minute():
    sb = weekly_study_blocks("2 hours/week", date(2026, 1, 1), date(2026, 3, 1),
                             hour=7, minute=30)
    assert sb["dtstart_dt"] == datetime(2026, 1, 1, 7, 30)


def test_timed_block_duration_is_capped():
    # A 40 h/week path must not emit a single 40-hour event spanning days.
    sb = weekly_study_blocks("40 hours/week", date(2026, 1, 1), date(2026, 3, 1), hour=9)
    assert sb["hours_per_week"] == 40
    assert sb["duration"] == timedelta(hours=MAX_BLOCK_HOURS)


def test_timed_block_helper_rejects_out_of_range():
    for bad in (-1, 24, 99):
        with pytest.raises(ValueError):
            weekly_study_blocks("5 hours/week", date(2026, 1, 1), date(2026, 3, 1), hour=bad)
    with pytest.raises(ValueError):
        weekly_study_blocks("5 hours/week", date(2026, 1, 1), date(2026, 3, 1),
                            hour=10, minute=60)


def test_omitting_hour_keeps_the_all_day_block():
    sb = weekly_study_blocks("5 hours/week", date(2026, 1, 1), date(2026, 3, 1))
    assert "dtstart_dt" not in sb
    assert "duration" not in sb


def test_study_block_hour_produces_a_timed_vevent(client):
    pid = _make_path(hours="6 hours/week")
    res = client.get(f"/api/paths/{pid}/calendar.ics?study_blocks=1&study_block_hour=18")
    assert res.status_code == 200
    cal = Calendar.from_ical(res.content)
    block = next(ev for ev in cal.walk("VEVENT") if "RRULE" in ev)
    start, end = block["DTSTART"].dt, block["DTEND"].dt
    # datetime, not date — i.e. a real time-boxed commitment
    assert isinstance(start, datetime)
    assert start.hour == 18
    assert end - start == timedelta(hours=6)
    # floating time: no tzinfo, so it lands at 18:00 in the viewer's own zone
    assert start.tzinfo is None
    assert "RRULE" in block


def test_study_block_without_hour_stays_all_day(client):
    # AP29's shipped behaviour must be untouched for existing links.
    pid = _make_path()
    res = client.get(f"/api/paths/{pid}/calendar.ics?study_blocks=1")
    cal = Calendar.from_ical(res.content)
    block = next(ev for ev in cal.walk("VEVENT") if "RRULE" in ev)
    assert not isinstance(block["DTSTART"].dt, datetime)
    assert isinstance(block["DTSTART"].dt, date)


def test_study_block_hour_ignored_without_study_blocks(client):
    pid = _make_path()
    res = client.get(f"/api/paths/{pid}/calendar.ics?study_block_hour=18")
    cal = Calendar.from_ical(res.content)
    assert [ev for ev in cal.walk("VEVENT") if "RRULE" in ev] == []


def test_out_of_range_hour_is_a_422_not_a_500(client):
    pid = _make_path()
    res = client.get(f"/api/paths/{pid}/calendar.ics?study_blocks=1&study_block_hour=25")
    assert res.status_code == 422
