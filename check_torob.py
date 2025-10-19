# check_torob.py
# Dependencies: requests, beautifulsoup4
import os, json, re, statistics, time
from dataclasses import dataclass
from typing import List, Optional, Tuple
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {"User-Agent": "Mozilla/5.0 (TorobPriceBot; +https://github.com/yourrepo)"}
REQ_TIMEOUT = 30

TRANS_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
SEP = " ,\u200f\u200e\u2066\u2067\u2068\u2069\u202a\u202b\u202c\u202d\u202e\u202f\u00a0\u066c"
PRICE_RE = re.compile(rf"([\d\u06F0-\u06F9][\d\u06F0-\u06F9{re.escape(SEP)}]+)\s*تومان")
MIN_TOMAN, MAX_TOMAN = 10_000, 10_000_000_000

CITIES = ["تهران","شیراز","اصفهان","مشهد","تبریز","کرج","قم","اهواز","کرمان","کرمانشاه","رشت","یزد","اراک","اردبیل","ساری","گرگان","زنجان","همدان","قزوین","بندرعباس","سنندج","خرم‌آباد","بوشهر","ایلام","بیرجند","بجنورد","یاسوج","سمنان","ارومیه","شهرکرد","قشم","کیش","مازندران","گیلان","خراسان","فارس","البرز","هرمزگان","قم"]
CITY_RE = re.compile("|".join(map(re.escape, CITIES)))

@dataclass
class Offer:
    price: int
    seller: str
    city: Optional[str]

def to_int_price(txt: str) -> Optional[int]:
    if not txt: return None
    t = re.sub(r"[^\d]", "", txt.translate(TRANS_DIGITS))
    if not t: return None
    n = int(t)
    return n if MIN_TOMAN <= n <= MAX_TOMAN else None

def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT); r.raise_for_status(); return r.text

def try_jsonld_offers(soup: BeautifulSoup) -> List[Offer]:
    offers: List[Offer] = []
    for s in soup.find_all("script", {"type": "application/ld+json"}):
        if not s.string: continue
        try: data = json.loads(s.string)
        except:
            try: data = json.loads(s.string.strip().split("\n")[0])
            except: continue
        def collect(obj):
            if isinstance(obj, list):
                for x in obj: collect(x); return
            if not isinstance(obj, dict): return
            off = obj.get("offers")
            if off:
                if isinstance(off, dict): off = [off]
                for o in off:
                    price = to_int_price(str(o.get("price") or o.get("priceSpecification", {}).get("price")))
                    if not price: continue
                    seller = ""
                    sll = o.get("seller")
                    if isinstance(sll, dict): seller = (sll.get("name") or "").strip()
                    elif isinstance(sll, str): seller = sll.strip()
                    city = None
                    loc = o.get("availableAtOrFrom") or o.get("areaServed")
                    if isinstance(loc, dict):
                        city = loc.get("address", {}).get("addressLocality") or loc.get("name")
                    offers.append(Offer(price, seller or "نامشخص", city))
            for _, v in obj.items():
                if isinstance(v, (dict, list)): collect(v)
        collect(data)
    uniq, seen = [], set()
    for o in offers:
        k = (o.price, o.seller, o.city)
        if k in seen: continue
        seen.add(k); uniq.append(o)
    return uniq

def text_offers_from_page_text(full_text: str) -> List[Offer]:
    offers: List[Offer] = []
    for m in PRICE_RE.finditer(full_text):
        price = to_int_price(m.group(1))
        if not price: continue
        start = max(0, m.start() - 120)
        left = re.sub(r"\s+", " ", full_text[start:m.start()]).strip()
        city_m = CITY_RE.search(left); city = city_m.group(0) if city_m else None
        hint = re.sub(r"(سال در ترب|گزارش|خرید اینترنتی|خرید حضوری|ضمانت ترب|★|\d+★)", "", left[-40:]).strip(" .،:|-")
        if city: hint = re.sub(re.escape(city), "", hint).strip(" .،:|-")
        seller = hint if hint else "فروشنده نامشخص"
        offers.append(Offer(price, seller, city))
    dedup, seen = [], set()
    for o in offers:
        k = (o.price, o.seller, o.city)
        if k in seen: continue
        seen.add(k); dedup.append(o)
    return dedup

def extract_offers(html: str) -> Tuple[List[Offer], str]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.find("h1").get_text(strip=True) if soup.find("h1") else None) \
         or (soup.find("meta", {"property": "og:title"}) or {}).get("content") \
         or (soup.title.string if soup.title else "عنوان نامشخص")
    offers = try_jsonld_offers(soup)
    if not offers:
        offers = text_offers_from_page_text(soup.get_text(" ", strip=True))
    offers = [o for o in offers if o.price]
    return offers, (title or "عنوان نامشخص")

def stats_for(offers: List[Offer]):
    if not offers: return {"count": 0, "min": None, "max": None, "avg": None, "closest": None, "cheapest": []}
    prices = [o.price for o in offers]
    mn, mx = min(prices), max(prices); avg = int(statistics.mean(prices))
    closest = min(offers, key=lambda o: abs(o.price - avg))
    cheapest = sorted(offers, key=lambda o: o.price)[:3]
    return {"count": len(offers), "min": mn, "max": mx, "avg": avg, "closest": closest, "cheapest": cheapest}

def money(n: Optional[int]) -> str: return "N/A" if n is None else f"{n:,} تومان".replace(",", ",")

def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT: 
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"); return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try: requests.post(url, json=payload, timeout=25).raise_for_status()
    except Exception as e: print("Telegram send failed:", e)

def main():
    with open("urls.json", "r", encoding="utf-8") as f: items = json.load(f)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"🛒 گزارش قیمت توروب — {now}", ""]
    for it in items:
        label, url = it["label"], it["url"]
        try:
            html = fetch_html(url); offers, title = extract_offers(html); s = stats_for(offers)
        except Exception:
            offers, title, s = [], "خطا در دریافت صفحه", {"count": 0, "min": None, "max": None, "avg": None, "closest": None, "cheapest": []}
        lines.append(f"• <b>{label}</b>"); lines.append(url)
        if s["count"] == 0:
            lines.append("قیمت یافت نشد ❌"); lines.append(""); time.sleep(1.2); continue
        lines.append(f"تعداد قیمت‌های فروشندگان: {s['count']}")
        lines.append(f"حداقل: {money(s['min'])}")
        lines.append(f"حداکثر: {money(s['max'])}")
        lines.append(f"میانگین: {money(s['avg'])}")
        c = s["closest"]
        if c:
            city = f" — {c.city}" if c.city else ""
            lines.append(f"نزدیک‌ترین به میانگین: <b>{money(c.price)}</b> | فروشنده: «{c.seller}»{city}")
        if s["cheapest"]:
            lines.append("ارزان‌ترین‌ها:")
            for o in s["cheapest"]:
                city = f" — {o.city}" if o.city else ""
                lines.append(f"  - {money(o.price)} | «{o.seller}»{city}")
        lines.append(""); time.sleep(1.2)
    send_telegram("\n".join(lines))

if __name__ == "__main__": main()
