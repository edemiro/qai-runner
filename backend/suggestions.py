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
    "menu": r"\b(menü|menu|kategori|category|navigation)\b",
    "contact": r"\b(iletişim|iletisim|contact|bize ulaşın|bize ulasin|destek|support)\b",
    "language": r"\b(dil|language|türkçe|turkce|english|tr\b|en\b)\b",
    "date": r"\b(tarih|date|gidiş|gidis|dönüş|donus|takvim|calendar|check.?in)\b",
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
        return [
            {
                "id": "back",
                "kind": "prompt",
                "label": "Geri dönüş",
                "text": "Bir alt ekrana git, geri tuşuna bas ve ana ekrana sorunsuz dönüldüğünü doğrula",
                "hint": None,
            },
            {
                "id": "reachable",
                "kind": "prompt",
                "label": "Ana menü erişimi",
                "text": "Menüyü aç, ilk iki bölüme sırayla gir ve her birinin açıldığını doğrula",
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
    """Every named element, control or not.

    Restricting this to clickable elements missed the most common case: a
    search box whose only clue is the `<label>` beside it or its placeholder,
    neither of which is itself interactive.
    """
    entries = []
    for element in snapshot.get_all_elements():
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
    cookie = _has(entries, "cookie")
    if cookie:
        add("cookie", "Çerez uyarısını kapat",
            f'"{cookie}" butonuna basıp çerez uyarısının kapandığını doğrula')

    search = _has(entries, "search")
    if search and textboxes:
        # There may be no search *button* — plenty of sites submit on Enter —
        # so the wording must not assume one.
        button = _has(entries, "search", role="button", interactive_only=True)
        press = f'"{button}" butonuna bas' if button else "Enter'a bas"
        add("search", "Arama akışı",
            f'Arama kutusuna "İstanbul" yaz, {press} ve sonuçların listelendiğini doğrula')
        add("search-empty", "Boş arama",
            f'Arama kutusunu boş bırakıp {press}, uygun bir uyarı çıktığını doğrula')

    login = _has(entries, "login")
    if login:
        add("login-bad", "Hatalı giriş",
            f'"{login}" ekranında geçersiz bir e-posta ve şifre dene, '
            f'hata mesajının göründüğünü doğrula')
        add("login-empty", "Boş form doğrulaması",
            f'"{login}" formunu boş gönder ve zorunlu alan uyarılarının çıktığını doğrula')

    cart = _has(entries, "cart")
    if cart:
        add("cart", "Sepete ekleme",
            f'"{cart}" ile bir ürünü sepete ekle ve sepet sayacının arttığını doğrula')

    date = _has(entries, "date")
    if date:
        add("date", "Tarih seçimi",
            f'"{date}" alanından bir tarih seç ve seçimin alana yansıdığını doğrula')

    filters = _has(entries, "filter")
    if filters:
        add("filter", "Filtreleme",
            f'"{filters}" ile sonuçları filtrele ve listenin değiştiğini doğrula')

    language = _has(entries, "language")
    if language:
        add("language", "Dil değiştirme",
            f'"{language}" ile dili değiştir ve sayfa metinlerinin çevrildiğini doğrula')

    contact = _has(entries, "contact")
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
    if _has(entries, "date"):
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
