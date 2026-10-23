// GitHub Inbox panel. A web component with no build step: it subscribes to
// github_inbox/subscribe and redraws from each snapshot. Bodies arrive already
// sanitised by the integration; everything else is escaped here.

const STYLE = `
:host{display:block;height:100%;overflow-y:auto;-webkit-overflow-scrolling:touch}
.wrap{--bg:#111418;--card:#181c21;--line:#2c3138;--track:#232830;--text:#e3e6ea;--muted:#9aa3ad;--faint:#5f6b78;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--good:#0ca30c;--warn:#fab219;--crit:#d03b3b;
--code:#0d1014;--hover:#1f242b;color-scheme:dark;background:var(--bg);color:var(--text);min-height:100%;box-sizing:border-box;
padding:16px;font:14px/1.45 var(--paper-font-body1_-_font-family,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif)}
.wrap[data-theme=light]{--bg:#f4f5f7;--card:#fff;--line:#d9dde3;--track:#eceff3;--text:#1b1f24;--muted:#57606a;--faint:#8c959f;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;--code:#f6f8fa;--hover:#f0f3f6;color-scheme:light}
*{box-sizing:border-box}main{max-width:80rem;margin:0 auto}
button{font:inherit;color:var(--text);background:var(--track);border:1px solid var(--line);border-radius:8px;padding:5px 12px;cursor:pointer}
button:hover{border-color:var(--faint)}button:focus-visible,[tabindex]:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--s1);outline-offset:2px}
select,input{font:inherit;color:var(--text);background:var(--track);border:1px solid var(--line);border-radius:8px;padding:5px 10px}
input[type=search]{flex:1;min-width:10rem}
.top{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}.top h1{font-size:1.45rem;margin:0;font-weight:600}
.brand{display:flex;align-items:center;gap:10px}.status{display:flex;gap:8px;flex-wrap:wrap;color:var(--muted);font-size:.85rem}
.actions{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(10.5rem,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;display:flex;gap:12px;align-items:center;min-width:0}
.tile .k{color:var(--muted);font-size:.8rem}.tile .v{font-size:1.5rem;font-weight:600;line-height:1.15}.tile .sub{color:var(--muted);font-size:.78rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .txt{min-width:0;flex:1}.tile .ico{flex:none;width:36px;height:36px;border-radius:10px;display:grid;place-items:center;background:var(--track)}
.tile[data-act]{cursor:pointer}.tile[data-act]:hover{border-color:var(--faint);background:var(--hover)}.tile[aria-pressed=true]{border-color:var(--s1);box-shadow:inset 0 0 0 1px var(--s1)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(21rem,1fr));gap:12px;margin-top:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;min-width:0}.card.wide{grid-column:1/-1;margin-top:12px}
.card h2{font-size:1rem;margin:0 0 10px;font-weight:600;display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.card h2 small{color:var(--muted);font-weight:400;font-size:.8rem}
.muted{color:var(--muted)}.small{font-size:.82rem}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;flex:none;background:var(--faint)}
.chip{display:inline-flex;align-items:center;gap:5px;padding:1px 8px;border-radius:10px;background:var(--track);font-size:.78rem;white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:8px;font-size:.8rem;color:var(--muted)}.legend span{display:inline-flex;align-items:center;gap:6px}.legend b{color:var(--text);font-weight:600}
.sw{display:inline-block;width:10px;height:10px;border-radius:3px}
.hatch{background:repeating-linear-gradient(135deg,var(--s2) 0 2px,transparent 2px 5px)!important;box-shadow:inset 0 0 0 1px var(--s2)}
.bars{display:grid;gap:7px}.brow{display:grid;grid-template-columns:minmax(6rem,11rem) 1fr 2rem;gap:10px;align-items:center;font-size:.84rem;cursor:pointer;border-radius:6px;padding:1px 4px;margin:0 -4px}
.brow:hover{background:var(--hover)}.brow.on{background:var(--hover);box-shadow:inset 3px 0 0 var(--s1)}
.brow .n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.brow .t{text-align:right;color:var(--muted)}
.track{height:12px}.stack{display:flex;gap:2px;height:100%;border-radius:4px;overflow:hidden}.stack>i{display:block;height:100%;min-width:3px}
.cols{display:flex;align-items:flex-end;gap:6px;height:140px;padding-top:16px;border-bottom:1px solid var(--line)}
.col{flex:1;display:flex;flex-direction:column-reverse;gap:2px;height:100%;position:relative;min-width:0}
.col>i{display:block;width:100%;min-height:2px}.col>i:last-of-type{border-radius:4px 4px 0 0}
.col .tot{position:absolute;left:0;right:0;text-align:center;font-size:.72rem;color:var(--muted)}
.xl{display:flex;gap:6px;margin-top:4px}.xl span{flex:1;text-align:center;font-size:.72rem;color:var(--muted);white-space:nowrap;overflow:hidden}
.cols.days{gap:3px}.xl.days{gap:3px}
.split{display:flex;gap:16px;align-items:center}.ring{flex:none}
.wl{display:grid;grid-template-columns:minmax(0,1fr);gap:6px;flex:1;min-width:0}.wl a{color:var(--text);text-decoration:none;display:flex;gap:8px;align-items:center;font-size:.84rem;cursor:pointer;min-width:0}
.wl a:hover .tt{text-decoration:underline}.wl .tt{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
.seg{display:inline-flex;background:var(--track);border:1px solid var(--line);border-radius:9px;padding:2px;gap:2px}
.seg button{border:0;background:transparent;border-radius:7px;padding:4px 10px;color:var(--muted)}.seg button.on{background:var(--card);color:var(--text);box-shadow:0 0 0 1px var(--line)}
.seg b{font-weight:600;margin-left:4px;color:var(--text)}
.item{border-top:1px solid var(--line)}.item:first-child{border-top:0}
.row{display:grid;grid-template-columns:14px 18px minmax(0,1fr) auto;gap:10px;align-items:start;padding:10px 6px;cursor:pointer;border-radius:8px}
.row:hover{background:var(--hover)}.row .gl{padding-top:2px}.caret{transition:transform .15s;padding-top:3px;color:var(--faint)}.item.open .caret{transform:rotate(90deg)}
.title{font-weight:600;overflow-wrap:anywhere}.title .num{color:var(--muted);font-weight:400}
.meta{color:var(--muted);font-size:.8rem;display:flex;flex-wrap:wrap;gap:4px 10px;margin-top:2px;align-items:center}
.labels{display:flex;flex-wrap:wrap;gap:4px;margin-top:5px}
.side{display:flex;gap:10px;align-items:center;color:var(--muted);font-size:.8rem;white-space:nowrap}.side .cm{display:inline-flex;gap:4px;align-items:center}
.new{color:var(--text)}
.av{display:inline-grid;place-items:center;width:18px;height:18px;border-radius:50%;background:var(--track);color:var(--muted);font-size:.62rem;font-weight:700;flex:none;border:1px solid var(--line)}
.av.bot{border-radius:5px}.av.me{border-color:var(--s1);color:var(--text)}
.detail{padding:0 6px 12px 48px}.ldg{color:var(--muted);font-size:.85rem;padding:6px 0}
.box{border:1px solid var(--line);border-radius:10px;margin-top:8px;overflow:hidden}
.ehead{display:flex;gap:8px;align-items:center;padding:7px 10px;cursor:pointer;font-size:.84rem;background:var(--track);min-width:0}
.ehead:hover{filter:brightness(1.08)}.ehead .who{font-weight:600;white-space:nowrap}.ehead .what{color:var(--muted);white-space:nowrap}
.ehead .pv{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0}.ehead .when{color:var(--muted);white-space:nowrap;margin-left:auto}
.entry.open .pv{visibility:hidden}.ebody{display:none;padding:2px 14px 10px}.entry.open .ebody,.desc .ebody{display:block}
.dactions{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}.dactions a{color:var(--s1)}
.md{overflow-wrap:anywhere;line-height:1.55}.md a{color:var(--s1)}.md pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:10px;overflow-x:auto;font-size:.82rem}
.md code{background:var(--code);border-radius:4px;padding:1px 4px;font-size:.86em}.md pre code{background:none;padding:0}
.md blockquote{margin:6px 0;padding:0 12px;border-left:3px solid var(--line);color:var(--muted)}
.md table{border-collapse:collapse;display:block;overflow-x:auto}.md td,.md th{border:1px solid var(--line);padding:4px 8px}
.md h1,.md h2,.md h3,.md h4{font-size:1rem;margin:10px 0 4px}.md p{margin:6px 0}.md ul,.md ol{padding-left:22px;margin:6px 0}
.md hr{border:0;border-top:1px solid var(--line)}.md details{margin:6px 0}.md summary{cursor:pointer;color:var(--muted)}
.path{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.78rem;color:var(--muted)}
.feed{display:grid;gap:2px}.fe{display:grid;grid-template-columns:18px minmax(0,1fr) auto;gap:10px;align-items:center;font-size:.84rem;padding:5px 4px;border-radius:6px;cursor:pointer}
.fe:hover{background:var(--hover)}.fe .tx{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.fe .tx b{font-weight:600}
.empty{display:grid;place-items:center;text-align:center;padding:36px 12px;color:var(--muted);gap:10px}
.tip{position:fixed;z-index:10;pointer-events:none;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:6px 9px;font-size:.8rem;
box-shadow:0 6px 18px rgba(0,0,0,.35);max-width:22rem;white-space:pre-line}
.err{border-color:var(--crit)}
@media (max-width:640px){.wrap{padding:12px}.detail{padding-left:6px}.row{grid-template-columns:12px 18px minmax(0,1fr)}.side{grid-column:3}.brow{grid-template-columns:minmax(5rem,8rem) 1fr 1.6rem}}
`;

