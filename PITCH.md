# Voyce — Pitch Document

---

## One-liner

**Voyce learns your writing style and posts to LinkedIn every day — automatically.**

---

## The Problem

LinkedIn is the #1 platform for B2B leads, hiring, and personal brand building.  
Everyone knows they should post. Almost no one does.

**Why:**
- Writing one good post takes 30–60 minutes
- You have to do it every single day to see results
- Generic AI tools (ChatGPT, etc.) still require you to prompt, write, edit, copy-paste — every day
- Ghostwriters cost ₹40,000–₹1,50,000/month
- Scheduling tools (Buffer, Hootsuite) only schedule — they don't write

**The result:** Founders, sales professionals, and consultants with real expertise stay invisible on LinkedIn because posting is too much work.

---

## The Solution — Voyce

Set up once in 3 minutes. Voyce posts every day, forever.

- Learns your voice from your past LinkedIn posts
- Finds today's relevant news in your industry
- Generates a post that sounds like you wrote it
- Posts it at your chosen time — automatically

No prompting. No copy-pasting. No ghostwriter.

---

## Who It's For

| Audience | Pain | What Voyce solves |
|---|---|---|
| **Founders / CEOs** | Want LinkedIn presence, no time | Daily posts without writing |
| **Sales professionals** | Need personal brand for pipeline | Consistent thought leadership, automated |
| **Consultants / Freelancers** | Visibility = income, but writing is slow | Posts every day in their expert voice |
| **Agencies** | Managing 5–10 client LinkedIn pages | Multi-profile automation from one dashboard |
| **Content marketers** | Repurposing content is tedious | URL/YouTube → 5 formats in 10 seconds |

---

## Features

### 1. Autonomous Daily Posting
- Set a post time once — Voyce posts every day at that time
- Rotating content schedule: Trend Reaction, Hot Take, Lesson Learned, Expert Insight, Stat Reaction, Personal Story — never repetitive
- Pulls fresh news from the web every day using Tavily Search API
- Works for both **company pages** and **personal brands**

### 2. Voice Learning
- Upload your LinkedIn data export (ZIP) or profile PDF
- Voyce reads your past posts and learns your exact writing style, vocabulary, sentence length, and tone
- All future posts are generated in your voice — not generic AI voice

### 3. Carousel PDF Generation
- Generates branded 5-slide LinkedIn carousel documents (1080×1080)
- Posted directly as LinkedIn documents — performs 3× better than text posts
- Available in both manual tool and autonomous posting
- Alternates with text posts automatically (carousel one day, text next day)

### 4. Manual Content Tool
- Paste text, drop a website URL, or share a YouTube link
- Voyce extracts key insights and generates:
  - **LinkedIn post** (long-form, ready to post)
  - **Twitter/X thread** (multi-tweet format)
  - **Email newsletter snippet**
  - **Blog intro paragraph**
  - **Carousel PDF**
- Post directly to LinkedIn or schedule for later from the tool

### 5. Content Calendar
- 7-day visual calendar showing what type of post is scheduled each day
- Shows text vs carousel alternation per profile
- Updates in real-time when profiles are added or edited

### 6. Post Analytics
- Full history of every post generated and posted
- Engagement tracking (likes, comments, reposts) via LinkedIn API
- Bar chart showing performance over last 14 posts
- Colour-coded by format: purple = carousel, teal = text

### 7. Multiple Profiles
- Company profile: scraped from website, professional voice
- Personal brand: learns from LinkedIn data upload, first-person voice
- Each profile has its own schedule, tone, industry, and post time
- Manage multiple profiles from one dashboard
- *(Per-plan profile limits enforced post-Stripe integration)*

### 8. Designation / Authority Context
- For personal profiles: add your title (e.g. "Head of Product at Knowella")
- AI uses this to write from the right level of authority
- Posts sound like they come from someone with real credibility

### 9. Onboarding Flow
- 3-step guided setup: Connect LinkedIn → Create profile → Preview your content calendar
- New users are live in under 3 minutes

### 10. Free Plan with Generation Limit
- 3 free generations — no credit card required
- Dashboard shows generations remaining with progress bar
- Upgrade prompt when limit is hit

---

## How the Tech Works (for technical audiences)

| Layer | Technology |
|---|---|
| **Backend** | Python / FastAPI |
| **AI generation** | Groq API (LLaMA 3.3 70B) with JSON mode — fast, reliable |
| **News search** | Tavily Search API — real-time web search |
| **Web scraping** | BeautifulSoup — scrapes company website for voice/context |
| **Carousel PDFs** | Pillow (Python image library) — 1080×1080 slides rendered server-side |
| **LinkedIn posting** | Official LinkedIn OAuth + REST API (v202503) |
| **Scheduling** | APScheduler — cron jobs per profile, fires at exact post time |
| **Auth** | Token-based auth, bcrypt-equivalent password hashing |
| **Frontend** | Vanilla HTML/CSS/JS — zero frameworks, fast load |

