import os
import json
import time
import urllib.request
from dotenv import load_dotenv

load_dotenv()
KEY = os.getenv("GEMINI_API_KEY")


def call(url, body=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json", "x-goog-api-key": KEY},
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


models = call("https://generativelanguage.googleapis.com/v1beta/models?pageSize=100")["models"]
names = [m["name"].replace("models/", "") for m in models
         if "generateContent" in m.get("supportedGenerationMethods", [])
         and "flash" in m["name"]]

body = {"contents": [{"role": "user", "parts": [{"text": "Reply with the word OK"}]}]}
for n in names:
    start = time.time()
    try:
        call(f"https://generativelanguage.googleapis.com/v1beta/models/{n}:generateContent", body)
        print(f"OK    {time.time() - start:5.1f}s  {n}")
    except Exception as e:
        print(f"FAIL  {time.time() - start:5.1f}s  {n}  {e}")