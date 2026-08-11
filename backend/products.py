"""products.py — the company → products layer.

A company document may carry a products[] array. Each product is a *subject*
(its own niche, website brief, brand color, theme) while the company stays the
*identity* (voice, tone, schedule, LinkedIn connection, approval mode).

Backward compatibility is the load-bearing rule: a company with no products[]
behaves exactly as before — the company itself is the implicit product, and
product_view(company, None) returns the company unchanged. Nothing downstream
needs to know whether products exist.
"""

import hashlib
import re
import uuid
from datetime import date, datetime

import db

# Fields a product overlays on the merged view handed to generation/search/
# rendering. Voice and identity fields (tone, designation, linkedin_analysis,
# linkedin_top_posts, post_time, approval_mode) deliberately stay company-level.
_SUBJECT_FIELDS = (
    "industry", "website_url", "website_content", "analysis",
    "brand_color", "theme_spec", "carousel_theme", "topics", "search_angles",
)

# Products allowed per company by plan (companies themselves stay plan-capped
# as before; products scale the subjects underneath one identity).
MAX_PRODUCTS = {"free": 1, "pro": 5, "admin": 20}


def list_products(company: dict) -> list[dict]:
    return [p for p in (company.get("products") or []) if isinstance(p, dict)]


def enabled_products(company: dict) -> list[dict]:
    return [p for p in list_products(company) if p.get("enabled", True)]


def get_product(company: dict, product_id: str) -> dict | None:
    for p in list_products(company):
        if p.get("id") == product_id:
            return p
    return None


def product_view(company: dict, product: dict | None) -> dict:
    """The merged dict that generation consumes: company identity + product
    subject. With no product it's the company itself (implicit product)."""
    if not product:
        return {**company, "product_id": "", "product_name": ""}
    view = dict(company)
    for f in _SUBJECT_FIELDS:
        v = product.get(f)
        if v not in (None, "", [], {}):
            view[f] = v
    view["product_id"] = product.get("id", "")
    view["product_name"] = product.get("name", "")
    return view


def pick_product(company: dict, runs_today: int = 0) -> dict | None:
    """Deterministic weighted pick, same idea as the post-type rotation hash:
    stable for (company, date, run-number) so the week plan can forecast it,
    different across repeated Run Now clicks. Returns None when the company
    has no enabled products (implicit-product mode)."""
    prods = enabled_products(company)
    if not prods:
        return None
    if len(prods) == 1:
        return prods[0]
    today = date.today().isoformat()
    seed = hashlib.md5(
        f"{company.get('id', '')}:{today}:{runs_today}:product".encode()
    ).hexdigest()
    weights = [max(1, int(p.get("weight", 1) or 1)) for p in prods]
    tick = int(seed[:8], 16) % sum(weights)
    for p, w in zip(prods, weights):
        tick -= w
        if tick < 0:
            return p
    return prods[-1]


# ── CRUD (read-modify-write on the parent company doc, matching company.py) ──

def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or uuid.uuid4().hex[:8]


def _scrape_subject(url: str, name: str) -> dict:
    """Product-page scrape via the existing company scraper. Fail-open: a dead
    page still yields a usable (manual-fields-only) product."""
    if not url:
        return {}
    try:
        from scraper import scrape_company
        sr = scrape_company(url, name)
        return {
            "website_content": "\n\n".join(sr.get("raw_pages", {}).values())[:4000],
            "analysis":        sr.get("analysis", {}),
            "brand_color":     sr.get("brand_color", ""),
        }
    except Exception:
        return {}


def _generate_theme(product: dict, company: dict) -> dict:
    """Claude theme spec — fail-open to {} (renderer falls back to the
    brand-color/industry palette chain)."""
    try:
        from design import generate_theme_spec
        return generate_theme_spec(product, company) or {}
    except Exception:
        return {}


def _save_products(company_id: str, products: list[dict]):
    db.companies.update_one({"id": company_id}, {"$set": {"products": products}})


def add_product(company: dict, data: dict) -> dict:
    """Create a product under a company. `data`: name (required), url,
    industry, topics[], search_angles[], weight."""
    products = list_products(company)
    pid = _slugify(data["name"])
    if any(p.get("id") == pid for p in products):
        pid = f"{pid}-{uuid.uuid4().hex[:4]}"

    url = (data.get("url") or "").strip()
    product = {
        "id":            pid,
        "name":          data["name"].strip(),
        "url":           url,
        "website_url":   url,   # subject-field name the merged view/scraper uses
        "industry":      (data.get("industry") or "").strip(),
        "topics":        [t for t in (data.get("topics") or []) if t],
        "search_angles": [a for a in (data.get("search_angles") or []) if a],
        "weight":        max(1, int(data.get("weight", 1) or 1)),
        "enabled":       True,
        "theme_spec":    {},
        "created_at":    datetime.now().isoformat(),
    }
    product.update(_scrape_subject(url, product["name"]))
    product["theme_spec"] = _generate_theme(product, company)

    products.append(product)
    _save_products(company["id"], products)
    return product


def update_product(company: dict, product_id: str, data: dict) -> dict | None:
    products = list_products(company)
    for i, p in enumerate(products):
        if p.get("id") != product_id:
            continue
        for f in ("name", "industry", "topics", "search_angles"):
            if f in data and data[f] is not None:
                p[f] = data[f]
        if "weight" in data and data["weight"]:
            p["weight"] = max(1, int(data["weight"]))
        new_url = (data.get("url") or "").strip() if "url" in data else None
        if new_url is not None and new_url != p.get("url", ""):
            p["url"] = p["website_url"] = new_url
            if new_url:
                p.update(_scrape_subject(new_url, p.get("name", "")))
                p["theme_spec"] = _generate_theme(p, company)
            else:
                p["website_content"] = ""
                p["analysis"] = {}
        products[i] = p
        _save_products(company["id"], products)
        return p
    return None


def delete_product(company: dict, product_id: str) -> bool:
    products = list_products(company)
    remaining = [p for p in products if p.get("id") != product_id]
    if len(remaining) == len(products):
        return False
    _save_products(company["id"], remaining)
    return True


def toggle_product(company: dict, product_id: str, enabled: bool) -> bool:
    products = list_products(company)
    for p in products:
        if p.get("id") == product_id:
            p["enabled"] = bool(enabled)
            _save_products(company["id"], products)
            return True
    return False


def regenerate_theme(company: dict, product_id: str) -> dict | None:
    products = list_products(company)
    for p in products:
        if p.get("id") == product_id:
            p["theme_spec"] = _generate_theme(p, company)
            _save_products(company["id"], products)
            return p["theme_spec"]
    return None
