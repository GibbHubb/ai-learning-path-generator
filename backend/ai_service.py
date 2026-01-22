import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

def generate_learning_path(goal: str, experience_level: str, time_commitment: str):
    """Generate a structured learning path using OpenAI"""
    
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
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert learning path designer who creates structured, actionable learning plans."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        return result
    
    except Exception as e:
        # probably should add better error handling here
        print(f"Error generating learning path: {e}")
        raise Exception(f"Failed to generate learning path: {str(e)}")
