/* Capy & Co. — progressive enhancement only.
   Every section is fully readable with JS disabled. */
(function () {
  "use strict";

  /* ---------- Theme ---------- */
  var root   = document.documentElement;
  var toggle = document.getElementById("themeToggle");

  function readStoredTheme() {
    try { return localStorage.getItem("capy-theme"); } catch (e) { return null; }
  }
  function storeTheme(value) {
    try { localStorage.setItem("capy-theme", value); } catch (e) { /* private mode */ }
  }
  function applyTheme(theme) {
    root.setAttribute("data-theme", theme);
    var dark = theme === "dark";
    toggle.setAttribute("aria-pressed", String(dark));
    toggle.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
  }

  var stored = readStoredTheme();
  var prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
  applyTheme(stored || (prefersDark.matches ? "dark" : "light"));

  toggle.addEventListener("click", function () {
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
    storeTheme(next);
  });

  // Follow the OS only while the visitor hasn't chosen for themselves.
  prefersDark.addEventListener("change", function (e) {
    if (!readStoredTheme()) applyTheme(e.matches ? "dark" : "light");
  });

  /* ---------- Mobile nav ---------- */
  var nav       = document.getElementById("nav");
  var navToggle = document.getElementById("menuToggle");

  function setNav(open) {
    nav.classList.toggle("is-open", open);
    document.body.classList.toggle("nav-open", open);
    navToggle.setAttribute("aria-expanded", String(open));
    navToggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  }

  navToggle.addEventListener("click", function () {
    setNav(navToggle.getAttribute("aria-expanded") !== "true");
  });

  nav.addEventListener("click", function (e) {
    if (e.target.closest("a")) setNav(false);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && nav.classList.contains("is-open")) {
      setNav(false);
      navToggle.focus();
    }
  });

  // Reset the panel state if the viewport grows past the mobile breakpoint.
  window.matchMedia("(min-width: 761px)").addEventListener("change", function (e) {
    if (e.matches) setNav(false);
  });

  /* ---------- Sticky header shadow ---------- */
  var header = document.getElementById("siteHeader");
  var onScroll = function () {
    header.classList.toggle("is-stuck", window.scrollY > 8);
  };
  onScroll();
  window.addEventListener("scroll", onScroll, { passive: true });

  /* ---------- Menu tabs ---------- */
  var tabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"]'));

  function selectTab(tab, moveFocus) {
    tabs.forEach(function (t) {
      var active = t === tab;
      t.classList.toggle("is-active", active);
      t.setAttribute("aria-selected", String(active));
      t.tabIndex = active ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !active;
    });
    document.getElementById(tab.getAttribute("aria-controls")).classList.add("is-active");
    if (moveFocus) tab.focus();
  }

  tabs.forEach(function (tab, i) {
    tab.addEventListener("click", function () { selectTab(tab, false); });
    tab.addEventListener("keydown", function (e) {
      var delta = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (delta) {
        e.preventDefault();
        selectTab(tabs[(i + delta + tabs.length) % tabs.length], true);
      } else if (e.key === "Home" || e.key === "End") {
        e.preventDefault();
        selectTab(e.key === "Home" ? tabs[0] : tabs[tabs.length - 1], true);
      }
    });
  });

  /* ---------- Reveal on scroll ---------- */
  var items = document.querySelectorAll(".reveal");
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reduced || !("IntersectionObserver" in window)) {
    items.forEach(function (el) { el.classList.add("is-visible"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        io.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -10% 0px", threshold: 0.08 });

    items.forEach(function (el, i) {
      // Stagger siblings so rows of cards cascade rather than pop together.
      el.style.transitionDelay = (i % 4) * 70 + "ms";
      io.observe(el);
    });
  }
})();
