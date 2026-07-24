/* =====================================================================
   MUMBLE — Nav scroll behavior + mobile menu. Vanilla JS, no dependencies.
   Mirrors the nav + mobile-menu logic from the original app.js.
   ===================================================================== */

(function () {
  "use strict";

  /* ------------------------- Nav scroll ---------------------------- */
  var navWrap = document.getElementById("navWrap");
  var onScroll = function () {
    if (navWrap) navWrap.classList.toggle("scrolled", window.scrollY > 12);
  };
  onScroll();
  window.addEventListener("scroll", onScroll, { passive: true });

  /* ------------------------ Mobile menu ---------------------------- */
  var toggle = document.getElementById("navToggle");
  var menu = document.getElementById("mobileMenu");

  var setMenu = function (open) {
    if (!menu) return;
    menu.classList.toggle("open", open);
    if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
    document.body.style.overflow = open ? "hidden" : "";
  };

  if (toggle) {
    toggle.addEventListener("click", function () {
      setMenu(!menu.classList.contains("open"));
    });
  }

  if (menu) {
    var links = menu.querySelectorAll("a");
    links.forEach(function (a) {
      a.addEventListener("click", function () {
        setMenu(false);
      });
    });
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") setMenu(false);
  });

})();
