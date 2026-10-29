from pathlib import Path
import re

path = Path("main.py")
text = path.read_text(encoding="utf-8")
if "import time" not in text:
    text = text.replace("import subprocess", "import subprocess\\nimport time")
if "import random" not in text:
    text = text.replace("import time", "import time\\nimport random")

pattern = re.compile(r"def generate_script\\(keys, prompt\\):.*?(?=\\n\\n# ={5,}\\n# FONTS AND IMAGE HELPERS)", re.S)
replacement = \'''def generate_script(keys, prompt):
    last_error = ""
    for attempt in range(3):
        for slot, key in enumerate(keys, start=1):
            try:
                client = genai.Client(api_key=key)
                response = client.models.generate_content(model=MODEL, contents=prompt)
                script = (response.text or "").strip()
                if script:
                    print(f"Script generated using key slot {slot}.")
                    return script
                last_error = "Empty response from model."
            except Exception as exc:
                last_error = str(exc)
                low = last_error.lower()
                retryable = any(term in low for term in (
                    "429", "503", "unavailable", "resource_exhausted",
                    "quota exceeded", "rate limit", "server disconnected",
                    "deadline exceeded", "temporarily",
                ))
                print(f"AI error on key {slot}, attempt {attempt + 1}/3: {last_error[:180]}")
                if not retryable:
                    return ""
        if attempt < 2:
            delay = (2 ** attempt) * 5 + random.uniform(0, 2)
            print(f"Retrying AI generation in {delay:.1f}s.")
            time.sleep(delay)
    print("AI generation failed after retries:", last_error[:300])
    return ""
\'''
text, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise SystemExit("Could not safely find generate_script() in main.py; no changes written.")
text = text.replace('"FontSize=17,"', '"FontSize=27,"')
old_filter = 'video_filter = f"{subtitles_filter},{ticker_filter}"'
new_filter = \'''video_filter = (
        "zoompan=z='min(zoom+0.00025,1.035)':d=1:"
        "s=1080x1920:fps=25,"
        f"{subtitles_filter},{ticker_filter}"
    )\'''
if old_filter in text:
    text = text.replace(old_filter, new_filter)
else:
    print("Note: video_filter differed; zoom motion was not changed.")
path.write_text(text, encoding="utf-8")
print("Updated main.py: retry handling, larger subtitles, and gentle zoom where matched.")
