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
