const mobileQuery = window.matchMedia('(max-width: 760px)');
const header = document.querySelector('[data-site-header]');
const menuToggle = document.querySelector('[data-menu-toggle]');
const siteMenu = document.querySelector('[data-site-menu]');
const inertTargets = [
  document.querySelector('main'),
  document.querySelector('[data-site-footer]'),
  document.querySelector('[data-menu-background]'),
].filter(Boolean);

let menuOpen = false;

function setMenu(open, { returnFocus = false } = {}) {
  if (!menuToggle || !siteMenu) return;
  menuOpen = Boolean(open && mobileQuery.matches);
  menuToggle.setAttribute('aria-expanded', String(menuOpen));
  menuToggle.setAttribute('aria-label', menuOpen ? 'Close menu' : 'Open menu');
  siteMenu.setAttribute('aria-hidden', String(mobileQuery.matches && !menuOpen));
  siteMenu.toggleAttribute('data-open', menuOpen);
  document.body.classList.toggle('menu-open', menuOpen);
  inertTargets.forEach((target) => {
    target.inert = menuOpen;
  });

  if (menuOpen) {
    siteMenu.querySelector('a')?.focus();
  } else if (returnFocus) {
    menuToggle.focus();
  }
}

function syncMenuMode() {
  if (!mobileQuery.matches) {
    setMenu(false);
    siteMenu?.setAttribute('aria-hidden', 'false');
  } else {
    setMenu(false, { returnFocus: menuOpen });
  }
}

if (menuToggle && siteMenu) {
  menuToggle.addEventListener('click', () => setMenu(!menuOpen));
  siteMenu.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', () => {
      setMenu(false);
      requestAnimationFrame(() => menuToggle.focus());
    });
  });

  document.addEventListener('keydown', (event) => {
    if (!menuOpen) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      setMenu(false, { returnFocus: true });
      return;
    }
    if (event.key !== 'Tab') return;

    const focusable = [menuToggle, ...siteMenu.querySelectorAll('a')];
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

  mobileQuery.addEventListener('change', syncMenuMode);
  syncMenuMode();
}

function detectedPlatform() {
  const userAgent = navigator.userAgent || '';
  const mobile = /Android|iPhone|iPad|iPod|Mobile/i.test(userAgent) || navigator.userAgentData?.mobile === true;
  if (mobile) return 'mobile';
  if (/Windows/i.test(userAgent)) return 'windows';
  if (/Macintosh|Mac OS X/i.test(userAgent)) return 'macos';
  if (/Linux/i.test(userAgent)) return 'linux';
  return 'unknown';
}

const platform = detectedPlatform();
document.documentElement.dataset.platform = platform;
document.querySelectorAll('[data-platform-action]').forEach((action) => {
  const label = action.dataset[`${platform}Label`];
  const href = action.dataset[`${platform}Href`];
  const download = action.dataset[`${platform}Download`] === 'true';
  if (!label || !href) return;
  action.href = href;
  action.querySelector('span').textContent = label;
  action.toggleAttribute('download', download);
});

document.querySelectorAll('[data-current-year]').forEach((year) => {
  year.textContent = String(new Date().getFullYear());
});

