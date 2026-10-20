// ── Auth (Clerk) ──────────────────────────────────────────────────────────────
let _clerkToken = "";

async function _refreshToken() {
  if (window.Clerk?.session) _clerkToken = await window.Clerk.session.getToken();
}

async function initClerk() {
  const clerk = window.Clerk;
  await clerk.load();
  if (!clerk.user) { window.location.href = "/login?next=" + encodeURIComponent(location.pathname + location.hash); return false; }
  await _refreshToken();
  setInterval(_refreshToken, 50000);
  // Background tabs get their timers throttled and sleep pauses them entirely,
  // so also refresh the moment the user comes back to the tab.
  window.addEventListener("focus", _refreshToken);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) _refreshToken(); });
  try {
    const res = await fetch("/auth/me", { headers: { "x-token": _clerkToken } });
    if (res.ok) { localStorage.setItem("cm_user", JSON.stringify(await res.json())); if (window._showSalesLink) _showSalesLink(); if (window.vIdentify) vIdentify(JSON.parse(localStorage.getItem("cm_user") || "null")); }
  } catch (_) {}
  return true;
}

function getToken() { return _clerkToken; }

// ── Writing-as profile picker ────────────────────────────────────────────────
let _profiles = [];
// The setup strip waits for both answers, so a set-up account never sees it flash.
let _profilesKnown = false, _liKnown = false;
function getActiveProfileId() { return localStorage.getItem("cm_profile_id") || ""; }
function setActiveProfile(id) { localStorage.setItem("cm_profile_id", id); }
// "Writing as" changed, by the picker or by opening a draft written as someone else
function pickProfile(id) {
  setActiveProfile(id);
  const sel = document.getElementById("profile-picker");
  if (sel) sel.value = id;
  _restoreIdeas();   // that person's ideas, not the last person's
  _setPreviewAuthor();
  _syncWhatsAppTarget();
  _renderDrafts();
}
async function loadProfilePicker() {
  try {
    const res = await fetch("/companies", { headers: { "x-token": getToken() } });
    _profilesKnown = true;
    if (!res.ok) { updateProgress(); return; }
    _profiles = await res.json();
    updateProgress();
    if (!_profiles.length) return;
    let active = getActiveProfileId();
    if (!_profiles.some(p => p.id === active)) active = _profiles[0].id;
    setActiveProfile(active);
    const wrap = document.getElementById("writing-as");
    const sel  = document.getElementById("profile-picker");
    if (wrap && sel && _profiles.length >= 2) {  // only worth a picker with 2+ profiles
      sel.innerHTML = _profiles.map(p =>
        `<option value="${_escHtmlCreate(p.id)}" ${p.id === active ? "selected" : ""}>${_escHtmlCreate(p.name)}${p.profile_type === "personal" ? "" : " · company"}</option>`
      ).join("");
      wrap.style.display = "flex";
    }
    // Ideas need a profile to draw a niche from; the tool panel is always visible,
    // so it is the run button that waits rather than the whole row.
    _syncToolRuns();
    _setPreviewAuthor();
    _syncWhatsAppTarget();
    _renderDrafts();   // now it knows whose drafts and ideas to show
    _restoreIdeas();
  } catch (_) { _profilesKnown = true; updateProgress(); }
}

