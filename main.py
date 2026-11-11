import os
import re
import json
import time
import hashlib
import logging
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
TZ = ZoneInfo("Europe/Moscow")
GROUP_ID = os.environ.get("VK_GROUP_ID", "242840147").strip()
TOKEN = os.environ.get("VK_ACCESS_TOKEN", "").strip()
VK_API_VERSION = "5.199"
HISTORY_FILE = "post_history.json"
SLOT_MINUTES = 72
POSTS_PER_DAY = 20
MAX_AGE_HOURS = 72

FEEDS = [
    ("Motor", "https://motor.ru/exports/rss"),
    ("За рулём", "https://www.zr.ru/rss-news/"),
]

TAG_POOL = [
    "Автоновости", "МирАвто", "АвтоОбзор", "АвтоРынок", "НовостиАвто",
    "Автопром", "АвтоТехнологии", "НовыеАвто", "АвтоИндустрия", "АвтоРоссия",
    "АвтоМир", "Машины", "АвтоНовостиДня", "АвтоТренды", "АвтоБудущее",
    "Электрокары", "Электромобили", "Гибриды", "Кроссоверы", "Внедорожники",
    "Седаны", "Спорткары", "Автодизайн", "Автоспорт", "Автозакон",
    "АвтоБезопасность", "АвтоПроизводство", "АвтоПродажи", "АвтоЦены",
    "ТестДрайв", "АвтоНовинки", "РоссийскийАвтопром", "КитайскиеАвто",
    "ЕвропейскиеАвто", "ЯпонскиеАвто", "НемецкиеАвто", "АмериканскиеАвто",
    "Lada", "Toyota", "BMW", "MercedesBenz", "Volkswagen", "Hyundai",
    "Kia", "Geely", "Chery", "BYD", "Tesla", "Porsche", "Volvo",
    "АвтоГаджеты", "АвтоЭлектроника", "АвтоСервис", "АвтоРемонт", "АвтоТюнинг",
    "Автокомпоненты", "АвтоЭкология", "Транспорт", "Мобильность", "Дороги",
    "Грузовики", "Пикапы", "Микроавтобусы", "Мототехника", "МотоНовости",
    "Автокультура", "КлассическиеАвто", "РетроАвто", "КонцептКары", "Автопремьеры",
    "АвтоИспытания", "КрашТест", "БезопасностьДвижения", "Топливо", "Бензин",
    "Дизель", "АвтоЭкономика", "АвтоБизнес", "Дилеры", "Автодилеры",
    "ИмпортАвто", "ЭкспортАвто", "АвтоЛогистика", "АвтоСпрос", "АвтоСтатистика",
    "АвтоНовостиМира", "АвтоНовостиРоссии", "АвтоНовостиКитая", "АвтоНовостиЕвропы",
    "АвтоНовостиСША", "АвтоНовостиЯпонии", "АвтоНовостиКореи", "АвтоПромНовости",
    "АвтоИнновации", "АвтоРазработка", "АвтоПлатформы", "Двигатели", "АвтоМоторы",
    "Трансмиссия", "Аккумуляторы", "ЗарядныеСтанции", "АвтономноеВождение",
    "УмныйАвтомобиль", "АвтоСофт", "АвтоСвязь", "АвтоРынокРоссии", "МировойАвторынок",
    "АвтоВладельцы", "АвтоПокупка", "АвтоПродажа", "АвтоЛизинг", "АвтоКредит",
    "АвтоСтрахование", "ОСАГО", "КАСКО", "АвтоНалоги", "Техосмотр",
    "ЗимняяРезина", "ЛетняяРезина", "Шины", "АвтоЗапчасти", "АвтоАксессуары",
    "АвтоПутешествия", "Автотуризм", "АвтоМаршруты", "Парковка", "АвтоГород",
]

def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("used_links", [])
            data.setdefault("used_tags", [])
            data.setdefault("last_slot", "")
            data.setdefault("posts", [])
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"used_links": [], "used_tags": [], "last_slot": "", "posts": []}