document.querySelectorAll('[data-home-montage]').forEach((montage) => {
  const tabs = [...montage.querySelectorAll('[data-home-step-select]')];
  const panels = [...montage.querySelectorAll('[data-home-panel]')];
  const status = montage.querySelector('[data-home-status]');
  const previous = montage.querySelector('[data-home-previous]');
  const next = montage.querySelector('[data-home-next]');
  const play = montage.querySelector('[data-home-play]');
  const pause = montage.querySelector('[data-home-pause]');
  if (tabs.length !== 5 || panels.length !== 5) return;

  const constrained = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    || navigator.connection?.saveData === true;
  let selectedIndex = 0;
  let timer = null;
  let passCount = 0;

  const updateStatus = (state) => {
    montage.dataset.homeState = state;
    montage.dataset.homePassCount = String(passCount);
    if (play) {
      play.disabled = state === 'playing' || passCount >= 1;
      play.setAttribute('aria-label', passCount >= 1
        ? 'Guided pass complete'
        : state === 'paused' ? 'Resume guided pass' : state === 'playing' ? 'Guided pass is playing' : 'Play one guided pass');
    }
    if (pause) {
      pause.disabled = state !== 'playing';
      pause.setAttribute('aria-label', state === 'playing' ? 'Pause guided pass' : 'Guided pass is not playing');
    }
    if (status) {
      const descriptions = {
        playing: `Guided pass playing · ${tabs[selectedIndex].textContent.trim().split(':')[0]}`,
        paused: `Guided pass paused · ${tabs[selectedIndex].textContent.trim().split(':')[0]}`,
        settled: 'Guided pass complete · manual controls ready',
        manual: `Manual view · ${tabs[selectedIndex].textContent.trim().split(':')[0]}`,
      };
      status.textContent = descriptions[state];
    }
  };

  const show = (index, { focus = false, state = montage.dataset.homeState } = {}) => {
    selectedIndex = Math.max(0, Math.min(index, tabs.length - 1));
    montage.dataset.homeStep = String(selectedIndex + 1);
    tabs.forEach((tab, tabIndex) => {
      const active = tabIndex === selectedIndex;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      if (focus && active) tab.focus();
    });
    panels.forEach((panel, panelIndex) => panel.toggleAttribute('data-active', panelIndex === selectedIndex));
    updateStatus(state);
  };

  const stop = (state = 'manual') => {
    if (timer) window.clearInterval(timer);
    timer = null;
    updateStatus(state);
  };

  const start = () => {
    if (passCount >= 1) {
      updateStatus('settled');
      return;
    }
    if (selectedIndex === tabs.length - 1) show(0, { state: 'playing' });
    stop('playing');
    timer = window.setInterval(() => {
      if (selectedIndex < tabs.length - 1) {
        show(selectedIndex + 1, { state: 'playing' });
      } else {
        passCount = 1;
        stop('settled');
      }
    }, 2000);
  };

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => {
      stop('manual');
      show(index, { state: 'manual' });
    });
    tab.addEventListener('keydown', (event) => {
      const destinations = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: tabs.length - 1 };
      if (!(event.key in destinations)) return;
      event.preventDefault();
      stop('manual');
      show((destinations[event.key] + tabs.length) % tabs.length, { focus: true, state: 'manual' });
    });
  });
  previous?.addEventListener('click', () => {
    stop('manual');
    show((selectedIndex - 1 + tabs.length) % tabs.length, { state: 'manual' });
  });
  next?.addEventListener('click', () => {
    stop('manual');
    show((selectedIndex + 1) % tabs.length, { state: 'manual' });
  });
  play?.addEventListener('click', start);
  pause?.addEventListener('click', () => stop('paused'));

  show(0, { state: 'manual' });
  if (!constrained) start();
});