function _escHtmlCreate(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ── Post ideas on Create: pillar-spanning, grounded in live news ──────────────
let _createIdeas = [];
// Ideas stay put: saved per profile (this browser and account), so a trip to Queue or Settings,
// a reload, or switching "Writing as" no longer throws a list away. They lapse after a week,
// since they react to that week's news.
const _IDEAS_TTL = 7 * 864e5;
function _ideasKey(pid) { return `cm_ideas:${_uid()}:${pid}`; }
function _ideasPid() { return getActiveProfileId() || (_profiles[0] && _profiles[0].id) || ""; }
function _saveIdeas(pid, ideas) {
  try { localStorage.setItem(_ideasKey(pid), JSON.stringify({ at: Date.now(), ideas })); } catch (_) {}
}
function _savedIdeas(pid) {
  try {
    const s = JSON.parse(localStorage.getItem(_ideasKey(pid)) || "null");
    return s && Date.now() - s.at < _IDEAS_TTL && (s.ideas || []).length ? s : null;
  } catch (_) { return null; }
}
function _renderIdeas(ideas, at) {
  _createIdeas = ideas;
  _markToolFilled("ideas", true);
  document.getElementById("ideas-out").innerHTML =
    (at ? `<div class="ideas-when">Suggested ${_escHtmlCreate(_ago(at))} · run it again for fresh ones</div>` : "") +
    ideas.map((it, i) => `
      <div class="idea-card">
        <div class="idea-hook">${_escHtmlCreate(it.hook)}</div>
        <div class="idea-meta">
          <span class="idea-type">${_escHtmlCreate(it.post_type_label || "Post")}</span>${it.why ? " · " + _escHtmlCreate(it.why) : ""}${it.source ? " · 📰 " + _escHtmlCreate(it.source) : " · evergreen"}
        </div>
        ${it.based_on && it.based_on !== it.source ? `<div class="idea-basis">Based on: ${_escHtmlCreate(it.based_on)}</div>` : ""}
        <button type="button" class="idea-use" onclick="useIdea(${i})">Use this idea →</button>
      </div>`).join("");
}
// The "Writing as" profile's saved ideas, or an empty panel
function _restoreIdeas() {
  const out = document.getElementById("ideas-out");
  if (!out) return;
  const pid = _ideasPid(), s = pid ? _savedIdeas(pid) : null;
  if (s) { _renderIdeas(s.ideas, s.at); return; }
  out.innerHTML = "";
  _createIdeas = [];
  _markToolFilled("ideas", false);
}
async function suggestIdeasCreate() {
  const out = document.getElementById("ideas-out");
  const pid = _ideasPid();
  if (!pid) { toast("Create a profile first, then I can suggest ideas.", "warn"); return; }
  showTool("ideas");
  // Only Paste Text is a brief. A link in the URL or YouTube tab used to steer the
  // ideas too, as if "https://youtube.com/…" were the topic.
  const seed = activeType === "text" ? ((document.getElementById("content-input") || {}).value || "") : "";
  const seeded = !!seed.trim();
  out.innerHTML = `<div class="tool-empty">${seeded
    ? "Drafting ideas from what you typed, plus the latest news…"
    : "Reading the latest news and drafting ideas…"} about 10 seconds, and nothing gets posted.</div>`;
  try {
    // What's typed in Paste Text steers the ideas. Without it they could only
    // ever be "your niche + today's news", which is why they came back generic.
    const res = await fetch(`/companies/${pid}/ideas`, {
      method: "POST", headers: authHeaders(),
      body: JSON.stringify({ context: seed.trim().slice(0, 1200) }),
    });
    const data = await res.json();
    if (data.detail === "LIMIT_REACHED") {
      // Free accounts: idea lists come out of the 5 generations.
      out.innerHTML = `<div class="tool-empty">You've used your 5 free generations, and idea lists use them too.</div>`;
      showError("LIMIT_REACHED");
      return;
    }
    if (String(data.detail || "").startsWith("PASS_LIMIT_REACHED:")) {
      // India pass: idea lists come out of its 60 generations.
      out.innerHTML = `<div class="tool-empty">${_escHtmlCreate(String(data.detail).slice("PASS_LIMIT_REACHED:".length))}</div>`;
      showError(data.detail);
      return;
    }
    if (!res.ok) throw new Error(data.detail || "Couldn't get ideas right now.");
    const ideas = data.ideas || [];
    if (ideas.length) _saveIdeas(pid, ideas);
    if (_ideasPid() !== pid) return;   // switched "Writing as" meanwhile: they're saved for later
    if (!ideas.length) { out.innerHTML = `<div class="tool-empty">No ideas came back — try again in a moment.</div>`; return; }
    _renderIdeas(ideas, 0);
  } catch (e) {
    out.innerHTML = `<div style="font-size:12.5px;color:#c0392b;line-height:1.5;">${_escHtmlCreate(e.message)}</div>`;
  }
}

function useIdea(i) {
  const it = _createIdeas[i];
  if (!it) return;
  const ta = document.getElementById("content-input");
  if (!ta) return;
  // Switch to Paste Text mode so the seed generates correctly (mirrors applySeedTopic)
  _switchInputTab("text");   // keeps whatever was in the other tab there
  const parts = [`Write a ${it.post_type_label || "LinkedIn"} post about: ${it.hook}.`];
  if (it.why) parts.push(it.why);
  if (it.based_on && it.based_on !== it.source) parts.push(`Build it on this real fact about them: "${it.based_on}".`);
  if (it.source) parts.push(`It should react to this from the news: "${it.source}".`);
  ta.value = parts.join(" ");
  ta.scrollIntoView({ behavior: "smooth", block: "center" });
  ta.focus();
  // Don't auto-generate — the idea has no format of its own, so let the user pick
  // Generate Content (text post) or Carousel PDF (respects the Brand/Format above).
  _flashGenButtons();
  toast("Idea loaded — pick Generate Content or Carousel PDF.", "success");
}

// Briefly highlight the two generate buttons so it's obvious what to do next.
function _flashGenButtons() {
  ["generate-btn", "carousel-btn"].forEach(id => {
    const b = document.getElementById(id);
    if (!b) return;
    const prev = b.style.boxShadow;
    b.style.transition = "box-shadow .2s";
    b.style.boxShadow = "0 0 0 3px rgba(108,71,255,.55)";
    setTimeout(() => { b.style.boxShadow = prev; }, 1600);
  });
}
function getUser()  { try { return JSON.parse(localStorage.getItem("cm_user") || "null"); } catch { return null; } }
function authHeaders(extra) { return { "Content-Type": "application/json", "x-token": getToken(), ...(extra||{}) }; }

let activeType = "text";
// One textarea serves all three tabs, so each tab's text is kept here while another
// tab is showing. It used to carry over: a YouTube link showed up under Paste Text.
const _tabText = { text: "", url: "", youtube: "" };
const _TAB_PLACEHOLDERS = {
  text: "Paste your blog post, article, notes, or any content here…",
  url: "https://example.com/blog/your-article",
  youtube: "https://www.youtube.com/watch?v=…"
};
function _switchInputTab(type) {
  const ta = document.getElementById("content-input");
  if (!ta || !(type in _tabText)) return;
  document.querySelectorAll(".input-tab").forEach(t => t.classList.toggle("active", t.dataset.type === type));
  if (type === activeType) return;
  _tabText[activeType] = ta.value;
  activeType = type;
  ta.value = _tabText[type];
  ta.placeholder = _TAB_PLACEHOLDERS[type];
}
let linkedInConnected = false;
// A profile can post to its own LinkedIn account (a client's). Posting from the
// Create page goes through the selected profile's connection, not just the user's.
function _activeProfile() { return (_profiles || []).find(p => p.id === getActiveProfileId()); }
// What to do once LinkedIn finishes connecting: the post you were trying to make.
let _afterLiConnect = null;
function _ensurePostingConnected(then) {
  const p = _activeProfile();
  if (p && p.li && p.li.own_account) {
    if (p.li.connected) return true;
    toast("This profile's LinkedIn connection has expired — send a new invite from Settings.", "warn");
    return false;
  }
  if (linkedInConnected) return true;
  toast(then ? "Connect LinkedIn in the new window, and Voyce carries on with your post." : "Connect LinkedIn first — opening the connect window…", "warn");
  _afterLiConnect = then || null;
  connectLinkedIn();
  return false;
}

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
    el.innerHTML = `You've used all 5 free generations. <a href="#" onclick="openUpgradeModal();return false;" style="color:#24365e;font-weight:700;">Upgrade to Pro</a> to keep going.`;
  } else if (typeof msg === "string" && msg.startsWith("PASS_LIMIT_REACHED:")) {
    // An India pass with its generations used up: renewing adds more.
    el.textContent = msg.slice("PASS_LIMIT_REACHED:".length) + " ";
    const a = document.createElement("a");
    a.href = "#"; a.textContent = "Renew Pro";
    a.style.cssText = "color:#24365e;font-weight:700;";
    a.onclick = (e) => { e.preventDefault(); openUpgradeModal(); };
    el.appendChild(a);
  } else {
    el.textContent = msg;
  }
  el.style.display = "block";
  setTimeout(() => el.style.display = "none", 10000);
}

// ── Init (called by Clerk script onload) ─────────────────────────────────────
async function startApp() {
  if (!(await initClerk())) return;
  // A new account goes through onboarding first, however sign-up got it here.
  if ((getUser() || {}).needs_onboarding) { location.replace("/onboarding"); return; }
  loadProfilePicker();
  _loadDrafts();
  const user = getUser();
  if (user) {
    const tag = document.getElementById("user-tag");
    if (tag) tag.textContent = user.name || user.email;
  }
  checkLinkedInStatus();
  loadQueue();
  updateProgress();
  loadWaiting();
  // The upsell starts hidden and shows only for a known free plan: people who
  // already pay (or admins) never see it flash while the plan loads.
  const plan = ((user || {}).gen_info || {}).plan;
  const up = document.getElementById("side-upsell");
  if (up) up.style.display = plan && plan !== "pro" && plan !== "admin" ? "" : "none";
  applySeedTopic();
  // The composer starts empty on every visit — no stale post is auto-restored.
  // Previously generated posts live in the "Past" tab, loaded on demand.
  _syncToolRuns();
}

