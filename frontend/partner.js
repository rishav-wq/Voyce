// A partner's address only (branding.py loads this when window.PARTNER is set): seats by invite.
// Someone signed in without a seat takes the one they were invited to, or is told how to get one; the
// partner's team goes to its panel. Every page calls partnerGate(user) right after it knows who's signed in,
// and stops when it returns true.
(function () {
  const P = window.PARTNER;
  if (!P) return;
  const KEY = "cm_invite";

  // An invite link (/signup?invite=…) is remembered through sign-up, which can land on any page.
  try {
    const t = new URLSearchParams(location.search).get("invite");
    if (t) localStorage.setItem(KEY, t);
  } catch (_) {}

  function invite() { try { return localStorage.getItem(KEY) || ""; } catch (_) { return ""; } }
  function forget() { try { localStorage.removeItem(KEY); } catch (_) {} }

  async function token() {
    try { return await window.Clerk.session.getToken(); } catch (_) { return ""; }
  }

  function screen(title, lines, buttons) {
    const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const wrap = document.createElement("div");
    wrap.id = "partner-gate";
    wrap.style.cssText = "position:fixed;inset:0;z-index:5000;background:#f6f7f9;display:flex;align-items:center;" +
      "justify-content:center;padding:24px;font-family:Inter,system-ui,sans-serif;";
    wrap.innerHTML =
      `<div style="max-width:440px;width:100%;background:#fff;border:1px solid #e4e7ec;border-radius:16px;padding:32px 28px;text-align:center;">
        <img src="${esc(P.logo_on_light)}" alt="${esc(P.brand)}" style="height:26px;margin:0 auto 22px;display:block;">
        <h1 style="font-size:20px;margin:0 0 10px;color:${esc(P.theme.ink)};">${esc(title)}</h1>
        ${lines.map(l => `<p style="font-size:15px;line-height:1.55;color:#5b6270;margin:0 0 12px;">${l}</p>`).join("")}
        <div style="display:flex;gap:10px;justify-content:center;margin-top:18px;flex-wrap:wrap;">${buttons}</div>
      </div>`;
    document.body.appendChild(wrap);
  }
  const btn = (label, onclick, primary) =>
    `<button onclick="${onclick}" style="border:${primary ? "none" : "1px solid #d3d8e0"};background:${primary ? P.theme.orange : "#fff"};` +
    `color:${primary ? "#fff" : "#16181d"};font:600 14px Inter,system-ui,sans-serif;padding:11px 18px;border-radius:10px;cursor:pointer;">${label}</button>`;
  const mail = `<a href="mailto:${P.support_email}" style="color:${P.theme.ink};font-weight:600;">${P.support_email}</a>`;

  window.partnerSignOut = async function () {
    forget();
    try { localStorage.removeItem("cm_user"); await window.Clerk.signOut(); } catch (_) {}
    location.href = "/login";
  };

  async function join(user, t) {
    let res, data = {};
    try {
      res = await fetch("/partner/join", { method: "POST", headers: { "x-token": await token(), "Content-Type": "application/json" },
                                           body: JSON.stringify({ token: t }) });
      data = await res.json().catch(() => ({}));
    } catch (_) { res = null; }
    if (res && res.ok) {
      forget();
      try {   // the seat is theirs: refresh what the page knows, then carry on (onboarding first for a new account)
        const me = await fetch("/auth/me", { headers: { "x-token": await token() } });
        if (me.ok) { const u = await me.json(); localStorage.setItem("cm_user", JSON.stringify(u));
                     location.replace(u.needs_onboarding ? "/onboarding" : location.pathname); return; }
      } catch (_) {}
      location.reload();
      return;
    }
    forget();
    screen("This invite didn't work", [String((data && data.detail) || "Something went wrong. Try the link again.").replace(/</g, "&lt;"),
                                       `Need help? Write to ${mail}.`], btn("Sign out", "partnerSignOut()", true));
  }

  // true: the page must stop here (a screen is showing, or the browser is moving on)
  window.partnerGate = async function (user) {
    if (!user || !user.gen_info) return false;
    const team = !!(user.partner && user.partner.team);
    if (user.gen_info.seat === "active") {
      forget();
      if (team) addPanelLink();
      return false;
    }
    const t = invite();
    if (t) { await join(user, t); return true; }
    if (team) {
      if (location.pathname !== "/partner") { location.replace("/partner"); return true; }
      return false;
    }
    if (user.gen_info.seat === "removed") {
      screen(`Your ${P.brand} seat has ended`, [`${P.company} has ended your seat, so you can't write new posts here.`,
             `Questions? Write to ${mail}.`], btn("Sign out", "partnerSignOut()", true));
    } else {
      screen(`${P.brand} is by invitation`, [`Your account is ready, but it doesn't have a seat yet. ${P.company} invites each person by email.`,
             `Ask them for an invite, or write to ${mail}. Already invited? Open the link in that email.`],
             btn("Sign out", "partnerSignOut()", true));
    }
    return true;
  };

  // Before a client first connects LinkedIn: what connecting means, and a clear yes (LinkedIn API terms 5.2).
  // The yes has to be the click that opens LinkedIn's window, or the browser blocks the window, so it runs
  // the page's own connect (fn) straight away; the yes goes with that request and the server records it.
  window.partnerConsentFirst = function (fn) {
    let u = null;
    try { u = JSON.parse(localStorage.getItem("cm_user") || "null"); } catch (_) {}
    if (window.__liConsent || (u && u.li_consent_at)) return false;
    const wrap = document.createElement("div");
    wrap.id = "partner-consent";
    wrap.style.cssText = "position:fixed;inset:0;z-index:5000;background:rgba(10,23,57,.45);display:flex;align-items:center;" +
      "justify-content:center;padding:20px;font-family:Inter,system-ui,sans-serif;";
    const li = s => `<li style="margin:0 0 8px;">${s}</li>`;
    wrap.innerHTML =
      `<div role="dialog" aria-modal="true" aria-labelledby="pc-h" style="max-width:480px;width:100%;background:#fff;border-radius:16px;padding:26px 24px;">
        <h2 id="pc-h" style="font-size:19px;margin:0 0 12px;color:${P.theme.ink};">Before you connect LinkedIn</h2>
        <ul style="font-size:14.5px;line-height:1.5;color:#3d4250;padding-left:18px;margin:0 0 14px;">
          ${li(`${P.brand} posts to your LinkedIn only when you tap <b>Approve</b> or <b>Post</b>. Nothing goes out on its own.`)}
          ${li(`It uses your LinkedIn sign-in to publish those posts and to show your name and photo on your drafts.`)}
          ${li(`${P.company}'s team can see your drafts and published posts in ${P.brand}, to help you. They can't post, approve or edit for you.`)}
          ${li(`You can disconnect any time in LinkedIn (Settings → Data privacy → Permitted services), and ask ${P.company} to delete your data.`)}
        </ul>
        <p style="font-size:13px;color:#5b6270;margin:0 0 18px;">More in the <a href="/privacy" target="_blank" style="color:${P.theme.ink};">privacy notice</a>.</p>
        <div style="display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap;">
          <button id="pc-no" style="border:1px solid #d3d8e0;background:#fff;color:#16181d;font:600 14px Inter,system-ui,sans-serif;padding:11px 16px;border-radius:10px;cursor:pointer;">Not now</button>
          <button id="pc-yes" style="border:none;background:${P.theme.orange};color:#fff;font:600 14px Inter,system-ui,sans-serif;padding:11px 16px;border-radius:10px;cursor:pointer;">I agree, connect LinkedIn</button>
        </div>
      </div>`;
    document.body.appendChild(wrap);
    wrap.querySelector("#pc-no").addEventListener("click", () => wrap.remove());
    wrap.querySelector("#pc-yes").addEventListener("click", () => {
      window.__liConsent = true;   // for this page; after a reload /auth/me says the server recorded it
      wrap.remove();
      fn();
    });
    return true;
  };

  // The team's own writers get a way back to the panel
  function addPanelLink() {
    const nav = document.querySelector(".side-nav, nav.side, .sidebar nav, .sidebar");
    if (!nav || document.getElementById("partner-panel-link")) return;
    const a = document.createElement("a");
    a.id = "partner-panel-link"; a.href = "/partner"; a.className = "side-item";
    a.textContent = `${P.company} clients`;
    const items = nav.querySelectorAll(".side-item");
    (items.length ? items[items.length - 1] : nav).after(a);
  }

  // The sign-up page: say who the invite is for, or that it's by invitation
  document.addEventListener("DOMContentLoaded", async function () {
    if (location.pathname !== "/signup" && location.pathname !== "/login") return;
    const t = invite();
    const box = document.createElement("p");
    box.style.cssText = "font-size:14px;color:#5b6270;text-align:center;max-width:420px;line-height:1.5;margin:0 auto 14px;";
    if (t) {
      try {
        const info = await (await fetch("/partner/invite-info?token=" + encodeURIComponent(t))).json();
        if (info.valid) box.innerHTML = `You're invited to ${P.brand}. Create your account with <b>${info.email.replace(/</g, "&lt;")}</b>.`;
        else { box.textContent = `This invite has expired or was used. Ask ${P.company} for a new one.`; forget(); }
      } catch (_) { return; }
    } else if (location.pathname === "/signup") {
      box.innerHTML = `${P.brand} is by invitation from ${P.company}. Use the link in your invite email.`;
    } else return;
    const mount = document.getElementById("clerk-sign-in");
    if (mount) mount.before(box);
  });
})();
