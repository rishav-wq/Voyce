import re

import partners
from llm import generate, generate_json, CLAUDE_EDIT_MODEL, CLAUDE_EDIT_EFFORT

# How much of a profile each writer is given (8 Nov 2026 audit). The facts-and-rules box holds 4,000
# characters (the partner setup's limit), but the writers took 2,000, so a long box lost its writing rules
# (all of Mandar's, six of Raj's). Example posts were cut at 600, so no writer ever saw how an author ends a
# post (their last line, their hashtags): about two in three examples were longer than that.
KNOWLEDGE_CHARS = 4000
EXAMPLE_CHARS = 1500

# Said wherever the box reaches a writer. Without it the general rules (130-260 words, no hashtags, end on
# the takeaway) beat the author's own, and lines quoted in the box as examples of a style came back as
# sentences ("the real question is quieter").
RULES_NOTE = ("These are the author's own rules. Where they differ from the general writing rules (length, "
              "lists, hashtags, how to end), follow the author's. A line quoted in them shows a style: never "
              "reuse it word for word, and never restate an argument from their past posts as new.")


def author_rules(company: dict | None) -> str:
    """The profile's facts and writing rules, whole, then their partner's house rules."""
    if not company:
        return ""
    return partners.with_house_rules((company.get("knowledge") or "").strip()[:KNOWLEDGE_CHARS], company)


def past_post_gist(post, part: int = 350) -> str:
    """A past post's opening and its ending, for the "don't repeat these" lists: its hook and where it
    lands. The first 300 characters alone missed the argument, so an idea restated an old post as new
    (Raj's 37signals post came back as "cloud vs no cloud is the wrong debate", 8 Nov 2026)."""
    text = " ".join(str(post or "").split())
    return text if len(text) <= 2 * part + 20 else f"{text[:part]} … {text[-part:]}"


def uses_hashtags(company: dict | None) -> bool:
    return any(re.search(r"(^|\s)#[A-Za-z]\w", str(p)) for p in (company or {}).get("linkedin_top_posts") or [])


def _lines(post) -> list[str]:
    return [ln.strip() for ln in str(post or "").splitlines() if ln.strip() and not ln.strip().startswith("#")]


def signature_lines(company: dict | None) -> list[str]:
    """Each example post's first line and last two lines: where an author's memorable phrases sit (their hook,
    their closing line). The writer echoed them even when told not to ("the business you've become")."""
    out = []
    for post in ((company or {}).get("linkedin_top_posts") or [])[:5]:
        ls = _lines(post)
        for ln in dict.fromkeys(ls[:1] + ls[-2:]):
            out.append(ln[:200])
    return out


def editor_brief(company: dict | None, source: str = "") -> str:
    """What the final editor needs to know. It used to see the draft alone, so its checklist beat the
    author's rules, "no hashtags unless the author's own posts use them" always stripped them (it had never
    seen the posts), and "cut any number that isn't from the source" couldn't work (it had never seen the
    source either: a figure Claude knew from memory went out unchecked)."""
    rules = author_rules(company)
    out = []
    if rules:
        out.append("THE AUTHOR'S OWN RULES (where they differ from your checklist, theirs win; keep what they "
                   "ask for, such as their length, lists, ending or hashtags):\n" + rules)
    if uses_hashtags(company):
        out.append("This author's own posts carry hashtags at the end: keep the post's hashtags, trimmed to any "
                   "limit their rules set.")
    sig = signature_lines(company)
    if sig:
        out.append("LINES FROM THE AUTHOR'S PAST POSTS (their own hooks and closing lines). The post must not repeat "
                   "any of them or reword one closely; rewrite any sentence that echoes one:\n"
                   + "\n".join(f"- {s}" for s in sig))
    if (source or "").strip():
        out.append("WHAT THE POST WAS WRITTEN FROM (check its facts against this):\n" + source.strip()[:4000] +
                   "\n\nA statistic, amount or date about the world (a study, survey, market figure or news event) "
                   "that is in neither this nor the author's rules stays only if the post says where it comes from "
                   "(the organisation or publication, and the year) and it is widely documented. Otherwise cut it "
                   "or make the sentence general. Facts about the author's own work and career are not for you to "
                   "check: leave them.")
    return ("\n\n".join(out) + "\n\n") if out else ""


