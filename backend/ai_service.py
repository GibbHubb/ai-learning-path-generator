import os
import json
import logging
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Simple in-memory cache to avoid repeated API calls
CACHE = {}


# AP27 — supported content languages. Unknown values fall back to "en".
LANGUAGE_NAMES = {
    "en": "English",
    "nl": "Dutch",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
}


def language_instruction(language: str | None) -> str:
    """One deterministic line appended to the system prompt to localise
    *values* while keeping JSON keys/structure pinned in English.

    Returns "" for English / unknown / falsy (so the prompt is byte-
    identical to pre-AP27 behaviour for the default English path).
    """
    if not language or language == "en":
        return ""
    name = LANGUAGE_NAMES.get(language)
    if not name:
        return ""
    return (
        f" Generate all output text values (titles, descriptions, resources)"
        f" in {name}. Keep the JSON keys and overall structure exactly as"
        f" specified — only the free-text values change language."
    )

def generate_learning_path(goal: str, experience_level: str, time_commitment: str, language: str = "en"):
    """Generate a structured learning path using OpenAI"""

    # Normalise unknown languages → English so the cache key + prompt stay
    # consistent (an attacker can't poison the cache with arbitrary codes).
    if language not in LANGUAGE_NAMES:
        language = "en"

    # Check cache first
    cache_key = f"{goal}:{experience_level}:{time_commitment}:{language}"
    if cache_key in CACHE:
        logger.info(f"Returning cached result for: {cache_key}")
        return CACHE[cache_key]

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    # TODO: might want to cache common paths later
    prompt = f"""You are an expert learning path designer. Create a detailed, structured learning path for someone who wants to: {goal}

Experience Level: {experience_level}
Time Commitment: {time_commitment}

Generate a comprehensive learning path with 5-8 major milestones. For each milestone, provide:
1. A clear, concise title
2. A detailed description of what will be learned
3. Estimated hours to complete
4. 2-3 specific resource recommendations (books, courses, websites, or practice projects)

Also classify the path into exactly ONE of these categories: "Programming", "Design", "Business", "Science", "Creative", "Language", "Other".

Return your response as a JSON object with this exact structure:
{{
  "path_title": "A compelling title for this learning path",
  "path_description": "A brief overview of what this learning path covers and why it's structured this way",
  "category": "Programming",
  "milestones": [
    {{
      "title": "Milestone title",
      "description": "What you'll learn and why it's important",
      "estimated_hours": 10,
      "resources": ["Resource 1", "Resource 2", "Resource 3"]
    }}
  ]
}}

Make the path progressive - each milestone should build on previous ones. Be specific and actionable. The `category` field MUST be one of the seven allowed values, with exact casing."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an expert learning path designer who creates structured, actionable learning plans. Always respond with valid JSON." + language_instruction(language)},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        
        # Store in cache
        CACHE[cache_key] = result
        return result
    
    except Exception as e:
        logger.error(f"Error generating learning path: {e}")
        raise Exception(f"Failed to generate learning path: {str(e)}")


def stream_learning_path(goal: str, experience_level: str, time_commitment: str, language: str = "en"):
    """
    Generator that yields milestones one-by-one after generating the full path.
    Uses the same caching and OpenAI call as generate_learning_path so the
    sync endpoint remains untouched.
    """
    result = generate_learning_path(goal, experience_level, time_commitment, language)
    for milestone in result.get("milestones", []):
        yield milestone


def adjust_difficulty(
    goal: str,
    experience_level: str,
    time_commitment: str,
    path_title: str,
    path_description: str,
    completed_milestones: list,
    remaining_milestones: list,
    feedback: str,
    language: str = "en",
):
    """AP5 — Regenerate remaining milestones at adjusted difficulty.

    Uses OpenAI (same path as generate_learning_path) with a prompt that:
    - Lists already-completed milestone titles (for coherence)
    - Lists the remaining milestone titles to be replaced
    - Instructs GPT to produce N new milestones at higher/lower difficulty

    Yields milestones one-by-one (generator) so callers can stream.
    Returns a list of the generated milestones.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    adjustment = {
        "too_easy": "The learner found the path too easy so far. Make the remaining milestones MORE challenging — go deeper, cover advanced topics, and expect the learner to tackle harder practice projects.",
        "too_hard": "The learner found the path too difficult so far. Make the remaining milestones EASIER — slow the pace, add more foundational explanations, and suggest gentler practice projects.",
    }.get(feedback, "Keep the difficulty consistent with the current path.")

    completed_titles = [m["title"] for m in completed_milestones]
    remaining_count = len(remaining_milestones)

    prompt = f"""You are an expert learning path designer. You are adjusting an existing learning path in response to user feedback.

Goal: {goal}
Experience Level: {experience_level}
Time Commitment: {time_commitment}

Path title: {path_title}
Path overview: {path_description}

The learner has already completed these milestones (DO NOT regenerate them, just keep them in mind for coherence):
{json.dumps(completed_titles, indent=2)}

The next {remaining_count} milestones need to be regenerated. {adjustment}

Produce exactly {remaining_count} new milestones that continue logically from the completed ones. For each milestone provide:
1. A clear, concise title
2. A detailed description of what will be learned
3. Estimated hours to complete
4. 2-3 specific resource recommendations

Return ONLY a JSON object with this exact structure:
{{
  "milestones": [
    {{
      "title": "...",
      "description": "...",
      "estimated_hours": 10,
      "resources": ["...", "...", "..."]
    }}
  ]
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an expert learning path designer. Always respond with valid JSON." + language_instruction(language)},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        return result.get("milestones", [])
    except Exception as e:
        logger.error(f"Error adjusting difficulty: {e}")
        raise Exception(f"Failed to adjust difficulty: {str(e)}")


def enrich_milestone_resources(milestone_id: int, title: str, description: str, goal: str):
    """Enrich a milestone with 2-3 real resource links via Claude Haiku.

    Runs synchronously (called from a BackgroundTask thread).
    Updates the milestone's resources column in the DB.
    """
    import re
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic SDK not installed — skipping resource enrichment")
        return

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping resource enrichment")
        return

    from database import SessionLocal
    from models import Milestone

    prompt = (
        f"You are a learning resource curator. For the milestone '{title}' "
        f"(description: {description}) in a learning path about '{goal}', "
        f"list exactly 3 real, publicly accessible learning resources. "
        f'Return ONLY a JSON array: [{{"title": "...", "url": "...", "type": "video|docs|article"}}]. '
        f"Use specific, named resources you know exist. No markdown fences."
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()
        # Strip markdown fences if present
        raw = re.sub(r'^```json?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        parsed = json.loads(raw)

        if not isinstance(parsed, list):
            logger.warning(f"Enrichment for milestone {milestone_id}: expected list, got {type(parsed)}")
            return

        db = SessionLocal()
        try:
            m = db.query(Milestone).filter(Milestone.id == milestone_id).first()
            if m:
                m.resources = json.dumps(parsed)
                db.commit()
                logger.info(f"Enriched milestone {milestone_id} with {len(parsed)} resources")
        finally:
            db.close()

    except Exception as e:
        logger.warning(f"Resource enrichment failed for milestone {milestone_id}: {e}")
