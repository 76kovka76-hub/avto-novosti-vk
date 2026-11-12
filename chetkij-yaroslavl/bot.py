"""VK bot: Чёткий Ярославль. One verified local story per 48-minute slot."""
import difflib
import hashlib
import html
import json
import logging
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")
GROUP_ID = "243223029"
HISTORY_FILE = Path(__file__).with_name("history.json")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "yes", "true")
MODEL = os.getenv("YAROSLAVL_OPENAI_MODEL", "gpt-4o-mini")
FEEDS = [
    ("Городской телеканал", "https://gtk.tv/rss.xml"),
    ("Google Новости", "https://news.google.com/rss/search?q=" + urllib.parse.quote('"Ярославль" when:2d') + "&hl=ru&gl=RU&ceid=RU:ru"),
    ("Google Новости — город", "https://news.google.com/rss/search?q=" + urllib.parse.quote('"в Ярославле" when:2d') + "&hl=ru&gl=RU&ceid=RU:ru"),
]
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def slot(now):
    local = now.astimezone(TZ)
    return f"{local.date()}-{(local.hour * 60 + local.minute) // 48:02d}"


def history_load():
    if not HISTORY_FILE.exists():
        return {"posts": [], "used_links": [], "used_tags": []}
    with HISTORY_FILE.open(encoding="utf-8") as f:
        data = json.load(f)
    return {key: data.get(key, []) for key in ("posts", "used_links", "used_tags")}


def history_save(data):
    # Never truncate: full history prevents repeats across days.
    HISTORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", s or ""))).strip()


def url_key(url):
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), "", ""))


def title_key(s):
    # Remove news provider suffix in syndicated headlines.
    s = re.sub(r"\s+-\s+[^-]{3,50}$", "", s or "").casefold()
    return " ".join(re.findall(r"[а-яёa-z0-9]+", s))


def title_duplicate(a, b):
    a, b = title_key(a), title_key(b)
    if not a or not b:
        return False
    if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.83:
        return True
    x, y = set(a.split()), set(b.split())
    return len(x & y) / max(1, len(x | y)) >= 0.72 and min(len(x), len(y)) >= 4


def published_at(s):
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def read_feed(source, url):
    req = urllib.request.Request(url, headers={"User-Agent": "ChetkiyYaroslavl/1.0 (RSS reader)"})
    with urllib.request.urlopen(req, timeout=18) as response:
        root = ET.fromstring(response.read(2_000_000))
    result = []
    for e in root.findall(".//item"):
        title, link = clean_text(e.findtext("title")), clean_text(e.findtext("link"))
        stamp = published_at(e.findtext("pubDate") or e.findtext("date"))
        if title and link and stamp:
            result.append({"source": source, "title": title, "link": link,
                           "description": clean_text(e.findtext("description"))[:400], "published": stamp})
    atom = {"a": "http://www.w3.org/2005/Atom"}
    for e in root.findall(".//a:entry", atom):
        el = e.find("a:link", atom)
        link = el.attrib.get("href", "") if el is not None else ""
        title = clean_text(e.findtext("a:title", "", atom))
        stamp = published_at(e.findtext("a:published", "", atom) or e.findtext("a:updated", "", atom))
        if title and link and stamp:
            result.append({"source": source, "title": title, "link": link,
                           "description": "", "published": stamp})
    return result


def candidates(history, now):
    used = set(history["used_links"])
    titles = [p.get("title", "") for p in history["posts"]]
    found = []
    for name, feed in FEEDS:
        try:
            items = read_feed(name, feed)
            logging.info("RSS %s: %d материалов", name, len(items))
            for item in items:
                if not timedelta() <= now - item["published"] <= timedelta(hours=36):
                    continue
                if not re.search(r"ярославл|брагино", (item["title"] + " " + item["description"]).casefold()):
                    continue
                if url_key(item["link"]) in used:
                    continue
                if any(title_duplicate(item["title"], t) for t in titles):
                    continue
                if any(title_duplicate(item["title"], other["title"]) for other in found):
                    continue
                found.append(item)
        except Exception as ex:
            logging.warning("Недоступный RSS %s: %s", name, ex)
    return sorted(found, key=lambda x: x["published"], reverse=True)


