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
