(() => {
  "use strict";

  const OWNER = "amuejunankar";
  const REPO = "Youtube-Downloader";
  const TAG = "1.3.6";

  const repoUrl = `https://github.com/${OWNER}/${REPO}`;
  // Windows builds are rebuilt by the "Build Windows app and installer"
  // workflow, which publishes them to the newest release, so link to it.
  const latestBase = `${repoUrl}/releases/latest/download`;

  const ASSETS = {
    "YouTubeDownloader-Installer.exe": `${latestBase}/YouTubeDownloader-Installer.exe`,
    "YouTubeDownloader_Portable.exe": `${latestBase}/YouTubeDownloader_Portable.exe`,
    "YouTubeDownloader-Android.apk": `${latestBase}/YouTubeDownloader-Android.apk`,
  };

  /* ---------- Download links ---------- */

  document.querySelectorAll("[data-asset]").forEach((link) => {
    const asset = link.getAttribute("data-asset");
    link.href = ASSETS[asset] || `${repoUrl}/releases`;
    link.setAttribute("rel", "noopener");
  });

  const fallbackLabels = new Map();
  document.querySelectorAll("[data-size-for]").forEach((el) => {
    fallbackLabels.set(el, el.textContent.trim());
  });

  const human = (bytes) => {
    if (!Number.isFinite(bytes) || bytes <= 0) return "";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let i = 0;
    while (value >= 1024 && i < units.length - 1) {
      value /= 1024;
      i += 1;
    }
    const digits = value >= 100 ? 0 : value >= 10 ? 1 : 1;
    return `${value.toFixed(digits)} ${units[i]}`;
  };

  const versionEl = document.getElementById("version");
  if (versionEl) versionEl.textContent = TAG;
  document.querySelectorAll("[data-tag]").forEach((el) => {
    el.textContent = TAG;
  });

  /* ---------- Release metadata ---------- */

  fetch(`https://api.github.com/repos/${OWNER}/${REPO}/releases/latest`, {
    headers: { Accept: "application/vnd.github+json" },
  })
    .then((res) => (res.ok ? res.json() : null))
    .then((release) => {
      if (!release) return;

      const tag = String(release.tag_name || TAG).replace(/^v/i, "");
      if (versionEl) versionEl.textContent = tag;
      document.querySelectorAll("[data-tag]").forEach((el) => {
        el.textContent = tag;
      });

      (release.assets || []).forEach((asset) => {
        // Point the buttons at this release's own asset URL, so the link a
        // visitor copies names the version it downloads. The "latest" hrefs in
        // the markup stay as the no-JS / offline fallback.
        if (asset.browser_download_url) {
          document
            .querySelectorAll(`[data-asset="${CSS.escape(asset.name)}"]`)
            .forEach((link) => {
              link.href = asset.browser_download_url;
            });
        }

        const size = human(asset.size);
        if (!size) return;
        document
          .querySelectorAll(`[data-size-for="${CSS.escape(asset.name)}"]`)
          .forEach((el) => {
            const fallback = fallbackLabels.get(el) || "";
            el.textContent = fallback ? `${size} · ${fallback}` : size;
          });
      });
    })
    .catch(() => {
      /* offline or rate-limited: keep the static labels */
    });

  /* ---------- Theme ---------- */

  const root = document.documentElement;
  const toggle = document.getElementById("theme-toggle");
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  const media = window.matchMedia("(prefers-color-scheme: light)");

  const applyTheme = (theme, persist) => {
    root.dataset.theme = theme;
    if (themeMeta) themeMeta.content = theme === "light" ? "#f4f7ff" : "#070b14";
    if (toggle) {
      toggle.setAttribute(
        "aria-label",
        theme === "dark" ? "Switch to light theme" : "Switch to dark theme"
      );
    }
    if (persist) {
      try {
        localStorage.setItem("ytdl-theme", theme);
      } catch (e) {
        /* storage disabled */
      }
    }
  };

  let savedTheme = null;
  try {
    savedTheme = localStorage.getItem("ytdl-theme");
  } catch (e) {
    /* storage disabled */
  }

  applyTheme(savedTheme || (media.matches ? "light" : "dark"), false);

  if (toggle) {
    toggle.addEventListener("click", () => {
      applyTheme(root.dataset.theme === "dark" ? "light" : "dark", true);
    });
  }

  /* ---------- Header state ---------- */

  const header = document.getElementById("header");
  const onScroll = () => {
    if (!header) return;
    header.classList.toggle("scrolled", window.scrollY > 8);
  };
  onScroll();
  window.addEventListener("scroll", onScroll, { passive: true });

  /* ---------- Reveal on scroll ---------- */

  const revealItems = document.querySelectorAll(".reveal");

  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("in");
          observer.unobserve(entry.target);
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -36px 0px" }
    );
    revealItems.forEach((el) => observer.observe(el));
  } else {
    revealItems.forEach((el) => el.classList.add("in"));
  }

  /* ---------- Mobile menu ---------- */

  const menuToggle = document.getElementById("menu-toggle");
  const mobileNav = document.getElementById("mobile-nav");

  const setMenu = (open) => {
    if (!menuToggle || !mobileNav) return;
    mobileNav.classList.toggle("open", open);
    menuToggle.setAttribute("aria-expanded", open ? "true" : "false");
    menuToggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  };

  if (menuToggle && mobileNav) {
    menuToggle.addEventListener("click", () => {
      setMenu(menuToggle.getAttribute("aria-expanded") !== "true");
    });
    mobileNav.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => setMenu(false));
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") setMenu(false);
    });
    window.addEventListener("resize", () => {
      if (window.innerWidth > 900) setMenu(false);
    });
  }

  /* ---------- Footer year ---------- */

  const year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());
})();