def ai_draft(item, history):
    key = os.getenv("YAROSLAVL_OPENAI_KEY", "").strip()
    if not key:
        raise RuntimeError("Нужен Secret YAROSLAVL_OPENAI_KEY")
    system = (
        "Ты автор паблика «Чёткий Ярославль». Ответь ТОЛЬКО валидным JSON-объектом: "
        '{"publish":true/false,"sentence":"...","hashtags":["#...","#...","#..."]}. '
        "Прочитай заголовок и краткое описание. Пропускай публикацию, если сведения не относятся "
        "именно к городу Ярославлю или событие уже было опубликовано (в том числе пересказанное другим СМИ). "
        "Если недостаточно фактов, пропусти. Никаких вымышленных подробностей, дат, имён или цифр. "
        "Если публикуешь — строго одно короткое русское предложение до 32 слов, начинай с уместного "
        "эмодзи, обращайся по-дружески: «Братва», «Пацаны», «Народ», «Родные», меняй обращения. "
        "Лёгкий юмор допустим, но не над трагедиями, жертвами или преступлениями. "
        "Затем придумай ровно 3 тематических НОВЫХ хэштега, каждый с основой «Ярослав»; "
        "не повторяй ни один из ранее использованных. Не переписывай заголовок дословно. "
        "Если решение пропустить, верни publish=false, sentence='', hashtags=[]."
    )
    user = {"news": {k: item[k] for k in ("source", "title", "description", "link")},
            "previous_news_titles": [p.get("title", "") for p in history["posts"][-220:]],
            "previous_hashtags": history["used_tags"][-500:]}
    payload = {"model": MODEL, "response_format": {"type": "json_object"}, "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]}
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=55) as resp:
        answer = json.loads(resp.read().decode("utf-8"))
    return json.loads(answer["choices"][0]["message"]["content"])


def format_message(draft, history):
    if not draft.get("publish"):
        return None
    sentence = re.sub(r"\s+", " ", str(draft.get("sentence", ""))).strip()
    tags = draft.get("hashtags")
    if not sentence or not isinstance(tags, list) or len(tags) != 3:
        return None
    if len(sentence.split()) > 35 or len(sentence) > 290 or "#" in sentence:
        return None
    if len(re.findall(r"[.!?…](?:\s|$)", sentence)) > 1:
        return None
    if any(not isinstance(t, str) or not re.fullmatch(r"#[А-Яа-яЁёA-Za-z0-9_]+", t)
           or "ярослав" not in t.casefold() for t in tags):
        return None
    lowered = [x.casefold() for x in tags]
    if len(set(lowered)) != 3 or set(lowered) & {x.casefold() for x in history["used_tags"]}:
        return None
    return sentence + "\n\n" + " ".join(tags)


def post_vk(message, time_slot):
    token = os.getenv("YAROSLAVL_VK_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Нужен Secret YAROSLAVL_VK_TOKEN")
    random_id = int(hashlib.sha256((GROUP_ID + time_slot).encode()).hexdigest()[:8], 16)
    body = urllib.parse.urlencode({
        "owner_id": "-" + GROUP_ID, "from_group": "1", "message": message,
        "random_id": random_id, "access_token": token, "v": "5.199"}).encode()
    req = urllib.request.Request("https://api.vk.com/method/wall.post", data=body)
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode())
    if "error" in data:
        raise RuntimeError("VK API: " + str(data["error"].get("error_msg", "unknown")))
    return data["response"]["post_id"]


def main():
    now = datetime.now(timezone.utc)
    time_slot = slot(now)
    h = history_load()
    if any(p.get("slot") == time_slot for p in h["posts"]):
        logging.info("Слот %s уже опубликован", time_slot)
        return
    if sum(str(p.get("slot", "")).startswith(time_slot[:10]) for p in h["posts"]) >= 30:
        logging.info("Достигнут лимит 30 за сутки")
        return
    feed_items = candidates(h, now)
    for item in feed_items[:8]:
        draft = ai_draft(item, h)
        message = format_message(draft, h)
        if not message:
            logging.info("Не прошёл проверку: %s", item["title"])
            continue
        if DRY_RUN:
            logging.info("ТЕСТ — не публикуем:\n%s", message)
            return
        post_id = post_vk(message, time_slot)
        h["posts"].append({"slot": time_slot, "post_id": post_id, "source": item["source"],
                           "title": item["title"], "link": item["link"],
                           "text": message.split("\n\n")[0], "published_at": now.isoformat()})
        h["used_links"].append(url_key(item["link"]))
        h["used_tags"].extend(message.split("\n\n")[1].split())
        history_save(h)
        logging.info("Опубликовано: %s", post_id)
        return
    logging.info("Нет новых проверенных новостей — ничего не публикуем.")


if __name__ == "__main__":
    main()
