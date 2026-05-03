import re
from datetime import datetime
from scraper import scrape_company

import db

_P = {"_id": 0}


def save_company(profile: dict) -> dict:
    website_url = profile.get("website_url", "").strip()

    if website_url:
        company_id = website_url.replace("https://", "").replace("http://", "").split("/")[0]
    else:
        company_id = re.sub(r"[^a-z0-9]+", "-", profile["name"].lower()).strip("-")

    existing = db.companies.find_one({"id": company_id}, _P)
    if existing and existing.get("name") != profile["name"]:
        company_id = f"{company_id}-{datetime.now().strftime('%H%M%S')}"

    scrape_result = {}
    if website_url:
        try:
            scrape_result = scrape_company(website_url, profile["name"])
        except Exception:
            scrape_result = {}

    entry = {
        "id":               company_id,
        "user_id":          profile.get("user_id", ""),
        "profile_type":     profile.get("profile_type", "company"),
        "website_type":     profile.get("website_type", "own"),
        "name":             profile["name"],
        "website_url":      website_url,
        "linkedin_url":     profile.get("linkedin_url", ""),
        "industry":         profile["industry"],
        "tone":             profile.get("tone", "professional"),
        "post_time":        profile["post_time"],
        "website_content":  "\n\n".join(scrape_result.get("raw_pages", {}).values())[:4000],
        "analysis":         scrape_result.get("analysis", {}),
        "brand_color":      scrape_result.get("brand_color", ""),
        "pages_scraped":    scrape_result.get("pages_scraped", 0),
        "blog_posts_found": scrape_result.get("blog_posts_found", 0),
        "linkedin_analysis": {},
        "linkedin_top_posts": [],
        "created_at":       datetime.now().isoformat(),
        "active":           profile.get("active", True),
        "carousel_enabled": profile.get("carousel_enabled", False),
        "designation":      profile.get("designation", ""),
    }

    db.companies.replace_one({"id": company_id}, {"_id": company_id, **entry}, upsert=True)
    return entry


def get_company(company_id: str) -> dict | None:
    return db.companies.find_one({"id": company_id}, _P)


def list_companies(user_id: str = "") -> list[dict]:
    q = {"user_id": user_id} if user_id else {}
    return list(db.companies.find(q, _P))


def delete_company(company_id: str):
    db.companies.delete_one({"id": company_id})


def update_company(company_id: str, data: dict) -> dict | None:
    c = db.companies.find_one({"id": company_id}, _P)
    if not c:
        return None

    for field in ("name", "industry", "tone", "post_time", "linkedin_url",
                  "website_type", "carousel_enabled", "designation", "carousel_theme"):
        if field in data:
            c[field] = data[field]

    new_url = data.get("website_url", "").strip()
    old_url = c.get("website_url", "")
    if not new_url and old_url:
        c["website_url"]     = ""
        c["website_content"] = ""
        c["analysis"]        = {}
        c["pages_scraped"]   = 0
    elif new_url and new_url != old_url:
        try:
            sr = scrape_company(new_url, c["name"])
            c["website_url"]     = new_url
            c["website_content"] = "\n\n".join(sr.get("raw_pages", {}).values())[:4000]
            c["analysis"]        = sr.get("analysis", {})
            c["brand_color"]     = sr.get("brand_color", "")
            c["pages_scraped"]   = sr.get("pages_scraped", 0)
        except Exception:
            c["website_url"] = new_url
    elif new_url:
        c["website_url"] = new_url

    db.companies.replace_one({"id": company_id}, {"_id": company_id, **c})
    return c


def toggle_company(company_id: str, active: bool):
    db.companies.update_one({"id": company_id}, {"$set": {"active": active}})


def save_linkedin_data(company_id: str, linkedin_result: dict):
    result = db.companies.update_one(
        {"id": company_id},
        {"$set": {
            "linkedin_analysis":  linkedin_result.get("analysis", {}),
            "linkedin_top_posts": linkedin_result.get("top_posts", []),
            "linkedin_data_type": linkedin_result.get("type", ""),
        }}
    )
    return result.matched_count > 0