def save_history(data):
    # Keep the history compact but long enough to prevent repeats.
    data["used_links"] = data["used_links"][-1500:]
    data["used_tags"] = data["used_tags"][-300:]
    data["posts"] = data["posts"][-300:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def parse_date(value):
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

def fetch_feed(name, url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AutoNewsBot/1.0)"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
    root = ET.fromstring(raw)
    entries = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = item.findtext("pubDate") or item.findtext("date") or ""
        if title and link:
            entries.append({"source": name, "title": title, "link": link, "published": parse_date(published)})
    # Atom feeds
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for item in root.findall(".//a:entry", ns):
        title = (item.findtext("a:title", default="", namespaces=ns) or "").strip()
        link_el = item.find("a:link", ns)
        link = link_el.attrib.get("href", "") if link_el is not None else ""
        published = item.findtext("a:published", default="", namespaces=ns) or item.findtext("a:updated", default="", namespaces=ns)
        if title and link:
            entries.append({"source": name, "title": title, "link": link, "published": parse_date(published)})
    return entries

def get_news(history):
    used = set(history["used_links"])
    now = datetime.now(timezone.utc)
    found = []
    for name, url in FEEDS:
        try:
            items = fetch_feed(name, url)
            logging.info("%s: загружено новостей: %d", name, len(items))
            for item in items:
                age = now - item["published"]
                if item["link"] not in used and timedelta(0) <= age <= timedelta(hours=MAX_AGE_HOURS):
                    found.append(item)
        except Exception as exc:
            logging.warning("Не удалось загрузить ленту %s: %s", name, exc)
    found.sort(key=lambda x: x["published"], reverse=True)
    return found

def make_sentence(title):
    text = re.sub(r"\s+", " ", title).strip().strip('«»" ')
    text = re.sub(r"\s+[—–-]\s+", " — ", text)
    if not text:
        return ""
    # A headline is kept factual; only punctuation is normalized.
    text = text.rstrip(" .!?…")
    return text + "."

def make_tags(item, slot_key, history):
    title = item["title"].lower()
    specific = []
    brands = [
        "lada", "toyota", "bmw", "mercedes", "volkswagen", "hyundai", "kia",
        "geely", "chery", "byd", "tesla", "porsche", "volvo", "автоваз",
        "уаз", "газ", "москвич", "haval", "jetour", "changan", "exeed"
    ]
    for brand in brands:
        if brand in title and brand.title() not in specific:
            specific.append(brand.title().replace(" ", ""))
    topic_map = [
        (("электро", "электрокар", "электромоб"), "Электромобили"),
        (("гибрид",), "Гибриды"),
        (("авар", "дтп", "столкнов"), "ДорожныеНовости"),
        (("штраф", "закон", "госдум", "пдд", "прав"), "Автозакон"),
        (("цена", "подорож", "дешев", "стоимост"), "ЦеныНаАвто"),
        (("завод", "производств", "сборк"), "Автопроизводство"),
        (("ралли", "гонк", "формул", "автоспорт"), "Автоспорт"),
        (("кроссовер",), "Кроссоверы"),
        (("внедорож",), "Внедорожники"),
    ]
    for words, tag in topic_map:
        if any(word in title for word in words):
            specific.append(tag)
            break
    seed = int(hashlib.sha256((item["link"] + slot_key).encode()).hexdigest(), 16)
    pool = [x for x in TAG_POOL if x not in specific]
    start = seed % len(pool)
    ordered = pool[start:] + pool[:start]
    candidates = specific[:2] + ordered
    chosen = []
    used_recent = set(history["used_tags"][-60:])
    for tag in candidates:
        if tag not in chosen and tag not in used_recent:
            chosen.append(tag)
        if len(chosen) == 3:
            break
    if len(chosen) < 3:
        for tag in candidates:
            if tag not in chosen:
                chosen.append(tag)
            if len(chosen) == 3:
                break
    return ["#" + re.sub(r"[^0-9A-Za-zА-Яа-я_]", "", tag) for tag in chosen]

def due_slot(now, history):
    local = now.astimezone(TZ)
    index_now = min(POSTS_PER_DAY - 1, (local.hour * 60 + local.minute) // SLOT_MINUTES)
    today = local.strftime("%Y-%m-%d")
    last = history.get("last_slot", "")
    if not last.startswith(today + "-"):
        index = index_now
    else:
        try:
            last_index = int(last.rsplit("-", 1)[1])
        except Exception:
            last_index = -1
        index = last_index + 1
    if index > index_now or index >= POSTS_PER_DAY:
        return None
    slot_key = f"{today}-{index}"
    return slot_key

def vk_post(message, random_id):
    params = {
        "owner_id": "-" + GROUP_ID,
        "from_group": 1,
        "message": message,
        "random_id": random_id,
        "access_token": TOKEN,
        "v": VK_API_VERSION,
    }
    body = urllib.parse.urlencode(params).encode()
    request = urllib.request.Request("https://api.vk.com/method/wall.post", data=body)
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if "error" in result:
        raise RuntimeError(f"VK API error: {result['error']}")
    return result["response"]["post_id"]

def main():
    if not TOKEN:
        raise RuntimeError("Не задан секрет VK_ACCESS_TOKEN")
    if not GROUP_ID.isdigit():
        raise RuntimeError("VK_GROUP_ID должен содержать числовой ID сообщества")
    history = load_history()
    now = datetime.now(timezone.utc)
    slot_key = due_slot(now, history)
    if slot_key is None:
        logging.info("Сейчас публикация не запланирована.")
        return
    news = get_news(history)
    if not news:
        logging.warning("Нет свежей уникальной новости за последние %s часов — ничего не публикуем.", MAX_AGE_HOURS)
        return
    item = news[0]
    sentence = make_sentence(item["title"])
    tags = make_tags(item, slot_key, history)
    message = sentence + "\n\n" + " ".join(tags)
    random_id = int(hashlib.sha256(slot_key.encode()).hexdigest()[:8], 16)
    post_id = vk_post(message, random_id)
    history["used_links"].append(item["link"])
    history["used_tags"].extend(tag.lstrip("#") for tag in tags)
    history["last_slot"] = slot_key
    history["posts"].append({
        "slot": slot_key, "post_id": post_id, "source": item["source"],
        "title": item["title"], "link": item["link"], "published_at": now.isoformat()
    })
    save_history(history)
    logging.info("УСПЕХ: опубликовано из %s, post_id=%s, слот=%s", item["source"], post_id, slot_key)
    logging.info("%s", message)

if __name__ == "__main__":
    main()
