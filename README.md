# Capy & Co. Coffee

A one-page marketing site for a fictional riverside coffee bar staffed entirely by capybaras.

## Running it

There is no build step. Open `index.html` directly, or serve the folder:

```sh
python3 -m http.server 8000
# then visit http://localhost:8000
```

## What's here

| File | Purpose |
| --- | --- |
| `index.html` | Every section: hero, menu, baristas, story, visit. Includes one inline `<script>` that sets the theme before first paint. |
| `styles.css` | All styling. Design tokens live in `:root`; the dark theme overrides the same tokens under `:root[data-theme="dark"]`. |
| `main.js` | Progressive enhancement only — theme toggle, mobile nav, menu tabs, scroll reveals. |
| `favicon.svg` | The capybara mark. |

All illustrations are inline SVG, so there are no image assets and nothing to optimise.

## Design notes

- **Theming.** Colours are CSS custom properties. To reskin, change the token block at the top of `styles.css` — nothing downstream hardcodes a colour.
- **Capybara artwork.** Defined once as an SVG `<symbol id="capyFace">` near the top of `index.html` and drawn with `<use>`. The fur colours are custom properties, so each barista card recolours the same drawing with an inline `style`.
- **No-JS.** The site is fully readable without JavaScript. Scroll-reveal only hides content once the inline script has added a `js` class to `<html>`, and menu tab panels degrade to the first panel visible.
- **Motion.** Every animation is disabled under `prefers-reduced-motion: reduce`.
- **Accessibility.** Skip link, semantic landmarks, ARIA tabs with arrow-key/Home/End navigation, visible focus rings, and a hours `<table>` with a caption.

## Deploying

It is static, so any host works. For GitHub Pages: push this branch and point Pages at the branch root.

## Note

Capy & Co. is not a real business. The address, phone number, and email are fictional.
