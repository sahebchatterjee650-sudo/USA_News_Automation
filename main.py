import feedparser
import sqlite3
import hashlib
from datetime import datetime, timezone

RSS_URL = "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"
DATABASE = "news_database.db"

def main():
    feed = feedparser.parse(RSS_URL)
    if not feed.entries:
        print("No news received. Check RSS or internet connection.")
        return
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS news (id TEXT PRIMARY KEY, title TEXT, link TEXT, published TEXT, collected_at TEXT)""")
        count = 0
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue
            news_id = hashlib.sha256(link.encode("utf-8")).hexdigest()
            cursor = conn.execute("INSERT OR IGNORE INTO news VALUES (?, ?, ?, ?, ?)", (news_id, title, link, entry.get("published", ""), datetime.now(timezone.utc).isoformat()))
            if cursor.rowcount:
                count += 1
                print("\nNEWS:", title, "\nLINK:", link)
    print(f"\nCollection complete. New items saved: {count}")

if __name__ == "__main__":
    main()
