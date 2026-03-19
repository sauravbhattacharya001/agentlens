/**
 * AgentLens Docs — Client-Side Search
 * Builds an in-memory index from page metadata and provides instant results.
 */
(function () {
  'use strict';

  const PAGES = [
    { url: 'index.html', title: 'Introduction', section: 'Overview',
      keywords: 'agentlens observability monitoring llm ai agents overview introduction' },
    { url: 'getting-started.html', title: 'Getting Started', section: 'Getting Started',
      keywords: 'install setup quickstart pip npm getting started configuration' },
    { url: 'quickstart.html', title: 'Quickstart', section: 'Getting Started',
      keywords: 'quickstart tutorial hello world first steps beginner' },
    { url: 'architecture.html', title: 'Architecture', section: 'Overview',
      keywords: 'architecture design system components backend frontend sdk pipeline' },
    { url: 'decorators.html', title: 'Decorators', section: 'Core Concepts',
      keywords: 'decorators python trace span wrap function annotation' },
    { url: 'models.html', title: 'Models', section: 'Core Concepts',
      keywords: 'models openai anthropic llm provider configuration integration' },
    { url: 'sampling.html', title: 'Sampling', section: 'Core Concepts',
      keywords: 'sampling traces filter rate sample percentage head tail' },
    { url: 'transport.html', title: 'Transport', section: 'Core Concepts',
      keywords: 'transport http grpc websocket batching export send data' },
    { url: 'monitoring.html', title: 'Monitoring', section: 'Features',
      keywords: 'monitoring alerts dashboard metrics latency tokens cost errors' },
    { url: 'explainability.html', title: 'Explainability', section: 'Features',
      keywords: 'explainability interpretability reasoning chain thought attribution' },
    { url: 'session-replay.html', title: 'Session Replay', section: 'Features',
      keywords: 'session replay playback conversation history debug trace' },
    { url: 'cost-optimization.html', title: 'Cost Optimization', section: 'Features',
      keywords: 'cost optimization pricing budget token usage spend reduce' },
    { url: 'api.html', title: 'REST API Reference', section: 'Reference',
      keywords: 'api rest http endpoints routes request response json' },
    { url: 'sdk-reference.html', title: 'SDK Reference', section: 'Reference',
      keywords: 'sdk python javascript typescript client library class method' },
    { url: 'database.html', title: 'Database', section: 'Infrastructure',
      keywords: 'database postgresql sqlite migration schema storage persistence' },
    { url: 'deployment.html', title: 'Deployment', section: 'Infrastructure',
      keywords: 'deployment docker kubernetes production hosting server deploy' },
    { url: 'dashboard.html', title: 'Dashboard', section: 'Features',
      keywords: 'dashboard ui visualization charts graphs metrics frontend react' },
    { url: 'integrations.html', title: 'Integrations', section: 'Reference',
      keywords: 'integrations langchain llamaindex autogen crewai framework plugin' },
  ];

  /** Simple scoring: count how many query tokens appear in the page keywords + title. */
  function search(query) {
    const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!tokens.length) return [];

    return PAGES
      .map(function (page) {
        const hay = (page.title + ' ' + page.section + ' ' + page.keywords).toLowerCase();
        let score = 0;
        tokens.forEach(function (t) {
          // Exact word boundary match = 3 pts, substring match = 1 pt
          if (new RegExp('\\b' + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b').test(hay)) {
            score += 3;
          } else if (hay.indexOf(t) !== -1) {
            score += 1;
          }
        });
        return { page: page, score: score };
      })
      .filter(function (r) { return r.score > 0; })
      .sort(function (a, b) { return b.score - a.score; })
      .slice(0, 8);
  }

  /** Render the search UI into the header. */
  function init() {
    var header = document.querySelector('.header-nav');
    if (!header) return;

    // Create search container
    var container = document.createElement('div');
    container.className = 'doc-search';
    container.innerHTML =
      '<input type="search" class="doc-search-input" placeholder="Search docs…" aria-label="Search documentation" />' +
      '<div class="doc-search-results" hidden></div>';
    header.insertBefore(container, header.firstChild);

    var input = container.querySelector('.doc-search-input');
    var resultsEl = container.querySelector('.doc-search-results');
    var debounce = null;

    input.addEventListener('input', function () {
      clearTimeout(debounce);
      debounce = setTimeout(function () {
        var q = input.value.trim();
        if (!q) { resultsEl.hidden = true; return; }
        var results = search(q);
        if (!results.length) {
          resultsEl.innerHTML = '<div class="doc-search-empty">No results found</div>';
        } else {
          resultsEl.innerHTML = results.map(function (r) {
            return '<a class="doc-search-item" href="' + r.page.url + '">' +
              '<span class="doc-search-title">' + r.page.title + '</span>' +
              '<span class="doc-search-section">' + r.page.section + '</span>' +
              '</a>';
          }).join('');
        }
        resultsEl.hidden = false;
      }, 150);
    });

    // Keyboard: Escape closes, Ctrl+K or / focuses
    document.addEventListener('keydown', function (e) {
      if ((e.ctrlKey && e.key === 'k') || (e.key === '/' && document.activeElement.tagName !== 'INPUT')) {
        e.preventDefault();
        input.focus();
      }
      if (e.key === 'Escape') {
        resultsEl.hidden = true;
        input.blur();
      }
    });

    // Click outside closes
    document.addEventListener('click', function (e) {
      if (!container.contains(e.target)) resultsEl.hidden = true;
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
