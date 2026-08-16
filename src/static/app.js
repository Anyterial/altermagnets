(function () {
  const THEME_KEY = "anyterial_theme";
  const DEFAULT_THEME = "twilight";
  const THEME_OPTIONS = new Set(["dark", "twilight", "light"]);

  const root = document.documentElement;
  const themeButtons = Array.from(document.querySelectorAll("[data-theme-option]"));
  const sidebar = document.querySelector(".sidebar");

  const normalizeTheme = (value) => {
    if (typeof value !== "string") {
      return DEFAULT_THEME;
    }
    const lowered = value.trim().toLowerCase();
    return THEME_OPTIONS.has(lowered) ? lowered : DEFAULT_THEME;
  };

  const applyTheme = (theme) => {
    const active = normalizeTheme(theme);
    root.setAttribute("data-theme", active);
    themeButtons.forEach((btn) => {
      const option = normalizeTheme(btn.getAttribute("data-theme-option"));
      btn.classList.toggle("is-active", option === active);
      btn.setAttribute("aria-pressed", option === active ? "true" : "false");
    });
    document.querySelectorAll("img.theme-aware-figure").forEach((image) => {
      const lightSrc = image.getAttribute("data-src-light") || image.getAttribute("src") || "";
      const darkSrc = image.getAttribute("data-src-dark") || lightSrc;
      const selected = active === "dark" ? darkSrc : lightSrc;
      if (selected && image.getAttribute("src") !== selected) {
        image.setAttribute("src", selected);
      }
    });
  };

  const stored = window.localStorage.getItem(THEME_KEY);
  applyTheme(stored || DEFAULT_THEME);

  themeButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const selected = normalizeTheme(btn.getAttribute("data-theme-option"));
      applyTheme(selected);
      window.localStorage.setItem(THEME_KEY, selected);
    });
  });

  const initBidirectionalSidebar = () => {
    if (!sidebar) {
      return;
    }

    const topGap = 20;
    const bottomGap = 20;
    const mobileQuery = window.matchMedia("(max-width: 980px)");
    let active = false;
    let minOffset = topGap;
    let currentOffset = topGap;
    let lastScrollY = window.scrollY || window.pageYOffset || 0;

    const applyOffset = () => {
      sidebar.style.setProperty("--sidebar-sticky-offset", `${currentOffset}px`);
    };

    const recalc = () => {
      const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
      const sidebarHeight = sidebar.offsetHeight;
      const fitsViewport = sidebarHeight + topGap + bottomGap <= viewportHeight;

      if (mobileQuery.matches || fitsViewport || viewportHeight <= 0) {
        active = false;
        minOffset = topGap;
        currentOffset = topGap;
        applyOffset();
        lastScrollY = window.scrollY || window.pageYOffset || 0;
        return;
      }

      active = true;
      minOffset = viewportHeight - sidebarHeight - bottomGap;
      currentOffset = Math.min(topGap, Math.max(minOffset, currentOffset));
      applyOffset();
      lastScrollY = window.scrollY || window.pageYOffset || 0;
    };

    const onScroll = () => {
      if (!active) {
        lastScrollY = window.scrollY || window.pageYOffset || 0;
        return;
      }

      const nextScrollY = window.scrollY || window.pageYOffset || 0;
      const delta = nextScrollY - lastScrollY;
      lastScrollY = nextScrollY;
      if (delta === 0) {
        return;
      }

      const nextOffset = Math.min(topGap, Math.max(minOffset, currentOffset - delta));
      if (nextOffset === currentOffset) {
        return;
      }
      currentOffset = nextOffset;
      applyOffset();
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", recalc, { passive: true });
    if (typeof mobileQuery.addEventListener === "function") {
      mobileQuery.addEventListener("change", recalc);
    }
    window.addEventListener("load", recalc);
    if (document.fonts && typeof document.fonts.addEventListener === "function") {
      document.fonts.addEventListener("loadingdone", recalc);
    }

    recalc();
  };

  initBidirectionalSidebar();

  const initFloatingInfoBubbles = (scope = document) => {
    const infoDots = Array.from(scope.querySelectorAll(".info-dot"));
    if (infoDots.length === 0) {
      return;
    }

    root.classList.add("js-fixed-tooltips");
    let floatingBubble = document.querySelector(".floating-info-bubble");
    if (!floatingBubble) {
      floatingBubble = document.createElement("div");
      floatingBubble.className = "floating-info-bubble";
      floatingBubble.setAttribute("aria-hidden", "true");
      document.body.appendChild(floatingBubble);
    }

    let activeDot = null;

    const positionBubble = () => {
      if (!activeDot) {
        return;
      }
      const rect = activeDot.getBoundingClientRect();
      const margin = 8;
      const centerX = rect.left + rect.width / 2;
      let verticalTransform = "translate(-50%, -100%)";

      floatingBubble.style.left = `${centerX}px`;
      floatingBubble.style.top = `${Math.max(margin, rect.top - margin)}px`;
      floatingBubble.style.transform = verticalTransform;

      const bubbleRect = floatingBubble.getBoundingClientRect();
      if (bubbleRect.top < margin) {
        floatingBubble.style.top = `${rect.bottom + margin}px`;
        verticalTransform = "translate(-50%, 0)";
        floatingBubble.style.transform = verticalTransform;
      }

      const adjustedRect = floatingBubble.getBoundingClientRect();
      if (adjustedRect.left < margin) {
        floatingBubble.style.left = `${margin}px`;
        floatingBubble.style.transform =
          verticalTransform === "translate(-50%, 0)" ? "translate(0, 0)" : "translate(0, -100%)";
      } else if (adjustedRect.right > window.innerWidth - margin) {
        floatingBubble.style.left = `${window.innerWidth - margin}px`;
        floatingBubble.style.transform =
          verticalTransform === "translate(-50%, 0)" ? "translate(-100%, 0)" : "translate(-100%, -100%)";
      }
    };

    const hideBubble = () => {
      activeDot = null;
      floatingBubble.classList.remove("is-visible");
      floatingBubble.innerHTML = "";
    };

    const showBubble = (dot) => {
      const sourceBubble = dot.querySelector(".info-bubble");
      if (!sourceBubble) {
        return;
      }
      activeDot = dot;
      floatingBubble.innerHTML = sourceBubble.innerHTML;
      floatingBubble.classList.add("is-visible");
      positionBubble();
    };

    infoDots.forEach((dot) => {
      if (dot.dataset.infoBubbleInitialized === "1") {
        return;
      }
      dot.dataset.infoBubbleInitialized = "1";
      dot.addEventListener("mouseenter", () => showBubble(dot));
      dot.addEventListener("mouseleave", () => {
        if (activeDot === dot && !dot.matches(":focus")) {
          hideBubble();
        }
      });
      dot.addEventListener("focus", () => showBubble(dot));
      dot.addEventListener("blur", () => {
        if (activeDot === dot) {
          hideBubble();
        }
      });
      dot.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          hideBubble();
          dot.blur();
        }
      });
    });

    window.addEventListener("resize", positionBubble, { passive: true });
    window.addEventListener(
      "scroll",
      () => {
        if (!activeDot) {
          return;
        }
        positionBubble();
      },
      { passive: true }
    );
  };

  initFloatingInfoBubbles();

  const renderMath = (element) => {
    if (!element || element.dataset.katexRendered === "1" || typeof window.renderMathInElement !== "function") {
      return;
    }
    window.renderMathInElement(element, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "$", right: "$", display: false },
        { left: "\\(", right: "\\)", display: false },
        { left: "\\[", right: "\\]", display: true },
      ],
      throwOnError: false,
    });
    element.dataset.katexRendered = "1";
  };

  const formulaSource = (value) => {
    let latex = value.replaceAll("\\", "\\\\").replaceAll("{", "\\{").replaceAll("}", "\\}");
    latex = latex.replaceAll("%", "\\%").replaceAll("&", "\\&").replaceAll("#", "\\#");
    latex = latex.replaceAll("$", "\\$").replaceAll("_", "\\_");
    latex = latex.replaceAll("·", "\\cdot ").replaceAll("⋅", "\\cdot ");
    return `$\\mathrm{${latex.replace(/(?<=[A-Za-z)\]])(\d+(?:\.\d+)?)/g, "_{$1}")}}$`;
  };

  document.addEventListener("httk-serve:optimade-table-updated", (event) => {
    const table = event.target;
    if (!(table instanceof Element)) return;
    const rows = Array.from(table.querySelectorAll("tbody tr"));
    rows.forEach((row) => {
      const cells = row.querySelectorAll("td");
      if (cells.length !== 9) return;
      // The widget makes the material cell a detail link; write into the anchor when present so
      // formula beautification (and the KaTeX pass) does not overwrite and drop the navigation link.
      const formulaTarget = cells[0].querySelector("a") ?? cells[0];
      const formula = formulaTarget.textContent || "";
      if (formula && formula !== "—") formulaTarget.textContent = formulaSource(formula);
      const labels = { collinear: "Collinear", "noncollinear-derived": "Based on noncollinear", mixed: "Both", unclassified: "Not classified yet" };
      const classification = cells[2].textContent || "";
      if (labels[classification]) cells[2].textContent = labels[classification];
      const abundance = Number.parseFloat(cells[8].textContent || "");
      if (Number.isFinite(abundance)) {
        cells[8].textContent = abundance >= 1000 ? `${abundance.toLocaleString("en-US", { maximumFractionDigits: 0 })} ppm`
          : abundance >= 1 ? `${abundance.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} ppm`
          : `${abundance.toFixed(3)} ppm`;
      }
    });
    const tbody = table.querySelector("tbody");
    if (tbody) {
      delete tbody.dataset.katexRendered;
      renderMath(tbody);
    }
  });

  renderMath(document.body);

  window.altermagnetsUi = {
    initSubtree(subtree) {
      if (!(subtree instanceof Element)) {
        return;
      }
      renderMath(subtree);
      applyTheme(root.getAttribute("data-theme"));
      initFloatingInfoBubbles(subtree);
    },
  };

  window.toggleBlock = (windowId, buttonId) => {
    const panel = document.getElementById(windowId);
    const button = document.getElementById(buttonId);
    if (!panel || !button) {
      return;
    }

    const marker = button.querySelector("b");
    const isOpen = panel.style.display !== "none";
    if (isOpen) {
      panel.style.display = "none";
      button.classList.remove("active");
      if (marker) {
        marker.textContent = "+";
      }
      return;
    }

    panel.style.display = "block";
    button.classList.add("active");
    if (marker) {
      marker.textContent = "-";
    }
  };
})();
