/* =====================================================================
   MUMBLE — Ambient gold dust canvas.
   Vanilla JS, no dependencies. Resource-aware: pauses when tab hidden,
   respects prefers-reduced-motion. Particle count scales with viewport.
   ===================================================================== */

(function () {
  "use strict";

  var reduce =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reduce) {
    /* Hide the canvas entirely when reduced motion is preferred */
    var dustCanvas = document.getElementById("dust");
    if (dustCanvas) dustCanvas.style.display = "none";
    return;
  }

  var canvas = document.getElementById("dust");
  if (!canvas || !canvas.getContext) return;

  var ctx = canvas.getContext("2d");
  var dpr = Math.min(window.devicePixelRatio || 1, 2);
  var w, h, parts = [];

  function rand(a, b) {
    return a + Math.random() * (b - a);
  }

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
        x: Math.random() * w,
        y: Math.random() * h,
        r: rand(0.5, 1.7) * dpr,
        vx: rand(-0.12, 0.12) * dpr,
        vy: rand(-0.28, -0.05) * dpr,
        a: rand(0.06, 0.4),
        tw: rand(0.005, 0.02),
        tp: Math.random() * Math.PI * 2,
      });
    }
  }

  var running = true;
  var rafId = null;

  function draw() {
    if (!running) {
      rafId = null;
      return;
    }

    ctx.clearRect(0, 0, w, h);
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      p.x += p.vx;
      p.y += p.vy;
      p.tp += p.tw;

      if (p.y < -10) {
        p.y = h + 10;
        p.x = Math.random() * w;
      }
      if (p.x < -10) p.x = w + 10;
      if (p.x > w + 10) p.x = -10;

      var a = p.a * (0.6 + 0.4 * Math.sin(p.tp));
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(212,175,55," + a.toFixed(3) + ")";
      ctx.shadowBlur = 6 * dpr;
      ctx.shadowColor = "rgba(212,175,55,0.5)";
      ctx.fill();
    }
    rafId = requestAnimationFrame(draw);
  }

  function start() {
    if (!running) {
      running = true;
      draw();
    }
  }

  function stop() {
    running = false;
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
  }

  resize();
  seed();
  draw();

  /* Rebuild particles on resize (debounced) */
  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      resize();
      seed();
    }, 200);
  });

  /* Pause when tab hidden — resource-aware, matching Mumble's ethos */
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      stop();
    } else {
      start();
    }
  });
})();
