# Customising the UI

Two files in the config folder are loaded after the page's own stylesheet and script, when
they exist: `/config/custom.css` and `/config/custom.js`. Their presence is checked on every
page load and they are served uncached, so adding or editing one shows on the next reload;
no restart, no rebuild. Delete the file to go back.

## Colours

The theme is a handful of CSS variables on `:root`, with a second set for the dark theme:

| Variable | Used for |
|---|---|
| `--bg` | page background |
| `--card` | card background, buttons |
| `--border` | card and table borders |
| `--text` | body text |
| `--muted` | secondary text, headings, paths |
| `--accent` | Run button, links, the running state, selected rows |
| `--ok` | completed runs, reachable, healthy pools |
| `--warn` | the `--delete` toggle, completed with errors, warnings |
| `--danger` | Stop, failed, unreachable |
| `--log-bg`, `--log-text` | the log panel |

A warmer dark theme, for example:

```css
:root[data-theme="dark"] {
  --bg: #14110f; --card: #1d1916; --border: #3a322c;
  --accent: #f0a35e; --log-bg: #0b0908;
}
```

The theme itself is `ui.dark_theme` in the configuration; `html` carries
`data-theme="dark"` or `"light"`, so rules can target one of them.

## Layout

The Status, TrueNAS, Runs and Folders cards have ids: `status-card`, `truenas-card`,
`runs-card`, `folders-card`. The Remote card and the log's card have none (target them with
`.cards > .card:nth-child(2)` and `main > section.card`), and the log panel itself is
`#log`. Hide the TrueNAS card and give the log more room:

```css
#truenas-card { display: none; }
.log { max-height: 70vh; }
```

Phone rules in the page live under `@media (max-width: 640px)`; the same breakpoint works
for your own.

## Scripts

`custom.js` runs after the page's script, as a classic script in the same page, so it can
use what the page defines: `$("id")` for `document.getElementById`, `api(path, options)`
for a JSON call with the right headers, `toast(message)` for the notice at the top. These
are not a stable API; they are what the page happens to have, and a release may change them.

A link in the header to the NAS, and a notice when the page loads:

```js
const link = document.createElement("a");
link.href = "https://nas.lan";
link.textContent = "NAS";
link.className = "muted small";
document.querySelector("header h1").append(" · ", link);
toast("Hello from custom.js");
```

The page polls the status every 5 seconds, the runs every 15 and TrueNAS every 60, so a
script that reads the DOM after those updates should do the same with `setInterval`.

## Dates, clock and language

`UI_LOCALE` (a BCP 47 tag such as `es-ES`) and `UI_CLOCK` (`24h` or `12h`) set the date
format, without touching the files. The page's own words are English only.
