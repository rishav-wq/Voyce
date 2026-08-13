// ── Auth (Clerk) ──────────────────────────────────────────────────────────────
let _clerkToken = "";

async function _refreshToken() {
  if (window.Clerk?.session) _clerkToken = await window.Clerk.session.getToken();
}

async function initClerk() {
  const clerk = window.Clerk;
  await clerk.load();
  if (!clerk.user) { window.location.href = "/login"; return false; }
  await _refreshToken();
  setInterval(_refreshToken, 50000);
  // Background tabs get their timers throttled and sleep pauses them entirely,
  // so also refresh the moment the user comes back to the tab.
  window.addEventListener("focus", _refreshToken);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) _refreshToken(); });
  try {
    const res = await fetch("/auth/me", { headers: { "x-token": _clerkToken } });
    if (res.ok) localStorage.setItem("cm_user", JSON.stringify(await res.json()));
  } catch (_) {}
  return true;
}

function getToken() { return _clerkToken; }

// ── Writing-as profile picker ────────────────────────────────────────────────
let _profiles = [];
function getActiveProfileId() { return localStorage.getItem("cm_profile_id") || ""; }
function setActiveProfile(id) { localStorage.setItem("cm_profile_id", id); }
async function loadProfilePicker() {
  try {
    const res = await fetch("/companies", { headers: { "x-token": getToken() } });
    if (!res.ok) return;
    _profiles = await res.json();
    if (!_profiles.length) return;
    let active = getActiveProfileId();
    if (!_profiles.some(p => p.id === active)) active = _profiles[0].id;
    setActiveProfile(active);
    const wrap = document.getElementById("writing-as");
    const sel  = document.getElementById("profile-picker");
    if (wrap && sel && _profiles.length >= 2) {  // only worth a picker with 2+ profiles
      sel.innerHTML = _profiles.map(p =>
        `<option value="${p.id}" ${p.id === active ? "selected" : ""}>${p.name}${p.profile_type === "personal" ? "" : " · company"}</option>`
      ).join("");
      wrap.style.display = "flex";
    }
    // Idea suggestions work with even a single profile
    const ideasRow = document.getElementById("ideas-row");
    if (ideasRow) ideasRow.style.display = "";
    applyProfileScopedUI();
  } catch (_) {}
}

// Ergonomics/EHS work (KnowErgo assessments, the Knowella brand deck, skeleton and
// footage heroes) is vertical-specific. Showing it on every profile makes the tool
// look like someone else's product to a consultant or fractional exec, so these
// controls only appear when the selected profile actually lives in that world.
const _ERGO_HINTS = ["ergo", "knowella", "knowergo", "safety", "ehs", "occupational",
                     "workplace injury", "physio", "musculoskeletal", "industrial"]

function isErgoProfile() {
  const p = _profiles.find((x) => x.id === getActiveProfileId()) || _profiles[0]
  if (!p) return false
  const hay = `${p.name || ""} ${p.industry || ""} ${p.designation || ""}`.toLowerCase()
  return _ERGO_HINTS.some((k) => hay.includes(k))
}

function applyProfileScopedUI() {
  const ergo = isErgoProfile()
  const block = document.getElementById("ergo-block")
  if (block) {
    block.style.display = ergo ? "" : "none"
    if (!ergo) block.open = false
  }
  for (const id of ["theme-opt-knowella", "fmt-opt-posture", "fmt-opt-photo"]) {
    const opt = document.getElementById(id)
    if (opt) opt.hidden = !ergo
  }
  // A hidden option can still be the current value after a profile switch — reset it.
  if (!ergo) {
    const theme = document.getElementById("carousel-theme")
    if (theme && theme.value === "knowella_deep") theme.value = ""
    const fmt = document.getElementById("carousel-format")
    if (fmt && (fmt.value === "posture" || fmt.value === "photo")) fmt.value = "standard"
  }
}

function _escHtmlCreate(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ── Post ideas on Create: pillar-spanning, grounded in live news ──────────────
let _createIdeas = [];
async function suggestIdeasCreate() {
  const out = document.getElementById("ideas-out");
  const pid = getActiveProfileId() || (_profiles[0] && _profiles[0].id);
  if (!pid) { toast("Create a profile first, then I can suggest ideas.", "warn"); return; }
  out.innerHTML = `<div style="font-size:13px;color:#8a8272;">Reading today's news and drafting ideas… (~10s, nothing gets posted)</div>`;
  try {
    const res = await fetch(`/companies/${pid}/ideas`, { method: "POST", headers: { "x-token": getToken() } });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Couldn't get ideas right now.");
    _createIdeas = data.ideas || [];
    if (!_createIdeas.length) { out.innerHTML = `<div style="font-size:13px;color:#8a8272;">No ideas came back — try again in a moment.</div>`; return; }
    out.innerHTML = _createIdeas.map((it, i) => `
      <div style="border:1.5px solid #d3d8e0;border-radius:10px;padding:12px 14px;margin-bottom:8px;background:#fff;">
        <div style="font-size:14px;font-weight:600;color:#16181d;line-height:1.35;">${_escHtmlCreate(it.hook)}</div>
        <div style="font-size:12px;color:#8a8272;margin-top:4px;">
          <span style="color:#24365e;font-weight:600;">${_escHtmlCreate(it.post_type_label || "Post")}</span>${it.why ? " · " + _escHtmlCreate(it.why) : ""}${it.source ? " · 📰 " + _escHtmlCreate(it.source) : " · evergreen"}
        </div>
        <button type="button" class="img-src-btn" style="margin-top:10px;" onclick="useIdea(${i})">Use this idea →</button>
      </div>`).join("");
  } catch (e) {
    out.innerHTML = `<div style="font-size:13px;color:#c0392b;">${_escHtmlCreate(e.message)}</div>`;
  }
}

function useIdea(i) {
  const it = _createIdeas[i];
  if (!it) return;
  const ta = document.getElementById("content-input");
  if (!ta) return;
  // Switch to Paste Text mode so the seed generates correctly (mirrors applySeedTopic)
  const textTab = document.querySelector('.input-tab[data-type="text"]');
  if (textTab) {
    document.querySelectorAll(".input-tab").forEach(t => t.classList.remove("active"));
    textTab.classList.add("active");
    activeType = "text";
  }
  const parts = [`Write a ${it.post_type_label || "LinkedIn"} post about: ${it.hook}.`];
  if (it.why) parts.push(it.why);
  if (it.source) parts.push(`It should react to this from the news: "${it.source}".`);
  ta.value = parts.join(" ");
  ta.scrollIntoView({ behavior: "smooth", block: "center" });
  generate();
}
function getUser()  { try { return JSON.parse(localStorage.getItem("cm_user") || "null"); } catch { return null; } }
function authHeaders(extra) { return { "Content-Type": "application/json", "x-token": getToken(), ...(extra||{}) }; }

let activeType = "text";
let linkedInConnected = false;

function isDryRun() { return false; }

// ── Toast ─────────────────────────────────────────────────────────────────────
let _toastTimer;
function toast(msg, type = "") {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.className = "show " + type;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.className = "", 3500);
}

