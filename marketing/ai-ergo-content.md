# KnowErgo — Content Package (Knowella profile)

Purpose: the content foundation for KnowErgo LinkedIn posts generated through Voyce.
Everything here is grounded in what the product **actually does** (from the
knowella-ml backend + the customerweb postural-analysis UI) and in the **HSE
Manual Handling Assessment Charts (MAC)** reference in the ai_ergo folder — no
invented statistics.

**Brand consistency:** colors and risk language are pulled from Knowella's real
design system (`customerweb/src/app/core/styles/_variable.scss`): primary violet
`#6E63FF`, secondary teal `#04B492`, font Poppins, risk palette green
`#45A631` → amber `#FF6E00` → red `#E70000`. Every KnowErgo asset uses these so
the content matches what a prospect sees when they log into the product.

---

## 1. What it is (positioning)

**Product name: KnowErgo** (confirmed — matches the live page,
knowella.com/ergonomics; "AI Ergo" is only a section heading there). The Knowella
logo lockup on every asset reads **"Knowella | KnowErgo"**. The repo folder
`ai_ergo/` keeps its name; only the product-facing name is KnowErgo.

**Real tagline (use verbatim):** *"Catch strain before it becomes injury."*

**Real one-liner (from the page):** *"Score any task from a short video against
RULA, REBA, and NIOSH, get the fix, and prove it worked with a re-assessment."*

Note the marketing leads with **RULA / REBA / NIOSH** (three systems) — lead
content with those three; the full six (adding Snook, HSE MAC, WISHA) are depth
to cite, not the headline. The page's own promises to reuse: *"the same
frameworks ergonomists use"*, *"no wearables"*, *"assess every task, not just
a sampled few"*, *"every risky task gets checked, not just the few a consultant
had time for"*, and the outcome *"fewer sprains and strains, fewer recordable
injuries."*

**The wedge:** a full manual REBA/RULA assessment is a stopwatch, a protractor,
and an hour per task. KnowErgo turns a short clip into scored systems in one
pass — so EHS teams assess *every* task, not just the sampled few. And it closes
the loop: re-assess after a change and **prove the risk dropped**.

**Real CTAs (use these, not invented ones):** "Start free trial" · "Book a demo".

**Who it's for (ICP):** EHS / safety managers, ergonomists, and plant/ops
leaders in manufacturing, warehousing, logistics, and construction — anyone who
owns MSD risk and currently assesses by hand or not at all.

**Anti-claims — never say:**
- "Replaces your ergonomist." It's the assessor's instrument, not their
  replacement — lead with *"score every task"*, not *"fire the expert."*
- Any injury-cost or prevalence statistic we can't cite. Use the HSE facts in
  §6 or a live figure the news pull surfaces — never a round number from memory.
- Medical or diagnostic claims. It scores *task risk*, not a person's health.
- "100% accurate." The product's honesty (it declines to score low-confidence
  frames) is the story — see the "honest-by-design" angle.

---

## 2. Voyce product config (seed)

Add under the Knowella profile as a product. Drop-in for the `products[]` entry
(the `theme_spec` is contrast-validated — passes WCAG AA on every role):

```json
{
  "id": "ai-ergo",
  "name": "KnowErgo",
  "brand_name": "Knowella",
  "product_name": "KnowErgo",
  "brand_logo": "knowella-mark.png",
  "url": "https://www.knowella.com/ergonomics/",
  "website_url": "https://www.knowella.com/ergonomics/",
  "industry": "workplace ergonomics and MSD-injury prevention (EHS)",
  "topics": [
    "musculoskeletal disorder (MSD) prevention",
    "REBA / RULA / NIOSH ergonomic assessment",
    "manual handling risk (HSE MAC)",
    "computer-vision safety analytics",
    "warehouse and manufacturing ergonomics"
  ],
  "search_angles": [
    "OSHA ergonomics enforcement",
    "MSD injury cost workers compensation",
    "warehouse injury rate automation",
    "manual handling regulation",
    "workplace safety AI"
  ],
  "weight": 1,
  "enabled": true,
  "theme_spec": {
    "name": "Knowella Deep",
    "palette": {
      "bg": "#16132E", "accent": "#8B82FF", "accent2": "#04B492",
      "title": "#FFFFFF", "subtitle": "#A9A3D8", "body": "#E4E1F5", "muted": "#3A356A"
    },
    "rationale": "Knowella's house palette (violet #6E63FF primary, teal #04B492 secondary) on a deep violet-black field. Accent violet lifted to #8B82FF so the brand hue clears WCAG AA on dark; teal carries the AI-Ergo association as the secondary pop.",
    "generated_by": "claude-code-in-session"
  }
}
```