// Every login lands here, so a post waiting for approval has to be visible here too.
async function loadWaiting() {
  try {
    const res = await fetch("/pending", { headers: { "x-token": getToken() } });
    if (!res.ok) return;
    const n = (await res.json()).length;
    const badge = document.getElementById("nav-pending-count");
    if (badge) { badge.textContent = n; badge.hidden = !n; }
    const bar = document.getElementById("waiting-banner");
    if (!bar) return;
    bar.style.display = n ? "" : "none";
    bar.innerHTML = n ? `<span><b>${n} post${n === 1 ? " is" : "s are"} waiting for your OK.</b> Nothing goes out until you approve${n === 1 ? " it" : ""}.</span>
      <a href="/setup#today">Review ${n === 1 ? "it" : "them"} →</a>` : "";
  } catch (_) {}
}

// First-run handoff from onboarding: pre-fill the generator with the user's topic
function applySeedTopic() {
  // "Edit in Write" from onboarding: the first post itself, ready to edit and post.
  const seedPost = localStorage.getItem("cm_seed_post");
  if (seedPost) {
    localStorage.removeItem("cm_seed_post");
    renderOutputs({ linkedin_post: seedPost });
    document.getElementById("output-section").classList.add("visible");
    markGenerated();
    setTimeout(() => toast("Your first post. Edit anything, then post it.", "success"), 400);
    return;
  }
  const seed = localStorage.getItem("cm_seed_topic");
  if (!seed) return;
  localStorage.removeItem("cm_seed_topic");
  const ta = document.getElementById("content-input");
  if (!ta) return;
  // Switch to text mode so the pre-filled brief generates correctly
  _switchInputTab("text");   // keeps whatever was in the other tab there
  ta.value = `Write a specific, useful LinkedIn post about ${seed}.`;
  ta.focus();
  ta.scrollIntoView({ behavior: "smooth", block: "center" });
  setTimeout(() => toast("We pre-filled your topic — hit Generate to see your first post.", "success"), 600);
}

