"""The app in a partner's look: the same pages, scripts and styles Voyce serves, with the partner's colours,
name, logo, icon and sign-in, and no way to pay Voyce. voyce.co.in always gets the files exactly as they are.

Voyce's brand is a handful of navy shades (about 250 uses across the app); everything else is neutral grey
or LinkedIn blue, which stays. So a partner's look is a colour swap plus a few rules added to the page,
not a second copy of the app to keep in step.
"""
import json
import os
import re

import partners

FRONT = os.path.join(os.path.dirname(__file__), "..", "frontend")

# Voyce's navy → the partner's theme key
_NAVY = {"#24365e": "navy", "#1e305c": "navy", "#1c2b4d": "navy_deep", "#172440": "navy_deep",
         "#35507f": "blue", "#4a67a0": "blue"}
_NAVY_RGB = {(36, 54, 94): "navy", (23, 36, 64): "navy_deep"}
_HEX = re.compile("|".join(re.escape(h) for h in _NAVY), re.I)
_RGB = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")
_WORD = re.compile(r"\bVoyce\b")
# A seat counts drafts (the partner's word for what Voyce calls a generation), and has no Pro to upsell
_WORDS = [(re.compile(r"\bgenerations\b"), "drafts"), (re.compile(r"\bgeneration\b"), "draft"),
          (re.compile(r"\bGenerations\b"), "Drafts"), (re.compile(r" \(Pro\)"), ""),
          (re.compile(r"before scheduled or manual posting"), "before posting"),   # no scheduling on a seat
          (re.compile(r"Allow popups for voyce\.co\.in"), "Allow popups for this site")]
_CLERK_KEY = re.compile(r'data-clerk-publishable-key="[^"]*"')
_CLERK_SRC = re.compile(r'src="https://clerk\.voyce\.co\.in/')
_FAVICON = re.compile(r'<link rel="icon"[^>]*>')
_LOGO = re.compile(r'<span class="logo-mark">V</span>(?:<span>)?oyce(?:</span>)?')

_cache: dict = {}


def _rgb(hex_: str) -> tuple:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _colours(text: str, partner: dict) -> str:
    theme = partner["theme"]
    text = _HEX.sub(lambda m: theme[_NAVY[m.group(0).lower()]], text)

    def rgb(m):
        key = _NAVY_RGB.get((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        if not key:
            return m.group(0)
        r, g, b = _rgb(theme[key])
        return m.group(0).split("(")[0] + f"({r},{g},{b}"
    return _RGB.sub(rgb, text)


def _assets(partner: dict) -> str:
    return "/static/" + partner["assets"]


def client_config(partner: dict) -> dict:
    """What the page's scripts know about the partner (window.PARTNER)."""
    a = _assets(partner)
    return {"id": partner["id"], "brand": partner["brand"], "brand_full": partner["brand_full"],
            "company": partner["company_short"], "support_email": partner["support_email"],
            "invite_only": partner["invite_only"], "auto_posting": partner["auto_posting"],
            "scheduling": partner["scheduling"], "logo_on_dark": f"{a}/logo-on-dark.png",
            "logo_on_light": f"{a}/logo-on-light.png", "theme": partner["theme"]}


def _head(partner: dict) -> str:
    t = partner["theme"]
    return (f"<script>window.PARTNER = {json.dumps(client_config(partner))};</script>\n"
            '<script src="/static/partner.js?v=1"></script>\n'
            f"""<style id="partner-css">
  /* {partner['brand']}: the partner bills its clients, so no upgrade or payment anywhere */
  .side-upsell, #side-upsell, .plan-upgrade-btn, #plan-upgrade-btn, #acct-plan-btn, #upgrade-modal,
  [onclick*="openUpgradeModal"], .pricing-link {{ display: none !important; }}
  /* no publishing as someone else: each client connects their own LinkedIn */
  [onclick*="inviteLinkedIn"] {{ display: none !important; }}
  .partner-logo {{ height: 22px; width: auto; display: block; }}
  .side-foot::after {{ content: "Powered by Voyce"; display: block; font-size: 11px; color: rgba(255,255,255,.45);
    padding: 2px 4px 0; letter-spacing: .02em; }}
  :root {{ --partner-navy: {t['navy']}; --partner-accent: {t['orange']}; --partner-accent-hover: {t['orange_hover']}; }}
</style>
""")


def transform(text: str, kind: str, partner: dict, logo: str = "on-dark") -> str:
    """kind: "html", "js" or "css". logo: which wordmark a page's logo sits on ("on-dark", "on-light")."""
    text = _colours(text, partner)
    if kind == "html":
        a = _assets(partner)
        text = _LOGO.sub(f'<img class="partner-logo" src="{a}/logo-{logo}.png" alt="{partner["brand"]}">', text)
        pk, _ = partners.clerk_keys(partner)
        text = _CLERK_KEY.sub(f'data-clerk-publishable-key="{pk}"', text)
        domain = _clerk_frontend(pk)
        if domain:
            text = _CLERK_SRC.sub(f'src="https://{domain}/', text)
        text = _FAVICON.sub(f'<link rel="icon" type="image/png" href="{a}/icon-64.png" />'
                            f'<link rel="apple-touch-icon" href="{a}/icon-180.png" />', text, count=1)
    text = _WORD.sub(partner["brand"], text)
    for pattern, word in _WORDS:
        text = pattern.sub(word, text)
    if kind == "html":   # after the word swaps, so its "Powered by Voyce" stays as it is
        text = text.replace("</head>", _head(partner) + "</head>", 1)
    return text


def _clerk_frontend(publishable_key: str) -> str:
    import auth
    return auth._clerk_domain(publishable_key) if publishable_key else ""


# Which wordmark each page's logo sits on: the app's sidebar is navy; sign-in and onboarding are light
_LOGO_ON = {"index.html": "on-dark", "dashboard.html": "on-dark", "sales.html": "on-dark",
            "auth.html": "on-light", "onboarding.html": "on-light", "404.html": "on-light", "partner.html": "on-light"}


def file(path: str, partner: dict) -> str:
    """A frontend file (path relative to frontend/) in the partner's look, cached until it changes."""
    full = os.path.join(FRONT, path)
    mtime = os.path.getmtime(full)
    key = (partner["id"], path, mtime)
    if key not in _cache:
        raw = open(full, encoding="utf-8").read()
        kind = "html" if path.endswith(".html") else "css" if path.endswith(".css") else "js"
        _cache[key] = transform(raw, kind, partner, _LOGO_ON.get(os.path.basename(path), "on-dark"))
    return _cache[key]


def partner_file(name: str, partner: dict) -> str | None:
    """A file of the partner's own (landing, terms, privacy, panel), as written: it's already theirs."""
    full = os.path.join(FRONT, partner["assets"], name)
    return open(full, encoding="utf-8").read() if os.path.exists(full) else None
