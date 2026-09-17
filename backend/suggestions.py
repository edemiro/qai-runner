"""Test suggestions read off the page itself.

A blank prompt box is the worst part of an agent tool: the user knows the site
but not what the agent is good at, so they type something too vague and get a
bad first run. Generic examples do not help much either — "search for
headphones" means nothing on a banking page.

So these are derived from what is actually on the screen. A page with a search
box gets a search suggestion naming that box; a page with a login form gets the
wrong-password suggestion. No model call: the element tree already says what
kind of page this is, and a suggestion that takes two seconds and no tokens can
be regenerated on every navigation.
"""

import re
from typing import Any, Dict, List, Optional

# Matched against a control's visible label. Turkish first, since that is the
# language the suggestions are written in, but sites mix the two constantly.
PATTERNS = {
    "search": r"\b(ara|arama|search|bul|sorgula|query)\b",
    "login": r"\b(giriş|giris|oturum aç|oturum ac|log ?in|sign ?in|giriş yap|giris yap)\b",
    "signup": r"\b(kayıt|kayit|üye ol|uye ol|sign ?up|register|hesap oluştur|hesap olustur)\b",
    "cart": r"\b(sepet|cart|basket|sepete ekle|add to cart)\b",
    "filter": r"\b(filtre|filter|sırala|sirala|sort|gelişmiş|gelismis)\b",
    "cookie": r"\b(çerez|cerez|cookie|kabul et|accept|tümünü kabul|tumunu kabul|onayla)\b",
    # Anchored to the whole label, not a word inside it. "Digital menu" is an
    # inflight food menu, and matching it produced "open the menu with Digital
    # menu" — the same fault as the Check-in date step, one word further along.
    "menu": r"^(ana )?(menü|menu|kategoriler|categories|navigation)$",
    "contact": r"\b(iletişim|iletisim|contact|bize ulaşın|bize ulasin|destek|support)\b",
    "language": r"\b(dil|language|türkçe|turkce|english|tr\b|en\b)\b",
    # Check-in used to live in this pattern, which made a "Check-in" button on
    # an airline home screen look like a date field and produced "pick a date
    # from the Check-in field" — a step that cannot be carried out. Check-in is
    # a journey of its own, below.
    "date": r"\b(tarih|date|gidiş|gidis|dönüş|donus|takvim|calendar)\b",
    "checkin": r"\b(check.?in|çevrimiçi check|online check)\b",
    "booking": r"\b(uçuş ara|ucus ara|book a flight|bilet al|rezervasyon|flight search|uçuş bul|ucus bul)\b",
}

COMPILED = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in PATTERNS.items()}

# Offered on any screen, because they are the things people most often forget
# to test. The wording differs by target: a phone has no console, and telling a
# device tester to check one is noise they have to decode.
def _universal(kind: str):
    explore = {
        "id": "explore",
        "kind": "action",
        "label": "Keşif testi çalıştır",
        "text": (
            "Ekrandaki tüm kontrollere tek tek dokunup hangilerinin çalıştığını raporla"
            if kind == "mobile"
            else "Sayfadaki tüm kontrollere tek tek tıklayıp hangilerinin çalıştığını raporla"
        ),
        "hint": "Model kullanmaz, ücretsiz",
    }
    if kind == "mobile":
        # No exploratory crawl here: it drives a Playwright page, so on a device
        # it would be a button whose only outcome is an error message.
        #
        # The menu suggestion is not here either: it used to be offered on every
        # screen, including ones with no menu on them, which is the same fault
        # as naming a button that does not exist. It is added in for_snapshot
        # only when a menu control is actually on screen.
        return [
            {
                "id": "back",
                "kind": "prompt",
                "label": "Geri dönüş",
                "text": "Bir alt ekrana git, geri tuşuna bas ve ana ekrana sorunsuz dönüldüğünü doğrula",
                "hint": None,
            },
        ]
    return [explore, {
        "id": "console",
        "kind": "prompt",
        "label": "Konsol temiz mi?",
        "text": "Sayfayı gez ve konsolda ya da ağ isteklerinde hata olup olmadığını doğrula",
        "hint": None,
    }]


UNIVERSAL = _universal("web")


def _labels(snapshot) -> List[Dict[str, Any]]:
    """Every named element that is actually on the screen, control or not.

    Restricting this to clickable elements missed the most common case: a
    search box whose only clue is the `<label>` beside it or its placeholder,
    neither of which is itself interactive.

    Hidden elements are excluded, though. On a phone the tree still carries the
    screen behind an open sheet or modal, and suggesting a button that is under
    another screen is a step nobody can carry out. Web snapshots are filtered
    before they get here, so this costs them nothing.
    """
    entries = []
    for element in snapshot.get_all_elements():
        if not (getattr(element, "displayed", True) and getattr(element, "visible", True)):
            continue
        label = (element.text or element.name or element.resource_id or "").strip()
        if label:
            entries.append({
                "label": label,
                "role": element.role,
                "interactive": element.clickable,
            })
    return entries