window.addEventListener("message", (e) => {
  if (e.origin !== location.origin) return;   // only our own callback page may report this
  if (e.data === "linkedin_connected") {
    if (window.vTrack) vTrack("linkedin_connected", { page: "write" });
    linkedInConnected = true;
    updateLiPill(true);
    updateProgress();
    const next = _afterLiConnect; _afterLiConnect = null;
    if (next) { toast("LinkedIn connected. Carrying on…", "success"); next(); }
    else toast("LinkedIn connected!", "success");
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
  } catch (_) {}
  _liKnown = true;
  updateLiPill(linkedInConnected);
  updateProgress();
}

function updateLiPill(connected) {
  const pill = document.getElementById("li-pill");
  const text = document.getElementById("li-pill-text");
  if (!pill) return;
  pill.classList.toggle("connected", connected);
  text.textContent = connected ? "LinkedIn Connected" : "Connect LinkedIn";
  pill.style.visibility = "";   // hidden until we know which of the two it is
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
  // Set up = LinkedIn connected and a profile posting daily. Then the strip is noise.
  // Until both answers are in, it stays hidden rather than flashing.
  const strip = document.getElementById("progress-strip");
  const setUp = linkedInConnected && (_profiles || []).some(p => p.active);
  if (strip) strip.style.display = (!_profilesKnown || !_liKnown || setUp) ? "none" : "";
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
  tab.addEventListener("click", () => _switchInputTab(tab.dataset.type));
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
// The preview shows who the post is written as: the "Writing as" profile's name and title,
// with their LinkedIn photo only when that LinkedIn account is the profile's own (/author).
const _authorCache = {};
function _activeProfile() { return _profiles.find(p => p.id === getActiveProfileId()) || _profiles[0] || null; }
function _paintAuthor(name, title, photo) {
  const av = document.getElementById("li-avatar"), nm = document.getElementById("li-name"), tt = document.getElementById("li-title");
  if (av) {
    if (photo) { const img = new Image(); img.alt = ""; img.src = photo; av.replaceChildren(img); }
    else av.textContent = ((name || "").trim()[0] || "Y").toUpperCase();
  }
  if (nm) nm.textContent = name;
  if (tt) { tt.textContent = title || ""; tt.hidden = !title; }
}
async function _setPreviewAuthor() {
  const u = getUser(), prof = _activeProfile();
  _paintAuthor((prof && prof.name) || (u && (u.name || u.email)) || "You", (prof && prof.designation) || "", "");
  const key = (prof && prof.id) || "_me";
  try {
    if (!_authorCache[key]) {
      const r = await fetch("/author" + (prof ? "?profile_id=" + encodeURIComponent(prof.id) : ""), { headers: { "x-token": getToken() } });
      if (!r.ok) return;
      _authorCache[key] = await r.json();
    }
    const a = _authorCache[key], now = _activeProfile();
    if (((now && now.id) || "_me") === key) _paintAuthor(a.name || (prof && prof.name) || "You", a.title || "", a.photo || "");
    _syncDraft();   // the open draft's card gets the photo too
  } catch (_) {}
}
// The post as the user sees it. The preview is contenteditable, and pressing
// Enter there inserts <div>/<br> elements that textContent silently drops, so
// every line break typed by hand vanished from the posted text. innerText would
// keep them, but it depends on layout and the "…see more" fold, so walk the DOM.
function _editorText(el) {
  if (!el) return "";
  let out = "";
  const walk = (node) => {
    for (const n of node.childNodes) {
      if (n.nodeType === Node.TEXT_NODE) { out += n.nodeValue; continue; }
      if (n.nodeName === "BR") { out += "\n"; continue; }
      if (/^(DIV|P|LI)$/.test(n.nodeName) && out && !out.endsWith("\n")) out += "\n";
      walk(n);
    }
  };
  walk(el);
  return out.replace(/\s+$/, "");
}
function _postText() {
  return _editorText(document.getElementById("linkedin-content"));
}
// ── WhatsApp: send the post (and its image) as a sample ──────────────────────
// Writing as someone who is also a Sales lead with a mobile number: their chat opens directly
// (wa.me/<number>), which works even when they aren't in your contacts.
let _waTarget = null;
async function _syncWhatsAppTarget() {
  _waTarget = null;
  const btn = document.getElementById("wa-btn");
  if (btn) btn.textContent = "WhatsApp";
  const cb = document.getElementById("wa-btn-carousel"); if (cb) cb.textContent = "WhatsApp";
  const u = getUser(), prof = (typeof _activeProfile === "function") ? _activeProfile() : null;
  if (!u || !u.sales_access || !prof || !prof.name) return;
  const asked = prof.id;
  try {
    const r = await fetch("/sales/lead-for?name=" + encodeURIComponent(prof.name), { headers: { "x-token": getToken() } });
    const t = r.ok ? await r.json() : {};
    if ((_activeProfile() || {}).id !== asked) return;      // switched profile meanwhile
    if (t && t.whatsapp) {
      _waTarget = { ...t, name: prof.name || t.name };
      const lbl = "WhatsApp " + String(prof.name || t.name).split(" ")[0].replace(/[.,]$/, "");
      if (btn) btn.textContent = lbl;
      const cb = document.getElementById("wa-btn-carousel"); if (cb) cb.textContent = lbl;
    }
  } catch (_) {}
}
// What a prospect gets on WhatsApp: their post as it will look in the LinkedIn app, as one image.
// Drawn off-screen at phone width in LinkedIn's own font and colours (every line, no fold; the
// attached image or tweet card; hashtags in LinkedIn blue) on the feed's grey, so it reads on a
// phone and comes out the same whatever the size of this window. No made-up likes or comments.
let _h2cLoading = null;
function _loadHtml2Canvas() {
  if (window.html2canvas) return Promise.resolve();
  if (!_h2cLoading) _h2cLoading = new Promise((res, rej) => {
    const sc = document.createElement("script");
    sc.src = "https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js";
    sc.onload = res; sc.onerror = () => { _h2cLoading = null; rej(new Error("image tool didn't load")); };
    document.head.appendChild(sc);
  });
  return _h2cLoading;
}
const _GLOBE_SVG = '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6.3"/><path d="M1.7 8h12.6M8 1.7c2.1 2.3 2.1 10.3 0 12.6M8 1.7c-2.1 2.3-2.1 10.3 0 12.6"/></svg>';
function _el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function _buildShot() {
  const shot = _el("div", "wa-shot"), card = _el("div", "wa-shot-card"), head = _el("div", "wa-shot-head");
  const av = document.getElementById("li-avatar").cloneNode(true);
  av.removeAttribute("id"); av.className = "wa-shot-av";
  const who = _el("div", "wa-shot-who"), title = document.getElementById("li-title");
  who.append(_el("div", "wa-shot-name", document.getElementById("li-name").textContent));
  if (title && !title.hidden && title.textContent.trim()) who.append(_el("div", "wa-shot-title", title.textContent));
  const when = _el("div", "wa-shot-when", "Now • ");
  when.insertAdjacentHTML("beforeend", _GLOBE_SVG);
  who.append(when);
  head.append(av, who, _el("div", "wa-shot-dots", "•••"));
  const text = _el("div", "wa-shot-text");
  _postText().split(/(#[\p{L}_][\p{L}\p{N}_]*)/u).forEach((part, i) =>
    text.append(i % 2 ? _el("span", "wa-shot-tag", part) : document.createTextNode(part)));
  card.append(head, text);
  const media = document.getElementById("li-media"), img = document.getElementById("li-attach-img");
  if (media && getComputedStyle(media).display !== "none" && img && img.getAttribute("src")) {
    const pic = _el("img", "wa-shot-img");
    pic.src = img.src;
    card.append(pic);
  }
  const bar = document.querySelector("#card-linkedin .li-reactbar");
  if (bar) card.append(bar.cloneNode(true));
  const foot = _el("div", "wa-shot-foot", "Drafted with ");
  foot.append(_el("b", "", "Voyce"));
  shot.append(card, foot);
  return shot;
}
async function _previewImage() {
  await _loadHtml2Canvas();
  if (document.fonts && document.fonts.ready) await document.fonts.ready;
  const shot = _buildShot();
  // Off the page's edge, and moved into view only in html2canvas's copy. (Not visibility:hidden:
  // html2canvas carries that onto the SVG icons, which then come out blank.)
  document.body.appendChild(shot);
  try {
    const canvas = await window.html2canvas(shot, {
      scale: 2.5, backgroundColor: "#f4f2ee", logging: false,
      onclone: doc => { const s = doc.querySelector(".wa-shot"); if (s) s.style.left = "0px"; },
    });
    return await new Promise(res => canvas.toBlob(res, "image/png"));
  } finally {
    shot.remove();
  }
}
function _openChat(to, text) {
  const url = "https://wa.me/" + (to || "") + (text ? "?text=" + encodeURIComponent(text) : "");
  const w = window.open(url, "_blank");
  if (w) { try { w.opener = null; } catch (_) {} return true; }
  toast("The browser blocked WhatsApp from opening: allow pop-ups for voyce.co.in, then press the button again.", "warn");
  return false;
}
function _waIntro(kind) {
  const first = _waTarget ? String(_waTarget.name || "").split(" ")[0].replace(/[.,]$/, "") : "";
  return first ? `Hi ${first}, here's a LinkedIn ${kind} Voyce wrote for you:` : "";
}
async function shareWhatsApp(btn) {
  if (!_postText()) { toast("Generate a post first.", "warn"); return; }
  const to = (_waTarget && _waTarget.whatsapp) || "";
  const label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "Preparing…"; }
  try {
    let png = null;
    try { png = await _previewImage(); } catch (_) {}
    if (!png) { toast("Couldn't make the post image. Use Copy and paste the text instead.", "warn"); return; }
    const file = new File([png], "linkedin-post.png", { type: "image/png" });
    // Nobody in particular, and a share sheet that takes files (phones, Chrome/Edge on Windows): straight in
    if (!to && navigator.canShare && navigator.canShare({ files: [file] })) {
      try { await navigator.share({ files: [file] }); } catch (e) { if (!(e && e.name === "AbortError")) toast("Sharing didn't work here.", "warn"); }
      return;
    }
    // A WhatsApp link carries text only: the post image goes to the clipboard, to paste into the chat
    let copied = false;
    if (navigator.clipboard && window.ClipboardItem) {
      try { await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]); copied = true; } catch (_) {}
    }
    if (!copied) {   // no clipboard for images here: save it, to attach
      const a = document.createElement("a"); a.href = URL.createObjectURL(png); a.download = "linkedin-post.png"; a.click();
    }
    _openChat(to, _waIntro("post"));
    const who = to ? String(_waTarget.name || "").split(" ")[0].replace(/[.,]$/, "") + "'s" : "the";
    toast(copied ? `Post copied as an image: paste it into ${who} chat (Ctrl+V) and send.`
                 : `Post saved as an image: attach it in ${who} chat.`, "info");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}
// Carousels go as the real thing: the PDF
// Carousels go as every slide, as images: in WhatsApp they open as an album you can swipe,
// where a PDF shows as a file icon. The slides come from the carousel PDF (pdf.js).
let _pdfjsLoading = null;
function _loadPdfJs() {
  if (window.pdfjsLib) return Promise.resolve();
  if (!_pdfjsLoading) _pdfjsLoading = new Promise((res, rej) => {
    const sc = document.createElement("script");
    sc.src = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
    sc.onload = () => { window.pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js"; res(); };
    sc.onerror = () => { _pdfjsLoading = null; rej(new Error("slide tool didn't load")); };
    document.head.appendChild(sc);
  });
  return _pdfjsLoading;
}
// Drawn once per carousel: the card shows these slides and WhatsApp sends the very same ones.
let _slidesKey = null, _slidesJob = null;
function _carouselSlides() {
  if (_slidesKey !== _carouselPdfBase64 || !_slidesJob) {
    const key = _slidesKey = _carouselPdfBase64;
    _slidesJob = _drawCarouselSlides(key);
    _slidesJob.catch(() => { if (_slidesKey === key) _slidesJob = null; });   // try again next time
  }
  return _slidesJob;
}
async function _drawCarouselSlides(b64) {
  await _loadPdfJs();
  const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const pdf = await window.pdfjsLib.getDocument({ data: bytes }).promise;
  const out = [];
  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    const vp = page.getViewport({ scale: 1080 / page.getViewport({ scale: 1 }).width });
    const c = document.createElement("canvas"); c.width = Math.round(vp.width); c.height = Math.round(vp.height);
    await page.render({ canvasContext: c.getContext("2d"), viewport: vp }).promise;
    out.push(await new Promise(r => c.toBlob(r, "image/jpeg", 0.9)));
  }
  return out;
}
async function shareCarouselWhatsApp(btn) {
  if (!_carouselPdfBase64) { toast("Generate a carousel first.", "warn"); return; }
  const to = (_waTarget && _waTarget.whatsapp) || "";
  const label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "Preparing slides…"; }
  try {
    let slides = [];
    try { slides = await _carouselSlides(); } catch (_) {}
    if (!slides.length) {   // couldn't draw the slides here: send the PDF instead
      downloadCarousel(); _openChat(to, _waIntro("carousel"));
      toast("Carousel PDF downloaded: attach it in the chat (📎 → Document) and send.", "info");
      return;
    }
    const files = slides.map((b, i) => new File([b], `slide-${i + 1}.jpg`, { type: "image/jpeg" }));
    if (!to && navigator.canShare && navigator.canShare({ files })) {
      try { await navigator.share({ files }); } catch (e) { if (!(e && e.name === "AbortError")) toast("Sharing didn't work here.", "warn"); }
      return;
    }
    // A specific chat on a computer: save the slides, then drag them in (WhatsApp sends them as an album)
    files.forEach((f, i) => setTimeout(() => { const a = document.createElement("a"); a.href = URL.createObjectURL(f); a.download = f.name; a.click(); }, i * 250));
    _openChat(to, _waIntro("carousel"));
    const who = to ? String(_waTarget.name || "").split(" ")[0].replace(/[.,]$/, "") + "'s" : "the";
    toast(`${files.length} slides saved to Downloads: drag them into ${who} chat and they go as an album.`, "info");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

function _resetPostButton() {
  const btn = document.getElementById("post-now-btn");
  if (btn) { btn.disabled = false; btn.textContent = "Post now"; }
}

function saveDraft() {
  _syncDraft();
  const post = _postText();
  if (!post) { localStorage.removeItem(_DRAFT_KEY); return; }
  try {
    localStorage.setItem(_DRAFT_KEY, JSON.stringify(
      _attachB64 ? { post, img: _attachB64, mime: _attachMime } : { post }));
  } catch (_) {
    try { localStorage.setItem(_DRAFT_KEY, JSON.stringify({ post })); } catch (_) {}  // image too big → keep text
  }
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
    if (!folded) { el.classList.remove("li-clamp"); el.style.maxHeight = "none"; }
  });
}
function toggleFold() {
  const el  = document.getElementById("linkedin-content");
  const btn = document.getElementById("li-see-more");
  const nowClamped = el.classList.toggle("li-clamp");
  // Expanded shows the whole post: "" would fall back to the card's 480px cap and an inner scrollbar
  el.style.maxHeight = nowClamped ? _foldHeight(el) + "px" : "none";
  btn.textContent = nowClamped ? "…see more" : "see less";
}

