# AI Ergo — Content Kit (Knowella)

Built 2026-08-11 from the actual `knowella-ml` codebase + the HSE MAC reference
PDF in the ai_ergo folder. Every number below is verified in code or citable to
HSE — this kit obeys Voyce's own no-fabricated-stats rule.

Persona: the **Knowella** company profile in Voyce, with **AI Ergo** as a
product (posts publish to the connected member profile; company-page posting
is not supported by the current LinkedIn scopes).

---

## 1. Product entry (seed into the Knowella profile)

```json
{
  "name": "AI Ergo",
  "url": "https://knowella.com",
  "industry": "workplace ergonomics and EHS",
  "topics": [
    "musculoskeletal disorder prevention",
    "ergonomic risk assessment",
    "REBA and RULA scoring",
    "NIOSH lifting equation",
    "manual handling safety",
    "computer vision in workplace safety"
  ],
  "search_angles": [
    "OSHA ergonomics regulation",
    "MSD injury cost workers compensation",
    "warehouse manual handling injury",
    "HSE manual handling MAC",
    "ergonomic assessment software",
    "EHS technology adoption"
  ],
  "weight": 1,
  "analysis": {
    "description": "AI Ergo turns an ordinary video of a worker into a defensible ergonomic risk assessment. Upload a clip (or a URL), and it returns an annotated video with a color-coded skeleton plus scores on six recognized systems at once: REBA, RULA, the NIOSH Lifting Equation, Snook/Liberty Mutual tables, the UK HSE MAC, and the WISHA caution-zone checklist. Every person in frame is tracked separately and scored every second, left and right sides independently.",
    "products_services": [
      "video-based ergonomic assessment (upload or URL, up to 200MB)",
      "six scoring systems in one pass: REBA, RULA, NIOSH, Snook, HSE MAC, WISHA",
      "multi-person tracking with per-person score dashboards",
      "10 joint angles measured per person per second",
      "automatic lift counting for NIOSH/Snook frequency inputs",
      "face blur and background blur for consent- and IP-sensitive sites",
      "chart-ready per-second angle timelines per worker"
    ],
    "target_audience": "EHS managers, ergonomists, safety directors, plant and warehouse operations leaders, workers' comp and insurance risk teams",
    "unique_value": "Six defensible scoring systems from one phone video, scored per second per person, with the sub-scores exposed so an assessor can defend every number in an audit. The system declines to score when it can't see well enough, rather than guessing.",
    "key_topics": ["ergonomics", "MSD prevention", "REBA", "RULA", "NIOSH", "manual handling", "EHS technology"],
    "content_themes": [
      "what the angle thresholds actually mean",
      "assessment methodology explainers",
      "manual vs video-based assessment",
      "honest AI: quality gates and declining to guess",
      "regulation and standards (HSE MAC, NIOSH, WISHA)"
    ]
  },
  "brand_color": "#04B492",
  "theme_spec": {
    "name": "Ergo Field",
    "palette": {
      "bg": "#0A1A16",
      "accent": "#04B492",
      "accent2": "#F5A623",
      "title": "#FFFFFF",
      "subtitle": "#8FB5AA",
      "body": "#D7E5E0",
      "muted": "#2E4A42"
    },
    "rationale": "Anchored in AI Ergo's product accent (#04B492, from the product UI) on a deep green-black field; amber secondary echoes the HSE MAC risk bands so risk-scoring visuals feel native.",
    "generated_by": "claude-code-session"
  }
}
```

Contrast (validated): title 17.9:1, body 13.8:1, accent 6.8:1, accent2 8.8:1 —
all pass the WCAG gates in `design.validate_theme_spec`.

---

## 2. Verified fact bank (safe to cite in posts)

From the codebase:
- 6 scoring systems in one pass: REBA, RULA, NIOSH Lifting Equation,
  Snook/Liberty Mutual MMH, HSE MAC, WISHA caution-zone checklist.
- 133-keypoint pose model → 36-joint working skeleton → 10 angles measured per
  person per second: neck, trunk, both upper arms, both forearms, both wrists,
  both knees.
- Left and right sides scored independently (side-facing footage killed
  single-side scoring — fixed and logged in accuracy iterations).
- 5 postural events auto-detected and fed into scores: head twist >20°, head
  lateral bend >10°, trunk twist >10°, trunk bend >15°, wrist deviation >10°.
- Risk color thresholds on the skeleton: neck flexion >20° red, trunk >60°
  red, upper arm >40° red, knee <120° red.
- NIOSH: 51 lb load constant; reach beyond 25 inches automatically fails;
  Lifting Index >3 = redesign required.
- Snook: task acceptable when ≥75% of the working population is capable.
- Automatic lift counting from wrist-height cycles (≥6 inch rise-and-return) —
  no stopwatch needed for frequency inputs.
- Quality gates: no score below 0.45 pose confidence; person must be ≥15% of
  frame height; missing joints render as "?" — never fabricated.
- Multi-person: identity-stable tracking with 3-second occlusion recovery and
  per-person floating scorecards.
- Privacy: soft face blur + full-background blur while keeping skeleton and
  scores readable.
- Speed engineering: TensorRT 2–3× over CUDA; NVENC hardware encoding 5–10×
  faster than software at 1080p. (Relative speedups only — never quote an
  end-to-end minutes-per-video number; none is measured.)

From HSE MAC (the PDF in ai_ergo/, citable as "HSE Manual Handling Assessment
Charts"):
- "Work-related musculoskeletal disorders (MSDs), including manual handling
  injuries, are the most common type of occupational ill health in the UK."
- MAC risk bands: Green low, Amber medium, Red high (prompt action needed),
  Purple very high.
- The Manual Handling Operations Regulations 1992 hierarchy: avoid → assess →
  reduce.

### Do NOT claim (not verified / not true)
- Any end-to-end processing time ("results in X minutes").
- PDF report generation (backend produces annotated MP4 + JSON; report export
  lives in the web app and its scope is still moving — verify before citing).
- Injury-reduction percentages, ROI numbers, customer counts — none exist yet.
- "Replaces an ergonomist" — position as the assessor's instrument, not their
  replacement.

---

## 3. Content pillars → Voyce rotation types

| Rotation type | AI Ergo pillar | Example angle |
|---|---|---|
| `industry_stat` | Threshold literacy | "At what angle does a neck posture turn red? 20 degrees." |
| `expert_insight` | Methodology explainers | Why REBA scores left and right separately |
| `how_to_playbook` | Assessment playbooks | How to film a task so it can actually be scored |
| `myth_vs_reality` | Manual vs video assessment | "AI scores everything it sees" — no; below 0.45 confidence it declines |
| `teardown` | One system per post | NIOSH's six multipliers, what each punishes |
| `case_study` | The accuracy log as story | The 2 seconds smoothing recovered — engineering honesty |
| `trend_commentary` | EHS tech news reaction | Tavily feeds this via search_angles |
| `prediction` | Where assessment is going | Every plant camera becomes an assessment instrument |
| `product_spotlight` (≤2/wk cap) | Feature stories | Face blur: assessment without surveillance |

---

## 4. Carousel structures (Ergo Field theme, 5 slides)

**A. The Threshold Deck** (`industry_stat`)
1. Hook: "When does a posture become a risk? The exact angles."
2. Neck: green under 11°, red past 20° (amber chip = accent2)
3. Trunk: red past 60° — with the twist modifier explained
4. Upper arm >40°, knee <120°
5. CTA: "One video scores all ten angles, every second."