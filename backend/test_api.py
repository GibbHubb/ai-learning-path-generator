import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")

print(f"API Key loaded: {api_key[:20]}..." if api_key else "No API key found!")
print(f"API Key length: {len(api_key)}" if api_key else "")

try:
    client = OpenAI(api_key=api_key)
    print("\n✓ OpenAI client created successfully")
    
    # Test with a simple completion
    print("\nTesting API call...")
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "user", "content": "Say 'API key works!'"}
        ],
        max_tokens=10
    )
    
    print(f"✓ API Response: {response.choices[0].message.content}")
    print("\n✅ API KEY IS VALID AND WORKING!")
    
except Exception as e:
    print(f"\n❌ ERROR: {e}")
    print(f"\nError type: {type(e).__name__}")