function renderOutputs(data) {
  document.getElementById("linkedin-content").textContent = data.linkedin_post || "";
  _newDraft("post", { text: data.linkedin_post || "" });
  _loadHtml2Canvas().catch(() => {});
  _resetPostButton();   // a new post can be posted, even after the last one was
  _makeEditable();
  _applyFold();
  _setPreviewAuthor();
  if (!_attachIsUpload) attachRemove();   // keep a user-uploaded image; clear AI-generated ones
  saveDraft();
  // Fresh post → the old rewrites and hashtag suggestions belong to the old text
  hideVariants();
  window._tagPool = null;
  hideHashtags();
  _syncToolRuns();
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
  const post = _postText().trim();
  if (!post) { toast("Generate a post first, then I can rewrite it.", "warn"); return; }
  showTool("rewrites");
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
  _markToolFilled("rewrites", list.length > 1);
  row.innerHTML = `
    <div class="cap-head"><span>Tap one to load it into the post.</span></div>
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
  window._variants = null;
  _markToolFilled("rewrites", false);
}

// ── The tool panel ────────────────────────────────────────────────────────────
// Four tabs sharing one panel beside the work column. Each tool used to inject
// itself between the preview card's header and the post, which squeezed it into
// a gap and interrupted the thing that is meant to look like a LinkedIn post.
function showTool(name) {
  document.querySelectorAll(".tool-tab").forEach(t =>
    t.classList.toggle("active", t.dataset.tool === name));
  document.querySelectorAll(".tool-pane").forEach(p =>
    p.classList.toggle("active", p.id === `tool-${name}`));
  _syncToolRuns();
  // Only matters on narrow screens, where the panel sits below the work column.
  if (window.innerWidth < 1200) {
    const t = document.getElementById("work-tools");
    if (t) t.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

// A green dot on a tab means that tool has something in it right now.
function _markToolFilled(name, filled) {
  const tab = document.querySelector(`.tool-tab[data-tool="${name}"]`);
  if (tab) tab.classList.toggle("filled", !!filled);
}

// Rewrites and hashtags both need a post to work on. Rather than letting them
// fail with a toast, the buttons say so.
function _syncToolRuns() {
  const el = document.getElementById("linkedin-content");
  const hasPost = !!(el && (el.textContent || "").trim());
  [["rewrites-run", "⟳ Rewrite this post"], ["hashtags-run", "# Suggest hashtags"]].forEach(([id, label]) => {
    const b = document.getElementById(id);
    if (!b) return;
    b.disabled = !hasPost;
    b.textContent = hasPost ? label : "Generate a post first";
  });
  const ideas = document.getElementById("ideas-run");
  if (ideas) ideas.disabled = !(_profiles && _profiles.length);
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
  showTool("hashtags");
  const row = document.getElementById("hashtags-row");
  // Already fetched once: re-render from the pool, no second round trip.
  if (window._tagPool && window._tagPool.length) { _renderHashtags(); return; }
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
  _markToolFilled("hashtags", active.length > 0);
  row.innerHTML = `
    <div class="cap-head">
      <span>Tap to add or remove. They go on the post's last line.</span>
      <button type="button" class="cap-x" onclick="refreshHashtags()" aria-label="Suggest again" title="Suggest again">↻</button>
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
  suggestHashtags();
}

