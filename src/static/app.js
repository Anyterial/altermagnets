(function () {
  const THEME_KEY = "anyterial_theme";
  const DEFAULT_THEME = "twilight";
  const THEME_OPTIONS = new Set(["dark", "twilight", "light"]);

  const root = document.documentElement;
  const themeButtons = Array.from(document.querySelectorAll("[data-theme-option]"));
  const resultsTable = document.querySelector(".results-table tbody");
  const sidebar = document.querySelector(".sidebar");
  const themeAwareFigures = Array.from(document.querySelectorAll("img.theme-aware-figure"));

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
    themeAwareFigures.forEach((image) => {
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

  const buildRowUrl = (rowUrl) => {
    if (!rowUrl) {
      return "";
    }

    const currentParams = new URLSearchParams(window.location.search);
    currentParams.delete("id");
    if (!currentParams.toString()) {
      return rowUrl;
    }

    const separator = rowUrl.includes("?") ? "&" : "?";
    return `${rowUrl}${separator}${currentParams.toString()}`;
  };

  if (resultsTable) {
    resultsTable.addEventListener("click", (event) => {
      const row = event.target.closest("tr[data-row-url]");
      if (!row) {
        return;
      }
      const rowUrl = row.getAttribute("data-row-url");
      if (rowUrl) {
        window.location.assign(buildRowUrl(rowUrl));
      }
    });

    resultsTable.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") {
        return;
      }
      const row = event.target.closest("tr[data-row-url]");
      if (!row) {
        return;
      }
      event.preventDefault();
      const rowUrl = row.getAttribute("data-row-url");
      if (rowUrl) {
        window.location.assign(buildRowUrl(rowUrl));
      }
    });
  }

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

  const initFloatingInfoBubbles = () => {
    const infoDots = Array.from(document.querySelectorAll(".info-dot"));
    if (infoDots.length === 0) {
      return;
    }

    root.classList.add("js-fixed-tooltips");
    const floatingBubble = document.createElement("div");
    floatingBubble.className = "floating-info-bubble";
    floatingBubble.setAttribute("aria-hidden", "true");
    document.body.appendChild(floatingBubble);

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

  if (typeof window.renderMathInElement === "function") {
    window.renderMathInElement(document.body, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "$", right: "$", display: false },
        { left: "\\(", right: "\\)", display: false },
        { left: "\\[", right: "\\]", display: true },
      ],
      throwOnError: false,
    });
  }

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