def _has(
    entries: List[Dict[str, Any]], key: str,
    role: Optional[str] = None, interactive_only: bool = False,
) -> Optional[str]:
    """The first label matching `key`, preferring one that names a real control.

    A suggestion reads better when it quotes a button the user can see than
    when it quotes a stray heading, so interactive matches are returned first
    even though a non-interactive one still counts as evidence.
    """
    pattern = COMPILED[key]
    fallback = None
    for entry in entries:
        if role and entry["role"] != role:
            continue
        if not pattern.search(entry["label"]):
            continue
        if entry["interactive"]:
            return entry["label"]
        if fallback is None:
            fallback = entry["label"]
    return None if interactive_only else fallback


def _count_role(snapshot, role: str) -> int:
    return sum(1 for e in snapshot.get_all_elements() if e.role == role)


# A suggestion is only worth showing if the step it describes can actually be
# carried out on this screen, and that depends on what the matched control *is*
# as much as on what it is called. "Pick a date from X" needs X to be a field;
# "open X" needs X to be something that can be pressed. Matching on the label
# alone is what produced a date step aimed at a Check-in button.
FIELD_ROLES = ("textbox", "searchbox", "combobox", "spinbutton")
# Cells are in here because a list row is tappable even when the platform does
# not say so: on iOS an XCUIElementTypeCell arrives with clickable unset, so a
# menu of rows like "Book a flight" and "Check-in" looked like nothing could be
# pressed and the suggestion fell through to whatever stray button was left.
PRESSABLE_ROLES = (
    "button", "link", "menuitem", "tab", "checkbox", "radio", "switch",
    "cell", "listitem", "xcuielementtypecell",
)


def _field(entries: List[Dict[str, Any]], key: str) -> Optional[str]:
    """A label matching `key` that names something text can be entered into."""
    pattern = COMPILED[key]
    for entry in entries:
        if entry["role"] in FIELD_ROLES and pattern.search(entry["label"]):
            return entry["label"]
    return None


def _pressable(entries: List[Dict[str, Any]], key: str) -> Optional[str]:
    """A label matching `key` that names something that can be pressed.

    A field is never one of them, even though it is interactive: a search box
    called "Arama" would otherwise be offered as the button to press, and the
    suggestion would read "press the Arama button" about a text input. The
    looser `interactive` test still stands for everything else, because on a
    phone plenty of real controls come through with no role at all.
    """
    pattern = COMPILED[key]
    for entry in entries:
        if entry["role"] in FIELD_ROLES or not pattern.search(entry["label"]):
            continue
        if entry["role"] in PRESSABLE_ROLES or entry["interactive"]:
            return entry["label"]
    return None


