from pathlib import Path
import re
import shutil

path = Path("main.py")
if not path.exists():
    raise SystemExit("main.py was not found. Run this script inside your USA_News_Automation folder.")

text = path.read_text(encoding="utf-8")

required_markers = [
    "def generate_script(keys, prompt):",
    "async def create_voice(script, path):",
    'VOICE = "en-US-AndrewNeural"',
    'video_filter = f"{subtitles_filter},{ticker_filter}"',
]
if any(marker not in text for marker in required_markers):
    raise SystemExit("This main.py is not the expected original version. No changes were made.")

if "VOICES = [" in text:
    raise SystemExit("This patch appears to have already been applied. No changes made.")

if "import time\n" not in text:
    text = text.replace("import subprocess\n", "import subprocess\nimport time\n", 1)
if "import random\n" not in text:
    text = text.replace("import time\n", "import time\nimport random\n", 1)

pattern = re.compile(
    r"def generate_script\(keys, prompt\):.*?(?=\n\n# ={5,}\n# FONTS AND IMAGE HELPERS)",
    re.S,
)
replacement = '''def generate_script(keys, prompt):
    last_error = ""
    for attempt in range(3):
        for slot, key in enumerate(keys, start=1):
            try:
                client = genai.Client(api_key=key)
                response = client.models.generate_content(
                    model=MODEL,
                    contents=prompt,
                )
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
                print(
                    f"AI error on key {slot}, attempt "
                    f"{attempt + 1}/3: {last_error[:180]}"
                )
                if not retryable:
                    return ""
        if attempt < 2:
            delay = (2 ** attempt) * 5
            print(f"Retrying AI generation in {delay}s.")
            time.sleep(delay)
    print("AI generation failed after retries:", last_error[:300])
    return ""
'''
text, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise SystemExit("Could not safely replace generate_script(). No changes made.")

text = text.replace(
    'VOICE = "en-US-AndrewNeural"',
    'VOICES = ["en-US-AndrewNeural", "en-US-JennyNeural"]  # male, female, male, repeat',
    1,
)
text = text.replace(
    "async def create_voice(script, path):\n"
    "    communicator = edge_tts.Communicate(\n"
    "        script,\n"
    "        VOICE,",
    "async def create_voice(script, path, voice):\n"
    "    communicator = edge_tts.Communicate(\n"
    "        script,\n"
    "        voice,",
    1,
)
text = text.replace(
    "    asyncio.run(create_voice(script, audio_path))",
    "    voice = VOICES[(index - 1) % len(VOICES)]\n"
    "    print(f\"Using voice for video {index}: {voice}\")\n"
    "    asyncio.run(create_voice(script, audio_path, voice))",
    1,
)

text = text.replace('"FontSize=17,"', '"FontSize=30,"', 1)
text = text.replace(
    "words[i:i + 6]\n        for i in range(0, len(words), 6)",
    "words[i:i + 5]\n        for i in range(0, len(words), 5)",
    1,
)
text = text.replace(
    'video_filter = f"{subtitles_filter},{ticker_filter}"',
    '''video_filter = (
        "zoompan=z='min(zoom+0.00025,1.035)':d=1:"
        "s=1080x1920:fps=25,"
        f"{subtitles_filter},{ticker_filter}"
    )''',
    1,
)

checks = [
    "import time",
    "import random",
    "VOICES = [",
    "async def create_voice(script, path, voice):",
    "FontSize=30",
    "zoompan=",
    "Retrying AI generation",
]
missing = [item for item in checks if item not in text]
if missing:
    raise SystemExit(f"Patch validation failed: {missing}. No changes made.")

backup = Path("main.py.backup")
if not backup.exists():
    shutil.copy2(path, backup)

path.write_text(text, encoding="utf-8")
print("SUCCESS: main.py updated.")
print("Backup saved as main.py.backup")
print("Voice order: video 1 male Andrew, video 2 female Jenny, video 3 male Andrew.")
print("Captions: larger font and shorter word groups.")
print("Video: gentle zoom motion enabled.")
print("AI: temporary failures retry up to 3 rounds.")