function hideHashtags() {
  const row = document.getElementById("hashtags-row");
  if (row) { row.style.display = "none"; row.innerHTML = ""; }
  _markToolFilled("hashtags", false);
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

// The empty preview and the tools panel are as tall as Compose (CSS reads --compose-h), so the
// columns line up; it follows Compose as it changes (the picker appearing, a resized text box).
(function () {
  const card = document.querySelector(".input-card");
  if (!card || !window.ResizeObserver) return;
  new ResizeObserver(() => document.documentElement.style.setProperty("--compose-h", card.getBoundingClientRect().height + "px")).observe(card);
})();

// ── Recent drafts: every post and carousel made in this browser, as small cards under the preview ──
// In IndexedDB, not localStorage: a draft keeps its image or carousel PDF and who it was written as,
// so opening one brings back exactly what was made, with the right photo and WhatsApp chat.
// The old text-only history (cm_history) moves in once.
const _DRAFTS_MAX = 60;
let _drafts = [], _draftsAll = false, _draftsDb = null;
let _activeDraftId = null, _activeCarouselDraftId = null;   // what the preview and the carousel card show
function _draftsStore(mode) {
  if (!_draftsDb) _draftsDb = new Promise((res, rej) => {
    const r = indexedDB.open("voyce", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("drafts", { keyPath: "id" });
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
  return _draftsDb.then(db => db.transaction("drafts", mode).objectStore("drafts"));
}
function _idbReq(req) {
  return new Promise((res, rej) => { req.onsuccess = () => res(req.result); req.onerror = () => rej(req.error); });
}
async function _putDraft(d) { try { await _idbReq((await _draftsStore("readwrite")).put(d)); } catch (_) {} }
async function _dropDraft(id) { try { await _idbReq((await _draftsStore("readwrite")).delete(id)); } catch (_) {} }
function _uid() { return (getUser() || {}).id || ""; }
async function _loadDrafts() {
  try {
    const all = await _idbReq((await _draftsStore("readonly")).getAll());
    _drafts = all.filter(d => d.uid === _uid());   // a shared computer: only this account's drafts
    let old = [];
    try { old = JSON.parse(localStorage.getItem("cm_history") || "[]"); } catch (_) {}
    const add = old.filter(o => o && o.text && !_drafts.some(d => d.id === o.ts))
                   .map(o => ({ id: o.ts, uid: _uid(), kind: "post", text: o.text }));
    if (add.length) {
      const st = await _draftsStore("readwrite");
      await Promise.all(add.map(d => _idbReq(st.put(d))));
      _drafts.push(...add);
    }
    if (old.length) localStorage.removeItem("cm_history");
  } catch (_) {}   // no IndexedDB here (some private windows): drafts last for this visit
  _drafts.sort((a, b) => b.id - a.id);
  _renderDrafts();
}
function _newDraft(kind, fields) {
  const prof = _activeProfile(), a = _authorCache[(prof && prof.id) || "_me"] || {};
  const id = Math.max(Date.now(), _drafts.length ? _drafts[0].id + 1 : 0);   // unique: it's the store's key
  const d = { id, uid: _uid(), kind, profileId: prof ? prof.id : "",
              name: a.name || (prof && prof.name) || (getUser() || {}).name || "", photo: a.photo || "", ...fields };
  _drafts.unshift(d);
  _drafts.splice(_DRAFTS_MAX).forEach(x => _dropDraft(x.id));
  _putDraft(d);
  if (kind === "carousel") _activeCarouselDraftId = d.id; else _activeDraftId = d.id;
  _renderDrafts();
}
// The open draft follows edits, an added or removed image, and the author's photo once it loads.
function _syncDraft() {
  const d = _drafts.find(x => x.id === _activeDraftId);
  if (!d) return;
  const text = _postText().trim();
  if (!text) return;
  const a = _authorCache[d.profileId || "_me"] || {};
  const next = { text, img: _attachB64 || "", mime: _attachMime, upload: _attachIsUpload,
                 photo: a.photo || d.photo || "", name: a.name || d.name || "" };
  if (Object.keys(next).every(k => d[k] === next[k])) return;
  Object.assign(d, next);
  _putDraft(d);
  _renderDrafts();
}
function openDraft(id) {
  const d = _drafts.find(x => x.id === id);
  if (!d) return;
  // Back to the person it was written as: the preview's name and photo, and the WhatsApp chat
  if (d.profileId && d.profileId !== getActiveProfileId() && _profiles.some(p => p.id === d.profileId)) pickProfile(d.profileId);
  if (d.kind === "carousel") {
    _activeCarouselDraftId = d.id;
    _showCarouselResult({ pdf_base64: d.pdf, post_text: d.text, hook: d.hook });
  } else {
    _activeDraftId = d.id;   // edits from here update this same draft
    const el = document.getElementById("linkedin-content");
    el.textContent = d.text;
    _resetPostButton(); _makeEditable(); _applyFold();
    _attachB64 = d.img || null; _attachMime = d.mime || "image/png"; _attachIsUpload = !!d.upload;
    if (d.img) _renderAttachment(`data:${_attachMime};base64,${d.img}`); else _attachState("empty");
    saveDraft();
    hideVariants(); window._tagPool = null; hideHashtags();   // they belonged to the other post
    document.getElementById("output-section").classList.add("visible");
    _syncToolRuns();
    document.getElementById("output-section").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  _renderDrafts();
}
function deleteDraft(id) {
  _drafts = _drafts.filter(d => d.id !== id);
  if (_activeDraftId === id) _activeDraftId = null;
  if (_activeCarouselDraftId === id) _activeCarouselDraftId = null;
  _dropDraft(id);
  _renderDrafts();
}
function _ago(ts) {
  const s = (Date.now() - ts) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  if (s < 172800) return "yesterday";
  return new Date(ts).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
function _draftCard(d) {
  const open = d.id === (d.kind === "carousel" ? _activeCarouselDraftId : _activeDraftId);
  const c = _el("div", "draft-card" + (open ? " open" : ""));
  c.tabIndex = 0; c.setAttribute("role", "button"); c.title = "Open this draft";
  c.onclick = () => openDraft(d.id);
  c.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDraft(d.id); } };
  if (d.kind === "carousel") {
    const t = _el("div", "draft-thumb draft-slide");
    t.append(_el("span", "", d.hook || (d.text || "").split("\n")[0]));
    c.append(t);
  } else if (d.img) {
    const t = _el("div", "draft-thumb"), im = _el("img");
    im.alt = ""; im.loading = "lazy"; im.src = `data:${d.mime || "image/png"};base64,${d.img}`;
    t.append(im); c.append(t);
  }
  // Drafts from the old history don't say who they were written as
  const who = _el("div", "draft-who" + (d.name ? "" : " anon")), av = _el("span", "draft-av");
  if (d.photo) { const im = _el("img"); im.alt = ""; im.src = d.photo; av.append(im); }
  else av.textContent = ((d.name || "?").trim()[0] || "?").toUpperCase();
  who.append(av, _el("span", "draft-name", d.name || "Earlier draft"));
  c.append(who);
  c.append(_el("div", "draft-text" + (d.kind === "post" && !d.img ? " long" : ""), (d.text || "").replace(/\n\s*\n+/g, "\n")));
  const foot = _el("div", "draft-foot");
  foot.append(_el("span", "draft-kind", d.kind === "carousel" ? "Carousel" : d.img ? "Post · image" : "Post"),
              _el("span", open ? "draft-open" : "", open ? "Open" : _ago(d.id)));
  c.append(foot);
  const del = _el("button", "draft-del", "×");
  del.type = "button"; del.title = "Remove this draft"; del.setAttribute("aria-label", "Remove this draft");
  del.onclick = e => { e.stopPropagation(); deleteDraft(d.id); };
  c.append(del);
  return c;
}
// Two rows to start with; the rest behind "Show all"
function _capDrafts() {
  const grid = document.getElementById("drafts-grid"), more = document.getElementById("drafts-more");
  if (!grid) return;
  const tpl = getComputedStyle(grid).gridTemplateColumns;
  const cap = 2 * (tpl && tpl !== "none" ? tpl.split(" ").filter(Boolean).length : 3);
  const cards = [...grid.querySelectorAll(".draft-card")];
  cards.forEach((c, i) => { c.hidden = !_draftsAll && i >= cap; });
  if (more) {
    more.style.display = cards.length > cap ? "" : "none";
    more.textContent = _draftsAll ? "Show fewer" : `Show all ${cards.length}`;
  }
}
// Drafts follow "Writing as": one person's samples at a time, everyone's a click away under All.
// Drafts from the old history don't say who they were for, so they show under All only.
let _draftsScope = "profile";
function _scopedDrafts() {
  if (_draftsScope === "all" || _profiles.length < 2) return _drafts;
  const pid = getActiveProfileId();
  return _drafts.filter(d => d.profileId === pid);
}
function setDraftsScope(scope) { _draftsScope = scope; _draftsAll = false; _renderDrafts(); }
function _scopeBtn(scope, label) {
  const b = _el("button", _draftsScope === scope ? "on" : "", label);
  b.type = "button";
  b.onclick = () => setDraftsScope(scope);
  return b;
}
function _renderDrafts() {
  const sec = document.getElementById("drafts-section"), grid = document.getElementById("drafts-grid");
  if (!sec || !grid) return;
  sec.style.display = _drafts.length ? "" : "none";
  const list = _scopedDrafts(), prof = _activeProfile(), split = _profiles.length >= 2;
  const count = document.getElementById("drafts-count"), filter = document.getElementById("drafts-filter");
  count.textContent = list.length;
  count.hidden = split;   // the filter carries the counts
  if (filter) {
    filter.hidden = !split;
    if (split) {
      const mine = _drafts.filter(d => d.profileId === (prof && prof.id)).length;
      filter.replaceChildren(_scopeBtn("profile", `${String((prof && prof.name) || "This profile").split(" ")[0]} · ${mine}`),
                             _scopeBtn("all", `All · ${_drafts.length}`));
    }
  }
  if (list.length) grid.replaceChildren(...list.map(_draftCard));
  else grid.replaceChildren(_el("div", "drafts-empty", `No drafts as ${(prof && prof.name) || "this profile"} yet. Generate one and it shows up here.`));
  _capDrafts();
}
function toggleAllDrafts() { _draftsAll = !_draftsAll; _capDrafts(); }
window.addEventListener("resize", () => { clearTimeout(window._draftsRz); window._draftsRz = setTimeout(_capDrafts, 150); });

// ── Post Now ──────────────────────────────────────────────────────────────────
async function postToLinkedIn() {
  if (!_ensurePostingConnected(postToLinkedIn)) return;
  const text = _postText();
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
      fd.append("profile_id", getActiveProfileId());
      res = await fetch("/post/linkedin/image", { method: "POST", headers: { "x-token": getToken() }, body: fd });
    } else {
      res = await fetch("/post/linkedin", {
        method: "POST", headers: authHeaders(),
        body: JSON.stringify({ text, profile_id: getActiveProfileId() })
      });
    }
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to post.");
    btn.textContent = "Posted!";
    btn.classList.add("primary");
    toast("Posted to LinkedIn!", "success");
  } catch (err) {
    toast(err.message, "error");
    _resetPostButton();
  }
}

// ── Schedule ──────────────────────────────────────────────────────────────────
function openSchedule() {
  if (!_ensurePostingConnected(openSchedule)) return;
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
        text: _postText(),
        schedule_time: new Date(val).toISOString(),
        profile_id: getActiveProfileId()
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
      <span class="queue-preview">${_escHtmlCreate(p.preview)}</span>
      <span class="queue-time">${new Date(p.scheduled_at).toLocaleString()}</span>
      <span class="queue-status ${statusKey}">${_escHtmlCreate(p.status)}</span>
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
    const res = await fetch("/generate/carousel", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ input_type: activeType, content, profile_id: getActiveProfileId() })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Carousel generation failed.");
    _showCarouselResult(data);
    _newDraft("carousel", { text: data.post_text || "", pdf: data.pdf_base64, hook: data.hook || "" });
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
  _showCarouselSlides();
  markGenerated();
  document.getElementById("carousel-section").scrollIntoView({ behavior: "smooth", block: "start" });
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
// Every slide, right in the carousel card (it used to show only the caption: the slides were a
// download or a new tab away). Click one to open the deck at that page.
// Shown the way LinkedIn shows a carousel: one slide at a time from the cover, with arrows, a
// counter and a progress bar, since the cover is what earns the swipe. "All N slides" lays
// them out side by side for proofreading.
let _slideUrls = [], _slideIdx = 0, _slidesAll = false;
async function _showCarouselSlides() {
  const box = document.getElementById("carousel-slides");
  if (!box) return;
  const key = _carouselPdfBase64;
  _slideUrls.forEach(u => URL.revokeObjectURL(u));
  _slideUrls = [];
  box.replaceChildren(_el("div", "slides-note", "Drawing the slides…"));
  try {
    const blobs = await _carouselSlides();
    if (key !== _carouselPdfBase64) return;   // a newer carousel took its place
    _slideUrls = blobs.map(b => URL.createObjectURL(b));
    _slideIdx = 0;
    _slidesAll = false;
    _paintSlides();
  } catch (_) {
    if (key === _carouselPdfBase64) box.replaceChildren(_el("div", "slides-note", "Couldn't draw the slides here. View slides opens them."));
  }
}
function _paintSlides() {
  const box = document.getElementById("carousel-slides");
  const n = _slideUrls.length;
  if (!box || !n) return;
  const mode = _el("button", "slides-mode", _slidesAll ? "◫ One at a time" : `▦ All ${n} slides`);
  mode.type = "button";
  mode.onclick = () => { _slidesAll = !_slidesAll; _paintSlides(); };
  if (_slidesAll) {
    const grid = _el("div", "slides-grid");
    grid.append(..._slideUrls.map((u, i) => {
      const im = _el("img");
      im.src = u; im.alt = `Slide ${i + 1}`; im.title = `Slide ${i + 1}`;
      im.onclick = () => { _slideIdx = i; _slidesAll = false; _paintSlides(); };
      return im;
    }));
    box.replaceChildren(grid, mode);
    return;
  }
  const viewer = _el("div", "slide-viewer"), stage = _el("div", "slide-stage"), im = _el("img");
  im.alt = ""; im.title = "Click to see it large";
  im.draggable = false;
  im.onclick = () => viewCarouselSlides(_slideIdx + 1);
  const nav = (cls, label, d) => {
    const b = _el("button", "slide-nav " + cls, label);
    b.type = "button"; b.setAttribute("aria-label", d < 0 ? "Previous slide" : "Next slide");
    b.onclick = () => _goSlide(d);
    return b;
  };
  const prev = nav("prev", "‹", -1), next = nav("next", "›", 1), count = _el("span", "slide-count");
  const bar = _el("div", "slide-progress"), fill = _el("i");
  bar.append(fill);
  stage.tabIndex = 0;
  stage.setAttribute("aria-label", "Carousel slides: use the arrow keys");
  stage.onkeydown = e => { if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); _goSlide(e.key === "ArrowLeft" ? -1 : 1); } };
  let sx = null, sy = 0;   // swipe on touch screens
  stage.onpointerdown = e => { sx = e.clientX; sy = e.clientY; };
  stage.onpointerup = e => {
    if (sx === null) return;
    const dx = e.clientX - sx, dy = e.clientY - sy;
    sx = null;
    if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) _goSlide(dx < 0 ? 1 : -1);
  };
  stage.append(im, prev, next, count);
  viewer.append(stage, bar);
  box.replaceChildren(viewer, mode);
  _slideEls = { im, prev, next, count, fill };
  _goSlide(0);
}
let _slideEls = null;
function _goSlide(d) {
  const n = _slideUrls.length;
  if (!n || !_slideEls) return;
  _slideIdx = Math.max(0, Math.min(n - 1, _slideIdx + d));
  const { im, prev, next, count, fill } = _slideEls;
  im.src = _slideUrls[_slideIdx];
  im.alt = `Slide ${_slideIdx + 1} of ${n}`;
  prev.hidden = _slideIdx === 0;
  next.hidden = _slideIdx === n - 1;
  count.textContent = `${_slideIdx + 1} / ${n}`;
  fill.style.width = `${((_slideIdx + 1) / n) * 100}%`;
}
function viewCarouselSlides(page) {
  if (!_carouselPdfBase64) { toast("Generate a carousel first.", "warn"); return; }
  try {
    if (_carouselBlobUrl) URL.revokeObjectURL(_carouselBlobUrl);
    const bytes = Uint8Array.from(atob(_carouselPdfBase64), c => c.charCodeAt(0));
    _carouselBlobUrl = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
    const w = window.open(_carouselBlobUrl + (typeof page === "number" ? "#page=" + page : ""), "_blank");
    if (!w) toast("Your browser blocked the popup — use Download PDF instead.", "warn");
  } catch (_) {
    toast("Couldn't open the slides. Download the PDF instead.", "error");
  }
}

