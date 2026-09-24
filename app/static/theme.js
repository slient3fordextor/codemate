(() => {
  const themes = ['princess', 'starry'];
  let saved = window.codemateTheme;
  if (!themes.includes(saved)) {
    try { saved = localStorage.getItem('codemate.theme'); } catch { /* Storage is optional. */ }
  }
  function apply(theme, persist = false) {
    if (!themes.includes(theme)) theme = 'princess';
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === theme));
    });
    if (persist) {
      try { localStorage.setItem('codemate.theme', theme); } catch { /* Keep the live theme. */ }
      window.webkit?.messageHandlers?.codemateTheme?.postMessage(theme);
    }
  }
  apply(saved);
  document.addEventListener('DOMContentLoaded', () => {
    apply(document.documentElement.dataset.theme);
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.addEventListener('click', () => apply(button.dataset.themeChoice, true));
    });
  });
})();
