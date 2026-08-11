import httpx
import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)

async def generate_image(api_key: str, prompt: str, aspect_ratio: str = "16:9") -> bytes:
    """Calls Gemini API with IMAGE modality and returns image bytes."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"]
        }
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        
        # Extract base64 image from response
        try:
            part = data["candidates"][0]["content"]["parts"][0]
            if "inlineData" in part:
                img_data = part["inlineData"]["data"]
                return base64.b64decode(img_data)
        except (KeyError, IndexError) as e:
            logger.error(f"Failed to parse image from response: {data}")
            raise Exception("Invalid response format from Gemini API")
            
        raise Exception("No image data found in response")

async def generate_speech(api_key: str, text: str, voice: str = "en-US-Journey-F", language: str = "en-US", speed: float = 1.0) -> bytes:
    """Calls Google Cloud TTS API and returns audio bytes."""
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
    
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": language, "name": voice},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": speed}
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        
        if "audioContent" in data:
            return base64.b64decode(data["audioContent"])
            
        raise Exception("No audio content found in response")

async def test_connection(api_key: str) -> bool:
    """Tests the Gemini API connection."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        return response.status_code == 200