# Common words: a five-word run of these ("one of the most important") is shared with half of LinkedIn, so it
# doesn't count as copying.
_COMMON = set("""a an the and or but nor of to in on for with at by from as into about over after before is are
was were be been being it its it's this that these those there here you your you're we our we're they their
they're i i'm my me he she his her them us not no so if then than what which who whom how why when where all
any each more most much many some such very just only also even still can could will would should may might
must do does did done have has had having get gets got one two""".split())


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", str(text or "").lower())


def lifted_phrases(post: str, company: dict | None, n: int = 5) -> list[str]:
    """Runs of n or more words the post shares word for word with the author's example posts, skipping runs
    made of common words. The examples teach a voice; a copied run is their old post, not a new one."""
    examples = (company or {}).get("linkedin_top_posts") or []
    words = _words(post)
    if not examples or len(words) < n:
        return []
    theirs = set()
    for ex in examples:
        w = _words(ex)
        theirs |= {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}
    hit = [False] * len(words)
    for i in range(len(words) - n + 1):
        g = tuple(words[i:i + n])
        if g in theirs and sum(x not in _COMMON for x in g) >= 2:
            for j in range(i, i + n):
                hit[j] = True
    spans, cur = [], []
    for w, h in zip(words, hit):
        if h:
            cur.append(w)
        elif cur:
            spans.append(" ".join(cur)); cur = []
    if cur:
        spans.append(" ".join(cur))
    return spans


_UNCOPY_PROMPT = """You fix one problem in a LinkedIn post: some phrases in it were copied word for word from the
author's earlier posts, so readers who follow them would see the same lines again. Rewrite only the sentences
that contain those phrases, saying the same thing in fresh words, in the same voice. Change nothing else: keep
every other line, every fact and number, the opening line's idea, the line breaks and any hashtags.
Return ONLY the post."""


def word_cap(company: dict | None) -> int | None:
    """The longest a post may be by the author's own rules: the largest upper bound they state ("180-250
    words", "never over 250"). The loosest one, so a long format they allow is never cut to a short one's size."""
    text = (company or {}).get("knowledge") or ""
    caps = [int(b) for _, b in re.findall(r"(\d{2,4})\s*(?:-|–|—|to)\s*(\d{2,4})\s*words", text)]
    caps += [int(n) for n in re.findall(r"(?:never|not|no)\s+(?:over|more than|longer than)\s+(\d{2,4})", text)]
    caps = [c for c in caps if 50 <= c <= 1000]
    return max(caps) if caps else None


def post_words(post: str) -> int:
    return len([w for w in str(post or "").split() if not w.startswith("#")])


_TRIM_PROMPT = """You trim a LinkedIn post to a word limit. Cut padding first: restated points, wind-up phrases,
a second closing line that repeats the first. Keep the opening line, every fact, number and named source, the
closing takeaway, any closing question and the hashtags, and keep the author's voice and line breaks.
Return ONLY the post."""


def fit_length(post: str, company: dict | None) -> str:
    """One trim pass when a post runs past the author's own longest length (8 Nov 2026: with "180-250 words"
    in his rules, Raj's drafts came out at 267; Mandar's at 296 against 280). Small overruns are left alone."""
    cap, n = word_cap(company), post_words(post)
    if not cap or n <= cap * 1.04:
        return post
    try:
        out = generate(f"Limit: {cap} words. The post has {n}.\n\nPost:\n\n{post}", system=_TRIM_PROMPT,
                       max_tokens=1024, temperature=0.3, model=CLAUDE_EDIT_MODEL, effort=CLAUDE_EDIT_EFFORT)
        out = (out or "").strip()
        return out if out and cap * 0.6 <= post_words(out) < n else post
    except Exception:
        return post


def uncopy(post: str, company: dict | None) -> str:
    """One pass that rewrites sentences copied from the author's example posts (8 Nov 2026 audit). The
    prompt rules alone let a signature line through now and then."""
    spans = lifted_phrases(post, company)
    if not spans:
        return post
    try:
        fixed = generate("Copied phrases:\n" + "\n".join(f"- {s}" for s in spans) + f"\n\nPost:\n\n{post}",
                         system=_UNCOPY_PROMPT, max_tokens=1024, temperature=0.5,
                         model=CLAUDE_EDIT_MODEL, effort=CLAUDE_EDIT_EFFORT)
        return fixed.strip() if fixed and len(fixed.strip()) > len(post) * 0.6 else post
    except Exception:
        return post


