# Voyce — Multi-Product Companies & Per-Product Design Spec

Status: IMPLEMENTED v1 (2026-08-09) — backend + dashboard UI shipped; deltas
from the original draft: theme_spec palette uses the renderer's own vocabulary
(bg/accent/accent2/title/subtitle/body/muted — zero mapping in carousel.py),
and layout enums (cover_style etc.) are deferred until a PREVIEW-prototyped
Pillow renderer actually exists for them. Asset upload endpoint also deferred.
Scope: (1) company → products data model, (2) per-product content generation,
(3) per-product assets & carousel theming, (4) Claude-generated design specs.

---

## 1. Problem

A `companies` document today conflates two things:

- **Identity** — whose voice, whose LinkedIn connection, schedule, approval mode.
- **Subject** — which website brief, which niche to search, which brand color.

A company with multiple products (e.g. Knowella → Ella, AI Ergo, App Builder,
KnowHealth) has ONE identity but MANY subjects. Faking it with separate
profiles duplicates voice/schedule and burns the profile limit.

## 2. Data model

Embed products inside the existing company document (no new collection, no
joins, no migration script — schemaless Mongo):

```jsonc
// companies document — new field
{
  "_id": "knowella",
  "name": "Knowella",
  // ...existing identity fields unchanged (voice, schedule, approval_mode)...
  "products": [
    {
      "id": "ai-ergo",                  // slug, unique within the company
      "name": "AI Ergo",
      "url": "https://knowella.com/ai-ergo",
      "industry": "workplace safety / ergonomics",
      "topics": ["MSD prevention", "OSHA compliance", "computer vision safety"],
      "search_angles": ["injury cost", "warehouse automation", "EHS regulation"],
      "brief": { /* scraper.py analysis output, per-product */ },
      "brand_color": "#1F6F5C",         // scraped or manual
      "theme_spec": { /* §5 — Claude-generated, cached */ },
      "enabled": true,
      "weight": 1                        // relative share of the rotation
    }
  ]
}
```

**Backward compatibility (load-bearing):** a company with no `products` array
behaves as one implicit product built from the company's own fields. All
resolution goes through one helper:

```python
def resolve_product(company: dict, product_id: str | None = None) -> dict:
    """Return the product context for generation. Falls back to the company
    itself as an implicit product when products[] is absent/empty."""
```

Nothing else in the codebase reads `company["industry"]` etc. directly after
this lands — everything takes the resolved product context.

## 3. Rotation — picking (product, post_type)

Extend the existing deterministic hash (keeps `get_week_plan` forecasting):

```python
h = md5(f"{company_id}:{date}:{runs_today}".encode()).hexdigest()
product = weighted_pick(enabled_products, int(h[:4], 16))   # dimension 1
post_type = rotation[int(h[4:8], 16) % len(rotation)]        # dimension 2
```

Precedence stays: explicit override → calendar `scheduled_types[day]`
(extended to hold `{"product": "ai-ergo", "type": "industry_stat"}`) → hash.

**Self-promo cap:** the existing rule (Product Spotlight is the sanctioned
exception to the self-promo ban) generalizes to: max **2 Product Spotlight
slots per company per week across all products**. Enforced in
`_get_post_type` by counting spotlight entries in the last 7 days of
`post_log`. The product otherwise defines the *niche* of a value post, not
its subject — an AI Ergo day means ergonomics-news commentary, not an ad.

## 4. Search & generation

- `search.py` takes industry/topics from the resolved product, not the company.
- Add product-level `search_angles` merged into `_DIVERSITY_ANGLES` rotation so
  each product gets niche-appropriate query variety.
- `autonomous.py` prompt context: company voice/tone (identity) + product brief
  (subject). The voice block never varies by product; the subject block always
  does.
- `post_log` entries gain `product_id` so analytics/calendar can group by
  product.

## 5. Assets & theming — per product

### 5.1 What a theme is

`carousel.py` today: 4 hardcoded palettes + industry→palette map + scraped
brand-color override. Replace the resolution with:

```
product.theme_spec  →  product.brand_color (auto palette)  →
company theme       →  industry map        →  default
```

### 5.2 theme_spec schema (consumed by Pillow, produced by Claude)

The renderer stays deterministic Pillow. Claude produces a **spec**, never
pixels — image models garble typography and we already composite all text in
Pillow (established design decision in `carousel.py`).

```jsonc
{
  "name": "Ergo Industrial",
  "palette": {
    "bg": "#0E1B17",
    "bg_alt": "#12241E",
    "fg": "#F2F5F3",
    "muted": "#9DB5AC",
    "accent": "#3ECF8E",
    "accent_2": "#F5A623"
  },
  "cover_style": "number_block" ,      // enum of existing cover renderers
  "body_style": "editorial",            // enum of existing body renderers
  "stat_treatment": "big_numeral",      // enum
  "corner_motif": "grid_dots",          // enum: none|grid_dots|diagonal|blob
  "heading_weight": "extrabold",        // maps to bundled Inter weights
  "rationale": "Industrial safety: dark green field, high-vis accent…"
}
```

Every enum maps to a renderer/asset that already exists — Claude picks and
colors, it does not invent layouts. New layouts are a dev-time job (§6.2).

### 5.3 Validation (code, not model)

Before saving a spec:
- WCAG contrast check `fg`/`bg` ≥ 4.5:1 and `accent`/`bg` ≥ 3:1 (pure Python).
- All enums validated against the renderer registry.
- On failure: one retry with the failure fed back, then fall back to the
  brand-color auto palette. (Same fail-open philosophy as `_value_gate`.)

### 5.4 Asset storage

- `backend/assets/products/<company_id>/<product_id>/` — logo, avatar
  (uploaded via a new endpoint, size-capped like screenshot uploads).
