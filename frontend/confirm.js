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
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
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
    .vc-ok { background: #24365e; border: 1.5px solid #24365e; color: #fff; }
    .vc-ok:hover { background: #5835e0; }
    .vc-ok.danger { background: #c0392b; border-color: #c0392b; }
    .vc-ok.danger:hover { background: #a93226; }
    .vc-ok:disabled { opacity: 0.45; cursor: not-allowed; }
    .vc-box.wide { width: 440px; }
    .vc-opt { display: flex; gap: 10px; align-items: flex-start; border: 1.5px solid #e4e7ec;
      border-radius: 10px; padding: 11px 12px; margin: 0 0 8px; cursor: pointer; }
    .vc-opt.sel { border-color: #24365e; background: #f6f8fc; }
    .vc-opt input { margin-top: 3px; accent-color: #24365e; }
    .vc-opt-title { font-size: 14px; font-weight: 600; color: #16181d; }
    .vc-opt-desc { font-size: 12.5px; color: #5b6270; line-height: 1.45; margin-top: 2px; }
    .vc-check { display: flex; gap: 8px; align-items: flex-start; font-size: 12.5px; color: #444;
      line-height: 1.45; margin: 4px 0 4px 2px; }
    .vc-check input { margin-top: 2px; accent-color: #24365e; }
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

  // Show a link to hand to someone else, with a Copy button.
  // Usage: voyceShowLink({ title, message, link, note })
  window.voyceShowLink = function (opts) {
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
      const box = document.createElement("div");
      box.className = "vc-box wide";
      box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true");
      box.setAttribute("aria-labelledby", "vc-title");
      const h = document.createElement("h3"); h.className = "vc-title"; h.id = "vc-title";
      h.textContent = opts.title || "Share this link";
      const msg = document.createElement("p"); msg.className = "vc-msg"; msg.style.marginBottom = "12px";
      msg.textContent = opts.message || "";
      const row = document.createElement("div"); row.style.cssText = "display:flex;gap:8px;margin-bottom:10px;";
      const input = document.createElement("input");
      input.type = "text"; input.readOnly = true; input.value = opts.link || "";
      input.setAttribute("aria-label", "Invite link");
      input.style.cssText = "flex:1;min-width:0;font-size:13px;padding:9px 10px;border:1.5px solid #d3d8e0;border-radius:8px;color:#16181d;background:#f9fafb;";
      const copy = document.createElement("button"); copy.type = "button"; copy.className = "vc-btn vc-ok"; copy.textContent = "Copy";
      row.appendChild(input); row.appendChild(copy);
      box.appendChild(h); box.appendChild(msg); box.appendChild(row);
      if (opts.note) {
        const note = document.createElement("p"); note.className = "vc-msg";
        note.style.cssText = "font-size:12.5px;color:#6b7280;margin:0 0 14px;";
        note.textContent = opts.note; box.appendChild(note);
      }
      const actions = document.createElement("div"); actions.className = "vc-actions";
      const done = document.createElement("button"); done.type = "button"; done.className = "vc-btn vc-cancel"; done.textContent = "Done";
      actions.appendChild(done); box.appendChild(actions);
      overlay.appendChild(box);
      const prevFocus = document.activeElement;
      function close() {
        document.removeEventListener("keydown", onKey, true);
        overlay.remove();
        if (prevFocus && prevFocus.focus) prevFocus.focus();
        resolve();
      }
      function onKey(e) {
        if (e.key === "Escape") { e.stopPropagation(); close(); }
        if (e.key === "Tab") {
          const f = [input, copy, done];
          const idx = f.indexOf(document.activeElement);
          e.preventDefault(); f[(idx + (e.shiftKey ? f.length - 1 : 1)) % f.length].focus();
        }
      }
      copy.onclick = async function () {
        try { await navigator.clipboard.writeText(input.value); }
        catch (_) { input.select(); document.execCommand && document.execCommand("copy"); }
        copy.textContent = "Copied"; setTimeout(function () { copy.textContent = "Copy"; }, 1600);
      };
      done.onclick = close;
      overlay.onclick = function (e) { if (e.target === overlay) close(); };
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(overlay);
      input.focus(); input.select();
    });
  };

  // A choice between options, one of which may need a ticked acknowledgement
  // before it can be confirmed (e.g. "post without asking me").
  // Usage: const v = await voyceChoose({ title, message, confirmText,
  //   options: [{ value, title, desc, check }] });   // check = acknowledgement text
  // Resolves to the chosen value, or null if cancelled.
  window.voyceChoose = function (opts) {
    opts = opts || {};
    if (!styleInjected) {
      const s = document.createElement("style");
      s.textContent = CSS;
      document.head.appendChild(s);
      styleInjected = true;
    }
    const options = opts.options || [];
    return new Promise(function (resolve) {
      const overlay = document.createElement("div");
      overlay.className = "vc-overlay";
      const box = document.createElement("div");
      box.className = "vc-box wide";
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "true");
      box.setAttribute("aria-labelledby", "vc-title");
      const h = document.createElement("h3");
      h.className = "vc-title"; h.id = "vc-title"; h.textContent = opts.title || "Choose";
      const msg = document.createElement("p");
      msg.className = "vc-msg"; msg.style.marginBottom = "14px"; msg.textContent = opts.message || "";
      box.appendChild(h); box.appendChild(msg);

      const name = "vc-choice-" + Math.random().toString(36).slice(2, 8);
      let selected = options.length ? options[0].value : null;
      const rows = options.map(function (o, i) {
        const row = document.createElement("label");
        row.className = "vc-opt" + (i === 0 ? " sel" : "");
        const radio = document.createElement("input");
        radio.type = "radio"; radio.name = name; radio.value = String(i); radio.checked = i === 0;
        const txt = document.createElement("div");
        const t = document.createElement("div"); t.className = "vc-opt-title"; t.textContent = o.title;
        const d = document.createElement("div"); d.className = "vc-opt-desc"; d.textContent = o.desc || "";
        txt.appendChild(t); txt.appendChild(d);
        row.appendChild(radio); row.appendChild(txt);
        box.appendChild(row);
        let check = null;
        if (o.check) {
          check = document.createElement("label");
          check.className = "vc-check"; check.style.display = "none";
          const cb = document.createElement("input"); cb.type = "checkbox";
          const ct = document.createElement("span"); ct.textContent = o.check;
          check.appendChild(cb); check.appendChild(ct);
          box.appendChild(check);
          cb.addEventListener("change", sync);
        }
        radio.addEventListener("change", function () { selected = o.value; sync(); });
        return { row: row, radio: radio, check: check, opt: o };
      });

      const actions = document.createElement("div");
      actions.className = "vc-actions"; actions.style.marginTop = "14px";
      const cancelBtn = document.createElement("button");
      cancelBtn.className = "vc-btn vc-cancel"; cancelBtn.type = "button";
      cancelBtn.textContent = opts.cancelText || "Cancel";
      const okBtn = document.createElement("button");
      okBtn.className = "vc-btn vc-ok"; okBtn.type = "button";
      okBtn.textContent = opts.confirmText || "Confirm";
      actions.appendChild(cancelBtn); actions.appendChild(okBtn);
      box.appendChild(actions);
      overlay.appendChild(box);

      function sync() {
        let ready = true;
        rows.forEach(function (r) {
          const on = r.radio.checked;
          r.row.classList.toggle("sel", on);
          if (r.check) {
            r.check.style.display = on ? "flex" : "none";
            if (on && !r.check.querySelector("input").checked) ready = false;
          }
        });
        okBtn.disabled = !ready;
      }

      const prevFocus = document.activeElement;
      function close(result) {
        document.removeEventListener("keydown", onKey, true);
        overlay.remove();
        if (prevFocus && prevFocus.focus) prevFocus.focus();
        resolve(result);
      }
      function onKey(e) {
        if (e.key === "Escape") { e.stopPropagation(); close(null); return; }
        if (e.key === "Tab") {
          // keep focus inside the dialog
          const f = Array.prototype.filter.call(box.querySelectorAll("input,button"),
            function (el) { return !el.disabled && el.offsetParent !== null; });
          if (!f.length) return;
          const idx = f.indexOf(document.activeElement);
          const next = e.shiftKey ? (idx <= 0 ? f.length - 1 : idx - 1) : (idx === f.length - 1 ? 0 : idx + 1);
          e.preventDefault(); f[next].focus();
        }
      }
      cancelBtn.onclick = function () { close(null); };
      okBtn.onclick = function () { if (!okBtn.disabled) close(selected); };
      overlay.onclick = function (e) { if (e.target === overlay) close(null); };
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(overlay);
      sync();
      (rows[0] ? rows[0].radio : okBtn).focus();
    });
  };
})();