def _strip_markdown(text: str) -> str:
    """LinkedIn renders markdown literally — convert to plain text.
    Handles bullet lines, headings, bold/italic emphasis, and inline code."""
    # Bullet lines (*, -) → arrow bullets
    text = re.sub(r"^(\s*)[\*\-]\s+", r"\1→ ", text, flags=re.MULTILINE)
    # Headings (#, ##, …) → drop the marker, keep the text
    text = re.sub(r"^\s*#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Emphasis: ***x*** / **x** / *x* → keep inner text, drop asterisks
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    # Inline code backticks render as literal junk on LinkedIn
    text = text.replace("`", "")
    # Em/en-dashes are the #1 AI tell. Keep them ONLY inside number ranges (e.g. 85-90%);
    # everywhere else convert to a comma so the post reads like a human typed it.
    text = re.sub(r"(?<=\d)[ \t]*[—–][ \t]*(?=\d)", "-", text)   # 85–90% -> 85-90%
    text = re.sub(r"[ \t]*[—–][ \t]*", ", ", text)               # word — word -> word, word
    text = re.sub(r"[ \t]+,", ",", text)                          # tidy stray " ,"
    text = re.sub(r",[ \t]*,", ", ", text)                        # collapse ",,"
    return text


_NUMBERED = re.compile(r"#\s*\d+|\b(day|episode|ep|part|edition|issue|vol|volume|no|chapter|week|note)\.?\s*#?\s*\d+\b", re.I)


def _line_key(line: str) -> str:
    """A line's words only: "TRUSTED ADVISOR NOTES | #5" and "Trusted Advisor Notes #6" match."""
    return " ".join(re.findall(r"[a-z]+", (line or "").lower()))


def _furniture_keys(examples: list) -> set[str]:
    """Lines that belong to the author's own posts only: a numbered series header
    ("TRUSTED ADVISOR NOTES | #5", "Day 12 of...") or a header/sign-off that recurs
    across their posts ("Insight to Impact."). Hashtag lines don't count."""
    counts, keys = {}, set()
    for post in (examples or [])[:5]:
        lines = [ln.strip() for ln in str(post).splitlines() if ln.strip()]
        lines = [ln for ln in lines if not re.fullmatch(r"(#\w+[\s,]*)+", ln)]
        edges = lines[:2] + lines[-4:]
        keys |= {_line_key(ln) for ln in lines[:2] if len(ln) <= 60 and _NUMBERED.search(ln)}
        for k in {_line_key(ln) for ln in edges}:
            counts[k] = counts.get(k, 0) + 1
    keys |= {k for k, n in counts.items() if n >= 2}
    keys.discard("")
    return keys


def drop_author_furniture(text: str, company: dict | None) -> str:
    """Take out what the writer copied from the author's own posts that isn't voice: their
    series header (Anand's samples came out as "TRUSTED ADVISOR NOTES | #6", an episode he
    never wrote, numbered by the model), a recurring sign-off, or their name and title typed
    as a signature (LinkedIn already shows both above the post)."""
    if not text or not company:
        return text
    keys = _furniture_keys(company.get("linkedin_top_posts") or [])
    name = _line_key(company.get("name", ""))
    role = set(_line_key(company.get("designation", "")).split())
    lines = text.splitlines()
    out, after_name = [], False
    for i, ln in enumerate(lines):
        k = _line_key(ln)
        if after_name and k and not ln.strip().startswith("#"):
            after_name = False
            if len(set(k.split()) & role) >= 2:
                continue                                   # the title line under the name
        if k in keys and k:
            continue
        if name and k == name and i >= len(lines) - 6:
            after_name = True
            continue
        out.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()

SYSTEM_PROMPT = """You are an elite ghostwriter. You write social content that sounds like a specific
human wrote it — never like AI, never like a template.

You will receive source content and (sometimes) a voice profile with real posts the author wrote.
Your job: repurpose the source content into ONE LinkedIn post in the author's voice.

Return ONLY a valid JSON object with exactly this key:
{
  "linkedin_post": "..."
}

═══ LINKEDIN POST ═══

THE ONE RULE THAT MATTERS: the post must carry ONE clear idea the reader hasn't heard
phrased this way before. Find the most surprising, specific, or contrarian angle in the
source content and build everything around it.

OPENING LINE (the only text visible before "...see more" — decides whether the rest is read):
- HARD RULE: the first line is AT MOST ~90 characters: a plain statement, a result or a
  story moment (a specific number helps). Never a question: question openers do worst.
  "I deleted my best-performing post." beats "I recently made a decision about my content
  strategy that surprised me." Short first line + tension = the brain stops scrolling.
- It must make total sense alone and leave an open loop the reader needs closed.
- Two hook shapes proven to pull readers past the fold (pick whichever the source content
  actually supports — never force one):
    1. CONTRARIAN — position the author against conventional wisdom. Patterns:
       "Most [group] [common advice]. That's backwards. Here's what actually works:"
       "Everyone says [X]. After [real experience], I do the opposite."
       Rule: back it with a real specific. Empty hot takes with no substance now get demoted.
    2. SPECIFIC OUTCOME / NUMBER — concreteness manufactures credibility. Patterns:
       "[Specific result] in [timeframe]. Without [the thing everyone assumes you need]."
       "I [did X] [specific number] times. Here's the one thing that actually moved it."
       Rule: the body MUST pay off the specific, or it reads as clickbait.
- Otherwise: a named company/event, a confession, or a claim that sounds wrong until you read on.
- Write it the way the AUTHOR would say it, not the way a marketer would.
- No generic warm-ups ("I read something interesting", "In today's world").
- The hook opens the door — it does NOT carry the post alone. A great hook on a thin body
  still flops. Substance is what earns reach.

VOICE (this outranks every other rule):
- If voice examples are provided, study their rhythm, vocabulary, and quirks — and write
  like THAT person. Match how they open, how casual they are, whether they use emoji.
- Never fabricate first-person experience. No "I built / my team / I invested" unless that
  exact claim appears in the provided content or voice profile. Present-tense claims about
  their own work count too ("As a CFO, I see this every day", "my clients keep asking"):
  use the role to frame a view ("As a CFO, here's how I read this"), never to invent what
  they see or do at work.
- IF THE INPUT CONTAINS NO REAL STORY, DO NOT WRITE A STORY. A first-person narrative
  ("I spent three weeks...", "I stared at a dashboard...") with invented events, timelines,
  user counts, or outcomes is the single worst possible output — it publishes a lie under
  the author's real name. With no lived events in the input, write an opinion, observation,
  or teaching post instead. Fiction is never a fallback.
- BUT where the source DOES support it, lean into first-person and real, specific experience
  ("I spent last week…", "I found…", "I kept seeing…"). Don't flatten a genuine personal story
  into detached, third-person analysis. Personal and specific beats analytical and general.
- Attribute third-party material ("According to X…", "Y's report shows…").

FACTUAL SAFETY (a fabricated specific that a reader can falsify destroys credibility):
- Never invent exact statistics ("70% of teams"), product version numbers ("DeepSeek-V3.2",
  "GPT-5.1"), dates, funding figures, or named studies just to sound authoritative.
- Only cite facts present in the source content, or things you are genuinely confident are real.
- Keep every fact exactly as strong as the source has it, in the hook too. "Up to 70%" never
  becomes "70%"; "expected to", "will" or "from November 9" never becomes "just did". A
  shorter hook that changes what the fact says is a false hook.
- When unsure, hedge instead of fabricating: "models like DeepSeek" not "DeepSeek-V3.2";
  "most teams" not "70% of teams"; "recent benchmarks" not a made-up percentage.
- NEVER invent how a platform or algorithm works: no made-up thresholds, mechanisms, or rules
  ("posts liked in under 3 seconds count as low-value", "the algorithm flags X after Y seconds").
  If a specific mechanism isn't in the source, state the idea generally or leave it out. A
  confident invented mechanic is the fastest way to get publicly corrected and lose all trust.

WRITING CRAFT:
- Vary your rhythm. Mix short punchy lines with longer natural sentences — text that is
  ALL short staccato lines reads as AI-generated in 2026 and gets scrolled past.
- PLAIN ENGLISH, never a whitepaper. Write the way a smart person actually talks, not the way a
  research paper reads. Use contractions (I'm, don't, it's, that's). Skip academic/corporate register.
- SIMPLICITY IS A HARD RULE, not a style preference: every word must be one a 15-year-old uses
  daily. If a simpler word exists, the simpler word wins ("use" not "utilize", "help" not
  "facilitate", "big" not "substantial"). Most sentences under 12 words. One idea per sentence.
  If a sentence needs a second read, rewrite it. Readers give you seconds, not effort.
- Concrete beats abstract in every sentence: real names, real numbers, real examples.
  Generic motivational lines and quote-style platitudes are treated as low-quality filler —
  every sentence must come from the author's actual experience or the source content.
- THE NOVELTY TEST (the bar that separates "fine" from "worth following"): the post must teach
  a sharp reader at least ONE thing they likely don't already know — a fresh number, a
  development they missed, a named mechanism, a counterintuitive claim you then support.
  If every sentence states something the reader already believes, the post is filler:
  find the one non-obvious point in the source and build the post around IT.
- AT LEAST ONE RECEIPT: every post carries one concrete, checkable specific from the source
  or author's material — a stat with its source, a named company/tool/event, a real artifact.
  Never invented. A post with zero receipts is an opinion column, and feeds are drowning in those.
- NEVER STACK TRUISMS: two or more consensus statements in a row ("focus beats volume",
  "quality over quantity", "consistency is key") is the single worst output this tool can
  produce. One consensus line is allowed ONLY as setup for a specific or contrarian payoff.
- HOLD ATTENTION (dwell time is a confirmed reach signal — the longer people read, the wider
  LinkedIn distributes): one idea per line, blank line between paragraphs, generous whitespace,
  and a shape that keeps pulling the eye down — a narrative arc, a numbered list of specifics,
  or a clear before→after. No wall of text.
- FIRST PICK THE SHAPE, then write to it. Decide by the content:
    • LIST post (multiple parallel items: tips, steps, findings, reasons, a framework):
      hook (1-2 lines) -> the points as a clean NUMBERED LIST or short bullet lines (one idea
      each, a short bold-ish label + at most one supporting line, NOT buried in paragraphs) ->
      1-2 line takeaway, and end there.
    • STORY post (ONE experience unfolding over time: a failure, a lesson, a moment, a
      before/after journey): write it as flowing SHORT PARAGRAPHS. Do NOT chop it into a numbered
      list. Let it breathe: tension, then the turn, then the lesson. A story forced into "1, 2, 3"
      loses all its power.
  Multiple parallel items = LIST. One thing happening over time = STORY. VARY the shape across
  posts, never make every post the same numbered-list template (LinkedIn demotes templated formats).
- LENGTH: aim for 130-260 words (about 800-1,600 characters), the range where posts do
  best, and let the material set it. A real process, story or set of numbers fills it; one
  sharp point can stop near 100 words. Never pad: a line that adds no fact, step, example or
  turn gets cut. Past 300 words only for a step-by-step process. Never summarize yourself at
  the end or restate the hook as a closer.
- SAVE-WORTHY: build the post so a reader thinks "I need this later" — a usable framework, a
  checklist, a concrete before→after. That reader keeps reading (dwell) and comes back.
- Keep it self-contained: no promotional URLs in the body (off-platform links drag reach —
  a link belongs in the first comment, not the post).
- END ON A STATEMENT by default: the takeaway, the sharpest claim, or the last point
  landing. Top posts almost never close on a question. Ask one only when it's specific and
  the post has earned it (it asks for something readers actually know and want to share),
  never as a reflex. NEVER directive engagement-bait ("Comment YES", "Tag someone",
  "Agree?", "Repost if…") — LinkedIn auto-suppresses it.
- No hashtags: top posts don't use them and they add no reach. (Only if the author's own
  example posts use them, follow the author.)

NEVER USE (instant AI tells):
- "It's not about X. It's about Y." or any "It's not X — it's Y" construction
- "Here's the thing:" / "Let that sink in" / "Read that again" / "The result?"
- Three-item lists in every paragraph (the rule-of-three tic)
- game-changer, landscape, unlock, leverage, dive deep, revolutionize, thrilled, excited
  to share, in today's fast-paced world, at the end of the day, paradigm, synergy,
  move the needle, learnings, impactful, groundbreaking, "the future of X is here",
  unleash, supercharge, 10x, "this will change everything"
- Starting consecutive paragraphs with the same word
- Opening with "Most [people / teams / CFOs / founders]...": the most overused hook shape.
- Corporate/academic register: programmatically, systemically, mimics, "information density",
  furthermore, moreover, "Ultimately,", "it is worth noting", "the takeaway is simple". Say it
  like a human instead.
- Em-dashes (—) or en-dashes (–): the SINGLE biggest AI tell. Use commas, periods, or
  parentheses instead. A plain hyphen is fine only inside a number range (e.g. 85-90%)."""


HUMANIZE_PROMPT = """You are the final editor before publishing. Your only job: remove every
trace of AI writing from this LinkedIn post while keeping its substance and voice.

Hunt and fix:
1. EM-DASHES and en-dashes (— –): the biggest AI tell of all. Replace EVERY one with a comma,
   a period, or parentheses. A plain hyphen is fine only inside a number range like 85-90%.
2. "It's not X. It's Y." constructions: rewrite as a direct statement.
3. Relentless staccato (every sentence under 10 words): merge some into natural longer sentences.
4. Rule-of-three tics (triads in every paragraph): break the pattern.
5. Directive engagement-bait ("Comment YES", "Tag someone who…", "Agree?", "Repost if…",
   "Let that sink in", "What do you think?"): LinkedIn suppresses these. End on the takeaway
   instead. Keep a closing question only if it is specific and genuinely answerable.
6. Banned vocabulary: game-changer, unlock, leverage, landscape, dive deep, thrilled,
   excited, paradigm, synergy, learnings, impactful, groundbreaking.
7. Generic platitudes / quote-style filler ("consistency is key", "success takes hard work")
   and any sentence that could appear in any post about any topic: make it specific or cut it.
8. Consecutive paragraphs opening with the same word.
9. Promotional URLs in the body: remove them (they drag reach); keep the post self-contained.
10. Fabricated specifics: any exact number, threshold, date, or "how the algorithm works" claim
    that isn't clearly from the source. Cut it or make it general. ONE made-up stat destroys the
    whole post's credibility, this is the most important check.
10b. Borrowed authority: when the input is someone else's article, report, or data, the post
    must NAME the source in the body ("Forbes' breakdown...", "per HubSpot's report"). Stating
    another outlet's findings in the author's own voice, as if they discovered it, reads as
    stolen expertise the moment anyone checks. Name the source once, naturally, no URL needed.
11. Corporate/academic register (programmatically, systemically, mimics, "information density",
    "Ultimately,", furthermore): rewrite in plain, spoken English with contractions.
12. LENGTH: cut padding, never substance. A line that adds no fact, step, example or turn
    goes, and so do self-summaries, restated hooks and closers that rephrase the point. Past
    ~300 words, cut toward 130-260. Never shorten a post that is all substance just to make it
    short. End on the strongest sentence, not a wind-down.
13. COMPLEX SENTENCES: any sentence over ~18 words or needing a second read gets split or
    simplified. Any word a 15-year-old wouldn't use daily gets swapped for the plain one.
14. WEAK HOOK: if the first line runs past ~90 characters, tighten it until it hits. If it
    is a question, make it a statement. Move the
    tension forward; cut wind-up words. Never cut a qualifier while tightening: "up to",
    "about", "expected" and "will" stay, and something that hasn't happened yet stays future.
15. NO NEWS TEST: read the post and ask "what does a sharp reader learn that they didn't
    already know?" If the answer is nothing — every line restates consensus (focus beats
    volume, quality over quantity, alignment matters) — the post FAILS. Rebuild it around
    the single most non-obvious specific in the source. Two consensus lines in a row is an
    automatic rewrite; a post with zero checkable specifics (no number, no name, no artifact)
    is an automatic rewrite.

Preserve:
- The opening line's claim (you may sharpen its wording, not change its idea)
- All facts, names, numbers — never add new ones
- Blank lines between paragraphs (and no hashtags, unless the author's own posts use them)
- The author's voice and any human quirks already present

Return ONLY the edited post text — no commentary."""


def _build_voice_block(company: dict | None) -> str:
    """Assemble the voice profile section of the prompt from a saved profile."""
    if not company:
        return ""
    parts = []
    is_personal = company.get("profile_type") == "personal"
    who = f"{company.get('name', '')}" + (f", {company['designation']}" if company.get("designation") else "")
    parts.append(f"Author: {who} ({'personal brand — first person' if is_personal else 'company page'})")
    if company.get("tone"):
        parts.append(f"Preferred tone: {company['tone']}")
    if (company.get("profile_facts") or "").strip():
        parts.append("Their career (from their LinkedIn profile; real background to draw on when it fits, "
                     "never embellished):\n" + company["profile_facts"].strip()[:8000])

    li = company.get("linkedin_analysis", {})
    if li.get("writing_style"):
        parts.append(f"Writing style: {li['writing_style']}")
    if li.get("post_style_summary"):
        parts.append(f"Style summary: {li['post_style_summary']}")
    if li.get("avoid_topics"):
        parts.append(f"Never write about: {', '.join(li['avoid_topics'])}")

    top_posts = company.get("linkedin_top_posts", [])
    if top_posts:
        if company.get("tone_shift") and company.get("tone"):
            # Deliberate, user-requested shift: keep the person, move the register.
            parts.append(f"\nREAL POSTS BY THIS AUTHOR — study these to learn their vocabulary, "
                         f"quirks, and signature moves, dialed toward a '{company['tone']}' register. "
                         f"Match the VOICE, never the content:")
        else:
            parts.append("\nREAL POSTS BY THIS AUTHOR — study these to learn the VOICE only:")
        for i, post in enumerate(top_posts[:5], 1):
            parts.append(f"--- Example {i} ---\n{post[:EXAMPLE_CHARS]}")
        # The examples teach how the author sounds, not what to say. Without this the
        # model lifts hooks verbatim (it reused "my last 7 posts bombed" across posts).
        parts.append("\nHOW TO USE THESE EXAMPLES: copy the author's sentence rhythm, vocabulary, "
                     "tone, and structure. NEVER reuse their hooks, opening lines, or any specific "
                     "phrase or sentence from them. Every post must open differently from all the "
                     "examples above. If a line you are about to write appears in an example, rewrite it. "
                     "Leave out any series title, episode number or sign-off they put on their own "
                     "posts (\"X NOTES | #5\", \"Day 12 of...\"): those belong to the posts they write, "
                     "and a number you add is made up. Don't sign off with their name or title.")

    return "\n".join(parts)


def generate_content(raw_text: str, company: dict = None) -> dict:
    voice_block = _build_voice_block(company)
    voice_section = f"\n\nVOICE PROFILE:\n{voice_block}" if voice_block else ""

    draft = generate_json(
        # main.py has cut the source to 6,000 before putting the profile's context (up to ~4,800 with a
        # full rules box) in front of it, so this cap only guards other callers
        f"Source content to repurpose:\n\n{raw_text[:11500]}{voice_section}",
        system=SYSTEM_PROMPT,
        max_tokens=2048,
        temperature=0.85,
    )

    # Humanize pass on the LinkedIn post only. It sees what the post was written from (main.py puts the
    # source after "Content to repurpose:"), so it can check the post's facts against it.
    source = raw_text.split("Content to repurpose:\n", 1)[-1]
    try:
        post = draft.get("linkedin_post", "")
        if post:
            edited = generate(
                f"{editor_brief(company, source)}Post to edit:\n\n{post}",
                system=HUMANIZE_PROMPT,
                max_tokens=1024,
                temperature=0.6,
                model=CLAUDE_EDIT_MODEL, effort=CLAUDE_EDIT_EFFORT,   # better and cheaper than Opus here (llm.py)
            )
            if edited:
                draft["linkedin_post"] = edited
    except Exception:
        pass  # keep the draft if humanize fails

    if draft.get("linkedin_post"):
        draft["linkedin_post"] = drop_author_furniture(_strip_markdown(draft["linkedin_post"]), company)
        draft["linkedin_post"] = _strip_markdown(fit_length(uncopy(draft["linkedin_post"], company), company))
    return draft
