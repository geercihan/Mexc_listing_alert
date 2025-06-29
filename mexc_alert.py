import os
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
from pycoingecko import CoinGeckoAPI
import subprocess
import json

# === Environment Variables ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
MEXC_RSS_URL = "https://www.mexc.com/rss"
MEXC_NEWLISTING_URL = "https://www.mexc.com/newlisting"
SEEN_TITLES_FILE = "seen_titles.txt"

cg = CoinGeckoAPI()

# === Load seen titles ===
def load_seen_titles():
    if not os.path.exists(SEEN_TITLES_FILE):
        return set()
    with open(SEEN_TITLES_FILE, "r") as f:
        return set(line.strip() for line in f.readlines())

# === Save new title ===
def save_seen_title(title):
    with open(SEEN_TITLES_FILE, "a") as f:
        f.write(title + "\n")

# === Send message to Telegram ===
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    requests.post(url, data=payload)

# === Search for contract using CoinGecko ===
def search_contract_coingecko(symbol):
    try:
        results = cg.search(symbol)
        if results.get("coins"):
            for coin in results["coins"]:
                if coin.get("id"):
                    data = cg.get_coin_by_id(coin["id"])
                    platforms = data.get("platforms", {})
                    if platforms:
                        lines = [f"{k}: {v}" for k, v in platforms.items() if v]
                        return "\n".join(lines)
        return None
    except Exception:
        return None

# === Search for contract using CoinMarketCap (Improved for all networks) ===
def search_contract_coinmarketcap(slug):
    try:
        url = f"https://coinmarketcap.com/currencies/{slug}/"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return None
        soup = BeautifulSoup(res.text, "html.parser")
        result = []

        # Modern format extraction (Solana, Ethereum, BSC, etc.)
        for div in soup.find_all("div", class_="content___Mm0HH"):
            spans = div.find_all("span")
            if not spans or not div.text:
                continue
            chain = spans[0].text.strip().replace(":", "")
            address = spans[-1].text.strip()
            if address and len(address) > 10:
                result.append(f"{chain}: {address}")

        # Legacy format fallback
        links = soup.find_all("a", href=True)
        for link in links:
            if "/currencies/" in link["href"] and "/contract/" in link["href"]:
                parts = link["href"].split("/")
                if "contract" in parts:
                    chain = parts[-2].capitalize()
                    address = parts[-1]
                    result.append(f"{chain}: {address}")

        return "\n".join(set(result)) if result else None
    except Exception:
        return None

# === Parse RSS Source ===
def parse_rss(seen_titles):
    feed = feedparser.parse(MEXC_RSS_URL)
    for entry in feed.entries:
        title = entry.title.strip()
        link = entry.link
        if title in seen_titles or "will list" not in title.lower():
            continue
        save_seen_title(title)
        token = title.split("(")[-1].split(")")[0].strip() if "(" in title else "?"
        name = title.split("(")[0].replace("MEXC", "").replace("Will List", "").strip()
        contract_info = search_contract_coingecko(token) or search_contract_coinmarketcap(name.lower().replace(" ", "-"))
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        message = f"🚨 New Token Listing (from RSS)\n\n*Name:* {name}\n*Symbol:* {token}\n*Detected:* {timestamp}\n[Official Announcement]({link})\n\n"
        message += f"Smart Contract(s):\n{contract_info}" if contract_info else "Smart Contract: Not found yet."
        send_telegram_message(message)

# === Parse MEXC New Listing Page ===
def parse_newlisting_page(seen_titles):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(MEXC_NEWLISTING_URL, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        items = soup.find_all("div", class_="new-listing-item")
        for item in items:
            name_tag = item.find("div", class_="coin-name")
            symbol_tag = item.find("div", class_="coin-short")
            time_tag = item.find("div", class_="time")
            if not (name_tag and symbol_tag and time_tag):
                continue
            name = name_tag.text.strip()
            symbol = symbol_tag.text.strip()
            listing_time = time_tag.text.strip()
            unique_id = f"{name}|{listing_time}"
            if unique_id in seen_titles:
                continue
            save_seen_title(unique_id)
            contract_info = search_contract_coingecko(symbol) or search_contract_coinmarketcap(name.lower().replace(" ", "-"))
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            message = f"🚨 New Token Listing (from NewListing Page)\n\n*Name:* {name}\n*Symbol:* {symbol}\n*Listing Time:* {listing_time}\n*Detected:* {timestamp}\n\n"
            message += f"Smart Contract(s):\n{contract_info}" if contract_info else "Smart Contract: Not found yet."
            send_telegram_message(message)
    except Exception as e:
        send_telegram_message(f"⚠ Error reading /newlisting page:\n{e}")

# === ✅ UPDATED: Parse Twitter Source using snscrape + JSON ===
def parse_twitter_listings(seen_titles):
    try:
        keywords = [
            "will list", "listing", "lists", "to be listed", "new listing",
            "initial listing", "listed", "listing soon", "available for trading"
        ]
        result = subprocess.run(
            ["snscrape", "--jsonl", "--max-results", "10", "twitter-user:MEXC_Listings"],
            capture_output=True, text=True
        )
        tweets = result.stdout.strip().split("\n")
        for raw in tweets:
            if not raw.strip():
                continue
            tweet = json.loads(raw)
            content = tweet.get("content", "")
            if any(keyword in content.lower() for keyword in keywords):
                tweet_id = str(tweet.get("id"))
                if tweet_id in seen_titles:
                    continue
                save_seen_title(tweet_id)
                timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                url = tweet.get("url", "")
                message = f"🚨 New Token Listing (from Twitter)\n\n*Detected:* {timestamp}\n"
                message += f"[View Tweet]({url})\n\nTweet Content:\n{content}"
                send_telegram_message(message)
    except Exception as e:
        send_telegram_message(f"⚠ Error reading tweets:\n{e}")

# === Main ===
def main():
    seen_titles = load_seen_titles()
    parse_rss(seen_titles)
    parse_newlisting_page(seen_titles)
    parse_twitter_listings(seen_titles)

if __name__ == "__main__":
    main()
