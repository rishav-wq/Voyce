/* reel-fx.js — the effects library for the reel renderer.
 *
 * Everything here is built to be SEEKED, never played. The renderer screenshots
 * one frame at a time by calling seek(t), so an effect that owns a clock —
 * a CSS keyframe, a requestAnimationFrame loop, a setInterval — renders a
 * different picture every time you ask for the same t, and the video flickers.
 * GSAP is the engine because its timelines are paused-and-seekable by design:
 * timeline.pause() then timeline.totalTime(t, false), which is exactly the
 * contract we already had.
 *
 * Two rules anything added here has to keep:
 *
 *   1. No clock of its own. Position everything on the master timeline.
 *   2. No unseeded randomness. ReelFX.frame(t) reseeds the generator from the
 *      frame number before each seek, so a given t always produces the same
 *      pixels — including inside ScrambleText, which reaches for Math.random
 *      on every render.
 */
window.ReelFX = (function () {
  'use strict';

  /* ── determinism ─────────────────────────────────────────────────────────
     Math.random is replaced wholesale rather than wrapped, because the plugins
     that need taming (ScrambleText, Physics2D) call the global and give us no
     hook. Reseeding per frame rather than once at load is the part that
     matters: a single sequence advancing across the render would make frame N
     depend on how many frames came before it, which is fine in one pass and
     wrong the moment anything re-seeks. */
  var _seed = 1;
  function mulberry32() {
    _seed |= 0; _seed = (_seed + 0x6D2B79F5) | 0;
    var t = Math.imul(_seed ^ (_seed >>> 15), 1 | _seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  Math.random = mulberry32;

  var FPS = 60;
  /* Quantise every seek. Two code paths arriving at the same logical time by
     different arithmetic otherwise land a hair apart and produce frames that
     differ by a pixel. */
  function q(t) { return Math.round(t * FPS) / FPS; }

  /* ── setup ──────────────────────────────────────────────────────────────── */
  var _plugins = [];
  function reg(name) {
    var p = window[name];
    if (p) { _plugins.push(p); }
    return p;
  }
  function init(opts) {
    opts = opts || {};
    FPS = opts.fps || 60;
    reg('SplitText'); reg('DrawSVGPlugin'); reg('MorphSVGPlugin');
    reg('CustomEase'); reg('CustomWiggle');
    reg('Physics2DPlugin'); reg('Flip'); reg('TextPlugin');
    if (_plugins.length) { gsap.registerPlugin.apply(gsap, _plugins); }

    /* The renderer never lets wall-clock time advance, so the ticker has
       nothing to do; leaving it running just burns a rAF per frame while the
       page sits still between screenshots. */
    gsap.ticker.lagSmoothing(0);
    gsap.ticker.sleep();

    /* Named eases, so a curve is chosen by what it is for rather than
       re-derived at each call site. glide is smootherstep: zero first AND
       second derivative at both ends, which is what keeps a camera push from
       showing a corner where it starts and stops. */
    gsap.registerEase('glide', function (t) { return t * t * t * (t * (t * 6 - 15) + 10); });
    gsap.registerEase('smooth', function (t) { return t * t * (3 - 2 * t); });
    gsap.registerEase('settle', function (t) { return 1 - Math.pow(1 - t, 3); });
    gsap.registerEase('spring', function (t) {
      if (t >= 1) { return 1; }
      return 1 - Math.pow(2, -9 * t) * Math.cos(t * 13.2);
    });
    return API;
  }

  /* A paused master. Nothing in a reel ever plays; the renderer owns time. */
  function master() {
    var tl = gsap.timeline({ paused: true });
    tl.pause();
    return tl;
  }

  /* Drive the whole tree to t. suppressEvents = false on the second argument
     keeps callbacks from firing on every scrub, which they would otherwise do
     once per frame for the entire render. */
  function seek(tl, t) {
    _seed = (Math.round(t * FPS) | 0) + 1;     // reseed per frame, see above
    tl.totalTime(q(t), false);
  }

  /* ── splitting ───────────────────────────────────────────────────────────
     SplitText rewrites the element's DOM, so it must happen once at build
     time. Splitting the same element twice silently nests spans and the
     stagger goes wrong, hence the cache. */
  var _splits = new WeakMap();
  function split(el, type) {
    type = type || 'words';
    var key = type;
    var m = _splits.get(el) || {};
    if (m[key]) { return m[key]; }
    var cfg = { type: type, mask: type.split(',')[0].trim(), autoSplit: false };
    var s;
    try { s = SplitText.create(el, cfg); }
    catch (e) { s = new SplitText(el, { type: type }); }   // pre-3.13 shape
    m[key] = s; _splits.set(el, m);
    return s;
  }
  function parts(el, type) {
    var s = split(el, type);
    return type && type.indexOf('chars') === 0 ? s.chars
         : type && type.indexOf('lines') === 0 ? s.lines
         : s.words;
  }

  /* ── the effects ─────────────────────────────────────────────────────────
     Each returns a timeline. Add it to a master at a position:
         m.add(FX.wordsIn(el), 2.05)
     None of them set their own absolute time, so the same effect can be
     reused at any point in any scene. */
  var FX = {

    /* Text ---------------------------------------------------------------- */

    /* Words rising out of a mask. The mask is what separates this from a
       fade: letters appear from behind a hard edge rather than materialising,
       which is the thing kinetic-text reels do that hand-rolled versions
       usually miss. */
    wordsIn: function (el, o) {
      o = o || {};
      var tl = gsap.timeline();
      tl.from(parts(el, 'words'), {
        yPercent: o.from === 'down' ? -120 : 120,
        opacity: 0,
        duration: o.dur || 0.52,
        stagger: o.stagger === undefined ? 0.055 : o.stagger,
        ease: o.ease || 'glide'
      });
      return tl;
    },

    charsIn: function (el, o) {
      o = o || {};
      var tl = gsap.timeline();
      tl.from(parts(el, 'chars'), {
        yPercent: 100, opacity: 0, rotateX: o.flip ? -90 : 0,
        duration: o.dur || 0.42,
        stagger: o.stagger === undefined ? 0.016 : o.stagger,
        ease: o.ease || 'glide'
      });
      return tl;
    },

    linesIn: function (el, o) {
      o = o || {};
      var tl = gsap.timeline();
      tl.from(parts(el, 'lines'), {
        yPercent: 110, opacity: 0,
        duration: o.dur || 0.6,
        stagger: o.stagger === undefined ? 0.1 : o.stagger,
        ease: o.ease || 'glide'
      });
      return tl;
    },

    /* Characters resolving out of noise. This is the one effect that reads as
       a machine writing rather than a person typing, which is the whole claim
       of the product — a typewriter caret says someone is at the keyboard.

       Hand-rolled rather than GSAP's ScrambleTextPlugin, which measured
       non-deterministic: it carries internal tick state across renders, so
       two fresh forward passes of the same timeline produced different noise
       even with the global generator seeded. Here the character shown at any
       moment is a hash of its index and the current tick, so the same t always
       gives the same letters and a render is reproducible. */
    scrambleTo: function (el, text, o) {
      o = o || {};
      var dur = o.dur || 1.8;
      var chars = o.chars || 'abcdefghijklmnopqrstuvwxyz';
      var band = o.band === undefined ? 0.35 : o.band;   // how long one char churns
      var speed = o.speed || 26;                          // noise changes per second
      /* Random letters spell things. The default noise put 'dead' on screen
         for two frames in the middle of the post, which nobody would catch
         watching and everybody would catch in a screenshot. Because the noise
         is a pure function, the whole space of frames can be read ahead of
         time -- tools/scramble_audit.py does exactly that, and this salt is
         the first value that comes back clean. Re-run it when the post copy
         changes. */
      var salt = o.salt === undefined ? 1 : o.salt;
      var st = { p: 0 };
      var tl = gsap.timeline();
      tl.to(st, {
        p: 1, duration: dur, ease: 'none',
        onUpdate: function () {
          var p = st.p, n = text.length, tick = Math.floor(p * dur * speed), out = '', i;
          for (i = 0; i < n; i++) {
            var c = text.charAt(i);
            if (c === ' ' || c === '\n') { out += c; continue; }
            var s0 = (n < 2 ? 0 : i / n) * (1 - band);
            if (p >= s0 + band) { out += c; }
            else if (p <= s0) { out += ' '; }
            else {
              var k = (Math.imul(i, 73856093) ^ Math.imul(tick, 19349663) ^ salt) >>> 0;
              k = (k ^ (k >>> 13)) >>> 0;
              out += chars.charAt(k % chars.length);
            }
          }
          el.textContent = out;
        }
      });
      return tl;
    },

    typeOn: function (el, text, o) {
      o = o || {};
      var tl = gsap.timeline();
      tl.to(el, { duration: o.dur || 1.4, ease: 'none', text: { value: text, delimiter: o.by || '' } });
      return tl;
    },

    countUp: function (el, to, o) {
      o = o || {};
      var obj = { n: o.from === undefined ? 0 : o.from };
      var fmt = o.format || function (n) { return Math.round(n); };
      var tl = gsap.timeline();
      tl.to(obj, {
        n: to, duration: o.dur || 1.0, ease: o.ease || 'settle',
        onUpdate: function () { el.textContent = fmt(obj.n); }
      });
      return tl;
    },

    /* Shape and line ------------------------------------------------------ */

    /* A stroke drawing itself on. Good for underlines, ticks, and the arrow
       from a coach mark to the control it is talking about. */
    drawOn: function (el, o) {
      o = o || {};
      var tl = gsap.timeline();
      tl.fromTo(el, { drawSVG: '0%' },
        { drawSVG: '100%', duration: o.dur || 0.6, ease: o.ease || 'glide' });
      return tl;
    },

    morph: function (el, to, o) {
      o = o || {};
      var tl = gsap.timeline();
      tl.to(el, { morphSVG: to, duration: o.dur || 0.5, ease: o.ease || 'glide' });
      return tl;
    },

    /* Motion -------------------------------------------------------------- */

    riseIn: function (el, o) {
      o = o || {};
      var tl = gsap.timeline();
      tl.from(el, {
        y: o.y === undefined ? 48 : o.y, opacity: 0,
        duration: o.dur || 0.6, ease: o.ease || 'glide',
        stagger: o.stagger || 0
      });
      return tl;
    },

    /* Rows arriving one after another. The reason to stagger a list rather
       than fade the whole tile is that it tells the eye how many there are. */
    staggerIn: function (els, o) {
      o = o || {};
      var tl = gsap.timeline();
      tl.from(els, {
        y: o.y === undefined ? 26 : o.y, opacity: 0,
        duration: o.dur || 0.5,
        stagger: o.each === undefined ? 0.12 : o.each,
        ease: o.ease || 'glide'
      });
      return tl;
    },

    pop: function (el, o) {
      o = o || {};
      var tl = gsap.timeline();
      tl.fromTo(el, { scale: o.from === undefined ? 0.6 : o.from, opacity: 0 },
        { scale: 1, opacity: 1, duration: o.dur || 0.5, ease: o.ease || 'spring' });
      return tl;
    },

    /* A press that reads as contact: down fast, back slower. Symmetric
       scaling looks like a hover, not a click. */
    press: function (el, o) {
      o = o || {};
      var d = o.dur || 0.26;
      var tl = gsap.timeline();
      tl.to(el, { scale: 0.94, duration: d * 0.35, ease: 'smooth' })
        .to(el, { scale: 1, duration: d * 0.65, ease: 'spring' });
      return tl;
    },

    /* An expanding ring at the point of contact. Paired with press, this is
       most of what makes a synthetic click look like a real one. */
    ripple: function (el, o) {
      o = o || {};
      var tl = gsap.timeline();
      tl.fromTo(el, { scale: 0.2, opacity: 0.45 },
        { scale: o.to || 2.6, opacity: 0, duration: o.dur || 0.62, ease: 'settle' });
      return tl;
    },

    /* A light sweep across a surface. Cheap, and it keeps a held frame from
       being frame-identical to the one before it. */
    shimmer: function (el, o) {
      o = o || {};
      var tl = gsap.timeline();
      tl.fromTo(el, { backgroundPositionX: '-150%' },
        { backgroundPositionX: '250%', duration: o.dur || 1.2, ease: 'smooth' });
      return tl;
    },

    blurIn: function (el, o) {
      o = o || {};
      var tl = gsap.timeline();
      tl.fromTo(el, { filter: 'blur(' + (o.blur || 14) + 'px)', opacity: 0 },
        { filter: 'blur(0px)', opacity: 1, duration: o.dur || 0.7, ease: 'glide' });
      return tl;
    },

    /* Attention without motion sickness: opacity only, and it returns to
       exactly where it started so a hold can sit on top of it. */
    pulse: function (el, o) {
      o = o || {};
      var tl = gsap.timeline();
      tl.to(el, {
        opacity: o.to === undefined ? 0.45 : o.to,
        duration: o.dur || 0.55, ease: 'smooth',
        yoyo: true, repeat: o.repeat === undefined ? 3 : o.repeat
      });
      return tl;
    },

    /* Particles ----------------------------------------------------------- */

    /* A burst, for the one moment in a reel that deserves one. Physics2D
       gives each piece its own angle, velocity and gravity, so it does not
       read as a canned sprite sheet. Seeded, like everything else here. */
    burst: function (container, o) {
      o = o || {};
      var n = o.count || 26;
      var colors = o.colors || ['#24365e', '#1aa07a', '#e7ebf4'];
      var tl = gsap.timeline();
      var bits = [];
      for (var i = 0; i < n; i++) {
        var b = document.createElement('i');
        b.style.cssText = 'position:absolute;left:50%;top:50%;width:' + (o.size || 12) + 'px;height:' +
          (o.size || 12) + 'px;border-radius:' + (i % 3 ? '50%' : '2px') +
          ';background:' + colors[i % colors.length] + ';pointer-events:none;opacity:0';
        container.appendChild(b); bits.push(b);
      }
      tl.set(bits, { opacity: 1 });
      tl.to(bits, {
        duration: o.dur || 1.3,
        physics2D: { velocity: 'random(380, 720)', angle: 'random(235, 305)', gravity: 1400 },
        rotation: 'random(-220, 220)',
        opacity: 0,
        ease: 'none',
        stagger: 0.004
      });
      return tl;
    },

    /* Layout -------------------------------------------------------------- */

    /* FLIP: record where things are, change the layout, animate the
       difference. This is the correct tool for an element that moves because
       the page reflowed around it, which hand-rolled offsets only approximate. */
    flip: function (targets, change, o) {
      o = o || {};
      var state = Flip.getState(targets);
      change();
      return Flip.from(state, {
        duration: o.dur || 0.7, ease: o.ease || 'glide',
        absolute: true, scale: o.scale !== false
      });
    },

    /* Pointing ------------------------------------------------------------ */

    /* Move the cursor to an element by measuring it, not by hard-coding
       coordinates — the last three times a cursor drifted off its button it
       was because the number was typed in rather than read off the box. */
    cursorTo: function (cursor, target, o) {
      o = o || {};
      var b = target.getBoundingClientRect();
      var tl = gsap.timeline();
      tl.to(cursor, {
        x: b.left + b.width / 2 + (o.dx || 0),
        y: b.top + b.height / 2 + (o.dy || 0),
        duration: o.dur || 0.6, ease: o.ease || 'glide'
      });
      return tl;
    },

    spotlightTo: function (ring, target, o) {
      o = o || {};
      var b = target.getBoundingClientRect();
      var pad = o.pad === undefined ? 10 : o.pad;
      var tl = gsap.timeline();
      tl.to(ring, {
        x: b.left - pad, y: b.top - pad,
        width: b.width + pad * 2, height: b.height + pad * 2,
        duration: o.dur || 0.5, ease: o.ease || 'glide'
      });
      return tl;
    }
  };

  var API = {
    init: init, master: master, seek: seek, q: q,
    split: split, parts: parts, fx: FX,
    random: mulberry32,
    get fps() { return FPS; }
  };
  return API;
})();