- Rendered PDFs stay ephemeral/base64 as today; the 16MB Mongo document risk
  in `pending_posts` is pre-existing — add a size check before insert while
  in here.

## 6. Claude integration

### 6.1 Runtime: theme generation (Anthropic SDK)

New module `backend/design.py` — Claude is used ONLY here; Gemini remains the
content engine untouched. One call per product (on create/URL change/manual
"regenerate theme"), cached on the product doc, so cost is a few cents per
product, not per post.

```python
# backend/design.py
import anthropic

_client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env

THEME_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "palette": {
            "type": "object",
            "properties": {
                "bg": {"type": "string"}, "bg_alt": {"type": "string"},
                "fg": {"type": "string"}, "muted": {"type": "string"},
                "accent": {"type": "string"}, "accent_2": {"type": "string"},
            },
            "required": ["bg", "bg_alt", "fg", "muted", "accent", "accent_2"],
            "additionalProperties": False,
        },
        "cover_style": {"type": "string", "enum": ["number_block", "editorial", "stat_hero"]},
        "body_style": {"type": "string", "enum": ["editorial", "card", "list"]},
        "stat_treatment": {"type": "string", "enum": ["big_numeral", "inline", "card"]},
        "corner_motif": {"type": "string", "enum": ["none", "grid_dots", "diagonal", "blob"]},
        "heading_weight": {"type": "string", "enum": ["bold", "extrabold", "black"]},
        "rationale": {"type": "string"},
    },
    "required": ["name", "palette", "cover_style", "body_style",
                  "stat_treatment", "corner_motif", "heading_weight", "rationale"],
    "additionalProperties": False,
}

def generate_theme_spec(product: dict, company: dict) -> dict:
    """One-shot brand-design call. Cached on the product doc by the caller."""
    resp = _client.messages.create(
        model="claude-opus-5",
        max_tokens=16000,
        system=(
            "You are a brand designer creating a LinkedIn carousel theme. "
            "Pick colors and layout options that fit the product's industry "
            "and brand color, read well at 1080x1080, and pass WCAG AA "
            "contrast (fg on bg >= 4.5:1, accent on bg >= 3:1)."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Product: {product['name']} — {product.get('industry','')}\n"
                f"Brand color (if any): {product.get('brand_color') or 'none'}\n"
                f"Company: {company.get('name')}\n"
                f"Brief: {str(product.get('brief'))[:1500]}"
            ),
        }],
        output_config={"format": {"type": "json_schema", "schema": THEME_SCHEMA}},
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("theme generation declined")
    import json
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)
```

Notes:
- `pip install anthropic`; add `ANTHROPIC_API_KEY` to `backend/.env` and
  `render.yaml` (`sync: false`).
- Structured outputs (`output_config.format`) guarantee valid JSON matching
  the schema — no regex extraction.
- Follow the fail-open convention: any exception → brand-color auto palette,
  log, still ship.

### 6.2 Dev-time: designing NEW layouts/renderers with Claude Code

New slide layouts don't come from the API — they come from the workflow this
repo already uses:

1. Prototype in a `PREVIEW-*.html` (Claude Code + the `frontend-design` /
   vibecurb skills already vendored in `.claude/skills/`), exactly like
   `PREVIEW-carousel-pro.html` prototyped the current pro themes.
2. Once approved visually, port the layout to a Pillow renderer in
   `carousel.py` and register its enum value in `THEME_SCHEMA` +
   the renderer registry.
3. The runtime Claude call can then select it for appropriate products.

This keeps the split clean: **Claude Code designs layouts (dev-time), Claude
API picks and colors them per product (runtime), Pillow renders (always).**

## 7. Endpoints (new/changed)

| Method | Path | Purpose |
|---|---|---|
| POST | `/companies/{id}/products` | Add product (triggers scrape + theme gen) |
| PUT | `/companies/{id}/products/{pid}` | Edit (re-scrape/re-theme on URL change) |
| DELETE | `/companies/{id}/products/{pid}` | Remove |
| PATCH | `/companies/{id}/products/{pid}/toggle` | Enable/disable in rotation |
| POST | `/companies/{id}/products/{pid}/theme` | Regenerate theme spec |
| POST | `/companies/{id}/products/{pid}/assets` | Upload logo/avatar (8MB cap) |
| GET | `/companies` | Response gains `products[]` + per-product `next_post_type` |

All owner-checked like existing `/companies/{id}/*` routes. Product count cap:
reuse plan gating (free: 1 product, pro: e.g. 5 per company).

## 8. Dashboard UI

Under each profile card: product chips (name + theme swatch + enabled toggle),
an "Add product" row (URL → scrape), and the calendar shows `product · type`
per day. Theme preview = render slide 1 via the existing carousel preview path.

## 9. Sequencing

1. **Pre-work (same code paths, do first):** fix `/analytics/refresh` global
   `delete_many({})` rewrite; fix profile-ID collision in `save_company`;
   SSRF bypass in `_fetch_article_meta`.
2. `resolve_product` helper + implicit-product backcompat (no behavior change).
3. Products CRUD + per-product scrape.
4. Rotation + search + prompt context per product; `product_id` in `post_log`.
5. `design.py` + theme resolution in `carousel.py` + validation.
6. Dashboard UI.
7. (Optional, later) new layout enums via the §6.2 preview workflow.

Estimate: steps 2–5 ≈ 2–3 focused days; UI ≈ 1 day. Steps are independently
shippable behind the implicit-product fallback.

## 10. Out of scope (explicitly)

- LinkedIn **organization** posting (`w_organization_social`) — separate
  effort; all posting remains member-scoped.
- Replacing Gemini for post content — Claude is scoped to design specs only.
- Per-product voice — voice stays a company/identity property.
