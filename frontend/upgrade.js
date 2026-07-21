// upgrade.js — shared Pro upgrade modal + Razorpay checkout.
// Included on both /tool (Create) and /setup so "Upgrade" opens IN PLACE, no navigation.
// Relies on the host page defining getToken() and toast(); refreshes plan state on success.
(function () {
  const MODAL_HTML = `
  <div id="upgrade-modal" style="display:none;position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,0.45);align-items:center;justify-content:center;padding:16px;" onclick="if(event.target===this)closeUpgradeModal()">
    <div style="background:#fff;border-radius:20px;padding:36px 32px;max-width:420px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,0.18);position:relative;max-height:92vh;overflow-y:auto;">
      <button onclick="closeUpgradeModal()" aria-label="Close" style="position:absolute;top:14px;right:18px;background:none;border:none;font-size:20px;color:#aaa;cursor:pointer;">✕</button>
      <div style="text-align:center;margin-bottom:24px;">
        <div style="font-size:13px;font-weight:700;color:#6c47ff;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">Pro Plan</div>
        <div style="font-size:48px;font-weight:900;line-height:1;color:#1c1813;"><sup id="upgrade-currency" style="font-size:22px;vertical-align:top;margin-top:10px;display:inline-block;">₹</sup><span id="upgrade-price">4,199</span></div>
        <div style="font-size:13px;color:#aaa;margin-top:4px;">31 days of Pro · no auto-charge · renew anytime</div>
      </div>
      <ul style="list-style:none;margin:0 0 28px;padding:0;">
        <li style="padding:8px 0;border-bottom:1px solid #f5f2ff;font-size:14px;color:#444;display:flex;gap:10px;align-items:flex-start;"><span style="color:#6c47ff;font-weight:800;">✓</span><span><strong>Unlimited</strong> generations</span></li>
        <li style="padding:8px 0;border-bottom:1px solid #f5f2ff;font-size:14px;color:#444;display:flex;gap:10px;align-items:flex-start;"><span style="color:#6c47ff;font-weight:800;">✓</span><span><strong>Autonomous</strong> daily posting</span></li>
        <li style="padding:8px 0;border-bottom:1px solid #f5f2ff;font-size:14px;color:#444;display:flex;gap:10px;align-items:flex-start;"><span style="color:#6c47ff;font-weight:800;">✓</span><span>Up to <strong>3 profiles</strong></span></li>
        <li style="padding:8px 0;border-bottom:1px solid #f5f2ff;font-size:14px;color:#444;display:flex;gap:10px;align-items:flex-start;"><span style="color:#6c47ff;font-weight:800;">✓</span><span>Carousel PDFs (automated)</span></li>
        <li style="padding:8px 0;border-bottom:1px solid #f5f2ff;font-size:14px;color:#444;display:flex;gap:10px;align-items:flex-start;"><span style="color:#6c47ff;font-weight:800;">✓</span><span>Post analytics + engagement</span></li>
        <li style="padding:8px 0;font-size:14px;color:#444;display:flex;gap:10px;align-items:flex-start;"><span style="color:#6c47ff;font-weight:800;">✓</span><span>Learns your style from LinkedIn</span></li>
      </ul>
      <button id="upgrade-pay-btn" onclick="startUpgradePayment()" style="display:block;width:100%;padding:14px;background:#6c47ff;color:#fff;border:none;border-radius:10px;font-size:15px;font-weight:700;text-align:center;cursor:pointer;transition:background 0.15s;" onmouseover="this.style.background='#5634e6'" onmouseout="this.style.background='#6c47ff'">
        Upgrade now — pay securely →
      </button>
      <div style="text-align:center;font-size:12px;color:#aaa;margin-top:12px;">Secured by Razorpay · Pro activates instantly · <a href="mailto:r65581350@gmail.com?subject=Upgrade to Voyce Pro" style="color:#6c47ff;">need help?</a></div>
      <div style="text-align:center;font-size:12px;color:#aaa;margin-top:6px;">
        <a href="#" onclick="restorePurchase();return false;" style="color:#888;">Paid but still on Free? Restore purchase</a>
        &nbsp;·&nbsp;
        <a href="#" onclick="toggleBillingHistory();return false;" style="color:#888;">Billing history</a>
      </div>
      <div id="billing-history" style="display:none;margin-top:14px;max-height:160px;overflow-y:auto;border-top:1px solid #f0ecff;padding-top:10px;"></div>
    </div>
  </div>`;

  function _ensureModal() {
    if (document.getElementById("upgrade-modal")) return;
    const wrap = document.createElement("div");
    wrap.innerHTML = MODAL_HTML.trim();
    document.body.appendChild(wrap.firstElementChild);
  }
  if (document.body) _ensureModal();
  else document.addEventListener("DOMContentLoaded", _ensureModal);

  let _payConfig = null;
  const _CURRENCY_SYMBOLS = { INR: "₹", USD: "$", EUR: "€", GBP: "£" };

  // Refresh plan state after a successful upgrade — smoothly on /setup, via reload elsewhere.
  function _refreshPlan() {
    if (typeof loadPlanBanner === "function") loadPlanBanner();
    else setTimeout(() => window.location.reload(), 1300);
  }
  function _toast(msg, type) { if (typeof toast === "function") toast(msg, type); }

  window.openUpgradeModal = function () {
    _ensureModal();
    const m = document.getElementById("upgrade-modal");
    m.style.display = "flex";
    loadPaymentConfig();
    const payBtn = document.getElementById("upgrade-pay-btn");
    if (payBtn) payBtn.focus();
  };

  window.closeUpgradeModal = function () {
    const m = document.getElementById("upgrade-modal");
    if (m) m.style.display = "none";
  };

  document.addEventListener("keydown", (e) => { if (e.key === "Escape") window.closeUpgradeModal(); });

  window.loadPaymentConfig = async function () {
    if (_payConfig) return _payConfig;
    try {
      const res = await fetch("/payments/config");
      if (!res.ok) return null;
      _payConfig = await res.json();
      if (_payConfig.configured) {
        const value = _payConfig.amount / 100;
        const cur = document.getElementById("upgrade-currency");
        const pr = document.getElementById("upgrade-price");
        if (cur) cur.textContent = _CURRENCY_SYMBOLS[_payConfig.currency] || _payConfig.currency + " ";
        if (pr) pr.textContent = Number.isInteger(value) ? value.toLocaleString("en-IN") : value.toFixed(2);
      }
    } catch (_) {}
    return _payConfig;
  };

  function loadRazorpayScript() {
    return new Promise((resolve, reject) => {
      if (window.Razorpay) return resolve();
      const s = document.createElement("script");
      s.src = "https://checkout.razorpay.com/v1/checkout.js";
      s.onload = resolve;
      s.onerror = () => reject(new Error("Could not load payment script"));
      document.head.appendChild(s);
    });
  }

  window.startUpgradePayment = async function () {
    const btn = document.getElementById("upgrade-pay-btn");
    const config = await loadPaymentConfig();
    if (!config || !config.configured) {
      window.location.href = "mailto:r65581350@gmail.com?subject=Upgrade to Voyce Pro&body=Hi, I'd like to upgrade my account to Pro.";
      return;
    }
    btn.disabled = true;
    btn.textContent = "Starting secure checkout…";
    try {
      await loadRazorpayScript();
      const res = await fetch("/payments/create-order", { method: "POST", headers: { "x-token": getToken() } });
      if (!res.ok) throw new Error((await res.json()).detail || "Could not start payment");
      const order = await res.json();

      const rzp = new Razorpay({
        key: order.key_id,
        order_id: order.order_id,
        amount: order.amount,
        currency: order.currency,
        name: "Voyce",
        description: "Voyce Pro — 1 month",
        theme: { color: "#6c47ff" },
        handler: async (response) => {
          try {
            const vres = await fetch("/payments/verify", {
              method: "POST",
              headers: { "Content-Type": "application/json", "x-token": getToken() },
              body: JSON.stringify({
                razorpay_order_id: response.razorpay_order_id,
                razorpay_payment_id: response.razorpay_payment_id,
                razorpay_signature: response.razorpay_signature,
              }),
            });
            if (!vres.ok) throw new Error("verification failed");
            _toast("🎉 Welcome to Voyce Pro! Unlimited generations unlocked.", "success");
            window.closeUpgradeModal();
            _refreshPlan();
          } catch (_) {
            _toast("Payment received but verification failed — contact support and we'll fix it.", "error");
          }
        },
        modal: { ondismiss: () => _toast("Payment cancelled", "error") },
      });
      rzp.on("payment.failed", () => _toast("Payment failed — you have not been charged. Please try again.", "error"));
      rzp.open();
    } catch (err) {
      _toast("Error: " + err.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Upgrade now — pay securely →";
    }
  };

  window.restorePurchase = async function () {
    try {
      const res = await fetch("/payments/restore", { method: "POST", headers: { "x-token": getToken() } });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "restore failed");
      const data = await res.json();
      if (data.restored) {
        _toast("🎉 Purchase restored — welcome to Pro!", "success");
        window.closeUpgradeModal();
        _refreshPlan();
      } else if (data.gen_info && data.gen_info.limit === -1) {
        _toast("Your account is already on Pro.", "success");
        window.closeUpgradeModal();
        _refreshPlan();
      } else {
        _toast("No completed payment found. If you were charged, contact support.", "warn");
      }
    } catch (err) {
      _toast("Error: " + err.message, "error");
    }
  };

  window.toggleBillingHistory = async function () {
    const box = document.getElementById("billing-history");
    if (box.style.display !== "none") { box.style.display = "none"; return; }
    box.style.display = "block";
    box.innerHTML = '<div style="font-size:12px;color:#aaa;">Loading…</div>';
    try {
      const res = await fetch("/payments/history", { headers: { "x-token": getToken() } });
      if (!res.ok) throw new Error("could not load");
      const data = await res.json();
      if (!data.payments.length) {
        box.innerHTML = '<div style="font-size:12px;color:#aaa;">No payments yet.</div>';
        return;
      }
      box.innerHTML = data.payments.map((p) => {
        const sym = _CURRENCY_SYMBOLS[p.currency] || p.currency + " ";
        const amt = (p.amount / 100).toFixed(2).replace(/\.00$/, "");
        const date = (p.paid_at || p.created_at || "").slice(0, 10);
        return `<div style="display:flex;justify-content:space-between;font-size:12.5px;color:#555;padding:5px 0;border-bottom:1px solid #f7f5ff;">
          <span>${date} · Voyce Pro</span><span style="font-weight:700;">${sym}${amt}</span>
        </div>`;
      }).join("");
    } catch (_) {
      box.innerHTML = '<div style="font-size:12px;color:#c0392b;">Could not load billing history.</div>';
    }
  };
})();
