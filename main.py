import os
import feedparser
from google import genai

RSS_URL = "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"
OUTPUT_FILE = "scripts.txt"


def main():
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY secret is not configured")

    client = genai.Client(api_key=api_key)
    feed = feedparser.parse(RSS_URL)
    if not feed.entries:
        print("No RSS news received; check the feed or network.")
        return

    scripts = []
    seen = set()
    for entry in feed.entries[:5]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        summary = entry.get("summary", "").strip()
        if not title or not link or link in seen:
            continue
        seen.add(link)
        prompt = f"""Write an original 30-60 second English YouTube Shorts news script based only on the supplied information. Do not invent facts or claim independent verification. Clearly distinguish allegations from established facts. If the information is insufficient, say so and skip the story. Include a brief attribution to the source. Output narration only.

Headline: {title}
RSS summary: {summary}
Source link: {link}"""
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt
            )
            script = (response.text or "").strip()
            if script:
                scripts.append(
                    f"HEADLINE: {title}\nSOURCE: {link}\n\n{script}\n"
                )
                print("SCRIPT CREATED:", title)
            else:
                print("No script returned for:", title)
        except Exception as exc:
            print("AI request failed for", title, "-", str(exc))

    if scripts:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n---\n\n".join(scripts))
        print(f"Saved {len(scripts)} scripts to {OUTPUT_FILE}")
    else:
        print("No scripts were created.")


if __name__ == "__main__":
    main()
