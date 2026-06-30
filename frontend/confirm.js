// Shared styled confirm dialog — replaces native confirm().
// Usage: if (!(await voyceConfirm("Delete this?", { confirmText: "Delete", danger: true }))) return;
(function () {
  const CSS = `
    .vc-overlay {
      position: fixed; inset: 0; z-index: 5000;
      background: rgba(26,26,46,0.45);
      display: flex; align-items: center; justify-content: center;
      animation: vc-fade 0.15s ease;
    }
    @keyframes vc-fade { from { opacity: 0; } to { opacity: 1; } }
    .vc-box {
      background: #fff; border-radius: 16px; padding: 26px 26px 22px;
      width: 360px; max-width: 90vw;
      box-shadow: 0 12px 40px rgba(0,0,0,0.2);
      font-family: 'Poppins', sans-serif;
      animation: vc-pop 0.15s ease;
    }
    @keyframes vc-pop { from { transform: scale(0.95); opacity: 0; } to { transform: scale(1); opacity: 1; } }
    .vc-title { font-size: 16px; font-weight: 700; color: #1a1a2e; margin: 0 0 8px; }
    .vc-msg { font-size: 14px; color: #555; line-height: 1.55; margin: 0 0 20px; }
    .vc-actions { display: flex; gap: 10px; justify-content: flex-end; }
    .vc-btn {
      border-radius: 8px; padding: 9px 18px; font-size: 13px; font-weight: 600;
      cursor: pointer; font-family: inherit; transition: all 0.12s;
    }
    .vc-cancel { background: none; border: 1.5px solid #e0e0e0; color: #555; }
    .vc-cancel:hover { background: #f5f5f5; }
    .vc-ok { background: #6c47ff; border: 1.5px solid #6c47ff; color: #fff; }
    .vc-ok:hover { background: #5835e0; }
    .vc-ok.danger { background: #c0392b; border-color: #c0392b; }
    .vc-ok.danger:hover { background: #a93226; }
  `;

  let styleInjected = false;

  window.voyceConfirm = function (message, opts) {
    opts = opts || {};
    if (!styleInjected) {
      const s = document.createElement("style");
      s.textContent = CSS;
      document.head.appendChild(s);
      styleInjected = true;
    }
    return new Promise(function (resolve) {
      const overlay = document.createElement("div");
      overlay.className = "vc-overlay";
      overlay.innerHTML =
        '<div class="vc-box" role="dialog" aria-modal="true" aria-labelledby="vc-title">' +
        '<h3 class="vc-title" id="vc-title"></h3>' +
        '<p class="vc-msg"></p>' +
        '<div class="vc-actions">' +
        '<button class="vc-btn vc-cancel" type="button"></button>' +
        '<button class="vc-btn vc-ok" type="button"></button>' +
        "</div></div>";
      overlay.querySelector(".vc-title").textContent = opts.title || "Are you sure?";
      overlay.querySelector(".vc-msg").textContent = message;
      const cancelBtn = overlay.querySelector(".vc-cancel");
      const okBtn = overlay.querySelector(".vc-ok");
      cancelBtn.textContent = opts.cancelText || "Cancel";
      okBtn.textContent = opts.confirmText || "Confirm";
      if (opts.danger) okBtn.classList.add("danger");

      const prevFocus = document.activeElement;
      function close(result) {
        document.removeEventListener("keydown", onKey, true);
        overlay.remove();
        if (prevFocus && prevFocus.focus) prevFocus.focus();
        resolve(result);
      }
      function onKey(e) {
        if (e.key === "Escape") { e.stopPropagation(); close(false); }
        if (e.key === "Tab") {
          // simple two-element focus trap
          e.preventDefault();
          (document.activeElement === okBtn ? cancelBtn : okBtn).focus();
        }
        if (e.key === "Enter" && document.activeElement !== cancelBtn) { close(true); }
      }
      cancelBtn.onclick = function () { close(false); };
      okBtn.onclick = function () { close(true); };
      overlay.onclick = function (e) { if (e.target === overlay) close(false); };
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(overlay);
      okBtn.focus();
    });
  };
})();
