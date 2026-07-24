/* =====================================================================
   MUMBLE — website interactions. Vanilla JS, no dependencies.
   ===================================================================== */
(function () {
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var $ = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };
  var rand = function (a, b) { return a + Math.random() * (b - a); };

  /* ----------------------------- Year ------------------------------ */
  var yr = $("#year"); if (yr) yr.textContent = new Date().getFullYear();

  /* -------------------------- Nav scroll --------------------------- */
  var navWrap = $("#navWrap");
  var onScroll = function () {
    if (navWrap) navWrap.classList.toggle("scrolled", window.scrollY > 12);
  };
  onScroll();
  window.addEventListener("scroll", onScroll, { passive: true });

  /* -------------------------- Mobile menu -------------------------- */
  var toggle = $("#navToggle"), menu = $("#mobileMenu");
  var setMenu = function (open) {
    if (!menu) return;
    menu.classList.toggle("open", open);
    if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
    document.body.style.overflow = open ? "hidden" : "";
  };
  if (toggle) toggle.addEventListener("click", function () { setMenu(!menu.classList.contains("open")); });
  if (menu) $$("a", menu).forEach(function (a) { a.addEventListener("click", function () { setMenu(false); }); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") setMenu(false); });

  /* ------------------------- Scroll reveal ------------------------- */
  var reveals = $$(".reveal");
  // Anything already in (or near) the viewport reveals immediately — so above-the-fold
  // content never waits on the async observer, and the page is correct without scrolling.
  function revealInView() {
    var vh = window.innerHeight || document.documentElement.clientHeight;
    reveals.forEach(function (el) {
      if (el.classList.contains("in")) return;
      if (el.getBoundingClientRect().top < vh * 0.92) el.classList.add("in");
    });
  }
  if (reduce || !("IntersectionObserver" in window)) {
    reveals.forEach(function (el) { el.classList.add("in"); });
  } else {
    revealInView();
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -8% 0px" });
    reveals.forEach(function (el) { if (!el.classList.contains("in")) io.observe(el); });
    // safety net: if anything is still hidden shortly after load, show it
    window.addEventListener("load", function () { setTimeout(revealInView, 300); });
  }

  /* ---------------------- Waveform generators ---------------------- */
  var waveClasses = ["wv-a", "wv-b", "wv-c", "wv-d", "wv-e"];
  function buildWave(el, count, maxH) {
    if (!el) return;
    var frag = document.createDocumentFragment();
    for (var i = 0; i < count; i++) {
      var b = document.createElement("span");
      b.className = "wb " + waveClasses[i % waveClasses.length];
      // edge-tapered amplitude so the middle peaks
      var t = 1 - Math.abs(i - (count - 1) / 2) / ((count - 1) / 2); // 0..1
      var h = (0.32 + 0.68 * t) * maxH * rand(0.62, 1);
      b.style.height = Math.max(8, h).toFixed(0) + "px";
      b.style.setProperty("--dur", rand(0.8, 1.4).toFixed(2) + "s");
      b.style.animationDelay = (i * 0.045).toFixed(2) + "s";
      frag.appendChild(b);
    }
    el.appendChild(frag);
  }
  buildWave($("#heroWave"), 22, 56);
  buildWave($("#scWave"), 18, 40);

  /* --------------------------- Stats chart ------------------------- */
  (function () {
    var chart = $("#scChart"); if (!chart) return;
    var n = 16;
    for (var i = 0; i < n; i++) {
      var bar = document.createElement("div");
      bar.className = "bar" + (i === n - 1 ? " today" : "");
      var t = i / (n - 1);
      var base = 24 + Math.sin(i * 0.9) * 16 + t * 46 + rand(-8, 8);
      bar.style.height = Math.max(8, Math.min(100, base)).toFixed(0) + "%";
      chart.appendChild(bar);
    }
  })();

  /* --------------------- Hero island state loop -------------------- */
  (function () {
    var island = $("#heroIsland"); if (!island) return;

    var wave = '<span class="island-mini-wave"><span style="--d:1.05s"></span><span style="--d:1.25s"></span><span style="--d:.95s"></span><span style="--d:1.15s"></span><span style="--d:1.3s"></span></span>';
    var dots = '<span class="island-dots"><span></span><span></span><span></span></span>';
    var check = '<svg class="island-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l4 4 10-11"/></svg>';

    var states = [
      { label: "Listening", mode: "gold", mid: wave, dot: true },
      { label: "Transcribing…", mode: "gold", mid: dots, dot: true },
      { label: "Email", mode: "email", mid: dots, dot: true },
      { label: "Pasted", mode: "green", mid: check, dot: false }
    ];
    var i = 0;
    function render(s) {
      var css = getComputedStyle(document.documentElement);
      var ac = s.mode === "gold" ? (css.getPropertyValue("--gold").trim() || "#D4AF37") :
               s.mode === "email" ? (css.getPropertyValue("--mode-email").trim() || "#5AA9E6") :
               (css.getPropertyValue("--green").trim() || "#5AB85A");
      
      island.style.setProperty("--ac", ac);
      island.style.boxShadow = "0 8px 30px rgba(0,0,0,0.55), 0 0 24px " + hexA(ac, 0.22) + ", inset 0 1px 0 rgba(245,238,222,0.10)";
      island.style.borderColor = hexA(ac, 0.5);
      island.innerHTML =
        (s.dot ? '<span class="island-dot"></span>' : "") +
        s.mid +
        '<span class="island-label" style="color:' + (s.label === "Email" ? ac : "var(--text)") + '">' + s.label + "</span>";
    }
    function hexA(hex, a) {
      hex = hex.replace("#", "");
      if (hex.length === 3) hex = hex.split("").map(function (c) { return c + c; }).join("");
      var r = parseInt(hex.substr(0, 2), 16), g = parseInt(hex.substr(2, 2), 16), b = parseInt(hex.substr(4, 2), 16);
      return "rgba(" + r + "," + g + "," + b + "," + a + ")";
    }
    render(states[0]);
    if (!reduce) {
      setInterval(function () {
        i = (i + 1) % states.length;
        render(states[i]);
      }, 2200);
    }
  })();

  /* ------------------------- The Big Shift ------------------------- */
  (function () {
    var data = {
      list: {
        c: "--mode-list", tag: "List",
        said: '"milk, eggs, bread, and pick up the dry cleaning"',
        body: '<ul><li>Milk</li><li>Eggs</li><li>Bread</li><li>Pick up the dry cleaning</li></ul>'
      },
      email: {
        c: "--mode-email", tag: "Email",
        said: '"let sarah know the latest revision looks great and we’ll ship friday"',
        body: "Hi Sarah,\n\nThe latest revision looks great — thank you. We’re on track to ship Friday as planned.\n\nBest,\nAlex"
      },
      text: {
        c: "--mode-text", tag: "Text",
        said: '"quick note from the call they want the beta by end of month and a pricing page first"',
        body: "Quick note from the call: they want the beta by the end of the month, and a pricing page in place before launch."
      },
      prompt: {
        c: "--mode-prompt", tag: "Prompt",
        said: '"write a short launch announcement for a privacy first dictation app friendly but confident"',
        body: "Write a short launch announcement for a privacy-first dictation app.\n\n• Audience: knowledge workers\n• Tone: friendly but confident\n• Emphasise: on-device transcription, paste anywhere\n• Length: 3 short paragraphs"
      }
    };
    var tabs = $$(".shift-tab");
    var out = $("#shiftOut"), tag = $("#shiftTag"), bodyEl = $("#shiftBody"), said = $("#shiftSaid");
    if (!tabs.length || !out) return;
    function apply(key) {
      var d = data[key]; if (!d) return;
      out.style.setProperty("--oc", "var(" + d.c + ")");
      tag.textContent = d.tag;
      said.textContent = d.said;
      if (d.body.indexOf("<ul>") === 0) bodyEl.innerHTML = d.body;
      else bodyEl.textContent = d.body;
      tabs.forEach(function (t) {
        var on = t.getAttribute("data-shift") === key;
        t.classList.toggle("active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
    }
    tabs.forEach(function (t) {
      t.addEventListener("click", function () { apply(t.getAttribute("data-shift")); });
    });
    apply("list");
  })();

  /* --------------------- Interface showcase tabs ------------------- */
  (function () {
    var scTabs = $$(".sc-tab"), npTabs = $$(".win-nav .np"), panes = $$(".win-pane");
    var status = $("#winStatus");
    if (!panes.length) return;
    function show(pane) {
      panes.forEach(function (p) { p.classList.toggle("active", p.getAttribute("data-pane") === pane); });
      scTabs.forEach(function (t) {
        var on = t.getAttribute("data-pane") === pane;
        t.classList.toggle("active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
      npTabs.forEach(function (t) { t.classList.toggle("active", t.getAttribute("data-pane") === pane); });
      if (status) {
        var rec = pane === "home";
        status.classList.toggle("rec", rec);
        var st = $(".st", status); if (st) st.textContent = rec ? "Listening" : "Ready";
      }
    }
    scTabs.concat(npTabs).forEach(function (t) {
      t.addEventListener("click", function () { show(t.getAttribute("data-pane")); });
    });
    show("home");
  })();

  /* ------------------- Reader word highlight (idle) ---------------- */
  (function () {
    if (reduce) return;
    $$(".reader-text").forEach(function (el) {
      var words = $$(".w", el); if (!words.length) return;
      var idx = -1, timer = null;
      function step() {
        if (idx >= 0) words[idx].classList.remove("spoken");
        idx = (idx + 1) % words.length;
        words[idx].classList.add("spoken");
      }
      function visible() {
        var r = el.getBoundingClientRect();           // display:none panes report 0×0
        return r.width > 0 && r.height > 0 && r.top < (window.innerHeight || 0) && r.bottom > 0;
      }
      function tick() {
        if (visible() && !timer) timer = setInterval(step, 360);
        else if (!visible() && timer) { clearInterval(timer); timer = null; }
      }
      window.addEventListener("scroll", tick, { passive: true });
      document.addEventListener("click", function () { setTimeout(tick, 60); });
      setInterval(tick, 800);
    });
  })();

  /* ----------------------------- FAQ ------------------------------- */
  $$(".faq-item").forEach(function (item) {
    var q = $(".faq-q", item), a = $(".faq-a", item);
    if (!q || !a) return;
    q.addEventListener("click", function () {
      var open = item.classList.toggle("open");
      a.style.maxHeight = open ? a.scrollHeight + "px" : "0px";
      // close siblings for an accordion feel
      if (open) {
        $$(".faq-item").forEach(function (other) {
          if (other !== item && other.classList.contains("open")) {
            other.classList.remove("open");
            var oa = $(".faq-a", other); if (oa) oa.style.maxHeight = "0px";
          }
        });
      }
    });
  });

  /* --------------------- Ambient gold dust (Enhanced) -------------- */
  (function () {
    if (reduce) return;
    var canvas = $("#dust"); if (!canvas || !canvas.getContext) return;
    var ctx = canvas.getContext("2d");
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var w, h, parts = [];
    function resize() {
      w = canvas.width = innerWidth * dpr;
      h = canvas.height = innerHeight * dpr;
      canvas.style.width = innerWidth + "px";
      canvas.style.height = innerHeight + "px";
    }
    function seed() {
      var count = Math.round((innerWidth * innerHeight) / 52000);
      count = Math.max(18, Math.min(70, count));
      parts = [];
      for (var i = 0; i < count; i++) {
        parts.push({
          x: Math.random() * w, y: Math.random() * h,
          r: rand(0.5, 1.7) * dpr,
          vx: rand(-0.12, 0.12) * dpr, vy: rand(-0.28, -0.05) * dpr,
          a: rand(0.06, 0.4), tw: rand(0.005, 0.02), tp: Math.random() * Math.PI * 2
        });
      }
    }
    var running = true;
    var dustRgb = "212,175,55";
    var frameCount = 0;
    function draw() {
      if (!running) return;
      frameCount++;
      if (frameCount % 60 === 0) {
        var css = getComputedStyle(document.documentElement);
        dustRgb = css.getPropertyValue("--accent-rgb").trim() || "212,175,55";
      }
      ctx.clearRect(0, 0, w, h);
      for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        p.x += p.vx; p.y += p.vy; p.tp += p.tw;
        if (p.y < -10) { p.y = h + 10; p.x = Math.random() * w; }
        if (p.x < -10) p.x = w + 10; if (p.x > w + 10) p.x = -10;
        var a = p.a * (0.6 + 0.4 * Math.sin(p.tp));
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(" + dustRgb + "," + a.toFixed(3) + ")";
        ctx.shadowBlur = 6 * dpr; ctx.shadowColor = "rgba(" + dustRgb + ",0.5)";
        ctx.fill();
      }
      requestAnimationFrame(draw);
    }
    resize(); seed(); draw();
    var rt;
    window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(function () { resize(); seed(); }, 200); });
    // pause when tab hidden (resource-aware, matching Mumble's own ethos)
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { running = false; }
      else if (!running) { running = true; draw(); }
    });
  })();

  /* --------------------- Theme Switcher (Colorways) ----------------- */
  (function () {
    var themes = [
      { id: 'gold-black', name: 'Golden Black (Default)', class: 'theme-gold-black', color: '#D4AF37' },
      { id: 'forest-emerald', name: 'Forest Emerald', class: 'theme-forest-emerald', color: '#10B981' },
      { id: 'midnight-sapphire', name: 'Midnight Sapphire', class: 'theme-midnight-sapphire', color: '#3B82F6' },
      { id: 'royal-amethyst', name: 'Royal Amethyst', class: 'theme-royal-amethyst', color: '#8B5CF6' },
      { id: 'nordic-frost', name: 'Nordic Frost', class: 'theme-nordic-frost', color: '#0EA5E9' },
      { id: 'sunset-crimson', name: 'Sunset Crimson', class: 'theme-sunset-crimson', color: '#EF4444' },
      { id: 'solar-amber', name: 'Solar Amber', class: 'theme-solar-amber', color: '#F59E0B' },
      { id: 'stealth-mono', name: 'Stealth Monochrome', class: 'theme-stealth-mono', color: '#E5E5E5' },
      { id: 'oceanic-teal', name: 'Oceanic Teal', class: 'theme-oceanic-teal', color: '#14B8A6' },
      { id: 'rose-gold', name: 'Rose Gold', class: 'theme-rose-gold', color: '#E0A899' },
      { id: 'frost-glacier', name: 'Nordic Frost (Arctic Glacier)', class: 'theme-frost-glacier', color: '#56C1FF' },
      { id: 'frost-sapphire', name: 'Nordic Frost (Deep Sapphire)', class: 'theme-frost-sapphire', color: '#2F63FF' },
      { id: 'frost-aurora', name: 'Nordic Frost (Nordic Aurora)', class: 'theme-frost-aurora', color: '#00E5C9' },
      { id: 'frost-polar', name: 'Nordic Frost (Polar Night)', class: 'theme-frost-polar', color: '#5F5BFF' },
      { id: 'frost-mint', name: 'Nordic Frost (Glacial Mint)', class: 'theme-frost-mint', color: '#00FFC4' },
      { id: 'crimson-scarlet', name: 'Sunset Crimson (Volcanic Scarlet)', class: 'theme-crimson-scarlet', color: '#FF1E1E' },
      { id: 'crimson-cherry', name: 'Sunset Crimson (Cherry Burgundy)', class: 'theme-crimson-cherry', color: '#D90429' },
      { id: 'crimson-ruby', name: 'Sunset Crimson (Vampire Ruby)', class: 'theme-crimson-ruby', color: '#9B001C' },
      { id: 'crimson-solstice', name: 'Sunset Crimson (Fiery Solstice)', class: 'theme-crimson-solstice', color: '#FF4D00' },
      { id: 'crimson-blood', name: 'Sunset Crimson (Blood Orange)', class: 'theme-crimson-blood', color: '#E63946' }
    ];

    // Determine initial theme based on filename or localStorage
    var defaultTheme = 'gold-black';
    var filename = window.location.pathname.split('/').pop() || 'index.html';
    themes.forEach(function (t) {
      if (filename.indexOf(t.id) !== -1) {
        defaultTheme = t.id;
      }
    });

    // If the filename explicitly points to a theme variant, use that theme.
    // Otherwise, check localStorage.
    var activeTheme = defaultTheme;
    if (defaultTheme === 'gold-black') {
      activeTheme = localStorage.getItem('mumble-theme') || defaultTheme;
    }

    function applyTheme(themeId) {
      var body = document.body;
      themes.forEach(function (t) {
        body.classList.remove(t.class);
      });
      var theme = themes.find(function (t) { return t.id === themeId; });
      if (theme && theme.class !== 'theme-gold-black') {
        body.classList.add(theme.class);
      }
      localStorage.setItem('mumble-theme', themeId);
      
      // Update UI active buttons
      $$('.theme-btn').forEach(function (btn) {
        var isCurrent = btn.getAttribute('data-theme') === themeId;
        btn.classList.toggle('active', isCurrent);
      });
      activeTheme = themeId;
    }

    // Build the Floating Switcher DOM
    var switcher = document.createElement('div');
    switcher.className = 'colorway-switcher';
    switcher.id = 'themeSwitcher';

    var toggleBtn = document.createElement('button');
    toggleBtn.className = 'switcher-toggle';
    toggleBtn.setAttribute('aria-label', 'Switch Colorway');
    toggleBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22C17.5228 22 22 17.5228 22 12C22 6.47715 17.5228 2 12 2C6.47715 2 2 6.47715 2 12C2 14.7255 3.09032 17.1962 4.85857 19C4.85857 19 4.3 20.5 3.5 21.5C3.5 21.5 5.5 21.5 7 20C8.47552 21.3 10.1528 22 12 22Z"/><circle cx="7.5" cy="10.5" r="1.5" fill="currentColor"/><circle cx="11.5" cy="7.5" r="1.5" fill="currentColor"/><circle cx="16.5" cy="9.5" r="1.5" fill="currentColor"/><circle cx="15.5" cy="14.5" r="1.5" fill="currentColor"/></svg>';
    
    var panel = document.createElement('div');
    panel.className = 'switcher-panel';
    panel.innerHTML = '<h4>Mumble Colorways</h4>';

    var list = document.createElement('div');
    list.className = 'theme-list';

    themes.forEach(function (t) {
      var btn = document.createElement('button');
      btn.className = 'theme-btn';
      btn.setAttribute('data-theme', t.id);
      btn.innerHTML = '<span class="theme-dot" style="--theme-color: ' + t.color + '"></span>' + t.name;
      btn.addEventListener('click', function () {
        applyTheme(t.id);
        
        var targetFile = 'index' + (t.id === 'gold-black' ? '' : '-' + t.id) + '.html';
        if (filename.indexOf('index') === 0 && filename !== targetFile) {
          window.location.href = targetFile;
        }
      });
      list.appendChild(btn);
    });

    panel.appendChild(list);
    switcher.appendChild(toggleBtn);
    switcher.appendChild(panel);
    document.body.appendChild(switcher);

    // Toggle open state
    toggleBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      switcher.classList.toggle('open');
    });

    document.addEventListener('click', function () {
      switcher.classList.remove('open');
    });

    panel.addEventListener('click', function (e) {
      e.stopPropagation();
    });

    // Apply the saved/default theme initially
    applyTheme(activeTheme);
  })();

})();