def for_snapshot(snapshot, kind: str = "web") -> List[Dict[str, Any]]:
    """Suggestions for the screen this snapshot came from, best first.

    `kind` decides which of the free scans are offered: both read a page object
    through Playwright, so on a device they would be buttons that can only fail.
    """
    if snapshot is None:
        return list(_universal(kind))

    entries = _labels(snapshot)
    textboxes = _count_role(snapshot, "textbox")
    suggestions: List[Dict[str, Any]] = []

    def add(sid: str, label: str, text: str, hint: Optional[str] = None, kind: str = "prompt"):
        suggestions.append({"id": sid, "kind": kind, "label": label, "text": text, "hint": hint})

    # A cookie wall blocks everything behind it, so it belongs first.
    cookie = _pressable(entries, "cookie")
    if cookie:
        add("cookie", "Çerez uyarısını kapat",
            f'"{cookie}" butonuna basıp çerez uyarısının kapandığını doğrula')

    # A search box, or failing that a search button with some field to type
    # into. Without either there is nothing to type "İstanbul" into and the
    # suggestion would be describing a screen that is not here.
    search_field = _field(entries, "search")
    search_button = _pressable(entries, "search")
    if search_field or (search_button and textboxes):
        # There may be no search *button* — plenty of sites submit on Enter —
        # so the wording must not assume one.
        press = f'"{search_button}" butonuna bas' if search_button else "Enter'a bas"
        add("search", "Arama akışı",
            f'Arama kutusuna "İstanbul" yaz, {press} ve sonuçların listelendiğini doğrula')
        add("search-empty", "Boş arama",
            f'Arama kutusunu boş bırakıp {press}, uygun bir uyarı çıktığını doğrula')

    login = _pressable(entries, "login")
    if login:
        add("login-bad", "Hatalı giriş",
            f'"{login}" ekranında geçersiz bir e-posta ve şifre dene, '
            f'hata mesajının göründüğünü doğrula')
        add("login-empty", "Boş form doğrulaması",
            f'"{login}" formunu boş gönder ve zorunlu alan uyarılarının çıktığını doğrula')

    cart = _pressable(entries, "cart")
    if cart:
        add("cart", "Sepete ekleme",
            f'"{cart}" ile bir ürünü sepete ekle ve sepet sayacının arttığını doğrula')

    # Only when there is a field to pick a date in. A button called "Check-in"
    # is a journey, not a date picker.
    date = _field(entries, "date")
    if date:
        add("date", "Tarih seçimi",
            f'"{date}" alanından bir tarih seç ve seçimin alana yansıdığını doğrula')

    # The two journeys an airline screen is actually built around. Both are
    # worded as "open it and check the screen that opens", because that is all
    # this screen proves exists — what the next screen asks for is not visible
    # from here, and a suggestion that guesses it sends the agent looking for
    # fields that may not be there.
    booking = _pressable(entries, "booking")
    if booking:
        add("booking", "Uçuş arama akışı",
            f'"{booking}" ile uçuş arama akışını aç ve arama formunun '
            f'(kalkış, varış, tarih) geldiğini doğrula')

    checkin = _pressable(entries, "checkin")
    if checkin:
        add("checkin", "Check-in akışı",
            f'"{checkin}" akışını aç ve açılan ekranın check-in için hangi '
            f'bilgileri istediğini doğrula')

    menu = _pressable(entries, "menu")
    if menu:
        add("reachable", "Ana menü erişimi",
            f'"{menu}" ile menüyü aç, ilk iki bölüme sırayla gir ve her birinin '
            f'açıldığını doğrula')

    filters = _pressable(entries, "filter")
    if filters:
        add("filter", "Filtreleme",
            f'"{filters}" ile sonuçları filtrele ve listenin değiştiğini doğrula')

    language = _pressable(entries, "language")
    if language:
        add("language", "Dil değiştirme",
            f'"{language}" ile dili değiştir ve sayfa metinlerinin çevrildiğini doğrula')

    contact = _pressable(entries, "contact")
    if contact:
        add("contact", "İletişim formu",
            f'"{contact}" sayfasına git ve formu eksik doldurup doğrulama mesajlarını kontrol et')

    if not suggestions and entries:
        # Nothing recognisable, so name the actual controls rather than
        # inventing a scenario that does not fit this page. Only real controls
        # qualify: suggesting a click on a heading would be nonsense.
        names = [e["label"] for e in entries if e["interactive"] and e["role"] != "textbox"]
        if names:
            add("generic", "İlk kontrolü dene",
                f'"{names[0]}" ögesine tıkla ve sayfanın beklenen şekilde tepki verdiğini doğrula')
        if len(names) > 1:
            add("generic-2", "Gezinme",
                f'"{names[1]}" ögesine git ve açılan sayfanın doğru yüklendiğini doğrula')

    # The scans go last: they are always available, so they should not crowd
    # out the suggestions that are specific to this page.
    suggestions.extend(_universal(kind))
    if kind == "web":
        suggestions.append({
            "id": "links", "kind": "action", "label": "Kırık bağlantı taraması",
            "text": "Sayfadaki tüm bağlantıları isteyip 404 verenleri bul",
            "hint": "Model kullanmaz",
        })
        suggestions.append({
            "id": "a11y", "kind": "action", "label": "Erişilebilirlik taraması",
            "text": "Alt metni olmayan görseller, etiketsiz form alanları ve adsız kontroller",
            "hint": "Model kullanmaz",
        })

    return suggestions


def describe_page(snapshot) -> Dict[str, Any]:
    """A one-line read of what kind of page this is, shown above the suggestions."""
    if snapshot is None:
        return {"title": None, "kind": "bilinmiyor", "controls": 0}

    entries = _labels(snapshot)
    kinds = []
    if _has(entries, "login"):
        kinds.append("giriş")
    if _has(entries, "cart"):
        kinds.append("e-ticaret")
    if _has(entries, "search"):
        kinds.append("arama")
    if _field(entries, "date") or _pressable(entries, "booking") or _pressable(entries, "checkin"):
        kinds.append("rezervasyon")

    # A page snapshot carries a title and a URL; a device snapshot has neither,
    # and asking for them raised rather than degrading. The summary is a nicety,
    # so the missing halves are simply absent.
    return {
        "title": getattr(snapshot, "title", None),
        "url": getattr(snapshot, "url", None),
        "kind": " / ".join(kinds) if kinds else "genel",
        "controls": sum(
            1 for e in snapshot.get_all_elements() if getattr(e, "clickable", False)
        ),
        "textboxes": _count_role(snapshot, "textbox"),
        "links": _count_role(snapshot, "link"),
    }
