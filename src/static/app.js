(function () {
  const THEME_KEY = "anyterial_theme";
  const DEFAULT_THEME = "twilight";
  const root = document.documentElement;
  const buttons = Array.from(document.querySelectorAll(".theme-btn"));

  if (!buttons.length) {
    return;
  }

  const applyTheme = (theme) => {
    root.setAttribute("data-theme", theme);
    buttons.forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.themeValue === theme);
    });
  };

  const stored = window.localStorage.getItem(THEME_KEY);
  const initial = stored || DEFAULT_THEME;
  applyTheme(initial);

  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const selected = btn.dataset.themeValue || DEFAULT_THEME;
      applyTheme(selected);
      window.localStorage.setItem(THEME_KEY, selected);
    });
  });
})();
