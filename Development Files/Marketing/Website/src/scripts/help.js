const searchForm = document.querySelector('[data-help-search]');

if (searchForm) {
  const input = searchForm.querySelector('input[type="search"]');
  const clearButton = searchForm.querySelector('[data-help-search-clear]');
  const status = searchForm.querySelector('[data-help-search-status]');
  const emptyState = document.querySelector('[data-help-search-empty]');
  const library = document.querySelector('#help-library');
  const articles = [...document.querySelectorAll('[data-help-article]')];
  const groups = [...document.querySelectorAll('[data-help-group]')];
  const contextLabels = [...document.querySelectorAll('[data-help-result-context]')];
  const normalize = (value) => value.toLowerCase().replace(/\s+/g, ' ').trim();

  const resetSearch = () => {
    if (!input) return;
    input.value = '';
    applySearch();
  };

  const applySearch = () => {
    if (!input || !status || !emptyState) return;
    const query = normalize(input.value);
    const queryTerms = query.split(' ').filter(Boolean);
    let shown = 0;
    const shownContexts = new Set();

    articles.forEach((article) => {
      const keywords = article.getAttribute('data-help-keywords') ?? '';
      const context = article.getAttribute('data-help-context')
        ?? article.querySelector('[data-help-result-context]')?.textContent
        ?? 'Help';
      const haystack = normalize(`${article.textContent ?? ''} ${keywords} ${context}`);
      const matches = !query || queryTerms.every((term) => haystack.includes(term));
      article.hidden = !matches;
      if (matches) {
        shown += 1;
        shownContexts.add(context);
      }
    });

    groups.forEach((group) => {
      group.hidden = Boolean(query) && !group.querySelector('[data-help-article]:not([hidden])');
    });

    emptyState.hidden = shown !== 0;
    library?.toggleAttribute('data-searching', Boolean(query));
    contextLabels.forEach((label) => label.setAttribute('aria-hidden', query ? 'false' : 'true'));
    if (clearButton) clearButton.disabled = query.length === 0;
    status.textContent = query
      ? `${shown} help ${shown === 1 ? 'topic' : 'topics'} shown${shown ? ` in ${[...shownContexts].join(', ')}` : ''}.`
      : `${shown} help ${shown === 1 ? 'topic' : 'topics'} available.`;
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
