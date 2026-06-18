"""
Plaza (plaza.newnewnew.space) Eindhoven oda takip botu - v2

Ne yapar:
  1. Plaza'nin ilan API'sinden tum ilanlari ceker (once POST, olmazsa GET; 3 deneme)
  2. Eindhoven'dakileri filtreler (istege bagli kira ust limiti ile)
  3. Daha once gorulmemis (yeni) ilan varsa Telegram'dan mesaj atar
  4. Gordugu ilanlari seen.json dosyasina kaydeder

Gerekli ortam degiskenleri (GitHub Secrets):
  TELEGRAM_BOT_TOKEN  -> BotFather'dan aldigin token
  TELEGRAM_CHAT_ID    -> Senin Telegram chat ID'n
"""

import hashlib
import json
import os
import sys
import time

import requests

PORTAL = "https://plaza.newnewnew.space"
API_URL = f"{PORTAL}/portal/object/frontend/getallobjects/format/json"
LISTING_PAGE = f"{PORTAL}/en/availables-places/living-place"
CITY = "eindhoven"
SEEN_FILE = "seen.json"
MAX_SINGLE_MESSAGES = 15  # bundan fazla yeni ilan varsa tek ozet mesaj at

# Istege bagli kira ust limiti (euro). Ornek: MAX_RENT = 900
# None birakirsan fiyat filtresi uygulanmaz. Kirasi bilinmeyen ilanlar
# her durumda gosterilir (veri eksik diye oda kacirmak istemeyiz).
MAX_RENT = None

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def telegram(text: str) -> None:
    """Telegram'a mesaj gonderir."""
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=30,
    )
    r.raise_for_status()


def load_state() -> dict:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"ids": [], "error_notified": False, "first_run": True}


def save_state(state: dict) -> None:
    state["first_run"] = False
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def parse_objects(data) -> list:
    if isinstance(data, dict):
        objects = data.get("result") or data.get("objects") or []
    elif isinstance(data, list):
        objects = data
    else:
        objects = None
    if not isinstance(objects, list):
        raise ValueError(f"Beklenmeyen API cevabi: {str(data)[:300]}")
    return objects


def fetch_objects() -> list:
    """Plaza API'sinden ilanlari ceker. Once POST dener, olmazsa GET.
    Gecici hatalara karsi toplam 3 tur dener."""
    last_err = None
    for attempt in range(3):
        for method in (requests.post, requests.get):
            try:
                r = method(API_URL, headers=HEADERS, timeout=30)
                r.raise_for_status()
                return parse_objects(r.json())
            except Exception as e:
                last_err = e
        time.sleep(5)  # sonraki turdan once bekle
    raise RuntimeError(f"API 3 denemede de basarisiz: {last_err}")


def is_eindhoven(obj: dict) -> bool:
    # Esitlik yerine bilerek "icinde geciyor mu" kontrolu yapiyoruz:
    # "Eindhoven Centrum" gibi varyasyonlari kacirmamak icin.
    # Oda kacirmak, nadiren fazladan 1 mesaj almaktan daha kotu.
    city = obj.get("city")
    if isinstance(city, dict):
        return CITY in str(city.get("name", "")).lower()
    if city:
        return CITY in str(city).lower()
    # city alani hic yoksa son care: tum objede sehir adini ara
    return CITY in json.dumps(obj, ensure_ascii=False).lower()


def obj_id(obj: dict) -> str:
    for key in ("id", "urlKey", "objectID", "dwellingID"):
        if obj.get(key):
            return str(obj[key])
    # hash() yerine hashlib: Python'in hash()'i her calistirmada degisir,
    # hashlib ise her zaman ayni sonucu verir (tekrar bildirim hatasini onler)
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def first(obj: dict, *keys):
    """Verilen anahtarlardan dolu olan ilk degeri dondurur."""
    for k in keys:
        v = obj.get(k)
        if v not in (None, "", 0, "0"):
            return v
    return None


def rent_of(obj: dict):
    rent = first(obj, "totalRent", "grossRent", "netRent", "basicRent", "rent")
    try:
        return float(str(rent).replace(",", "."))
    except (TypeError, ValueError):
        return None


def passes_rent_filter(obj: dict) -> bool:
    if MAX_RENT is None:
        return True
    rent = rent_of(obj)
    return rent is None or rent <= MAX_RENT


def describe(obj: dict) -> str:
    """Bir ilan icin Telegram mesaj metni olusturur."""
    street = first(obj, "street") or ""
    nr = first(obj, "houseNumber") or ""
    nr_add = first(obj, "houseNumberAddition") or ""
    address = " ".join(str(x) for x in (street, nr, nr_add) if x).strip()

    rent = rent_of(obj)
    area = first(obj, "areaDwelling", "area", "usableArea")
    url_key = first(obj, "urlKey")
    link = f"{LISTING_PAGE}/details/{url_key}" if url_key else LISTING_PAGE

    lines = ["\U0001F3E0 <b>Yeni Plaza ilani - Eindhoven!</b>"]
    if address:
        lines.append(f"\U0001F4CD {address}")
    if rent:
        lines.append(f"\U0001F4B6 €{rent:.0f} / ay")
    if area:
        lines.append(f"\U0001F4D0 {area} m²")
    lines.append(f'\U0001F517 <a href="{link}">Ilani ac ve hemen tepki ver</a>')
    return "\n".join(lines)


def main() -> None:
    state = load_state()
    try:
        objects = fetch_objects()
    except Exception as e:  # site/API hatasi
        print(f"HATA: {e}", file=sys.stderr)
        if not state.get("error_notified"):
            telegram(
                "\u26A0\uFE0F Plaza botu siteye ulasamadi veya API degisti. "
                f"Hata: {str(e)[:200]}\nSorun devam ederse kodu guncellemek gerekebilir."
            )
            state["error_notified"] = True
            save_state(state)
        return

    state["error_notified"] = False
    eindhoven = [
        o for o in objects
        if isinstance(o, dict) and is_eindhoven(o) and passes_rent_filter(o)
    ]
    current_ids = [obj_id(o) for o in eindhoven]
    known = set(state.get("ids", []))
    new_objects = [o for o in eindhoven if obj_id(o) not in known]

    print(f"Toplam ilan: {len(objects)} | Eindhoven: {len(eindhoven)} | Yeni: {len(new_objects)}")

    if state.get("first_run", False) or not os.path.exists(SEEN_FILE):
        telegram(
            "\u2705 <b>Plaza botu kuruldu! (v2)</b>\n"
            f"Su an Eindhoven'da takip edilen ilan sayisi: {len(eindhoven)}.\n"
            "Bundan sonra yeni ilan dustugunde sana buradan mesaj atacagim."
        )
    elif new_objects:
        if len(new_objects) > MAX_SINGLE_MESSAGES:
            telegram(
                f"\U0001F3E0 Eindhoven'da birden {len(new_objects)} yeni Plaza ilani goruldu!\n"
                f'\U0001F517 <a href="{LISTING_PAGE}">Tum ilanlara bak</a>'
            )
        else:
            for o in new_objects:
                telegram(describe(o))
                time.sleep(1)  # Telegram rate limitine takilmamak icin kisa bekleme

    # Sadece su an aktif olan ilanlari hatirla (kalkan ilan tekrar gelirse yine haber verir)
    state["ids"] = current_ids
    save_state(state)


if __name__ == "__main__":
    main()