const AGE_LABELS = ["< 1 day", "< 1 week", "< 1 month", "< 3 months", "< 1 year", "older"];
const ACT = [
  ["opened", "Opened", "--s3"],
  ["commented", "Comments", "--s4"],
  ["reviewed", "Reviews", "--s5"],
  ["closed", "Closed or merged", "--s6"],
];
const REVIEW = {
  approved: ["approved", "--good"],
  changes_requested: ["requested changes", "--crit"],
  commented: ["reviewed", "--faint"],
  dismissed: ["review dismissed", "--faint"],
};
const SYNC = {
  ok: ["live", "--good"],
  syncing: ["syncing", "--warn"],
  pending: ["starting", "--warn"],
  failed: ["sync failed", "--crit"],
  lost: ["app uninstalled or suspended", "--crit"],
};
const THEME_KEY = "github-inbox-theme";

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const cv = (name) => `var(${name})`;
const short = (repo) => String(repo).split("/").pop();

function ago(iso, now = Date.now()) {
  const t = Date.parse(iso);
  if (!t) return "never";
  const s = Math.max(0, (now - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 60) return `${Math.floor(s / 86400)} d ago`;
  return `${Math.floor(s / (86400 * 30))} mo ago`;
}
function span(iso, now = Date.now()) {
  const t = Date.parse(iso);
  if (!t) return "-";
  const d = Math.floor((now - t) / 86400000);
  return d < 1 ? "today" : d < 365 ? `${d} d` : `${(d / 365).toFixed(1)} y`;
}
const when = (iso) => {
  const t = Date.parse(iso);
  return t ? new Date(t).toLocaleString() : "";
};

// Glyphs: drawn here, 16 px, colour from the caller.
const svg = (body, color = "currentColor", size = 16) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 16 16" aria-hidden="true" style="color:${color};flex:none;display:block">${body}</svg>`;
const G = {
  issue: (c) => svg(`<circle cx="8" cy="8" r="6.2" style="fill:none;stroke:currentColor;stroke-width:1.6"/><circle cx="8" cy="8" r="1.7" style="fill:currentColor"/>`, c),
  pr: (c, draft = false) =>
    svg(
      `<circle cx="4" cy="3.5" r="1.8" style="fill:none;stroke:currentColor;stroke-width:1.5"/><circle cx="4" cy="12.5" r="1.8" style="fill:none;stroke:currentColor;stroke-width:1.5"/>` +
        `<circle cx="12" cy="12.5" r="1.8" style="fill:none;stroke:currentColor;stroke-width:1.5"/><path d="M4 5.3v5.4" style="fill:none;stroke:currentColor;stroke-width:1.5"/>` +
        `<path d="M12 10.7V6.5a2 2 0 0 0-2-2H7.2" style="fill:none;stroke:currentColor;stroke-width:1.5${draft ? ";stroke-dasharray:1.6 1.6" : ""}"/>` +
        `<path d="M8.6 2.9 7 4.5l1.6 1.6" style="fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round"/>`,
      c,
    ),
  comment: (c) => svg(`<path d="M2.5 3.5h11v7h-6l-3 2.6v-2.6h-2z" style="fill:none;stroke:currentColor;stroke-width:1.4;stroke-linejoin:round"/>`, c),
  review: (c) => svg(`<path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8s-2.4 4.5-6.5 4.5S1.5 8 1.5 8z" style="fill:none;stroke:currentColor;stroke-width:1.4"/><circle cx="8" cy="8" r="2" style="fill:currentColor"/>`, c),
  closed: (c) => svg(`<circle cx="8" cy="8" r="6.2" style="fill:none;stroke:currentColor;stroke-width:1.6"/><path d="M5.3 8.2l1.8 1.8 3.6-3.8" style="fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round"/>`, c),
  merged: (c) => svg(`<circle cx="4" cy="3.5" r="1.8" style="fill:none;stroke:currentColor;stroke-width:1.5"/><circle cx="4" cy="12.5" r="1.8" style="fill:none;stroke:currentColor;stroke-width:1.5"/><circle cx="12" cy="8" r="1.8" style="fill:none;stroke:currentColor;stroke-width:1.5"/><path d="M4 5.3v5.4M4 5.3c0 2.2 2.4 2.7 6.2 2.7" style="fill:none;stroke:currentColor;stroke-width:1.5"/>`, c),
  caret: () => svg(`<path d="M6 4l4 4-4 4" style="fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round"/>`, "currentColor", 12),
  clock: (c) => svg(`<circle cx="8" cy="8" r="6.2" style="fill:none;stroke:currentColor;stroke-width:1.5"/><path d="M8 4.5V8l2.4 1.6" style="fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round"/>`, c),
  pulse: (c) => svg(`<path d="M1.5 8.5h3l1.6-4 3 8 1.8-5h3.6" style="fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round"/>`, c),
  repo: (c) => svg(`<path d="M3.5 2.5h8.5v9.5H5a1.5 1.5 0 0 0-1.5 1.5zM3.5 13.5A1.5 1.5 0 0 1 5 12h7v1.5H5" style="fill:none;stroke:currentColor;stroke-width:1.4;stroke-linejoin:round"/>`, c),
};

// The brand mark: an inbox tray with an issue ring and a pull request dropping in.
const LOGO = `<svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true">
<path d="M4 18 7.5 7.5h17L28 18v7.5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z" style="fill:var(--track);stroke:var(--muted);stroke-width:1.6;stroke-linejoin:round"/>
<path d="M4 18h6.5l1.5 3h8l1.5-3H28" style="fill:none;stroke:var(--muted);stroke-width:1.6;stroke-linejoin:round"/>
<circle cx="12" cy="12.6" r="3.2" style="fill:none;stroke:var(--s1);stroke-width:1.8"/><circle cx="12" cy="12.6" r="1" style="fill:var(--s1)"/>
<path d="M20 9.2v6.4M20 9.2c0-1 .8-1.6 1.8-1.6" style="fill:none;stroke:var(--s2);stroke-width:1.8;stroke-linecap:round"/></svg>`;

function glyph(item) {
  if (item.kind === "pr") return item.draft ? G.pr(cv("--faint"), true) : G.pr(cv("--s2"));
  return G.issue(cv("--s1"));
}
// Whether a login is one of yours for an item's account; GitHub logins are case-insensitive.
function isMe(i) {
  const me = (i.me && i.me.length ? i.me : [i.account || ""]).map((l) => String(l).toLowerCase());
  return (login) => me.includes(String(login || "").toLowerCase());
}
function avatar(login, bot, me) {
  const name = String(login || "?").replace(/\[bot\]$/, "");
  const ini = name.split(/[-_ .]/).filter(Boolean).slice(0, 2).map((p) => p[0]).join("").toUpperCase() || "?";
  return `<span class="av${bot ? " bot" : ""}${me ? " me" : ""}" data-tip="${esc(login)}${bot ? " (bot)" : ""}">${esc(ini.slice(0, 2))}</span>`;
}
function labelChips(labels) {
  return (labels || [])
    .map((l) => `<span class="chip" data-tip="label ${esc(l.name)}"><span class="dot" style="background:#${esc(l.color)}"></span>${esc(l.name)}</span>`)
    .join("");
}
function ring(part, whole, color) {
  const r = 26, w = 8, c = 2 * Math.PI * r;
  const pct = whole > 0 ? part / whole : null;
  const arc = pct === null ? 0 : c * Math.min(1, Math.max(0, pct));
  const text = pct === null ? "-" : `${Math.round(pct * 100)}%`;
  const arcEl = arc === 0 ? "" : `<circle cx="34" cy="34" r="${r}" style="fill:none;stroke:${color};stroke-width:${w};stroke-linecap:round" stroke-dasharray="${arc.toFixed(2)} ${c.toFixed(2)}" transform="rotate(-90 34 34)"/>`;
  return `<svg class="ring" width="68" height="68" viewBox="0 0 68 68" role="img" aria-label="${part} of ${whole}">
<circle cx="34" cy="34" r="${r}" style="fill:none;stroke:var(--track);stroke-width:${w}"/>${arcEl}
<text x="34" y="38.5" text-anchor="middle" style="fill:var(--text);font-size:13px;font-weight:600">${text}</text></svg>`;
}

class GitHubInboxPanel extends HTMLElement {
  constructor() {
    super();
    this._snap = null;
    this._filter = "all";
    this._repo = "";
    this._query = "";
    this._sort = "updated";
    this._open = new Set();
    this._openEntries = new Set();
    this._details = new Map();
    this._loading = new Set();
    this._error = "";
    let theme = "dark";
    try {
      theme = localStorage.getItem(THEME_KEY) || "dark";
    } catch (_e) {
      /* storage blocked: default */
    }
    this._theme = theme === "light" ? "light" : "dark";
    try {
      this._deepLink = new URLSearchParams(window.location.search).get("item");
    } catch (_e) {
      this._deepLink = null;
    }
    this.attachShadow({ mode: "open" });
    this._skeleton();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._menu) this._menu.hass = hass;
    if (first && this.isConnected) this._subscribe();
  }
  get hass() {
    return this._hass;
  }
  set narrow(value) {
    this._narrow = !!value;
    if (this._menu) {
      this._menu.narrow = this._narrow;
      this._menu.style.display = this._narrow ? "" : "none";
    }
  }
  set panel(_value) {}

  connectedCallback() {
    if (this._hass && !this._unsub) this._subscribe();
    this._ticker = setInterval(() => this._renderStatus(), 30000);
  }
  disconnectedCallback() {
    clearInterval(this._ticker);
    if (this._unsub) {
      this._unsub.then((stop) => stop()).catch(() => {});
      this._unsub = null;
    }
  }

  _subscribe() {
    this._unsub = this._hass.connection.subscribeMessage((msg) => this._onSnapshot(msg.snapshot), {
      type: "github_inbox/subscribe",
    });
    this._unsub.catch((err) => {
      this._unsub = null;
      this._error = err && err.message ? err.message : String(err);
      this._render();
    });
  }

  _skeleton() {
    const root = this.shadowRoot;
    root.innerHTML = `<style>${STYLE}</style>
<div class="wrap" data-theme="${this._theme}"><main>
<header class="top"><span id="menu"></span><div class="brand">${LOGO}<h1>GitHub Inbox</h1></div>
<div class="status" id="status"></div>
<div class="actions"><button data-act="sync" data-tip="Read every open item from GitHub again">Sync now</button>
<button data-act="seen-all">Mark all seen</button><button data-act="theme" id="theme"></button></div></header>
<section class="tiles" id="tiles"></section>
<section class="grid" id="charts"></section>
<section class="card wide" id="listcard"><h2><span>Open issues and pull requests</span><small id="count"></small></h2>
<div class="toolbar"><div class="seg" id="seg"></div>
<select id="repo" aria-label="Repository"></select>
<input id="q" type="search" placeholder="Filter by title, number, author or label" aria-label="Filter">
<select id="sort" aria-label="Sort"><option value="updated">Recently updated</option><option value="created">Newest</option><option value="oldest">Oldest</option><option value="repo">Repository</option></select></div>
<div id="list"></div></section>
<section class="card wide" id="recent"></section>
</main><div class="tip" id="tip" hidden></div></div>`;
    this._wrap = root.querySelector(".wrap");
    this._tip = root.getElementById("tip");
    const menu = document.createElement("ha-menu-button");
    menu.style.display = "none";
    root.getElementById("menu").appendChild(menu);
    this._menu = menu;
    root.getElementById("q").addEventListener("input", (e) => {
      this._query = e.target.value.trim().toLowerCase();
      this._renderList();
    });
    root.getElementById("repo").addEventListener("change", (e) => {
      this._repo = e.target.value;
      this._renderCharts();
      this._renderList();
    });
    root.getElementById("sort").addEventListener("change", (e) => {
      this._sort = e.target.value;
      this._renderList();
    });
    root.addEventListener("click", (e) => this._onClick(e));
    root.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-act][tabindex]")) {
        e.preventDefault();
        this._onClick(e);
      }
    });
    root.addEventListener("pointerover", (e) => this._showTip(e));
    root.addEventListener("pointermove", (e) => this._moveTip(e));
    root.addEventListener("pointerout", (e) => {
      if (e.target.closest && e.target.closest("[data-tip]")) this._tip.hidden = true;
    });
    this._renderTheme();
  }

  _showTip(e) {
    const el = e.target.closest && e.target.closest("[data-tip]");
    if (!el) return;
    this._tip.textContent = el.getAttribute("data-tip");
    this._tip.hidden = false;
    this._moveTip(e);
  }
  _moveTip(e) {
    if (this._tip.hidden) return;
    const pad = 14, w = this._tip.offsetWidth, h = this._tip.offsetHeight;
    let x = e.clientX + pad, y = e.clientY + pad;
    if (x + w > window.innerWidth - 8) x = e.clientX - w - pad;
    if (y + h > window.innerHeight - 8) y = e.clientY - h - pad;
    this._tip.style.left = `${Math.max(8, x)}px`;
    this._tip.style.top = `${Math.max(8, y)}px`;
  }

  _onSnapshot(snap) {
    const before = new Map((this._snap ? this._snap.items : []).map((i) => [i.key, i.rev]));
    this._snap = snap;
    this._error = "";
    for (const key of [...this._open]) {
      const item = snap.items.find((i) => i.key === key);
      if (!item) {
        this._open.delete(key);
        this._details.delete(key);
      } else if (before.get(key) !== item.rev) {
        this._fetch(key, false);
      }
    }
    if (this._deepLink && snap.items.some((i) => i.key === this._deepLink)) {
      const key = this._deepLink;
      this._deepLink = null;
      this._toggle(key, true);
      requestAnimationFrame(() => {
        const el = this.shadowRoot.querySelector(`.item[data-key="${CSS.escape(key)}"]`);
        if (el) el.scrollIntoView({ block: "start", behavior: "smooth" });
      });
    }
    this._render();
  }

  _render() {
    this._renderStatus();
    this._renderTiles();
    this._renderCharts();
    this._renderToolbar();
    this._renderList();
    this._renderRecent();
  }

  _renderTheme() {
    this._wrap.setAttribute("data-theme", this._theme);
    this.shadowRoot.getElementById("theme").textContent = this._theme === "dark" ? "Light" : "Dark";
  }

  _renderStatus() {
    const el = this.shadowRoot.getElementById("status");
    if (!el) return;
    if (this._error) {
      el.innerHTML = `<span class="chip"><span class="dot" style="background:var(--crit)"></span>${esc(this._error)}</span>`;
      return;
    }
    const snap = this._snap;
    if (!snap) {
      el.innerHTML = `<span class="chip"><span class="dot" style="background:var(--warn)"></span>connecting</span>`;
      return;
    }
    el.innerHTML = snap.sync
      .map((s) => {
        const [word, color] = SYNC[s.state] || [s.state, "--faint"];
        const tip = [
          `${s.account}: ${word}`,
          s.error ? `error: ${s.error}` : "",
          `last delivery: ${s.last_delivery ? when(s.last_delivery) : "none yet"}`,
          `last sync: ${s.last_sync ? when(s.last_sync) : "none yet"}`,
        ].filter(Boolean).join("\n");
        return `<span class="chip" data-tip="${esc(tip)}"><span class="dot" style="background:var(${color})"></span>${esc(s.account)} ${esc(word)}</span>
<span class="chip" data-tip="${esc(tip)}">${G.pulse("var(--muted)")}delivery ${esc(ago(s.last_delivery))}</span>`;
      })
      .join("");
  }

  _renderTiles() {
    const el = this.shadowRoot.getElementById("tiles");
    const snap = this._snap;
    if (!snap) {
      el.innerHTML = "";
      return;
    }
    const t = snap.totals;
    const open = t.issues + t.prs;
    const days = snap.days.slice(-14);
    const max = Math.max(1, ...days.map((d) => d.opened + d.commented + d.reviewed + d.closed));
    const spark = `<div style="display:flex;align-items:flex-end;gap:2px;height:26px;margin-top:3px">${days
      .map((d) => {
        const n = d.opened + d.commented + d.reviewed + d.closed;
        return `<i data-tip="${esc(d.date)}: ${n}" style="flex:1;height:${Math.max(2, (26 * n) / max)}px;background:${n ? "var(--s4)" : "var(--track)"};border-radius:2px 2px 0 0"></i>`;
      })
      .join("")}</div>`;
    const oldest = snap.oldest;
    const tile = (icon, k, v, sub, extra = "", act = "") =>
      `<div class="tile"${act}><div class="ico">${icon}</div><div class="txt"><div class="k">${k}</div><div class="v">${v}</div>${sub ? `<div class="sub">${sub}</div>` : ""}${extra}</div></div>`;
    const btn = (act, tip, more = "") => ` data-act="${act}" tabindex="0" role="button" data-tip="${esc(tip)}"${more}`;
    const show = (filter, what) => btn("show", `Show ${what} in the list`, ` data-filter="${filter}" aria-pressed="${this._filter === filter}"`);
    el.innerHTML = [
      tile(G.issue(cv("--s1")), "Open issues", t.issues, `${esc(snap.by_repo.filter((r) => r.issues).length)} repositories`, "", show("issue", "open issues")),
      tile(G.pr(cv("--s2")), "Open pull requests", t.prs, t.drafts ? `${t.drafts} draft${t.drafts === 1 ? "" : "s"}` : "no drafts", "", show("pr", "open pull requests")),
      tile(`<span class="dot" style="width:12px;height:12px;background:var(--warn)"></span>`, "Unseen", t.unseen, `of ${open} open`, "", show("unseen", "unseen items")),
      tile(G.comment(cv("--s4")), "Waiting on you", t.waiting, "last word from someone else", "", show("waiting", "items waiting on you")),
      tile(G.repo("var(--muted)"), "Repositories", t.repos, `with open items, of ${t.repos_installed} installed`, "", btn("by-repo", "List every open item by repository")),
      tile(G.clock("var(--muted)"), "Oldest open", oldest ? span(oldest.created_at) : "-", oldest ? esc(oldest.key.split("/").pop()) : "nothing open", "",
        oldest ? btn("goto", `Open ${oldest.key}`, ` data-key="${esc(oldest.key)}"`) : ""),
      tile(G.pulse(cv("--s4")), "Activity, 7 days", t.activity_7d, "", spark, btn("recent", "Go to recent activity")),
    ].join("");
  }

  _markTiles() {
    for (const el of this.shadowRoot.querySelectorAll('.tile[data-act="show"]')) {
      el.setAttribute("aria-pressed", String(el.getAttribute("data-filter") === this._filter));
    }
  }

  // Tile and chart counts span every repository, so a jump to the list drops the repository and text filters.
  _reset(filter, sort = this._sort) {
    const root = this.shadowRoot;
    this._filter = filter;
    this._sort = sort;
    this._repo = "";
    this._query = "";
    root.getElementById("q").value = "";
    root.getElementById("sort").value = sort;
    this._markTiles();
    this._renderToolbar();
    this._renderCharts();
    this._renderList();
  }

  _scrollTo(el) {
    if (el) el.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  _renderCharts() {
    const el = this.shadowRoot.getElementById("charts");
    const snap = this._snap;
    if (!snap) {
      el.innerHTML = this._error ? "" : `<div class="card wide"><div class="empty">Loading the inbox</div></div>`;
      return;
    }
    if (!snap.sync.length) {
      el.innerHTML = `<div class="card wide"><div class="empty">${LOGO}<div>No GitHub Inbox is set up. Add it under Settings, Devices &amp; services.</div></div></div>`;
      return;
    }
    el.innerHTML = [this._chartRepos(), this._chartWaiting(), this._chartAges(), this._chartDays()].join("");
  }

  _chartRepos() {
    const rows = this._snap.by_repo;
    if (!rows.length) return `<div class="card"><h2>By repository</h2><div class="empty">Nothing open.</div></div>`;
    const top = rows.slice(0, 10);
    const max = Math.max(...top.map((r) => r.issues + r.prs + r.drafts));
    const bars = top
      .map((r) => {
        const total = r.issues + r.prs + r.drafts;
        const tip = `${r.repo}\n${r.issues} issues, ${r.prs} pull requests${r.drafts ? `, ${r.drafts} drafts` : ""}${r.unseen ? `\n${r.unseen} unseen` : ""}`;
        const seg = (n, style, cls = "") => (n ? `<i class="${cls}" style="flex:${n};${style}"></i>` : "");
        return `<div class="brow${this._repo === r.repo ? " on" : ""}" data-act="repo" data-repo="${esc(r.repo)}" tabindex="0" data-tip="${esc(tip)}">
<span class="n">${esc(short(r.repo))}</span><div class="track"><div class="stack" style="width:${(100 * total) / max}%">${seg(r.issues, "background:var(--s1)")}${seg(r.prs, "background:var(--s2)")}${seg(r.drafts, "", "hatch")}</div></div>
<span class="t">${total}</span></div>`;
      })
      .join("");
    const more = rows.length > top.length ? `<div class="small muted" style="margin-top:6px">and ${rows.length - top.length} more repositories</div>` : "";
    const t = this._snap.totals;
    return `<div class="card"><h2>By repository <small>select one to filter</small></h2><div class="bars">${bars}</div>${more}
<div class="legend"><span><i class="sw" style="background:var(--s1)"></i>Issues <b>${t.issues}</b></span><span><i class="sw" style="background:var(--s2)"></i>Pull requests <b>${t.prs - t.drafts}</b></span><span><i class="sw hatch"></i>Drafts <b>${t.drafts}</b></span></div></div>`;
  }

  _chartWaiting() {
    const items = this._snap.items.filter((i) => i.waiting);
    const open = this._snap.items.length;
    items.sort((a, b) => String(a.last_at).localeCompare(String(b.last_at)));
    const list = items
      .slice(0, 6)
      .map((i) => `<a data-act="goto" data-key="${esc(i.key)}" tabindex="0">${glyph(i)}<span class="tt">${esc(i.title)}</span><span class="muted small">${esc(ago(i.last_at))}</span></a>`)
      .join("");
    return `<div class="card"><h2>Waiting on you <small>oldest first</small></h2><div class="split">${ring(items.length, open, "var(--s4)")}
<div class="wl">${list || `<span class="muted small">Every open item has your last word.</span>`}</div></div>
<div class="legend"><span><i class="sw" style="background:var(--s4)"></i>Last word from someone else <b>${items.length}</b> of ${open}</span></div></div>`;
  }

  _chartAges() {
    const ages = this._snap.ages;
    const max = Math.max(1, ...ages.map((a) => a.issues + a.prs));
    const cols = ages
      .map((a, i) => {
        const total = a.issues + a.prs;
        const h = (n) => `${(100 * n) / max}%`;
        const tip = `${AGE_LABELS[i]}: ${a.issues} issues, ${a.prs} pull requests`;
        return `<div class="col" data-tip="${esc(tip)}">${a.issues ? `<i style="height:${h(a.issues)};background:var(--s1)"></i>` : ""}${a.prs ? `<i style="height:${h(a.prs)};background:var(--s2)"></i>` : ""}
${total ? `<span class="tot" style="bottom:calc(${h(total)} + 3px)">${total}</span>` : ""}</div>`;
      })
      .join("");
    return `<div class="card"><h2>Age of open items <small>since opened</small></h2><div class="cols">${cols}</div>
<div class="xl">${AGE_LABELS.map((l) => `<span>${l}</span>`).join("")}</div>
<div class="legend"><span><i class="sw" style="background:var(--s1)"></i>Issues</span><span><i class="sw" style="background:var(--s2)"></i>Pull requests</span></div></div>`;
  }

  _chartDays() {
    const days = this._snap.days;
    const max = Math.max(1, ...days.map((d) => ACT.reduce((s, [k]) => s + d[k], 0)));
    const sums = Object.fromEntries(ACT.map(([k]) => [k, days.reduce((s, d) => s + d[k], 0)]));
    const cols = days
      .map((d) => {
        const total = ACT.reduce((s, [k]) => s + d[k], 0);
        const date = new Date(`${d.date}T12:00:00`);
        const tip = [date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }), ...ACT.map(([k, label]) => `${label}: ${d[k]}`)].join("\n");
        const segs = ACT.map(([k, , color]) => (d[k] ? `<i style="height:${(100 * d[k]) / max}%;background:var(${color})"></i>` : "")).join("");
        return `<div class="col" data-tip="${esc(tip)}">${segs}${total ? `<span class="tot" style="bottom:calc(${(100 * total) / max}% + 3px)">${total}</span>` : ""}</div>`;
      })
      .join("");
    const labels = days
      .map((d, i) => {
        const date = new Date(`${d.date}T12:00:00`);
        return `<span>${i % 2 === days.length % 2 ? "" : date.toLocaleDateString(undefined, { month: "numeric", day: "numeric" })}</span>`;
      })
      .join("");
    return `<div class="card"><h2>Activity <small>last 14 days</small></h2><div class="cols days">${cols}</div><div class="xl days">${labels}</div>
<div class="legend">${ACT.map(([k, label, color]) => `<span><i class="sw" style="background:var(${color})"></i>${label} <b>${sums[k]}</b></span>`).join("")}</div></div>`;
  }

  _renderToolbar() {
    const snap = this._snap;
    const seg = this.shadowRoot.getElementById("seg");
    const repoSel = this.shadowRoot.getElementById("repo");
    if (!snap) return;
    const t = snap.totals;
    const opts = [
      ["all", "All", t.issues + t.prs],
      ["issue", "Issues", t.issues],
      ["pr", "Pull requests", t.prs],
      ["unseen", "Unseen", t.unseen],
      ["waiting", "Waiting on you", t.waiting],
    ];
    seg.innerHTML = opts.map(([k, label, n]) => `<button data-act="filter" data-filter="${k}" class="${this._filter === k ? "on" : ""}">${label}<b>${n}</b></button>`).join("");
    const repos = [...new Set(snap.items.map((i) => i.repo))].sort();
    if (this._repo && !repos.includes(this._repo)) this._repo = "";
    repoSel.innerHTML = `<option value="">All repositories</option>${repos.map((r) => `<option value="${esc(r)}"${r === this._repo ? " selected" : ""}>${esc(r)}</option>`).join("")}`;
  }

  _visible() {
    const q = this._query;
    let items = this._snap.items.filter((i) => {
      if (this._repo && i.repo !== this._repo) return false;
      if (this._filter === "issue" && i.kind !== "issue") return false;
      if (this._filter === "pr" && i.kind !== "pr") return false;
      if (this._filter === "unseen" && !i.unseen) return false;
      if (this._filter === "waiting" && !i.waiting) return false;
      if (!q) return true;
      const hay = [i.title, `#${i.number}`, i.repo, i.author, ...(i.labels || []).map((l) => l.name)].join(" ").toLowerCase();
      return hay.includes(q);
    });
    const by = {
      updated: (a, b) => b.updated_at.localeCompare(a.updated_at),
      created: (a, b) => b.created_at.localeCompare(a.created_at),
      oldest: (a, b) => a.created_at.localeCompare(b.created_at),
      repo: (a, b) => a.repo.localeCompare(b.repo) || b.number - a.number,
    }[this._sort];
    return items.sort(by);
  }

  _renderList() {
    const el = this.shadowRoot.getElementById("list");
    const count = this.shadowRoot.getElementById("count");
    if (!this._snap) {
      el.innerHTML = "";
      return;
    }
    const items = this._visible();
    count.textContent = `${items.length} shown`;
    if (!items.length) {
      const none = this._snap.items.length
        ? "Nothing matches the filter."
        : `Nothing open across ${this._snap.totals.repos_installed} repositories.`;
      el.innerHTML = `<div class="empty">${G.closed("var(--good)")}<div>${none}</div></div>`;
      return;
    }
    el.innerHTML = items.map((i) => this._itemHtml(i)).join("");
  }

  _itemHtml(i) {
    const open = this._open.has(i.key);
    const mine = isMe(i);
    const pr = i.pr
      ? `<span data-tip="${esc(`${i.pr.head} into ${i.pr.base}\n${i.pr.changed_files} files changed`)}">+${i.pr.additions} −${i.pr.deletions}</span>`
      : "";
    const draft = i.draft ? `<span class="chip">draft</span>` : "";
    const fresh = i.unseen ? `<span class="chip new"><span class="dot" style="background:var(--warn)"></span>new</span>` : "";
    const last = i.last
      ? `<span>${avatar(i.last.author, false, mine(i.last.author))} ${esc(i.last.kind === "review" ? (REVIEW[i.last.state] || ["reviewed"])[0] : "commented")} ${esc(ago(i.last.at))}</span>`
      : "";
    return `<div class="item${open ? " open" : ""}" data-key="${esc(i.key)}">
<div class="row" data-act="toggle" data-key="${esc(i.key)}" tabindex="0" role="button" aria-expanded="${open}">
<span class="caret">${G.caret()}</span><span class="gl" data-tip="${i.kind === "pr" ? (i.draft ? "draft pull request" : "pull request") : "issue"}">${glyph(i)}</span>
<div class="main"><div class="title">${esc(i.title)} <span class="num">#${i.number}</span></div>
<div class="meta"><span>${esc(short(i.repo))}</span><span>${avatar(i.author, i.author_bot, mine(i.author))} ${esc(i.author)} opened ${esc(ago(i.created_at))}</span>${last}${draft}</div>
${i.labels && i.labels.length ? `<div class="labels">${labelChips(i.labels)}</div>` : ""}</div>
<div class="side">${fresh}${pr}<span class="cm" data-tip="${i.comments} comments and reviews">${G.comment("var(--muted)")}${i.comments}</span></div></div>
${open ? `<div class="detail">${this._detailHtml(i, mine)}</div>` : ""}</div>`;
  }

  _detailHtml(i, mine) {
    const d = this._details.get(i.key);
    if (!d) return `<div class="ldg">${this._loading.has(i.key) ? "Loading" : "Not loaded"}</div>`;
    const seenAt = d.seen_at || "";
    const desc = `<div class="box desc"><div class="ehead">${avatar(d.author, d.author_bot, mine(d.author))}<span class="who">${esc(d.author)}</span><span class="what">opened this ${i.kind === "pr" ? "pull request" : "issue"}</span><span class="when" data-tip="${esc(when(d.created_at))}">${esc(ago(d.created_at))}</span></div>
<div class="ebody md">${d.body_html || `<p class="muted">No description.</p>`}</div></div>`;
    const entries = (d.timeline || [])
      .map((e) => {
        const isOpen = this._openEntries.has(e.id);
        const isNew = e.at > seenAt && !mine(e.author);
        let what = "commented";
        let mark = G.comment("var(--muted)");
        if (e.kind === "review") {
          const [word, color] = REVIEW[e.state] || ["reviewed", "--faint"];
          what = word;
          mark = `<span class="dot" style="background:var(${color})"></span>`;
        } else if (e.kind === "review_comment") {
          what = `on <span class="path">${esc(e.path)}${e.line ? `:${e.line}` : ""}</span>`;
          mark = G.review("var(--muted)");
        }
        return `<div class="box entry${isOpen ? " open" : ""}" data-id="${esc(e.id)}">
<div class="ehead" data-act="entry" data-key="${esc(i.key)}" data-id="${esc(e.id)}" tabindex="0" role="button" aria-expanded="${isOpen}">${mark}${avatar(e.author, e.author_bot, mine(e.author))}<span class="who">${esc(e.author)}</span><span class="what">${e.kind === "review_comment" ? what : esc(what)}</span>
${isNew ? `<span class="chip new"><span class="dot" style="background:var(--warn)"></span>new</span>` : ""}<span class="pv">${esc(e.preview)}</span><span class="when" data-tip="${esc(when(e.at))}">${esc(ago(e.at))}</span></div>
<div class="ebody md">${e.body_html || `<p class="muted">No text.</p>`}${e.url ? `<p class="small"><a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">Open this on GitHub</a></p>` : ""}</div></div>`;
      })
      .join("");
    const n = (d.timeline || []).length;
    return `${desc}${entries || `<div class="ldg">No comments.</div>`}
<div class="dactions">${n ? `<button data-act="expand" data-key="${esc(i.key)}">Expand all ${n}</button><button data-act="collapse" data-key="${esc(i.key)}">Collapse all</button>` : ""}
<a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer"><button>Open on GitHub</button></a></div>`;
  }

  _renderRecent() {
    const el = this.shadowRoot.getElementById("recent");
    const snap = this._snap;
    if (!snap || !snap.sync.length) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    const icon = (a) =>
      ({
        opened: a.kind === "pr" ? G.pr(cv("--s3")) : G.issue(cv("--s3")),
        reopened: a.kind === "pr" ? G.pr(cv("--s3")) : G.issue(cv("--s3")),
        commented: G.comment(cv("--s4")),
        reviewed: G.review(cv("--s5")),
        closed: G.closed(cv("--s6")),
        merged: G.merged(cv("--s6")),
      })[a.type] || G.pulse("var(--muted)");
    const verb = (a) =>
      a.type === "reviewed" ? (REVIEW[a.state] || ["reviewed"])[0] : a.type === "opened" || a.type === "reopened" ? `${a.type} ${a.kind === "pr" ? "pull request" : "issue"}` : a.type;
    const rows = snap.recent
      .map(
        (a) => `<div class="fe" data-act="goto" data-key="${esc(a.key)}" tabindex="0" data-tip="${esc(a.preview || a.title)}">${icon(a)}
<span class="tx"><b>${esc(a.actor)}</b> ${esc(verb(a))} <span class="muted">${esc(short(a.repo))}#${a.number}</span> ${esc(a.title)}</span><span class="muted small" data-tip="${esc(when(a.at))}">${esc(ago(a.at))}</span></div>`,
      )
      .join("");
    el.innerHTML = `<h2>Recent activity <small>newest first</small></h2><div class="feed">${rows || `<div class="empty">No activity yet. It appears as GitHub delivers it.</div>`}</div>`;
  }

  async _fetch(key, markSeen) {
    this._loading.add(key);
    try {
      const detail = await this._hass.callWS({ type: "github_inbox/item", key });
      this._details.set(key, detail);
    } catch (_err) {
      this._details.delete(key);
    } finally {
      this._loading.delete(key);
    }
    this._renderItem(key);
    if (markSeen) {
      this._hass.callWS({ type: "github_inbox/mark_seen", keys: [key] }).catch(() => {});
    }
  }

  _renderItem(key) {
    const el = this.shadowRoot.querySelector(`.item[data-key="${CSS.escape(key)}"]`);
    const item = this._snap && this._snap.items.find((i) => i.key === key);
    if (!el || !item) return;
    const tpl = document.createElement("template");
    tpl.innerHTML = this._itemHtml(item);
    el.replaceWith(tpl.content.firstElementChild);
  }

  _toggle(key, force) {
    const open = force === undefined ? !this._open.has(key) : force;
    if (open) {
      this._open.add(key);
      this._fetch(key, true);
    } else {
      this._open.delete(key);
    }
    this._renderItem(key);
  }

  _onClick(e) {
    const el = e.target.closest("[data-act]");
    if (!el || (e.target.closest("a[href]") && !el.matches("a[data-act]"))) return;
    const act = el.getAttribute("data-act");
    const key = el.getAttribute("data-key");
    if (act === "toggle") this._toggle(key);
    else if (act === "entry") {
      const id = el.getAttribute("data-id");
      if (this._openEntries.has(id)) this._openEntries.delete(id);
      else this._openEntries.add(id);
      const box = el.closest(".entry");
      box.classList.toggle("open", this._openEntries.has(id));
      el.setAttribute("aria-expanded", String(this._openEntries.has(id)));
    } else if (act === "expand" || act === "collapse") {
      const d = this._details.get(key);
      for (const entry of (d && d.timeline) || []) {
        if (act === "expand") this._openEntries.add(entry.id);
        else this._openEntries.delete(entry.id);
      }
      this._renderItem(key);
    } else if (act === "filter") {
      this._filter = el.getAttribute("data-filter");
      this._markTiles();
      this._renderToolbar();
      this._renderList();
    } else if (act === "show") {
      const filter = el.getAttribute("data-filter");
      this._reset(this._filter === filter ? "all" : filter);
      this._scrollTo(this.shadowRoot.getElementById("listcard"));
    } else if (act === "by-repo") {
      this._reset("all", "repo");
      this._scrollTo(this.shadowRoot.getElementById("listcard"));
    } else if (act === "recent") {
      this._scrollTo(this.shadowRoot.getElementById("recent"));
    } else if (act === "repo") {
      const repo = el.getAttribute("data-repo");
      this._repo = this._repo === repo ? "" : repo;
      this._renderCharts();
      this._renderToolbar();
      this._renderList();
    } else if (act === "goto") {
      this._reset("all");
      this._toggle(key, true);
      this._scrollTo(this.shadowRoot.querySelector(`.item[data-key="${CSS.escape(key)}"]`));
    } else if (act === "sync") {
      this._hass.callWS({ type: "github_inbox/resync" }).catch(() => {});
    } else if (act === "seen-all") {
      this._hass.callWS({ type: "github_inbox/mark_seen", all: true }).catch(() => {});
    } else if (act === "theme") {
      this._theme = this._theme === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(THEME_KEY, this._theme);
      } catch (_e) {
        /* storage blocked: the choice lasts this visit */
      }
      this._renderTheme();
    }
  }
}

if (!customElements.get("github-inbox-panel")) {
  customElements.define("github-inbox-panel", GitHubInboxPanel);
}
