"""Run the REAL app on a throwaway SQLite DB with llm.chat_json scripted.

Usage: python stub_server.py <port> <db_path>

* path generation: returns the AP39 fixture whose goal appears in the prompt;
  a goal containing FAILME gets invalid JSON twice -> the app's 502 path.
* quiz: 3 fixed questions (correct answers: 0, 1, 2).
* enrichment / adjust: small valid replies.
No network: GEMINI_API_KEY is set to a dummy only so llm.provider() is truthy
(quizzes.py refuses to call without one); chat_json itself never reaches it.
"""
import json
import os
import sys
from pathlib import Path

port, db_path = int(sys.argv[1]), sys.argv[2]
REPO = Path(r"C:\Users\max\SynologyDrive\Bureaublad\code\ai-learning-path-generator")
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)
os.environ["DATABASE_URL"] = f"sqlite:///{Path(db_path).as_posix()}"
os.environ["ENVIRONMENT"] = "development"
os.environ["GEMINI_API_KEY"] = "stub-not-a-real-key"
os.environ["APP_BASE_URL"] = "http://localhost:5174"
os.environ.pop("RESEND_API_KEY", None)

import llm  # noqa: E402

GOLD = json.loads((BACKEND / "evals" / "goldset.json").read_text(encoding="utf-8"))["cases"]
FIX = {c["id"]: json.loads((BACKEND / "evals" / "fixtures" / f"{c['id']}.json").read_text(encoding="utf-8"))
       for c in GOLD}

QUIZ = [
    {"question": "Which comes first?", "options": ["Basics", "Advanced", "Expert", "None"],
     "correct_index": 0, "explanation": "Start with the basics."},
    {"question": "What helps retention?", "options": ["Cramming", "Practice", "Skipping", "Guessing"],
     "correct_index": 1, "explanation": "Spaced practice."},
    {"question": "What is a milestone?", "options": ["A bug", "A song", "A checkpoint", "A tool"],
     "correct_index": 2, "explanation": "A checkpoint on the path."},
]


def fake_chat_json(system, user, **kwargs):
    if "comprehension quiz" in user:
        return json.dumps(QUIZ)
    if "learning resource curator" in user:
        return json.dumps([{"title": "Official docs", "url": "https://example.org/docs", "type": "docs"}])
    if "adjusting an existing learning path" in user:
        return json.dumps({"milestones": [{"title": "Adjusted", "description": "d",
                                           "estimated_hours": 3, "resources": []}]})
    if "FAILME" in user:
        return "this is not json"
    for c in GOLD:
        if f"Goal: {c['goal']}" in user:
            return json.dumps(FIX[c["id"]]["response"], ensure_ascii=False)
    return json.dumps(FIX["py-data"]["response"])


llm.chat_json = fake_chat_json

import uvicorn  # noqa: E402

uvicorn.run("main:app", host="127.0.0.1", port=port, log_level="info")