**Theme — "Knowella Deep"** (WCAG AA on every role, headroom to spare):
bg `#16132E` · accent `#8B82FF` (Knowella violet, lifted for dark legibility)
· accent2 `#04B492` (Knowella teal / KnowErgo) · title white · body `#E4E1F5`.

Why dark, not the light app UI: Knowella's teal `#04B492` on white only reaches
2.6:1 contrast — it fails on a light carousel. A deep violet-black field keeps
both brand colors legible and reads as premium. (A light variant is possible but
would need the teal darkened to ~`#02765F`, drifting from the exact brand teal —
so dark is the more brand-faithful choice for this format.)

**Font caveat:** the carousel renderer bundles Inter; Knowella's brand font is
Poppins. Matching it exactly means dropping Poppins `.ttf` files into
`backend/fonts/` and pointing `carousel.py` at them — a small follow-up, not a
blocker. Inter is a close neutral stand-in until then.

**Risk colors for any risk-band visual** (from the brand system, not invented):
green `#45A631` (low) → amber `#FF6E00` (medium) → red `#E70000` (high).

---

## 3. Content pillars (the recurring angles)

Rotate across these so the feed teaches, not sells. The product defines the
*niche*; only the Product Spotlight pillar is about KnowErgo itself (capped at
≤2/week by Voyce's self-promo guard).

1. **The invisible risk** — a posture people do 400×/shift that quietly scores
   High. Educational, threshold-driven. (Pillar workhorse.)
2. **Assessor's corner** — teach one ergonomic concept properly: what REBA
   actually measures, why RULA is upper-limb only, what NIOSH's Lifting Index
   means. Builds authority with the ICP.
3. **News reaction** — react to a fresh OSHA action, injury-cost report, or
   warehouse-automation story (Voyce's Tavily pull fills the current fact).
4. **Honest-by-design** — the counter-intuitive trust angle: the tool that
   *refuses to guess*. Great differentiator content.
5. **Product Spotlight** — one concrete capability, shown not told (the
   colour-coded video, six systems in one pass, per-worker tracking).

---

## 4. Carousel blueprints (5 slides, 1080×1080)

Each uses real product facts and the "Knowella Deep" theme. Slide notation:
**[HOOK]** cover · **[BODY]** · **[STAT]** big-numeral · **[CTA]** close.

### Blueprint A — "The 6-second assessment" (Product Spotlight)
1. **[HOOK]** "A REBA assessment by hand takes an hour. Watch it happen in one video."
2. **[BODY]** "Upload a 12-second clip of a worker. KnowErgo tracks 36 skeletal
   joints and measures 10 body angles — neck, trunk, both arms, both wrists,
   both knees — every single second."
3. **[STAT]** Big numeral: **6** — "ergonomic systems scored in one pass: REBA,
   RULA, NIOSH, Snook, HSE MAC, WISHA."
4. **[BODY]** "Left and right sides scored separately. Colour-coded on the video
   — green to red — so you see the exact frame a posture crosses into risk."
5. **[CTA]** "Assess every workstation, not just the one that already hurt
   someone. → KnowErgo by Knowella."

### Blueprint B — "What your body is doing at 20°" (The invisible risk)
1. **[HOOK]** "Your neck bends past 20°. That's the line between fine and a
   flag — and nobody's watching it."
2. **[BODY]** "Ergonomic risk isn't dramatic. It's a trunk bent 60°, a wrist
   cocked 10°, a knee under 120° — held for a shift, repeated for a career."
3. **[STAT]** Big numeral: **>20°** — "neck flexion where KnowErgo turns the
   bone red. Trunk flags at 60°, upper arm at 40°, knee below 120°."
4. **[BODY]** "A person can't feel a 20° drift. A camera scoring every second
   can — before it becomes a claim."
5. **[CTA]** "See the risk you can't feel. → KnowErgo."

### Blueprint C — "The colours of the HSE MAC" (Assessor's corner)
1. **[HOOK]** "Green, Amber, Red, Purple. The four words the UK's HSE uses to
   grade manual handling — and what they actually mean."
2. **[BODY]** "The Manual Handling Assessment Charts score a task and hand it a
   colour. G: low risk. A: examine closely. R: prompt action needed."
3. **[STAT]** Big numeral / block: **P = Purple** — "Very high risk. A serious
   risk of injury, especially when one person carries the whole load."
4. **[BODY]** "The MAC's job isn't to judge — it's to find the tasks that need
   attention first. KnowErgo scores the MAC automatically from video, alongside
   five other systems."
5. **[CTA]** "Stop guessing which task is the Red one. → KnowErgo."

### Blueprint E — "Where the shift actually hurt" (The invisible risk / body-map)
*Uses the real per-body-part breakdown from the postural-analysis UI: each joint
gets avg angle, peak, and % of the clip in low/medium/high risk.*
1. **[HOOK]** "You can't fix a whole body at once. So KnowErgo scores it one joint
   at a time."
2. **[BODY]** "Neck. Trunk. Shoulders. Elbows. Wrists. Knees. For each one:
   average angle, worst peak, and the share of the task spent in the red."
3. **[STAT]** Big numeral: **40%** *(illustrative — real number per clip)* —
   "of one lift cycle, this worker's trunk sat above the 60° red line. You'd
   never catch that by eye."
4. **[BODY]** "A body-map lights up green to red so the fix is obvious: it's the
   trunk, not the wrists. Redesign the reach, not the whole station."
5. **[CTA]** "Find the joint that's actually at risk. → KnowErgo by Knowella."

### Blueprint F — "Prove the fix worked" (Assessor's corner / before-after)
*Uses the initial-vs-post comparison + trend flags (improved / worsened).*
1. **[HOOK]** "You raised the bench 4 inches. Did the risk actually drop — or
   does it just feel better?"
2. **[BODY]** "KnowErgo scores the same task before and after your change and
   tags every joint: improved, worsened, unchanged."
3. **[STAT]** Big block: **REBA 8 → 4** *(illustrative)* — "High risk to Medium,
   with the trunk score doing the work. That's a number you can put in a report."
4. **[BODY]** "Ergonomics budgets die on 'we think it helped.' Bring a
   before-and-after score instead and the next intervention funds itself."
5. **[CTA]** "Measure the fix, not just the problem. → KnowErgo."

### Blueprint D — "The tool that refuses to guess" (Honest-by-design)
1. **[HOOK]** "Most AI gives you an answer for everything. Ours sometimes says
   'I can't see clearly enough to score that.'"
2. **[BODY]** "When a worker is turned away, or a joint is hidden, KnowErgo draws
   a grey '?' instead of inventing an angle. A fabricated score on a safety
   report is worse than no score."
3. **[BODY]** "Three layers of smoothing mean the numbers don't flicker frame to
   frame — and a single bad frame can never drag a score."
4. **[BODY]** "Every sub-score is exposed: neck, trunk, legs, load, coupling.
   Enough for an assessor to defend the number in an audit."
5. **[CTA]** "Risk scoring you can put your name on. → KnowErgo."

**Carousel rules (from Voyce's content engine):** LinkedIn renders no markdown —
plain text, bullets become `→`; no em-dashes (an AI tell); one idea per slide;
the STAT slide carries the single number that makes someone stop scrolling.

### Visual slide kinds (built into carousel.py)

Two ergonomic-visual slide renderers exist now, plus the Knowella logo lockup on
every slide (driven by the `brand_logo` / `brand_name` / `product_name` fields
above). Add these as `content_slides` entries:

```jsonc
// STRONGEST hero — a real KnowErgo annotated frame (skeleton overlay, angle
// read-outs and live scorecard already burned in by the product). Full-bleed,
// with the Knowella lockup + a headline on a dark scrim. Bundled frames live in
// backend/assets/ (ergo-frame-1.png, ergo-frame-2.png). Use footage the company
// has rights to; faces are shown per the product owner's decision.
{ "kind": "photo", "image": "ergo-frame-1.png",
  "title": "This is what KnowErgo sees",
  "body": "Every joint tracked, every angle scored, live on the floor." }

// Drawn alternative when no real frame fits — schematic annotated lift-skeleton
{ "kind": "posture", "title": "This is what KnowErgo sees", (neon bones, angle read-outs,
// risk colours) + floating scorecard. KnowErgo's on-video look, drawn schematically
// (no real person shown — real worker footage is face/consent-sensitive).
{ "kind": "posture", "title": "This is what KnowErgo sees",
  "body": "Every joint tracked, every angle scored, live.",
  "risk": {"trunk":"high","neck":"medium","left_leg":"medium","left_arm":"low"},
  "score": "REBA 8  ·  HIGH RISK" }

// Per-body-part risk distribution — green/amber/red bars, % time in red called out
{ "kind": "riskbars", "title": "Where the shift actually hurt",
  "body": "Share of the lift cycle each joint spent in the red.",
  "parts": [ {"name": "Trunk", "low": 22, "medium": 38, "high": 40},
             {"name": "Neck",  "low": 55, "medium": 35, "high": 10} ] }   // up to 6

// Body-map — schematic figure, each region lit by risk; side labels + takeaway panel
{ "kind": "bodymap", "title": "Fix the joint, not the whole station",
  "joints": {"neck":"medium","trunk":"high","left_arm":"low",
             "right_arm":"low","left_leg":"medium","right_leg":"low"},
  "worst": "Trunk drove the risk: redesign the reach, not the wrists." }
```

**Data-source rule (honesty):** the numbers in `parts` / `joints` must come from a
**real KnowErgo assessment** (or a hand-built sample clearly marked illustrative) —
never invented by the news-driven generator. So these visual slides belong to
**assessment-driven or hand-authored** decks; the daily autopilot keeps using the
text/stat slides (which carry no fabricated figures). Wiring live assessment JSON
into a carousel is the natural next integration.

Sample renders (deep-dark Knowella Deep, logo on every slide): see
`marketing/ai-ergo-samples/knowergo-carousel-v4.pdf` and `v4-slide-*.png`.

---

## 5. Text post templates

**Trend Reaction (news-pulled):**
> [Current OSHA/MSD news fact from the live pull.]
>
> Here's what most teams miss: by the time an MSD becomes a claim, the posture
> that caused it has been repeated for months. The risk was visible the whole
> time — nobody was scoring it.
>
> A 12-second video and six ergonomic systems later, you know which task on the
> floor is the Red one. That's the whole point of assessing before it hurts.

**Expert Insight (Assessor's corner):**
> REBA and RULA are not interchangeable, and using the wrong one quietly
> invalidates your assessment.
>
> → RULA is upper-limb: neck, trunk, arms, wrists. Built for seated, screen, and
>   precision work.
> → REBA is whole-body, including legs and load. Built for the floor — lifting,
>   carrying, awkward stances.
>
> Score a warehouse lift with RULA and you've ignored the knees doing the work.
> KnowErgo runs both, both sides, every second — so the system fits the task
> instead of the other way round.

**Hot Take (tweet-card day):**
> "We do ergonomic assessments" usually means "we did one, in 2019, for the desk
> job that complained." Every other task on the site is unscored. That's not a
> program — it's a paper trail.

---

## 6. Fact bank (citable only)

**From the HSE MAC document (in the ai_ergo folder — a real HSE/HSL publication):**
- Work-related MSDs, including manual handling injuries, are the most common
  type of occupational ill health in the UK.
- The Manual Handling Operations Regulations 1992 set a hierarchy: **avoid**
  hazardous manual handling → **assess** what can't be avoided → **reduce** the
  risk so far as reasonably practicable.
- MAC risk bands: **G** low · **A** medium · **R** high (prompt action) · **P**
  very high (serious risk, especially single-person loads).
- The MAC is a triage tool to find the highest-risk tasks first, not a full risk
  assessment on its own.

**From the product (all real, from the codebase):**
- 6 assessment systems in one pass: REBA, RULA, NIOSH, Snook/Liberty Mutual,
  HSE MAC, WISHA.
- 133-keypoint pose model → 36-joint skeleton → 10 measured angles → scored
  every second, left and right independently.
- Red-flag thresholds: neck >20°, trunk >60°, upper arm >40°, knee <120°.
- Postural events auto-detected: head twist >20°, trunk twist >10°, trunk bend
  >15°, wrist deviation >10° — each adjusts the score like a manual assessor
  would.
- NIOSH: 51 lb load constant, Lifting Index >3 = redesign required.
- Snook: below 75% of the working population capable = intervention needed.
- Refuses to score below 0.45 pose confidence (draws a grey "?" instead).
- Multi-person tracking with 3-second occlusion recovery; per-worker scorecards.
- Privacy: soft elliptical face blur + full background blur, skeleton still
  readable — safe for consent-sensitive and IP-sensitive plants.
- **Per-body-part breakdown** (customerweb postural-analysis): each of neck,
  trunk, shoulder, elbow, wrist, knee gets average angle, peak angle, and the
  **% of the task spent in low / medium / high risk**. Per-part medium/high
  thresholds — Neck 20°/45°, Trunk 20°/60°, Shoulder 45°/90°, Elbow 60°/100°,
  Wrist 15°/30°, Knee 30°/60°.
- **Before/after comparison**: the same task scored initial-vs-post-intervention,
  with each joint flagged **improved / worsened / unchanged / new_risk** — proof
  an ergonomic fix actually reduced the number.
- 23-part visual body-map (neck, upper/lower back, chest, abdomen, both
  shoulders/elbows/wrists/hands/knees/ankles/upper+lower arms+legs) that lights
  by risk — the native way KnowErgo shows *where* the risk is.

**Stats to NEVER state without a live source:** dollar cost of MSDs, % of
injuries that are MSDs in the US, "X billion in workers' comp." If a post needs
a number like this, it comes from the Tavily news pull with its source, or not
at all.

---

## 7. DIY video-day prompt seed (for `__video__` days)

When a video day fires, Voyce writes the caption + a generation prompt. A strong
seed for KnowErgo video content:

> 20–30s vertical (9:16) explainer. Scene 1: a worker lifting a box in a
> warehouse, a clean AI skeleton overlay appearing on their body in Knowella
> violet #6E63FF. Scene 2: one joint — the lower back — shifts green #45A631 →
> amber #FF6E00 → red #E70000 as they bend, with the angle "62°" appearing
> beside it. Scene 3: a calm dashboard card reading "REBA 8 · HIGH RISK" in the
> Knowella violet UI style. Scene 4: name card, "KnowErgo by Knowella," violet
> and teal #04B492 on deep violet-black #16132E. On-screen hook text from the
> post's first line. Poppins-style type. Minimal, premium, no stock-footage
> feel, readable captions.
