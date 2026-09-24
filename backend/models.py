from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Float, Date, Index
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


# AP9 — User accounts (magic-link auth)
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)
    # AP11 — daily reminder email opt-in + bookkeeping
    reminder_opt_in = Column(Boolean, default=False, nullable=False)
    reminder_sent_at = Column(DateTime, nullable=True)
    no_activity_reminders_sent = Column(Integer, default=0, nullable=False)
    # AP30 — opt-in public profile (badges + streak shareable at /u/:id)
    is_public_profile = Column(Boolean, default=False, nullable=False)


class MagicLink(Base):
    """Single-use, short-lived magic-link token. Stores sha256 of the token,
    never the raw value. Plain token only travels in the verification URL."""
    __tablename__ = "magic_links"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    token_hash = Column(String, index=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Session(Base):
    """Server-side session record. The session_id is an opaque UUID stored
    directly in an HttpOnly cookie — opaque + DB-backed lets us invalidate."""
    __tablename__ = "sessions"

    id = Column(String, primary_key=True)  # uuid4 hex
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class LearningPath(Base):
    __tablename__ = "learning_paths"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    description = Column(Text)
    experience_level = Column(String)  # beginner, intermediate, advanced
    time_commitment = Column(String)  # e.g., "10 hours/week"
    is_public = Column(Boolean, default=False, nullable=False)
    total_xp = Column(Integer, default=0, nullable=False)
    streak_days = Column(Integer, default=0, nullable=False)
    last_active_date = Column(Date, nullable=True)
    category = Column(String, nullable=True)  # AP6: "Programming" | "Design" | "Business" | "Science" | "Creative" | "Language" | "Other"
    language = Column(String, default="en", nullable=False)  # AP27 — content language for regeneration consistency
    # AP9 — ownership
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    anon_session_id = Column(String, nullable=True, index=True)  # browser-cookie id for the claim flow
    # AP10 — fork lineage (nearest ancestor; original author derived once on fork)
    forked_from_id = Column(Integer, ForeignKey("learning_paths.id"), nullable=True)
    original_author_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    fork_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    milestones = relationship("Milestone", back_populates="learning_path", cascade="all, delete-orphan")
    # AP24 — append-only revision history (initial / harder / easier / restore)
    revisions = relationship("PathRevision", back_populates="learning_path", cascade="all, delete-orphan", order_by="PathRevision.revision_number")


# AP24 — JSON snapshot of a path's milestones at a point in time. Append-only
# so the user can always revert (a `restore` is itself a new revision, never
# destructive). milestones_json holds the ordered list (title, description,
# order, estimated_hours, resources, completed, completed_at).
class PathRevision(Base):
    __tablename__ = "path_revisions"

    id = Column(Integer, primary_key=True, index=True)
    learning_path_id = Column(Integer, ForeignKey("learning_paths.id"), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    milestones_json = Column(Text, nullable=False)
    trigger = Column(String, nullable=False)  # "initial" | "harder" | "easier" | "restore"
    created_at = Column(DateTime, default=datetime.utcnow)

    learning_path = relationship("LearningPath", back_populates="revisions")


class Milestone(Base):
    __tablename__ = "milestones"

    id = Column(Integer, primary_key=True, index=True)
    learning_path_id = Column(Integer, ForeignKey("learning_paths.id"))
    title = Column(String)
    description = Column(Text)
    order = Column(Integer)  # sequence in the learning path
    estimated_hours = Column(Float)
    resources = Column(Text)  # JSON string of recommended resources
    completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    difficulty_feedback = Column(String, nullable=True)  # AP5: "too_easy" | "too_hard" | None

    learning_path = relationship("LearningPath", back_populates="milestones")
    notes = relationship("MilestoneNote", back_populates="milestone", cascade="all, delete-orphan")
    tasks = relationship("MilestoneTask", back_populates="milestone", cascade="all, delete-orphan", order_by="MilestoneTask.order")


# AP23 — optional ordered sub-task checklist under a milestone. When tasks
# exist on a milestone, its `completed` flag is DERIVED from
# all(tasks.completed) — completing the last open task auto-completes the
# milestone (same code path as a manual complete, see complete_milestone()
# in routes.py); un-ticking after auto-complete reverts. Zero-task
# milestones behave exactly as pre-AP23.
class MilestoneTask(Base):
    __tablename__ = "milestone_tasks"

    id = Column(Integer, primary_key=True, index=True)
    milestone_id = Column(Integer, ForeignKey("milestones.id"), nullable=False, index=True)
    order = Column(Integer, nullable=False, default=0)
    title = Column(String, nullable=False)
    completed = Column(Boolean, nullable=False, default=False)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    milestone = relationship("Milestone", back_populates="tasks")


# AP8 — cached MCQ set per milestone (one row per milestone).
# `questions_json` holds the full quiz INCLUDING correct_index +
# explanations. Plan §5 chose client-side grading for speed; server still
# re-grades on attempt-submit so the score in the DB is authoritative.
class MilestoneQuiz(Base):
    __tablename__ = "milestone_quizzes"

    id = Column(Integer, primary_key=True, index=True)
    milestone_id = Column(Integer, ForeignKey("milestones.id"), nullable=False, unique=True, index=True)
    questions_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# AP8 — audit row per quiz submission (drives AP5 difficulty signal too).
class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    milestone_id = Column(Integer, ForeignKey("milestones.id"), nullable=False, index=True)
    score = Column(Float, nullable=False)            # 0.0 .. 1.0
    passed = Column(Boolean, nullable=False)
    completed_at = Column(DateTime, default=datetime.utcnow)


# AP11 — audit row per outbound reminder email (debug + analytics).
class ReminderLog(Base):
    __tablename__ = "reminder_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    path_id = Column(Integer, ForeignKey("learning_paths.id"), nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="sent", nullable=False)  # 'sent' | 'failed' | 'skipped'
    note = Column(Text, nullable=True)


# AP12 — per-user free-text reflection on a milestone. One row per
# (milestone_id, user_id). `is_private` controls visibility on /share/:id.
# `difficulty_flag` is a small heuristic signal AP5 may consume:
#   -1 = "too easy"  ·  0 = neutral  ·  +1 = hard  ·  +2 = confused/stuck
class MilestoneNote(Base):
    __tablename__ = "milestone_notes"

    id = Column(Integer, primary_key=True, index=True)
    milestone_id = Column(Integer, ForeignKey("milestones.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    is_private = Column(Boolean, default=False, nullable=False)
    difficulty_flag = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    milestone = relationship("Milestone", back_populates="notes")


# AP35 — one row per accepted hit against a rate-limited (model-calling) route.
# Durable (survives a process restart / a new Vercel instance) and shared across
# every instance talking to this database — the two properties the old
# in-process `request_counts` dict in main.py never had. `key` is the visitor
# identity rate_limit.derive_key() derived (trusted X-Forwarded-For, or the raw
# peer address); `route` collapses id-bearing paths to one label per route (see
# rate_limit.route_label_for). Rows older than the window are pruned on every
# write (rate_limit.prune), so this table never grows unbounded.
class RateLimitHit(Base):
    __tablename__ = "rate_limit_hits"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, nullable=False)
    route = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_rate_limit_hits_key_route_created", "key", "route", "created_at"),
    )


# AP36 — one row per llm.chat_json call, success OR failure. Written by
# usage.record_call, called from INSIDE chat_json (llm.py) so every present and
# future caller is covered by construction, not by remembering to call it.
#
# `route` is the caller's function name, derived inside chat_json via
# inspect.currentframe().f_back — best-effort labelling, never a hard requirement
# (falls back to "unknown" rather than losing the row). `light` marks the
# higher-quota/cheaper model (enrichment, quizzes).
#
# `prompt_tokens`/`completion_tokens` are NULL when the provider response carried
# no `usage` attribute at all — observed as a real possibility on the
# Gemini-through-the-OpenAI-SDK path (see llm.py), not assumed away. A NULL never
# costs the row: it is still written, with `ok=True`.
#
# `finish_reason` holds the provider's finish reason on success, or the exception
# TYPE NAME on failure (`ok=False`) — so a burst of provider errors shows up as a
# value to group by, not silence.
#
# `est_cost_usd` is NULL for any model missing from usage._PRICES (never guessed
# — see usage.py) and 0.0 for a priced free-tier model, which is the honest value.
class ModelCall(Base):
    __tablename__ = "model_calls"

    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    route = Column(String, nullable=False)
    model = Column(String, nullable=False)
    light = Column(Boolean, nullable=False, default=False)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    finish_reason = Column(String, nullable=True)
    est_cost_usd = Column(Float, nullable=True)
    ok = Column(Boolean, nullable=False)
