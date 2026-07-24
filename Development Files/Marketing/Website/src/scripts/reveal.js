/* =====================================================================
   MUMBLE — Scroll-reveal animations via IntersectionObserver.
   Vanilla JS, no dependencies. Progressive enhancement: gated behind .js.
   Respects prefers-reduced-motion. Mirrors the original app.js reveal.
   ===================================================================== */

(function () {
  "use strict";

  var reduce =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var reveals = document.querySelectorAll(".reveal");

  /* ------- Show everything that is already in (or near) the viewport ------ */
  function revealInView() {
    var vh = window.innerHeight || document.documentElement.clientHeight;
    reveals.forEach(function (el) {
      if (el.classList.contains("in")) return;
      if (el.getBoundingClientRect().top < vh * 0.92)
        el.classList.add("in");
    });
  }

  /* ------- If reduced motion or no observer support → show all immediately ------ */
  if (reduce || !("IntersectionObserver" in window)) {
    reveals.forEach(function (el) {
      el.classList.add("in");
    });
  } else {
    /* Show above-fold content synchronously, then set up observer for the rest */
    revealInView();

    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) {
            en.target.classList.add("in");
            io.unobserve(en.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -8% 0px" }
    );

    reveals.forEach(function (el) {
      if (!el.classList.contains("in")) io.observe(el);
    });

    /* Safety net: re-check shortly after load to catch any stragglers */
    window.addEventListener("load", function () {
      setTimeout(revealInView, 300);
    });
  }
})();