function showError(msg) {
  const el = document.getElementById("error-banner");
  if (!el) { toast(msg, "error"); return; }
  if (msg === "LIMIT_REACHED") {
    el.innerHTML = `You've used all 5 free generations. <a href="#" onclick="openUpgradeModal();return false;" style="color:#24365e;font-weight:700;">Upgrade to Pro</a> for unlimited generations.`;
  } else {
    el.textContent = msg;
  }
  el.style.display = "block";
  setTimeout(() => el.style.display = "none", 10000);
}

// ── Init (called by Clerk script onload) ─────────────────────────────────────
async function startApp() {
  if (!(await initClerk())) return;
  loadProfilePicker();
  const user = getUser();
  if (user) {
    const tag = document.getElementById("user-tag");
    if (tag) tag.textContent = user.name || user.email;
  }
  checkLinkedInStatus();
  loadQueue();
  updateProgress();
  applySeedTopic();
  restoreDraft();   // bring back the last generated post after a refresh
}

// First-run handoff from onboarding: pre-fill the generator with the user's topic
function applySeedTopic() {
  const seed = localStorage.getItem("cm_seed_topic");
  if (!seed) return;
  localStorage.removeItem("cm_seed_topic");
  const ta = document.getElementById("content-input");
  if (!ta) return;
  // Switch to text mode so the pre-filled brief generates correctly
  const textTab = document.querySelector('.input-tab[data-type="text"]');
  if (textTab) {
    document.querySelectorAll(".input-tab").forEach(t => t.classList.remove("active"));
    textTab.classList.add("active");
    activeType = "text";
  }
  ta.value = `Write a specific, useful LinkedIn post about ${seed}.`;
  ta.focus();
  ta.scrollIntoView({ behavior: "smooth", block: "center" });
  setTimeout(() => toast("We pre-filled your topic — hit Generate to see your first post.", "success"), 600);
}

window.addEventListener("message", (e) => {
  if (e.data === "linkedin_connected") {
    linkedInConnected = true;
    updateLiPill(true);
    toast("LinkedIn connected!", "success");
  } else if (e.data === "linkedin_error") {
    toast("LinkedIn connection failed. Try again.", "error");
  }
});

// ── LinkedIn pill ─────────────────────────────────────────────────────────────
async function checkLinkedInStatus() {
  try {
    const res = await fetch("/auth/linkedin/status", { headers: { "x-token": getToken() } });
    const data = await res.json();
    linkedInConnected = data.connected;
    updateLiPill(data.connected);
  } catch (_) {}
}

function updateLiPill(connected) {
  const pill = document.getElementById("li-pill");
  const text = document.getElementById("li-pill-text");
  if (!pill) return;
  pill.classList.toggle("connected", connected);
  text.textContent = connected ? "LinkedIn Connected" : "Connect LinkedIn";
  updateProgress();
}

// ── Onboarding progress strip ───────────────────────────────────────────────
// Step 1 reflects the ACTUAL on-screen output (a LinkedIn post draft or a carousel),
// not a persistent flag — so it stays truthful across generate / clear / refresh.
function updateProgress() {
  const gen  = document.getElementById("pstep-generate");
  const li   = document.getElementById("pstep-linkedin");
  const auto = document.getElementById("pstep-automate");
  if (!gen || !li) return;
  const liText = document.getElementById("linkedin-content");
  const carSec = document.getElementById("carousel-section");
  const hasGen = !!(liText && liText.textContent.trim()) ||
                 !!(carSec && carSec.classList.contains("visible"));

  // Linear onboarding: a step turns "done" only once it AND every step before it is
  // complete; the "active" step is always the first one still incomplete. This keeps the
  // strip truthful — e.g. LinkedIn being connected doesn't jump ahead of an ungenerated post.
  const step1done = hasGen;
  const step2done = step1done && linkedInConnected;

  gen.classList.toggle("done", step1done);
  gen.classList.toggle("active", !step1done);

  li.classList.toggle("done", step2done);
  li.classList.toggle("active", step1done && !step2done);

  if (auto) {
    auto.classList.toggle("active", step2done);   // becomes the next step once post + LinkedIn are done
    auto.classList.remove("done");                // finishing setup is confirmed on /setup, not here
  }
}
function markGenerated() { updateProgress(); }

async function handleLiClick() {
  if (linkedInConnected) {
    if (!(await voyceConfirm("Disconnect LinkedIn from Voyce?", { confirmText: "Disconnect", danger: true }))) return;
    fetch("/auth/linkedin/logout", { method: "POST", headers: { "x-token": getToken() } });
    linkedInConnected = false;
    updateLiPill(false);
    toast("LinkedIn disconnected.");
  } else {
    connectLinkedIn();
  }
}

