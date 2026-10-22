import os
import json
import urllib.request
from dotenv import load_dotenv

load_dotenv()
req = urllib.request.Request(
    "https://generativelanguage.googleapis.com/v1beta/models?pageSize=100",
    headers={"x-goog-api-key": os.getenv("GEMINI_API_KEY")},
)
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read())

for m in data.get("models", []):
    if "generateContent" in m.get("supportedGenerationMethods", []):
        print(m["name"].replace("models/", ""))