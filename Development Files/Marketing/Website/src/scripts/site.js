const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// Sticky header state.
const siteHeader = document.querySelector('[data-site-header]');
if (siteHeader) {
  const updateHeader = () => siteHeader.classList.toggle('is-scrolled', window.scrollY > 12);
  updateHeader();
  window.addEventListener('scroll', updateHeader, { passive: true });
}

// Reveal content only after the page remains fully readable without JavaScript.
const revealItems = [...document.querySelectorAll('.reveal')];
if (reduceMotion || !('IntersectionObserver' in window)) {
  revealItems.forEach((item) => item.classList.add('is-visible'));
} else {
  const revealObserver = new IntersectionObserver(
    (entries, observer) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      });
    },
    { rootMargin: '0px 0px -8% 0px', threshold: 0.08 },
  );
  revealItems.forEach((item) => revealObserver.observe(item));
}

// Accessible mobile navigation with focus containment and background inertness.
const menuToggle = document.querySelector('[data-menu-toggle]');
const mobileNav = document.querySelector('[data-mobile-nav]');
const inertTargets = [document.querySelector('main'), document.querySelector('[data-site-footer]')].filter(Boolean);

const setMenu = (open, returnFocus = false) => {
  if (!menuToggle || !mobileNav) return;
  menuToggle.setAttribute('aria-expanded', String(open));
  menuToggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
  mobileNav.setAttribute('aria-hidden', String(!open));
  mobileNav.classList.toggle('is-open', open);
  siteHeader?.classList.toggle('menu-active', open);
  document.body.classList.toggle('menu-open', open);
  inertTargets.forEach((target) => { target.inert = open; });

  if (!open && returnFocus) {
    menuToggle.focus();
  }
};

if (menuToggle && mobileNav) {
  menuToggle.addEventListener('click', () => {
    setMenu(menuToggle.getAttribute('aria-expanded') !== 'true');
  });

  mobileNav.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', () => setMenu(false, true));
  });

  document.addEventListener('keydown', (event) => {
    if (menuToggle.getAttribute('aria-expanded') !== 'true') return;
    if (event.key === 'Escape') {
      event.preventDefault();
      setMenu(false, true);
      return;
    }
    if (event.key !== 'Tab') return;

    const focusable = [menuToggle, ...mobileNav.querySelectorAll('a')];
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  window.addEventListener('resize', () => {
    if (window.innerWidth > 900 && menuToggle.getAttribute('aria-expanded') === 'true') {
      const focusWasInsideMenu = mobileNav.contains(document.activeElement);
      setMenu(false);
      if (focusWasInsideMenu) document.querySelector('.brand-link')?.focus();
    }
  });
}

// Workflow tabs: roving tabindex, Arrow/Home/End keys, and labelled panels.
document.querySelectorAll('[data-tabs]').forEach((tabGroup) => {
  const tabs = [...tabGroup.querySelectorAll('[role="tab"]')];
  const panels = [...tabGroup.querySelectorAll('[role="tabpanel"]')];

  const initiallySelected = tabs.find((tab) => tab.getAttribute('aria-selected') === 'true') || tabs[0];
  panels.forEach((panel) => { panel.hidden = panel.dataset.panel !== initiallySelected?.dataset.tab; });

  const activate = (tab, moveFocus = false) => {
    const id = tab.dataset.tab;
    tabs.forEach((item) => {
      const selected = item === tab;
      item.setAttribute('aria-selected', String(selected));
      item.tabIndex = selected ? 0 : -1;
    });
    panels.forEach((panel) => { panel.hidden = panel.dataset.panel !== id; });
    if (moveFocus) tab.focus();
  };

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activate(tab));
    tab.addEventListener('keydown', (event) => {
      let nextIndex = index;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === 'Home') nextIndex = 0;
      else if (event.key === 'End') nextIndex = tabs.length - 1;
      else return;
      event.preventDefault();
      activate(tabs[nextIndex], true);
    });
  });
});

// Small, honest product demo. All controls shown here perform a real action.
document.querySelectorAll('[data-dictation-demo]').forEach((demo) => {
  const trigger = demo.querySelector('[data-demo-trigger]');
  const triggerLabel = trigger?.querySelector('span');
  const canvas = demo.querySelector('[data-demo-canvas]');
  const status = demo.querySelector('[data-demo-status]');
  const outputText = demo.querySelector('[data-demo-output] span');
  const steps = [...demo.querySelectorAll('[data-demo-step]')];
  const timers = [];
  const sentence = 'Mumble removes the distance between a thought and where it needs to go—turning spoken ideas into clear, usable text without breaking your flow.';

  const setStage = (index, state, label) => {
    steps.forEach((step, stepIndex) => step.classList.toggle('is-active', stepIndex === index));
    canvas?.classList.remove('is-listening', 'is-transcribing', 'is-complete');
    if (state) canvas?.classList.add(state);
    if (status) status.textContent = label;
  };

  const schedule = (callback, delay) => {
    const id = window.setTimeout(callback, delay);
    timers.push(id);
  };

  trigger?.addEventListener('click', () => {
    timers.splice(0).forEach((id) => window.clearTimeout(id));
    trigger.disabled = true;
    trigger.setAttribute('aria-busy', 'true');
    if (triggerLabel) triggerLabel.textContent = 'Playing…';
    if (outputText) {
      outputText.textContent = 'Your next sentence will appear here.';
      outputText.classList.add('output-placeholder');
    }
    setStage(0, '', 'Ready');

    const pace = reduceMotion ? 0.45 : 1;
    schedule(() => setStage(1, 'is-listening', 'Listening'), 650 * pace);
    schedule(() => setStage(2, 'is-transcribing', 'Transcribing…'), 2850 * pace);
    schedule(() => {
      if (outputText) {
        outputText.textContent = sentence;
        outputText.classList.remove('output-placeholder');
      }
      setStage(2, 'is-complete', 'Pasted');
      trigger.disabled = false;
      trigger.removeAttribute('aria-busy');
      if (triggerLabel) triggerLabel.textContent = 'Replay the flow';
    }, 4300 * pace);
  });
});

// Platform-aware availability note without offering unusable downloads.
const osNotice = document.querySelector('[data-os-notice]');
if (osNotice) {
  const platform = navigator.userAgentData?.platform || '';
  const userAgent = navigator.userAgent || '';
  if (/Mac/i.test(platform) || /Macintosh|Mac OS X/i.test(userAgent)) {
    osNotice.textContent = 'You appear to be on macOS. Mumble is currently Windows-only; the macOS version is in development.';
  } else if (/Linux/i.test(platform) || /Linux/i.test(userAgent)) {
    osNotice.textContent = 'You appear to be on Linux. Mumble is currently Windows-only; the Linux version is in development.';
  }
}

document.querySelectorAll('[data-current-year]').forEach((year) => {
  year.textContent = String(new Date().getFullYear());
});