async function connectLinkedIn() {
  // Open the popup synchronously so the browser keeps the user-gesture context
  // (a popup opened after an awaited fetch would be blocked). The session token
  // goes in a header via /auth/linkedin/start — never in the URL.
  const popup = window.open("", "linkedin-auth", "width=600,height=700,scrollbars=yes");
  if (!popup) { toast("Popup blocked — please allow popups for this site.", "warn"); return; }
  try {
    const res = await fetch("/auth/linkedin/start", { method: "POST", headers: { "x-token": getToken() } });
    if (!res.ok) throw new Error();
    const { auth_url } = await res.json();
    popup.location.href = auth_url;
  } catch {
    popup.close();
    toast("Could not start LinkedIn connect. Please try again.", "warn");
  }
}

// ── Input tabs ────────────────────────────────────────────────────────────────
document.querySelectorAll(".input-tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".input-tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    activeType = tab.dataset.type;
    const placeholders = {
      text: "Paste your blog post, article, notes, or any content here…",
      url: "https://example.com/blog/your-article",
      youtube: "https://www.youtube.com/watch?v=…"
    };
    document.getElementById("content-input").placeholder = placeholders[activeType];
  });
});

// ── Generation progress ───────────────────────────────────────────────────────
let _genTimer = null;

function startGenProgress(stages) {
  const panel   = document.getElementById("gen-progress");
  const stageEl = document.getElementById("gen-stage");
  const bar     = document.getElementById("gen-bar");
  if (!panel) return;
  let stageIdx = 0, pct = 6, elapsed = 0;
  const stageEvery = 4500;
  stageEl.textContent = stages[0];
  bar.style.width = pct + "%";
  panel.classList.add("visible");
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  clearInterval(_genTimer);
  _genTimer = setInterval(() => {
    elapsed += 400;
    pct = Math.min(92, pct + (92 - pct) * 0.035);  // ease toward 92%, never finish on its own
    bar.style.width = pct + "%";
    const idx = Math.min(stages.length - 1, Math.floor(elapsed / stageEvery));
    if (idx !== stageIdx) {
      stageIdx = idx;
      stageEl.style.opacity = 0;
      setTimeout(() => { stageEl.textContent = stages[stageIdx]; stageEl.style.opacity = 1; }, 200);
    }
  }, 400);
}

function stopGenProgress() {
  clearInterval(_genTimer);
  _genTimer = null;
  const panel = document.getElementById("gen-progress");
  const bar   = document.getElementById("gen-bar");
  if (!panel) return;
  bar.style.width = "100%";
  setTimeout(() => { panel.classList.remove("visible"); bar.style.width = "0%"; }, 350);
}

// ── Generate ──────────────────────────────────────────────────────────────────
async function generate() {
  const content = document.getElementById("content-input").value.trim();
  if (!content) { showError("Please enter some content first."); return; }
  await _refreshToken();  // token may be stale after sleep/background throttling

  const btn     = document.getElementById("generate-btn");
  const btnText = document.getElementById("btn-text");
  const btnLoad = document.getElementById("btn-loader");

  btn.disabled = true;
  btnText.style.display = "none";
  btnLoad.style.display = "inline";

  document.getElementById("error-banner").style.display = "none";
  document.getElementById("output-section").classList.remove("visible");

  const fetchStage = { text: "Reading your content…", url: "Fetching the article…", youtube: "Reading the video transcript…" }[activeType];
  startGenProgress([
    fetchStage,
    "Analyzing tone & writing style…",
    "Drafting your LinkedIn post…",
    "Making it sound like you…",
    "Polishing the hook…",
    "Almost there…",
  ]);

  let textOk = false;
  try {
    const res = await fetch("/generate", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ input_type: activeType, content, profile_id: getActiveProfileId() })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Something went wrong.");
    renderOutputs(data);
    document.getElementById("output-section").classList.add("visible");
    markGenerated();
    document.getElementById("output-section").scrollIntoView({ behavior: "smooth", block: "start" });
    textOk = true;
  } catch (err) {
    showError(err.message);
  } finally {
    stopGenProgress();
    btn.disabled = false;
    btnText.style.display = "inline";
    btnLoad.style.display = "none";
  }

  // If the "Add an AI image" toggle is on, create the illustration from the post we just made.
  // The post is already on screen and the button is re-enabled — the image fills in with its own loader.
  if (textOk && document.getElementById("add-image-toggle")?.checked) {
    await attachGenerateImage();
  }
}

// ── Draft persistence (survive a page refresh) ──────────────────────────────────
const _DRAFT_KEY = "cm_draft";
function _setPreviewAuthor() {
  const u = getUser();
  const name = (u && (u.name || u.email)) || "You";
  const av = document.getElementById("li-avatar");
  const nm = document.getElementById("li-name");
  if (av) av.textContent = (name.trim()[0] || "Y").toUpperCase();
  if (nm) nm.textContent = name;
}
function saveDraft() {
  const post = document.getElementById("linkedin-content")?.textContent || "";
  if (!post) { localStorage.removeItem(_DRAFT_KEY); return; }
  try {
    localStorage.setItem(_DRAFT_KEY, JSON.stringify(
      _attachB64 ? { post, img: _attachB64, mime: _attachMime } : { post }));
  } catch (_) {
    try { localStorage.setItem(_DRAFT_KEY, JSON.stringify({ post })); } catch (_) {}  // image too big → keep text
  }
}
function restoreDraft() {
  let d; try { d = JSON.parse(localStorage.getItem(_DRAFT_KEY) || "null"); } catch (_) { d = null; }
  const el = document.getElementById("linkedin-content");
  if (!d || !d.post || !el) return;
  el.textContent = d.post;
  _makeEditable();
  _applyFold();
  _setPreviewAuthor();
  if (d.img) {
    _attachB64 = d.img; _attachMime = d.mime || "image/png";
    _renderAttachment("data:" + _attachMime + ";base64," + d.img);
  }
  const sec = document.getElementById("output-section");
  if (sec) sec.classList.add("visible");
  updateProgress();
}

