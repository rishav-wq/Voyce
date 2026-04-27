// ── Auth ──────────────────────────────────────────────────────────────────────
function getToken() { return localStorage.getItem("cm_token") || ""; }
function getUser()  { try { return JSON.parse(localStorage.getItem("cm_user") || "null"); } catch { return null; } }
function authHeaders(extra) { return { "Content-Type": "application/json", "x-token": getToken(), ...(extra||{}) }; }

function checkAuth() {
  if (!getToken()) { window.location.href = "/login"; return false; }
  return true;
}

let activeType = "text";
let linkedInConnected = false;

function isDryRun() {
  return document.getElementById("dry-run-checkbox").checked;
}

document.getElementById("dry-run-checkbox").addEventListener("change", () => {
  const btn = document.getElementById("post-now-btn");
  if (btn) { btn.textContent = "Post Now"; btn.classList.remove("primary"); btn.disabled = false; }
});

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
  el.textContent = msg;
  el.style.display = "block";
  setTimeout(() => el.style.display = "none", 5000);
}

// ── Init ──────────────────────────────────────────────────────────────────────
window.addEventListener("load", () => {
  if (!checkAuth()) return;
  const user = getUser();
  if (user) {
    const tag = document.getElementById("user-tag");
    if (tag) tag.textContent = user.name || user.email;
  }
  checkLinkedInStatus();
  loadQueue();
});

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
}

function handleLiClick() {
  if (linkedInConnected) {
    if (!confirm("Disconnect LinkedIn?")) return;
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
    document.getElementById("output-section").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    showError(err.message);
  } finally {
    btn.disabled = false;
    btnText.style.display = "inline";
    btnLoad.style.display = "none";
  }
}

function renderOutputs(data) {
  document.getElementById("linkedin-content").textContent = data.linkedin_post || "";

  const twitterEl = document.getElementById("twitter-content");
  twitterEl.innerHTML = "";
  const tweets = Array.isArray(data.twitter_thread) ? data.twitter_thread : [data.twitter_thread];
  tweets.forEach(tw => {
    const div = document.createElement("div");
    div.className = "tweet";
    div.textContent = tw;
    twitterEl.appendChild(div);
  });

  document.getElementById("email-content").textContent = data.email_snippet || "";
  document.getElementById("blog-content").textContent  = data.blog_summary  || "";
}

// ── Post Now ──────────────────────────────────────────────────────────────────
async function postToLinkedIn() {
  if (!linkedInConnected) { toast("LinkedIn not connected — click the pill in the nav to connect.", "warn"); return; }
  const text = document.getElementById("linkedin-content").textContent;
  if (!text) { toast("Generate content first.", "warn"); return; }

  const btn = document.getElementById("post-now-btn");
  btn.disabled = true;
  btn.textContent = "Posting…";

  try {
    const res = await fetch("/post/linkedin", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ text, dry_run: isDryRun() })
    });
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
  if (!linkedInConnected) { toast("Connect LinkedIn first.", "warn"); return; }
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
    const res = await fetch("/schedule/list");
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
  await fetch(`/schedule/${jobId}`, { method: "DELETE" });
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
    document.getElementById("carousel-section").scrollIntoView({ behavior: "smooth", block: "start" });
    toast("Carousel ready! Download the PDF to preview.", "success");
  } catch (err) {
    showError(err.message);
  } finally {
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
  if (!linkedInConnected) { toast("LinkedIn not connected — click the pill in the nav to connect.", "warn"); return; }
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
  await fetch("/auth/logout", { method: "POST", headers: { "x-token": getToken() } });
  localStorage.clear();
  window.location.href = "/login";
}