---

## Competitive Landscape

| Tool | Price | Writes for you | Auto-posts | Learns your voice | Carousel |
|---|---|---|---|---|---|
| **Buffer / Hootsuite** | $15–99/mo | ✗ | ✓ (manual queue) | ✗ | ✗ |
| **Taplio** | $49/mo | Partial | ✗ | ✗ | ✗ |
| **Authory** | $49/mo | ✗ | ✗ | ✗ | ✗ |
| **ChatGPT** | $20/mo | ✓ | ✗ | ✗ | ✗ |
| **Ghostwriter** | $500–2000/mo | ✓ | ✓ | ✓ | Sometimes |
| **Voyce** | $29/mo | ✓ | ✓ | ✓ | ✓ |

**Key differentiator:** Voyce is the only tool that combines voice learning + daily auto-posting + carousel generation at under $30/month.

---

## Pricing

| Plan | Price | Key limits |
|---|---|---|
| **Free** | $0 | 3 generations to try, manual tool only |
| **Pro** | $29/mo | Unlimited, 3 profiles, full automation, carousels, analytics |
| **Agency** | $79/mo | Unlimited, 10 profiles, priority support |

**Unit economics (Pro):**
- Replaces ~2 hours of writing/week = $80–200 in time saved
- Replaces a ghostwriter = $500–2000/month saved
- ROI is immediate at $29/month

---

## Demo Flow (5 minutes)

**Open with the problem (30 seconds):**
> "How many of you know you should post on LinkedIn more but just don't? The writing takes too long. Voyce fixes that."

**Step 1 — Show the landing page** (30 sec)
- Open `localhost:8000` — show the hero, features, pricing
- "3-minute setup, posts every day, learns your voice"

**Step 2 — Show the dashboard** (1 min)
- Profile card with industry, tone, post time
- Content calendar — "it already knows what to post every day this week"
- Plan banner showing generations used

**Step 3 — Run Now** (1.5 min)
- Click "Run Now" on the profile
- Wait 10–15 seconds
- Show the generated post in Recent Activity
- Click through to LinkedIn to show it was actually posted

**Step 4 — Show the manual tool** (1 min)
- Go to Manual Tool, paste a URL (any article)
- Click "Generate Content" — show LinkedIn, Twitter, Email, Blog outputs
- Click "Carousel PDF" — download and open the PDF

**Step 5 — Close with pricing** (30 sec)
> "Free to try — 3 generations, no card. $29/month for full automation. At 30 minutes saved per day, that's ROI in the first hour."

---

## Common Objections & Responses

**"Won't LinkedIn detect this and ban my account?"**
> Voyce uses LinkedIn's official OAuth API — the same way Buffer and Hootsuite work. LinkedIn explicitly allows third-party posting apps. Millions of posts go through the API daily.

**"The content will sound generic / AI-ish"**
> Upload your LinkedIn data export and Voyce reads your actual past posts to match your vocabulary and style. You can also add your designation so it writes from the right level of authority.

**"I need to review posts before they go live"**
> The manual tool is exactly for that — generate, edit, then post or schedule. The autonomous mode is for people who trust the output and want full hands-off automation.

**"What if the news context it finds is wrong or irrelevant?"**
> Voyce uses Tavily, a real-time web search API. It queries specifically for your industry + today's date. The post always includes what news was used, visible in your activity log.

**"I already use ChatGPT for this"**
> ChatGPT requires you to prompt it, read the output, edit it, open LinkedIn, paste it — every single day. Voyce does that entire chain automatically at your scheduled time.

**"$29 is too expensive"**
> One LinkedIn post leading to one client call or one hire pays for a year of Voyce. And it replaces 2+ hours of writing per week.

---

## What's Next (Roadmap to share)

- **Stripe payments** — live billing, plan upgrades in-app
- **Profile limits enforcement** — cap profiles per plan tier (1 / 3 / 10)
- **MongoDB migration** — for scale, concurrent users, persistent deployment
- **Twitter/X auto-posting** — same autonomous pipeline for X
- **Approval queue** — optional "review before post" mode for cautious users
- **Team collaboration** — multiple users managing the same profile
- **LinkedIn analytics deep-dive** — follower growth, reach, profile views

---

## Contact

Built by **Rishav Agarwal**  
Email: r65581350@gmail.com
