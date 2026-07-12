# Voyce — Pitch Document

---

## One-liner

**Voyce writes and posts your LinkedIn content every day, on autopilot — in your voice — so consultants who win clients on LinkedIn stay visible without writing a word.**

---

## Who It's For

**Primary ICP: independent B2B consultants, fractional executives, and coaches who get clients from LinkedIn.**

For this person, LinkedIn visibility *is* pipeline. A quiet month on the feed is a quiet month of inbound. But writing a good post daily takes 30–60 minutes they don't have between client work — so they post in bursts, go silent for weeks, and the pipeline dries up. That inconsistency is the specific, expensive problem Voyce removes: one client won from one post pays for a year of Voyce many times over.

*Also works for (secondary, not the wedge): startup founders building a personal brand, sales professionals, agencies managing multiple client pages, and content marketers repurposing long-form into LinkedIn. We lead with consultants because that's where "not posting" has the clearest, most immediate revenue cost.*

---

## The Problem

For a consultant, LinkedIn is the cheapest client-acquisition channel there is — and the one they neglect most.

**Why:**
- Writing one good post takes 30–60 minutes, and it has to be consistent to compound
- Between billable client work, "write today's post" is always the thing that slips
- Generic AI tools (ChatGPT, LinkedIn's own AI writer) still make you prompt, edit, and copy-paste — every day
- Ghostwriters cost ₹40,000–₹1,50,000/month and still need managing
- Scheduling tools (Buffer, Hootsuite) only schedule — you still have to write everything first

**The result:** consultants with genuine expertise post in bursts, go quiet for weeks, and stay invisible exactly when they need pipeline.

---

## The Solution — Voyce

Set up once in 3 minutes. Voyce runs the entire loop, every day, unattended:

1. **Finds** today's relevant news in your niche (live web search)
2. **Writes** a post about it in your voice — learned from your past posts
3. **Publishes** it to LinkedIn at your chosen time — automatically

No prompting. No copy-pasting. No ghostwriter. You open LinkedIn to replies, not a blank composer.

**What makes this different from everything else:** other tools generate a draft you still have to review, schedule, and post. Voyce is the only one that closes the loop end-to-end — news → voice-matched post → published — with you out of the workflow entirely. (And when you *do* want control, the manual tool and preview mode are one click away — see Trust & Safety.)

---

## Features

### 1. Autonomous Daily Posting
- Set a post time once — Voyce writes and posts every day at that time, unattended
- Rotating content schedule: Trend Reaction, Hot Take, Lesson Learned, Expert Insight, Stat Reaction, Personal Story — never repetitive
- Pulls fresh news from the web every day (Tavily Search API) so posts are timely, not evergreen filler
- Works for both personal brands and company pages

### 2. Voice Learning
- Upload your LinkedIn data export (ZIP) or profile PDF, paste recent posts, or drop screenshots
- Voyce reads your past posts and learns your vocabulary, sentence length, and tone
- Every post is generated in *your* voice — not generic AI voice

### 3. Carousel PDF Generation
- Branded 5-slide LinkedIn carousels (1080×1080), posted as native LinkedIn documents
- Alternates automatically with text posts (carousel one day, text the next)

### 4. Manual Content Tool
- Paste text, a URL, or a YouTube link → LinkedIn post, X/Twitter thread, email snippet, blog intro, or carousel
- Post directly or schedule for later

### 5. Content Calendar & Analytics
- 7-day visual calendar of what's scheduled per profile
- Engagement tracking (likes, comments, reposts) via the official LinkedIn API, charted over your last 14 posts

### 6. Multiple Profiles & Authority Context
- Add your title (e.g. "Fractional CMO") so posts write from the right level of authority
- Manage multiple profiles, each with its own schedule, tone, and niche

---

## Trust & Safety (why autopilot is safe for your name)

Autopilot posts to *your* professional identity, so trust is the whole game. Voyce is built for it:

- **Preview before you commit** — run any profile with "preview"/dry-run to see exactly what it would post, before arming autopilot. Watch it for a few days, then let it run.
- **Every post is logged** — the manual tool and activity log show every post generated and published, with the news source it used. Nothing happens you can't see.
- **Edit or pause anytime** — one click pauses a profile; the manual tool lets you generate, edit, then post when you'd rather steer.
- **Official LinkedIn API** — Voyce posts through LinkedIn's official OAuth + REST API (the same mechanism Buffer/Hootsuite use), not browser automation or cookie scraping. No account-safety gray area.

*Roadmap: an optional approval queue ("review each post before it goes live") for users who want autopilot's convenience with a final human check.*

---

## Competitive Landscape (honest, 2026)

The LinkedIn-tools space is crowded. Here's where Voyce actually stands — including the tools that beat it on price:

| Tool | Price | Writes for you | Learns your voice | Carousels | **Fully autonomous daily posting** |
|---|---|---|---|---|---|
| LinkedIn native AI | Premium | Draft only | ✗ | ✗ | ✗ |
| ChatGPT | $20/mo | ✓ (you prompt) | ✗ | ✗ | ✗ |
| Buffer / Hootsuite | $15–99/mo | ✗ | ✗ | ✗ | ✗ (schedule only) |
| Taplio | $39–199/mo | ✓ | ✗ | Partial | ✗ (queue) |
| **Supergrow** | **$19/mo** | ✓ | ✓ | ✓ | ✗ (queue) |
| Ghostwriter | $500–2000/mo | ✓ | ✓ | Sometimes | ✓ (human) |
| **Voyce** | **$29/mo** | ✓ | ✓ | ✓ | **✓ (software)** |

**Honest read:** Voyce is *not* the cheapest — Supergrow does voice + carousels at $19. Voyce's real, defensible edge is the one column nobody else in software fills: it runs the whole loop unattended, daily, pulling live news and publishing on its own. Everyone else hands you a draft or a scheduled queue you still operate. Voyce is the only tool that replaces the *habit*, not just the *writing* — at 1/20th the cost of the one alternative that also does (a human ghostwriter).

---

## Pricing

| Plan | Price | Key limits |
|---|---|---|
| **Free** | $0 | 5 generations to try, manual tool only |
| **Pro** | $29/mo | Unlimited, 3 profiles, full autopilot, carousels, analytics |
| **Agency** *(coming soon)* | $79/mo | Unlimited, 10 profiles, priority support |

**Value framing (Pro):** For a consultant, the comparison isn't "$29 vs a cheaper tool" — it's "$29 vs one more silent month of pipeline." One post that lands one discovery call pays for a year. Against a ghostwriter ($500–2000/mo) doing the same job, it's a rounding error.

---

## How the Tech Works

| Layer | Technology |
|---|---|
| **Backend** | Python / FastAPI |
| **AI generation** | Groq (LLaMA 3.3 70B) / Gemini, JSON mode |
| **News search** | Tavily Search API — real-time web search |
| **Carousel PDFs** | Pillow — 1080×1080 slides rendered server-side |
| **LinkedIn posting** | Official LinkedIn OAuth + REST API (v202503) |
| **Scheduling** | APScheduler — cron per profile, on an always-on instance |
| **Auth / billing** | Clerk (auth) · Razorpay (payments) · MongoDB |
| **Frontend** | Vanilla HTML/CSS/JS — zero frameworks, fast load |

---

## Common Objections & Responses

**"Won't LinkedIn ban my account?"**
> Voyce uses LinkedIn's official OAuth API — the same way Buffer and Hootsuite work. No browser automation, no cookie scraping.

**"I don't trust an AI to post to my profile unattended."**
> Start in preview mode — watch exactly what it would post for a few days before arming autopilot. Every post is logged with its source, you can edit or pause anytime, and an optional approve-before-post queue is on the roadmap.

**"The content will sound generic."**
> Voyce learns from your actual past posts (upload, paste, or screenshots) and your stated title, so it writes in your voice at your level of authority.

**"I can get voice + carousels cheaper (Supergrow is $19)."**
> True — if you want a tool that hands you drafts to schedule. Voyce is for the consultant who doesn't want to open the app at all: it finds the news, writes, and posts on its own. You're paying for the habit being gone, not for a better editor.

**"I already use ChatGPT."**
> ChatGPT makes you prompt, read, edit, open LinkedIn, and paste — every day. That's the daily friction that makes you stop. Voyce does the whole chain automatically at your scheduled time.

---

## Demo Flow (5 minutes)

1. **Landing page** (30s) — hero, features, pricing
2. **Dashboard** (1m) — profile card, content calendar ("it already knows what to post this week"), plan banner
3. **Run Now** (1.5m) — click Run, wait ~15s, show the generated post in Recent Activity, click through to the live LinkedIn post
4. **Manual tool** (1m) — paste a URL → LinkedIn/Twitter/email/blog outputs; download a carousel PDF
5. **Close on pricing** (30s) — "Free to try, no card. $29/mo for full autopilot. One client from one post pays for the year."

---

## What's Next (Roadmap)

- **Approval queue** — optional "review before post" mode for cautious users (trust feature; priority)
- **Always-on infrastructure** — move off Render free tier so daily autopilot never misses a slot *(see note below)*
- **Stripe** — international billing alongside Razorpay
- **Twitter/X auto-posting** — same autonomous loop for X
- **Profile-limit enforcement** per plan tier
- **LinkedIn analytics deep-dive** — follower growth, reach, profile views

---

## Contact

Built by **Rishav Agarwal**
Email: r65581350@gmail.com