// Simulate LinkedIn's "...see more" fold so the preview shows exactly what
// survives in the feed before a tap — the hook test, live.
function _foldHeight(el) {
  const cs = getComputedStyle(el);
  const lh = parseFloat(cs.lineHeight) || 23;
  const pt = parseFloat(cs.paddingTop) || 0;
  return Math.round(lh * 3 + pt + 2);   // ~3 text lines, like the real feed
}
function _applyFold() {
  const el  = document.getElementById("linkedin-content");
  const btn = document.getElementById("li-see-more");
  if (!el || !btn) return;
  el.classList.add("li-clamp");
  el.style.maxHeight = _foldHeight(el) + "px";
  btn.textContent = "…see more";
  requestAnimationFrame(() => {
    const folded = el.scrollHeight > el.clientHeight + 4;
    btn.style.display = folded ? "" : "none";
    if (!folded) { el.classList.remove("li-clamp"); el.style.maxHeight = ""; }
  });
}
function toggleFold() {
  const el  = document.getElementById("linkedin-content");
  const btn = document.getElementById("li-see-more");
  const nowClamped = el.classList.toggle("li-clamp");
  el.style.maxHeight = nowClamped ? _foldHeight(el) + "px" : "";
  btn.textContent = nowClamped ? "…see more" : "see less";
}

function renderOutputs(data) {
  document.getElementById("linkedin-content").textContent = data.linkedin_post || "";
  _makeEditable();
  _applyFold();
  _setPreviewAuthor();
  if (!_attachIsUpload) attachRemove();   // keep a user-uploaded image; clear AI-generated ones
  saveDraft();
  _pushHistory(data.linkedin_post || "");
  // Fresh post → the old rewrites and hashtag suggestions belong to the old text
  document.getElementById("variants-row").style.display = "none";
  window._tagPool = null;
  hideHashtags();
  updateProgress();
}

// ── Editable post: the preview IS the editor (posting reads its textContent) ────
let _editableWired = false;
function _makeEditable() {
  const el = document.getElementById("linkedin-content");
  if (!el) return;
  el.setAttribute("contenteditable", "true");
  if (_editableWired) return;
  _editableWired = true;
  // On focus, unclamp so the whole post is editable; on blur, save + re-fold.
  el.addEventListener("focus", () => {
    el.classList.remove("li-clamp"); el.style.maxHeight = "";
    const b = document.getElementById("li-see-more"); if (b) b.style.display = "none";
  });
  el.addEventListener("blur", () => { saveDraft(); _applyFold(); });
}

