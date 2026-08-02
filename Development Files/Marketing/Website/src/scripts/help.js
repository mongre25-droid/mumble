const searchForm = document.querySelector('[data-help-search]');

if (searchForm) {
  const input = searchForm.querySelector('input[type="search"]');
  const clearButton = searchForm.querySelector('[data-help-search-clear]');
  const status = searchForm.querySelector('[data-help-search-status]');
  const emptyState = document.querySelector('[data-help-search-empty]');
  const articles = [...document.querySelectorAll('[data-help-article]')];
  const groups = [...document.querySelectorAll('[data-help-group]')];
  const normalize = (value) => value.toLowerCase().replace(/\s+/g, ' ').trim();

  const resetSearch = () => {
    if (!input) return;
    input.value = '';
    applySearch();
  };

  const applySearch = () => {
    if (!input || !status || !emptyState) return;
    const query = normalize(input.value);
    let shown = 0;

    articles.forEach((article) => {
      const keywords = article.getAttribute('data-help-keywords') ?? '';
      const haystack = normalize(`${article.textContent ?? ''} ${keywords}`);
      const matches = !query || haystack.includes(query);
      article.hidden = !matches;
      if (matches) shown += 1;
    });

    groups.forEach((group) => {
      group.hidden = Boolean(query) && !group.querySelector('[data-help-article]:not([hidden])');
    });

    emptyState.hidden = shown !== 0;
    if (clearButton) clearButton.disabled = query.length === 0;
    status.textContent = `${shown} help ${shown === 1 ? 'topic' : 'topics'} shown.`;
  };

  searchForm.hidden = false;
  searchForm.addEventListener('submit', (event) => event.preventDefault());
  input?.addEventListener('input', applySearch);
  input?.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    resetSearch();
  });
  clearButton?.addEventListener('click', () => {
    resetSearch();
    input?.focus();
  });

  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener('click', () => {
      const target = document.querySelector(link.getAttribute('href'));
      if (target?.matches('[data-help-article], [data-help-article] *')) resetSearch();
    });
  });

  window.addEventListener('hashchange', resetSearch);
  applySearch();
}
