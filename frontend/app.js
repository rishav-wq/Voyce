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
  try {
    const res = await fetch("/auth/me", { headers: { "x-token": _clerkToken } });
    if (res.ok) localStorage.setItem("cm_user", JSON.stringify(await res.json()));
  } catch (_) {}
  return true;
}

function getToken() { return _clerkToken; }
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
    el.innerHTML = `You've used all 5 free generations. <a href="#" onclick="openUpgradeModal();return false;" style="color:#6c47ff;font-weight:700;">Upgrade to Pro</a> for unlimited generations.`;
  } else {
    el.textContent = msg;
  }
  el.style.display = "block";
  setTimeout(() => el.style.display = "none", 10000);
}

// ── Init (called by Clerk script onload) ─────────────────────────────────────
async function startApp() {
  if (!(await initClerk())) return;
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
  const gen = document.getElementById("pstep-generate");
  const li  = document.getElementById("pstep-linkedin");
  if (!gen || !li) return;
  const liText = document.getElementById("linkedin-content");
  const carSec = document.getElementById("carousel-section");
  const hasGen = !!(liText && liText.textContent.trim()) ||
                 !!(carSec && carSec.classList.contains("visible"));
  gen.classList.toggle("done", hasGen);
  gen.classList.toggle("active", !hasGen);
  li.classList.toggle("done", linkedInConnected);
  li.classList.toggle("active", hasGen && !linkedInConnected);
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

function connectLinkedIn() {
  const popup = window.open(`/auth/linkedin?token=${getToken()}`, "linkedin-auth", "width=600,height=700,scrollbars=yes");
  if (!popup) toast("Popup blocked — please allow popups for this site.", "warn");
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
      body: JSON.stringify({ input_type: activeType, content })
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
  _setPreviewAuthor();
  if (d.img) {
    _attachB64 = d.img; _attachMime = d.mime || "image/png";
    _renderAttachment("data:" + _attachMime + ";base64," + d.img);
  }
  const sec = document.getElementById("output-section");
  if (sec) sec.classList.add("visible");
  updateProgress();
}

function renderOutputs(data) {
  document.getElementById("linkedin-content").textContent = data.linkedin_post || "";
  _setPreviewAuthor();
  if (!_attachIsUpload) attachRemove();   // keep a user-uploaded image; clear AI-generated ones
  saveDraft();
  updateProgress();
}

// ── Post Now ──────────────────────────────────────────────────────────────────
async function postToLinkedIn() {
  if (!linkedInConnected) { toast("Connect LinkedIn first — opening the connect window…", "warn"); connectLinkedIn(); return; }
  const text = document.getElementById("linkedin-content").textContent;
  if (!text) { toast("Generate content first.", "warn"); return; }

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
      body: JSON.stringify({ input_type: activeType, content })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Carousel generation failed.");

    _carouselPdfBase64 = data.pdf_base64;
    document.getElementById("carousel-post-text").textContent = data.post_text || "";
    const hookEl = document.getElementById("carousel-hook-label");
    hookEl.textContent = data.hook ? `"${data.hook}"` : "";
    const postBtn = document.getElementById("post-carousel-btn");
    postBtn.textContent = "Post to LinkedIn";
    postBtn.disabled = false;
    document.getElementById("carousel-section").classList.add("visible");
    markGenerated();
    document.getElementById("carousel-section").scrollIntoView({ behavior: "smooth", block: "start" });
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

function downloadCarousel() {
  if (!_carouselPdfBase64) { toast("Generate a carousel first.", "warn"); return; }
  const a = document.createElement("a");
  a.href = "data:application/pdf;base64," + _carouselPdfBase64;
  a.download = "voyce-carousel.pdf";
  a.click();
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

  _attachState("loading");
  try {
    const res = await fetch("/generate/image", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ input_type: "text", content: source, style: "illustration" })
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
    toast(err.message, "error");
    _attachState(_attachB64 ? "preview" : "controls");
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
  navigator.clipboard.writeText(el.innerText).then(() => {
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