async function postCarousel() {
  if (!_ensurePostingConnected(postCarousel)) return;
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
    formData.append("profile_id", getActiveProfileId());

    const res = await fetch("/post/linkedin/carousel", {
      method: "POST",
      headers: { "x-token": getToken() },
      body: formData
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to post carousel.");
    btn.textContent = "Posted!";
    toast("Carousel posted to LinkedIn!", "success");
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
  const post = _postText().trim();
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
      if (data.detail === "LIMIT_REACHED" || String(data.detail || "").startsWith("PASS_LIMIT_REACHED:")) {
        toast(data.detail === "LIMIT_REACHED" ? "You've used all 5 free generations — upgrade to keep going."
          : String(data.detail).slice("PASS_LIMIT_REACHED:".length), "warn");
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
function copyContent(id, btn) {
  const el = document.getElementById(id);
  // Not innerText: the clamped preview visually hides lines, but the clipboard
  // must always get the full post — with any line breaks the user typed.
  navigator.clipboard.writeText(el.isContentEditable ? _editorText(el) : el.textContent).then(() => {
    // The button has to be passed in. This used to grab the card's first
    // .action-btn, which stopped being the Copy button the moment anything was
    // added ahead of it — so Copy was relabelling Rewrites as "Copied!".
    const target = btn || el.closest(".output-card").querySelector(".action-btn");
    if (!target) return;
    const label = target.textContent;
    target.textContent = "Copied!";
    target.classList.add("copied");
    setTimeout(() => { target.textContent = label; target.classList.remove("copied"); }, 2000);
  });
}

// ── Sign out ──────────────────────────────────────────────────────────────────
async function doAppLogout() {
  if (window.vReset) vReset();
  try { await fetch("/auth/logout", { method: "POST", headers: { "x-token": getToken() } }); } catch (_) {}
  localStorage.removeItem("cm_user");
  if (window.Clerk) { try { await window.Clerk.signOut(); } catch (_) {} }
  window.location.href = "/login";
}
