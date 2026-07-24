/* =====================================================================
   MUMBLE — Interactive demo controllers.
   Vanilla JS, no dependencies. Handles:
   1. Smart Modes tab switcher (all 5 armable modes)
   2. Interface showcase tab switcher (Home, Deck, Stats, Reader, Settings)
   3. Reader word-highlight animation loop
   ===================================================================== */

(function () {
  "use strict";

  var reduce =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ===================================================================
     SMART MODES TAB SWITCHER — The Big Shift (5 armable modes)
     =================================================================== */
  (function () {
    var data = {
      text: {
        c: "--mode-text",
        tag: "Text",
        said: '"quick note from the call — they want the beta by end of month and a pricing page first"',
        body: "Quick note from the call: they want the beta by the end of the month, and a pricing page in place before launch.",
        description: "Default mode. Clean dictation with fillers removed, capitalisation and punctuation fixed. Always armed — no toggle needed.",
      },
      prompt: {
        c: "--mode-prompt",
        tag: "Prompt",
        said: '"write a short launch announcement for a privacy-first dictation app — friendly but confident"',
        body: "Write a short launch announcement for a privacy-first dictation app.\n\n\u2022 Audience: knowledge workers\n\u2022 Tone: friendly but confident\n\u2022 Emphasise: on-device transcription, paste anywhere\n\u2022 Length: 3 short paragraphs",
        description: "Builds a structured AI prompt with role, task, and guidelines. Ready to paste into any assistant.",
      },
      email: {
        c: "--mode-email",
        tag: "Email",
        said: '"let sarah know the latest revision looks great and we\u2019ll ship friday"',
        body: "Hi Sarah,\n\nThe latest revision looks great \u2014 thank you. We\u2019re on track to ship Friday as planned.\n\nBest,\nAlex",
        description: "Drafts a tidy email with greeting, body, and sign-off from your dictated message.",
      },
      reply: {
        c: "--mode-reply",
        tag: "Reply",
        said: '"thanks for the detailed feedback \u2014 I agree with points 1 and 3, point 2 needs more data, let\u2019s discuss tomorrow"',
        body: "Thanks for the detailed feedback.\n\nI agree with points 1 and 3. Point 2 needs more data \u2014 let\u2019s discuss tomorrow.\n\nBest,\nAlex",
        description: "Replies to your highlighted text (or latest clipboard item), matching its tone.",
      },
      foreign: {
        c: "--mode-foreign",
        tag: "Foreign",
        said: '"the report is due next week and the client requested changes to section four"',
        body: "The report is due next week and the client requested changes to section four.\n\n[Non-English terms preserved and resolved correctly, prioritising your chosen languages.]",
        description: "Preserves non-English terms and resolves them correctly using your configured languages.",
      },
    };

    var tabs = document.querySelectorAll(".shift-tab");
    var out = document.getElementById("shiftOut");
    var tag = document.getElementById("shiftTag");
    var bodyEl = document.getElementById("shiftBody");
    var said = document.getElementById("shiftSaid");
    var desc = document.getElementById("shiftDesc");

    if (!tabs.length || !out) return;

    function apply(key) {
      var d = data[key];
      if (!d) return;

      var accentVar = "var(" + d.c + ")";

      out.style.setProperty("--oc", accentVar);
      tag.textContent = d.tag;
      said.textContent = d.said;

      if (d.body.indexOf("\u2022") === 0) {
        var items = d.body.split("\n");
        var html = "<ul>";
        items.forEach(function (item) {
          var text = item.replace(/^\u2022\s*/, "");
          if (text) html += "<li>" + text + "</li>";
        });
        html += "</ul>";
        bodyEl.innerHTML = html;
      } else {
        bodyEl.textContent = d.body;
      }

      if (desc) desc.textContent = d.description;

      tabs.forEach(function (t) {
        var on = t.getAttribute("data-shift") === key;
        t.classList.toggle("active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
    }

    tabs.forEach(function (t) {
      t.addEventListener("click", function () {
        apply(t.getAttribute("data-shift") || "");
      });
    });

    /* Default: show Text mode */
    apply("text");
  })();

  /* ===================================================================
     INTERFACE SHOWCASE TAB SWITCHER
     =================================================================== */
  (function () {
    var scTabs = document.querySelectorAll(".sc-tab");
    var npTabs = document.querySelectorAll(".win-nav .np");
    var panes = document.querySelectorAll(".win-pane");
    var status = document.getElementById("winStatus");

    if (!panes.length) return;

    function show(pane) {
      panes.forEach(function (p) {
        p.classList.toggle("active", p.getAttribute("data-pane") === pane);
      });

      scTabs.forEach(function (t) {
        var on = t.getAttribute("data-pane") === pane;
        t.classList.toggle("active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });

      npTabs.forEach(function (t) {
        t.classList.toggle("active", t.getAttribute("data-pane") === pane);
      });

      if (status) {
        var rec = pane === "home";
        status.classList.toggle("rec", rec);
        var st = status.querySelector(".st");
        if (st) st.textContent = rec ? "Listening" : "Ready";
      }
    }

    scTabs.forEach(function (t) {
      t.addEventListener("click", function () {
        show(t.getAttribute("data-pane") || "");
      });
    });

    npTabs.forEach(function (t) {
      t.addEventListener("click", function () {
        show(t.getAttribute("data-pane") || "");
      });
    });

    show("home");
  })();

  /* ===================================================================
     READER WORD-HIGHLIGHT ANIMATION
     =================================================================== */
  (function () {
    if (reduce) return;

    var readerTexts = document.querySelectorAll(".reader-text");
    if (!readerTexts.length) return;

    readerTexts.forEach(function (el) {
      var words = el.querySelectorAll(".w");
      if (!words.length) return;

      var idx = -1;
      var timer = null;

      function step() {
        if (idx >= 0) words[idx].classList.remove("spoken");
        idx = (idx + 1) % words.length;
        words[idx].classList.add("spoken");
      }

      function visible() {
        var r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.top < (window.innerHeight || 0) && r.bottom > 0;
      }

      function tick() {
        if (visible() && !timer) {
          timer = setInterval(step, 360);
        } else if (!visible() && timer) {
          clearInterval(timer);
          timer = null;
        }
      }

      window.addEventListener("scroll", tick, { passive: true });
      document.addEventListener("click", function () {
        setTimeout(tick, 60);
      });
      setInterval(tick, 800);
    });
  })();
})();