document.querySelectorAll('[data-job-demo]').forEach((demo) => {
  const jobId = demo.dataset.jobDemo;
  const tabs = Array.from(demo.querySelectorAll('[data-job-tab]'));
  const panels = Array.from(demo.querySelectorAll('[data-job-panel]'));
  const previous = demo.querySelector('[data-job-previous]');
  const next = demo.querySelector('[data-job-next]');
  if (!jobId || tabs.length === 0 || tabs.length !== panels.length) return;
  if (tabs.some((tab) => tab.dataset.jobTab !== jobId)
    || panels.some((panel) => panel.dataset.jobPanel !== jobId)) return;

  let selectedIndex = Math.max(0, tabs.findIndex((tab) => tab.getAttribute('aria-selected') === 'true'));

  const selectStep = (index, { focus = false } = {}) => {
    selectedIndex = (index + tabs.length) % tabs.length;
    tabs.forEach((tab, tabIndex) => {
      const selected = tabIndex === selectedIndex;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });
    panels.forEach((panel, panelIndex) => {
      panel.hidden = panelIndex !== selectedIndex;
    });
    demo.dataset.jobStep = String(selectedIndex + 1);
    if (focus) tabs[selectedIndex].focus();
  };

  tabs.forEach((tab, tabIndex) => {
    tab.addEventListener('click', () => selectStep(tabIndex));
    tab.addEventListener('keydown', (event) => {
      const keys = {
        ArrowLeft: tabIndex - 1,
        ArrowRight: tabIndex + 1,
        Home: 0,
        End: tabs.length - 1,
      };
      if (!(event.key in keys)) return;
      event.preventDefault();
      selectStep(keys[event.key], { focus: true });
    });
  });

  previous?.addEventListener('click', () => selectStep(selectedIndex - 1));
  next?.addEventListener('click', () => selectStep(selectedIndex + 1));
  selectStep(selectedIndex);
});

if (header) {
  const updateHeader = () => header.toggleAttribute('data-scrolled', window.scrollY > 8);
  updateHeader();
  window.addEventListener('scroll', updateHeader, { passive: true });
}

document.querySelectorAll('[data-route-tabs]').forEach((routeList) => {
  const tabs = [...routeList.querySelectorAll('[data-route-tab]')];
  const panels = new Map(
    [...document.querySelectorAll('[data-route-panel]')].map((panel) => [panel.dataset.routePanel, panel]),
  );
  if (tabs.length < 2 || tabs.some((tab) => !panels.has(tab.dataset.routeTab))) return;

  routeList.setAttribute('role', 'tablist');

  const updateRouteOrientation = () => {
    routeList.setAttribute('aria-orientation', mobileQuery.matches ? 'vertical' : 'horizontal');
  };
  mobileQuery.addEventListener('change', updateRouteOrientation);
  updateRouteOrientation();

  function activate(routeId, { focus = false, updateHash = false } = {}) {
    tabs.forEach((tab) => {
      const selected = tab.dataset.routeTab === routeId;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      panels.get(tab.dataset.routeTab).hidden = !selected;
    });

    const selectedTab = tabs.find((tab) => tab.dataset.routeTab === routeId);
    if (focus) selectedTab?.focus();
    if (updateHash && selectedTab) {
      history.replaceState(history.state, '', selectedTab.getAttribute('href'));
    }
  }

  tabs.forEach((tab, index) => {
    const routeId = tab.dataset.routeTab;
    const panel = panels.get(routeId);
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-controls', panel.id);
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', tab.id);
    panel.tabIndex = 0;

    tab.addEventListener('click', (event) => {
      event.preventDefault();
      activate(routeId, { updateHash: true });
    });

    tab.addEventListener('keydown', (event) => {
      const vertical = routeList.getAttribute('aria-orientation') === 'vertical';
      let nextIndex;
      if (
        (vertical && event.key === 'ArrowDown') ||
        (!vertical && event.key === 'ArrowRight')
      ) {
        nextIndex = (index + 1) % tabs.length;
      } else if (
        (vertical && event.key === 'ArrowUp') ||
        (!vertical && event.key === 'ArrowLeft')
      ) {
        nextIndex = (index - 1 + tabs.length) % tabs.length;
      } else if (event.key === 'Home') {
        nextIndex = 0;
      } else if (event.key === 'End') {
        nextIndex = tabs.length - 1;
      } else {
        return;
      }
      event.preventDefault();
      activate(tabs[nextIndex].dataset.routeTab, { focus: true, updateHash: true });
    });
  });

  const hashRoute = window.location.hash.slice(1);
  const initialRoute = panels.has(hashRoute) ? hashRoute : tabs[0].dataset.routeTab;
  activate(initialRoute);
});
