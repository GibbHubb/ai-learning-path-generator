import os
import json
import logging
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Simple in-memory cache to avoid repeated API calls
CACHE = {}

def generate_learning_path(goal: str, experience_level: str, time_commitment: str):
    """Generate a structured learning path using OpenAI"""
    
    # Check cache first
    cache_key = f"{goal}:{experience_level}:{time_commitment}"
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

Return your response as a JSON object with this exact structure:
{{
  "path_title": "A compelling title for this learning path",
  "path_description": "A brief overview of what this learning path covers and why it's structured this way",
  "milestones": [
    {{
      "title": "Milestone title",
      "description": "What you'll learn and why it's important",
      "estimated_hours": 10,
      "resources": ["Resource 1", "Resource 2", "Resource 3"]
    }}
  ]
}}

Make the path progressive - each milestone should build on previous ones. Be specific and actionable."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an expert learning path designer who creates structured, actionable learning plans. Always respond with valid JSON."},
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


def stream_learning_path(goal: str, experience_level: str, time_commitment: str):
    """
    Generator that yields milestones one-by-one after generating the full path.
    Uses the same caching and OpenAI call as generate_learning_path so the
    sync endpoint remains untouched.
    """
    result = generate_learning_path(goal, experience_level, time_commitment)
    for milestone in result.get("milestones", []):
        yield milestone


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