// ── Rewrites: two alternate versions of the whole post, alongside the original ─
// The endpoint returns complete posts, not captions, so they are shown in full.
// A 120-character teaser was unreadable: the variants share their facts, so the
// openings often diverge only after the first sentence.
async function generateVariations() {
  const post = (document.getElementById("linkedin-content").textContent || "").trim();
  if (!post) { toast("Generate a post first, then I can rewrite it.", "warn"); return; }
  await _refreshToken();
  const row = document.getElementById("variants-row");
  row.style.display = "";
  row.innerHTML = `<div style="font-size:12.5px;color:#98a0ae;padding:6px 2px;">Rewriting this post two more ways…</div>`;
  try {
    const res = await fetch("/generate/variations", {
      method: "POST", headers: authHeaders(),
      body: JSON.stringify({ post, profile_id: getActiveProfileId() })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Couldn't write the rewrites.");
    const alts = (data.variants || []).filter(v => (v || "").trim());
    if (!alts.length) throw new Error("No rewrites came back — try again in a moment.");
    window._variants = [{ label: "Original", text: post }]
      .concat(alts.map((t, i) => ({ label: `Version ${i + 1}`, text: String(t).trim() })));
    _renderVariants(0);
  } catch (e) {
    row.innerHTML = `<div style="font-size:12.5px;color:#c0392b;padding:6px 2px;">${_escHtmlCreate(e.message)}</div>`;
  }
}

function _renderVariants(active) {
  const list = window._variants || [];
  const row = document.getElementById("variants-row");
  row.innerHTML = `
    <div class="cap-head">
      <span>Rewrites — same facts and voice, different hook. Picking one replaces the post below.</span>
      <button type="button" class="cap-x" onclick="hideVariants()" aria-label="Close rewrites">✕</button>
    </div>
    <div class="cap-list">` +
    list.map((v, i) => `
      <div class="cap-opt${i === active ? " on" : ""}" onclick="pickVariant(${i})" role="button" tabindex="0"
           onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();pickVariant(${i});}">
        <div class="cap-opt-head">
          <span class="cap-opt-lab">${_escHtmlCreate(v.label)}</span>
          <span class="cap-opt-len">${v.text.length.toLocaleString()} characters</span>
          <span class="cap-opt-act">${i === active ? "✓ in the post" : "Use this"}</span>
        </div>
        <div class="cap-opt-text">${_escHtmlCreate(v.text)}</div>
      </div>`).join("") + `</div>`;
}

function hideVariants() {
  const row = document.getElementById("variants-row");
  if (row) { row.style.display = "none"; row.innerHTML = ""; }
}

// ── Hashtags ──────────────────────────────────────────────────────────────────
// Hashtags used to arrive only as whatever the generator felt like adding on the
// last line, with no way to change them short of retyping the post. These chips
// own that last line: the post text stays the single source of truth, so a tag
// typed by hand shows up selected and nothing here can desync from the box.
const _TAG_ONLY_LINE = /^#[0-9A-Za-z_]+(?:\s+#[0-9A-Za-z_]+)*$/;

function _splitTagLine(text) {
  const lines = String(text || "").split("\n");
  let end = lines.length;
  while (end > 0 && !lines[end - 1].trim()) end--;      // ignore trailing blank lines
  const last = end > 0 ? lines[end - 1].trim() : "";
  if (last && _TAG_ONLY_LINE.test(last)) {
    return { body: lines.slice(0, end - 1).join("\n").replace(/\s+$/, ""),
             tags: last.split(/\s+/) };
  }
  return { body: lines.slice(0, end).join("\n").replace(/\s+$/, ""), tags: [] };
}

function _normTag(t) {
  const tag = String(t || "").trim().replace(/^#+/, "").toLowerCase().replace(/[^0-9a-z]/g, "");
  return tag.length >= 2 ? "#" + tag : "";   // #ai, #hr, #ux are real tags
}

function _writeTagLine(tags) {
  const el = document.getElementById("linkedin-content");
  if (!el) return;
  const { body } = _splitTagLine(el.textContent || "");
  const line = tags.join(" ");
  el.textContent = !tags.length ? body : (body ? `${body}\n\n${line}` : line);
  _applyFold(); saveDraft();
}

async function suggestHashtags() {
  const el = document.getElementById("linkedin-content");
  const post = (el ? el.textContent || "" : "").trim();
  if (!post) { toast("Generate a post first, then I can suggest hashtags.", "warn"); return; }
  const row = document.getElementById("hashtags-row");
  // Already fetched once: just reopen, no second round trip.
  if (window._tagPool && row.style.display === "none") { _renderHashtags(); return; }
  if (row.style.display !== "none" && window._tagPool) { hideHashtags(); return; }
  await _refreshToken();
  row.style.display = "";
  row.innerHTML = `<div style="font-size:12.5px;color:#98a0ae;padding:6px 2px;">Picking hashtags that fit this post…</div>`;
  try {
    const res = await fetch("/generate/hashtags", {
      method: "POST", headers: authHeaders(),
      body: JSON.stringify({ post, profile_id: getActiveProfileId() })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Couldn't suggest hashtags.");
    window._tagPool = (data.hashtags || []).map(_normTag).filter(Boolean);
    _renderHashtags();
  } catch (e) {
    row.innerHTML = `<div style="font-size:12.5px;color:#c0392b;padding:6px 2px;">${_escHtmlCreate(e.message)}</div>`;
  }
}

function _renderHashtags() {
  const row = document.getElementById("hashtags-row");
  const el = document.getElementById("linkedin-content");
  if (!row || !el) return;
  row.style.display = "";
  const text = el.textContent || "";
  const active = _splitTagLine(text).tags.map(_normTag).filter(Boolean);
  // A tag typed straight into the post is a real chip too, just an unsuggested one.
  const pool = (window._tagPool || []).slice();
  active.forEach(t => { if (!pool.includes(t)) pool.push(t); });
  const len = text.length;
  row.innerHTML = `
    <div class="cap-head">
      <span>Hashtags — tap to add or remove. They go on the post's last line.</span>
      <button type="button" class="cap-x" onclick="refreshHashtags()" aria-label="Suggest again" title="Suggest again">↻</button>
      <button type="button" class="cap-x" onclick="hideHashtags()" aria-label="Close hashtags">✕</button>
    </div>
    <div class="tag-chips">` +
    pool.map(t => `<button type="button" class="tag-chip${active.includes(t) ? " on" : ""}"
        onclick="toggleHashtag('${t}')">${_escHtmlCreate(t)}</button>`).join("") + `</div>
    <div class="tag-add">
      <input id="tag-custom" placeholder="Add your own — e.g. fractionalcmo"
             onkeydown="if(event.key==='Enter'){event.preventDefault();addCustomHashtag();}" />
      <button type="button" class="action-btn" onclick="addCustomHashtag()">Add</button>
    </div>
    <div class="tag-count"><b>${active.length}</b> selected · post is <b>${len.toLocaleString()}</b> of 3,000 characters</div>`;
}

function toggleHashtag(tag) {
  const t = _normTag(tag);
  if (!t) return;
  const el = document.getElementById("linkedin-content");
  const tags = _splitTagLine(el.textContent || "").tags.map(_normTag).filter(Boolean);
  const i = tags.indexOf(t);
  if (i >= 0) tags.splice(i, 1); else tags.push(t);
  _writeTagLine(tags);
  _renderHashtags();
}

function addCustomHashtag() {
  const input = document.getElementById("tag-custom");
  const t = _normTag(input ? input.value : "");
  if (!t) { toast("A hashtag needs at least 2 letters or digits, no spaces.", "warn"); return; }
  window._tagPool = window._tagPool || [];
  if (!window._tagPool.includes(t)) window._tagPool.push(t);
  const el = document.getElementById("linkedin-content");
  const tags = _splitTagLine(el.textContent || "").tags.map(_normTag).filter(Boolean);
  if (!tags.includes(t)) { tags.push(t); _writeTagLine(tags); }
  _renderHashtags();
}

function refreshHashtags() {
  window._tagPool = null;
  document.getElementById("hashtags-row").style.display = "none";
  suggestHashtags();
}

function hideHashtags() {
  const row = document.getElementById("hashtags-row");
  if (row) { row.style.display = "none"; row.innerHTML = ""; }
}

function pickVariant(i) {
  const list = window._variants || [];
  const el = document.getElementById("linkedin-content");
  if (!el || !list[i]) return;
  // Swapping used to overwrite the box outright, so any hand edit was gone with
  // no way back — and "Original" only restored the text as it was when Rewrites
  // ran. Anything not already an option is kept as one before it gets replaced.
  const live = (el.textContent || "").trim();
  if (live && !list.some(v => v.text.trim() === live)) {
    list.push({ label: "Your edit", text: live });
  }
  el.textContent = list[i].text;
  _applyFold(); saveDraft();
  _renderVariants(i);
  el.scrollIntoView({ behavior: "smooth", block: "center" });
}

// ── Generation history (this browser) ─────────────────────────────────────────
const _HIST_KEY = "cm_history";
function _pushHistory(text) {
  if (!text || !text.trim()) return;
  let h; try { h = JSON.parse(localStorage.getItem(_HIST_KEY) || "[]"); } catch (_) { h = []; }
  if (h[0] && h[0].text === text) return;   // dedupe consecutive identical
  h.unshift({ text, ts: Date.now() });
  h = h.slice(0, 25);
  try { localStorage.setItem(_HIST_KEY, JSON.stringify(h)); } catch (_) {}
}
function toggleHistory() {
  const p = document.getElementById("history-panel");
  if (!p.style.display || p.style.display === "none") { _renderHistory(); p.style.display = ""; }
  else p.style.display = "none";
}
function _renderHistory() {
  let h; try { h = JSON.parse(localStorage.getItem(_HIST_KEY) || "[]"); } catch (_) { h = []; }
  const p = document.getElementById("history-panel");
  if (!h.length) { p.innerHTML = `<div style="font-size:12.5px;color:#98a0ae;padding:8px 2px;">No history yet — posts you generate will show up here.</div>`; return; }
  p.innerHTML = `<div style="font-size:12px;font-weight:700;color:#5b6270;margin:2px 2px 8px;">🕘 Recent generations <span style="font-weight:400;color:#98a0ae;">(this browser)</span></div>` +
    h.map((it, i) => `<div style="border:1px solid #e6ded0;border-radius:8px;padding:9px 11px;margin-bottom:6px;">
      <div style="font-size:12.5px;color:#333;line-height:1.45;max-height:44px;overflow:hidden;">${_escHtmlCreate(it.text).slice(0, 180)}${it.text.length > 180 ? "…" : ""}</div>
      <div style="margin-top:7px;display:flex;gap:10px;align-items:center;">
        <span style="font-size:11px;color:#98a0ae;">${new Date(it.ts).toLocaleString()}</span>
        <button class="action-btn" onclick="loadFromHistory(${i})">Load &amp; edit</button>
      </div></div>`).join("");
}
function loadFromHistory(i) {
  let h; try { h = JSON.parse(localStorage.getItem(_HIST_KEY) || "[]"); } catch (_) { h = []; }
  const it = h[i]; if (!it) return;
  const el = document.getElementById("linkedin-content");
  el.textContent = it.text; _makeEditable(); _applyFold(); saveDraft();
  document.getElementById("output-section").classList.add("visible");
  document.getElementById("history-panel").style.display = "none";
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  toast("Loaded — edit it and post.", "success");
}

// ── Post Now ──────────────────────────────────────────────────────────────────
async function postToLinkedIn() {
  if (!linkedInConnected) { toast("Connect LinkedIn first — opening the connect window…", "warn"); connectLinkedIn(); return; }
  const text = document.getElementById("linkedin-content").textContent;
  if (!text) { toast("Generate content first.", "warn"); return; }
  await _refreshToken();

  const btn = document.getElementById("post-now-btn");
  btn.disabled = true;
  btn.textContent = "Posting…";

  try {
    let res;
    if (_attachB64) {
      // Text + attached image → single-image LinkedIn post
      const bytes = Uint8Array.from(atob(_attachB64), c => c.charCodeAt(0));
      const blob = new Blob([bytes], { type: _attachMime });
      const fd = new FormData();
      fd.append("file", blob, "post-image." + _attachExt());
      fd.append("text", text);
      fd.append("dry_run", isDryRun() ? "true" : "false");
      res = await fetch("/post/linkedin/image", { method: "POST", headers: { "x-token": getToken() }, body: fd });
    } else {
      res = await fetch("/post/linkedin", {
        method: "POST", headers: authHeaders(),
        body: JSON.stringify({ text, dry_run: isDryRun() })
      });
    }
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to post.");
    btn.textContent = isDryRun() ? "Dry Run OK!" : "Posted!";
    btn.classList.add("primary");
    toast(isDryRun() ? "Dry run — post previewed in console." : "Posted to LinkedIn!", "success");
  } catch (err) {
    toast(err.message, "error");
    btn.textContent = "Post Now";
    btn.disabled = false;
  }
}

// ── Schedule ──────────────────────────────────────────────────────────────────
function openSchedule() {
  if (!linkedInConnected) { toast("Connect LinkedIn first — opening the connect window…", "warn"); connectLinkedIn(); return; }
  if (!document.getElementById("linkedin-content").textContent) { toast("Generate content first.", "warn"); return; }
  const d = new Date(Date.now() + 5 * 60 * 1000);
  document.getElementById("schedule-time").value = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  document.getElementById("schedule-modal").style.display = "flex";
}

function closeSchedule() {
  document.getElementById("schedule-modal").style.display = "none";
}

function closeScheduleIfBg(e) {
  if (e.target === document.getElementById("schedule-modal")) closeSchedule();
}

async function confirmSchedule() {
  const val = document.getElementById("schedule-time").value;
  if (!val) { toast("Pick a time first.", "warn"); return; }
  await _refreshToken();

  const btn = document.getElementById("confirm-schedule-btn");
  btn.disabled = true; btn.textContent = "Scheduling…";

  try {
    const res = await fetch("/schedule/linkedin", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({
        text: document.getElementById("linkedin-content").textContent,
        schedule_time: new Date(val).toISOString(),
        dry_run: isDryRun()
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to schedule.");
    closeSchedule();
    await loadQueue();
    document.getElementById("queue-section").scrollIntoView({ behavior: "smooth" });
    toast("Post scheduled!", "success");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    btn.disabled = false; btn.textContent = "Confirm";
  }
}

// ── Queue ─────────────────────────────────────────────────────────────────────
async function loadQueue() {
  try {
    const res = await fetch("/schedule/list", { headers: { "x-token": getToken() } });
    const posts = await res.json();
    renderQueue(posts);
  } catch (_) {}
}

function renderQueue(posts) {
  const section = document.getElementById("queue-section");
  const list    = document.getElementById("queue-list");
  if (!posts.length) { section.classList.remove("visible"); return; }

  section.classList.add("visible");
  list.innerHTML = posts.map(p => {
    const statusKey = p.status.replace(/[^a-z_]/gi, "").toLowerCase();
    return `<div class="queue-item">
      <span class="queue-preview">${p.preview}</span>
      <span class="queue-time">${new Date(p.scheduled_at).toLocaleString()}</span>
      <span class="queue-status ${statusKey}">${p.status}</span>
      ${p.status === "scheduled" ? `<button class="queue-cancel" onclick="cancelPost('${p.id}')" title="Cancel">✕</button>` : ""}
    </div>`;
  }).join("");
}

async function cancelPost(jobId) {
  await fetch(`/schedule/${jobId}`, { method: "DELETE", headers: { "x-token": getToken() } });
  await loadQueue();
  toast("Scheduled post cancelled.");
}

setInterval(loadQueue, 15000);

// ── Carousel ──────────────────────────────────────────────────────────────────
let _carouselPdfBase64 = null;

async function generateCarousel() {
  const content = document.getElementById("content-input").value.trim();
  if (!content) { showError("Please enter some content first."); return; }
  await _refreshToken();

  const btn     = document.getElementById("carousel-btn");
  const btnText = document.getElementById("carousel-btn-text");
  const btnLoad = document.getElementById("carousel-btn-loader");

  btn.disabled = true;
  btnText.style.display = "none";
  btnLoad.style.display = "inline";
  document.getElementById("error-banner").style.display = "none";
  document.getElementById("carousel-section").classList.remove("visible");

  startGenProgress([
    "Reading your content…",
    "Planning the slide story…",
    "Writing slide copy…",
    "Designing & rendering the PDF…",
    "Almost there…",
  ]);

  try {
    const fmtEl = document.getElementById("carousel-format");
    const themeEl = document.getElementById("carousel-theme");
    const res = await fetch("/generate/carousel", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ input_type: activeType, content, profile_id: getActiveProfileId(),
                             carousel_format: fmtEl ? fmtEl.value : "standard",
                             carousel_theme: themeEl ? themeEl.value : "" })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Carousel generation failed.");
    _showCarouselResult(data);
    toast("Carousel ready! Download the PDF to preview.", "success");
  } catch (err) {
    showError(err.message);
  } finally {
    stopGenProgress();
    btn.disabled = false;
    btnText.style.display = "inline";
    btnLoad.style.display = "none";
  }
}

// Shared: drop a {pdf_base64, post_text, hook} result into the carousel preview + post UI.
function _showCarouselResult(data) {
  _carouselPdfBase64 = data.pdf_base64;
  document.getElementById("carousel-post-text").textContent = data.post_text || "";
  document.getElementById("carousel-hook-label").textContent = data.hook ? `"${data.hook}"` : "";
  const postBtn = document.getElementById("post-carousel-btn");
  postBtn.textContent = "Post to LinkedIn";
  postBtn.disabled = false;
  document.getElementById("carousel-section").classList.add("visible");
  markGenerated();
  document.getElementById("carousel-section").scrollIntoView({ behavior: "smooth", block: "start" });
}

// Build a data carousel from a real KnowErgo assessment result JSON.
async function generateErgoCarousel() {
  const raw = (document.getElementById("ergo-json").value || "").trim();
  if (!raw) { showError("Paste a KnowErgo assessment result JSON first."); return; }
  let result;
  try { result = JSON.parse(raw); }
  catch (e) { showError("That's not valid JSON — paste the full /video/result response."); return; }
  await _refreshToken();
  const btn = document.getElementById("ergo-carousel-btn");
  const bt = document.getElementById("ergo-btn-text"), bl = document.getElementById("ergo-btn-loader");
  btn.disabled = true; bt.style.display = "none"; bl.style.display = "inline";
  document.getElementById("error-banner").style.display = "none";
  startGenProgress(["Reading the assessment…", "Scoring each joint…", "Designing the data slides…", "Rendering the PDF…"]);
  try {
    const res = await fetch("/generate/ergo-carousel", {
      method: "POST", headers: authHeaders(),
      body: JSON.stringify({ result, profile_id: getActiveProfileId() })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Couldn't build the carousel from that assessment.");
    _showCarouselResult(data);
    toast("Data carousel ready from your assessment!", "success");
  } catch (err) {
    showError(err.message);
  } finally {
    stopGenProgress();
    btn.disabled = false; bt.style.display = "inline"; bl.style.display = "none";
  }
}

function downloadCarousel() {
  if (!_carouselPdfBase64) { toast("Generate a carousel first.", "warn"); return; }
  const a = document.createElement("a");
  a.href = "data:application/pdf;base64," + _carouselPdfBase64;
  a.download = "voyce-carousel.pdf";
  a.click();
}

// Look at the slides without committing to a download. A blob URL, not a data:
// one — Chrome refuses to hand a data:application/pdf to its PDF viewer.
let _carouselBlobUrl = null;
function viewCarouselSlides() {
  if (!_carouselPdfBase64) { toast("Generate a carousel first.", "warn"); return; }
  try {
    if (_carouselBlobUrl) URL.revokeObjectURL(_carouselBlobUrl);
    const bytes = Uint8Array.from(atob(_carouselPdfBase64), c => c.charCodeAt(0));
    _carouselBlobUrl = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
    const w = window.open(_carouselBlobUrl, "_blank");
    if (!w) toast("Your browser blocked the popup — use Download PDF instead.", "warn");
  } catch (_) {
    toast("Couldn't open the slides. Download the PDF instead.", "error");
  }
}

async function postCarousel() {
  if (!linkedInConnected) { toast("Connect LinkedIn first — opening the connect window…", "warn"); connectLinkedIn(); return; }
  if (!_carouselPdfBase64) { toast("Generate a carousel first.", "warn"); return; }

  const text = document.getElementById("carousel-post-text").textContent;
  const btn  = document.getElementById("post-carousel-btn");
  btn.disabled = true;
  btn.textContent = "Posting…";

  try {
    const pdfBytes = Uint8Array.from(atob(_carouselPdfBase64), c => c.charCodeAt(0));
    const blob = new Blob([pdfBytes], { type: "application/pdf" });
    const formData = new FormData();
    formData.append("file", blob, "carousel.pdf");
    formData.append("text", text);
    formData.append("dry_run", isDryRun() ? "true" : "false");

    const res = await fetch("/post/linkedin/carousel", {
      method: "POST",
      headers: { "x-token": getToken() },
      body: formData
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to post carousel.");
    btn.textContent = isDryRun() ? "Dry Run OK!" : "Posted!";
    toast(isDryRun() ? "Dry run — carousel previewed in console." : "Carousel posted to LinkedIn!", "success");
  } catch (err) {
    toast(err.message, "error");
    btn.textContent = "Post to LinkedIn";
    btn.disabled = false;
  }
}

// ── Image attachment (optional image on the LinkedIn post) ──────────────────────
let _attachB64  = null;         // base64 (no data: prefix) of the attached image
let _attachMime = "image/png";
let _attachIsUpload = false;    // true if the user uploaded their own (preserve across regeneration)

function _attachExt() { return _attachMime.includes("jpeg") ? "jpg" : (_attachMime.split("/")[1] || "png"); }

// Show one of the three image states: "empty" | "loading" | "has".
function _attachState(state) {
  const media = document.getElementById("li-media");
  const load  = document.getElementById("li-attach-loading");
  if (media) media.style.display = state === "has"     ? "flex" : "none";
  if (load)  load.style.display  = state === "loading" ? "flex" : "none";
}

function _renderAttachment(dataUrl) {
  const img = document.getElementById("li-attach-img");
  if (img) img.src = dataUrl;
  _attachState("has");
}

function attachRemove() {
  _attachB64 = null;
  _attachMime = "image/png";
  _attachIsUpload = false;
  _attachState("empty");
  const inp = document.getElementById("attach-upload");
  if (inp) inp.value = "";
  saveDraft();
}

// Generate an on-brand illustration FOR THE CURRENT POST and attach it.
// Used both by the "Add an AI image" toggle (during generate) and the Regenerate button.
async function attachGenerateImage() {
  const post = document.getElementById("linkedin-content").textContent.trim();
  const source = post || document.getElementById("content-input").value.trim();
  if (!source) { toast("Generate or write a post first.", "warn"); return; }

  const imgStyle = (document.querySelector('input[name="img-style"]:checked') || {}).value || "illustration";
  // Source cards render from the article's own page — they need the URL, not the post text.
  const articleUrl = activeType === "url" ? document.getElementById("content-input").value.trim() : "";
  if (imgStyle === "source" && !articleUrl) {
    toast("Source cards need an article link — paste it in the Website URL tab first.", "warn");
    _attachState(_attachB64 ? "has" : "empty");
    return;
  }

  await _refreshToken();
  _attachState("loading");
  try {
    const res = await fetch("/generate/image", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(
        imgStyle === "source"
          ? { input_type: "url", content: articleUrl, style: "source", post_text: source, profile_id: getActiveProfileId() }
          : { input_type: "text", content: source, style: imgStyle, profile_id: getActiveProfileId() }
      )
    });
    const data = await res.json();
    if (!res.ok) {
      if (data.detail === "LIMIT_REACHED") {
        toast("You've used all 5 free generations — upgrade for unlimited.", "warn");
        if (typeof openUpgradeModal === "function") openUpgradeModal();
        _attachState(_attachB64 ? "has" : "empty");
        return;
      }
      throw new Error(data.detail || "Image generation failed.");
    }
    _attachB64  = data.image_base64;
    _attachMime = "image/png";
    _attachIsUpload = false;
    _renderAttachment("data:image/png;base64," + data.image_base64);
    saveDraft();
    toast("Image added to your post.", "success");
  } catch (err) {
    const msg = /failed to fetch|networkerror|load failed/i.test(err.message || "")
      ? "The image took too long to generate. Your post is ready without it — or hit Regenerate to try again."
      : err.message;
    toast(msg, "error");
    _attachState(_attachB64 ? "has" : "empty");
  }
}

function attachTriggerUpload() {
  const input = document.getElementById("attach-upload");
  if (input) input.click();
}

async function attachHandleUpload(event) {
  const file = event.target.files && event.target.files[0];
  event.target.value = "";  // allow re-selecting the same file later
  if (!file) return;
  if (!file.type.startsWith("image/")) { toast("Please choose an image file (PNG or JPG).", "warn"); return; }
  if (file.size > 8 * 1024 * 1024) { toast("Image is too large — please use one under 8 MB.", "warn"); return; }

  const dataUrl = await new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
  _attachB64  = String(dataUrl).split(",")[1];
  _attachMime = file.type || "image/png";
  _attachIsUpload = true;
  _renderAttachment(dataUrl);
  saveDraft();
  toast("Image added to your post.", "success");
}

// ── Copy ──────────────────────────────────────────────────────────────────────
function copyContent(id) {
  const el = document.getElementById(id);
  // textContent, not innerText: the clamped preview visually hides lines, but the
  // clipboard must always get the full post.
  navigator.clipboard.writeText(el.textContent).then(() => {
    const btn = el.closest(".output-card").querySelector(".action-btn");
    btn.textContent = "Copied!";
    btn.classList.add("copied");
    setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 2000);
  });
}

// ── Sign out ──────────────────────────────────────────────────────────────────
async function doAppLogout() {
  try { await fetch("/auth/logout", { method: "POST", headers: { "x-token": getToken() } }); } catch (_) {}
  localStorage.removeItem("cm_user");
  if (window.Clerk) { try { await window.Clerk.signOut(); } catch (_) {} }
  window.location.href = "/login";
}
