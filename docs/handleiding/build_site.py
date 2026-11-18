#!/usr/bin/env python
# Builds docs/handleiding/coga-handleiding.html from the Markdown chapters, in Dutch (this
# folder) and English (en/), as one page with a language toggle at the top.
# Needs the Python package "markdown", in the version CI pins (ci/requirements-handleiding.txt).
# Run:  python docs/handleiding/build_site.py
#
# The output depends only on the Markdown and on VERSION (the chip in the sidebar), never on
# the clock or the commit: scripts/check-handleiding-sync.sh rebuilds and compares.
#
# Before it writes anything, the build compares every English chapter with its Dutch original:
# the same headings, tables, code blocks, inline code and links between chapters. A chapter
# changed in one language only stops the build, so the two versions cannot drift apart.
import html as htmlmod
import os
import re
import sys

import markdown

DOCS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(DOCS))
OUT = os.path.join(DOCS, "coga-handleiding.html")
MARKDOWN_EXTENSIONS = ["tables", "fenced_code", "sane_lists", "attr_list"]


def read_product_version():
    """The product version from VERSION, the single source of truth (RELEASING.md)."""
    try:
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


PRODUCT_VERSION = read_product_version()

# One entry per language, in the order of the toggle. Each language lists the same chapters.
LANGUAGES = [
    {
        "code": "nl",
        "dir": DOCS,
        "name": "Nederlands",
        "page_title": "CoGA — Codebase-handleiding",
        "brand": "CoGA<br>Codebase-handleiding",
        "sub": "Technische uitleg voor het review board: uitlegbaarheid, traceerbaarheid &amp; veiligheid.",
        "version": "versie",
        "unknown_version": "onbekend",
        "contents": "Inhoud",
        "intro_nav": "Introductie &amp; inhoudstabel",
        "chapters_label": "hoofdstukken",
        "nav_label": "Hoofdstukken",
        "callout": "Waar in de code",
        "language_label": "Taal",
        "theme_label": "Wissel thema",
        "to_dark": "☾ Donker",
        "to_light": "☀︎ Licht",
        "chapters": [
            (1,  "01-architectuur.md",                    "Algemene architectuur & structuur"),
            (2,  "02-beveiliging-rollen-rechten.md",      "Gebruikersrollen, machtigingen & afscherming"),
            (3,  "03-databankstructuren.md",              "Databankstructuren (Postgres & ClickHouse)"),
            (4,  "04-deployment-en-seeding.md",           "Initiële deployment & seeding"),
            (5,  "05-login-authenticatie.md",             "Login & authenticatie"),
            (6,  "06-import-pipeline.md",                 "Package import — manifest, controles, traceerbaarheid"),
            (7,  "07-backend-routers-en-services.md",     "Backend: routers & services in detail"),
            (8,  "08-filterpaginas-en-api.md",            "Filterpagina's ↔ API"),
            (9,  "09-visualisaties.md",                   "Visualisaties (chromosome, genome, circos, IGV)"),
            (10, "10-tagging-en-acmg-classificatie.md",   "Variant-tagging & semi-automatische ACMG-classificatie"),
            (11, "11-rapport-en-traceerbaarheid.md",      "Rapport & volledige traceerbaarheid"),
            (12, "12-hpo-monarch-prioritisatie.md",       "HPO, Monarch & variant-prioritisatie"),
            (13, "13-gene-explorer.md",                   "Gene Explorer & versiecontrole"),
            (14, "14-variant-explorer.md",                "Variant Explorer"),
            (15, "15-overige-modules-en-admin.md",        "Overige modules & adminfunctionaliteit"),
        ],
    },
    {
        "code": "en",
        "dir": os.path.join(DOCS, "en"),
        "name": "English",
        "page_title": "CoGA — Codebase manual",
        "brand": "CoGA<br>Codebase manual",
        "sub": "A technical explanation for the review board: explainability, traceability &amp; safety.",
        "version": "version",
        "unknown_version": "unknown",
        "contents": "Contents",
        "intro_nav": "Introduction &amp; contents",
        "chapters_label": "chapters",
        "nav_label": "Chapters",
        "callout": "Where in the code",
        "language_label": "Language",
        "theme_label": "Switch theme",
        "to_dark": "☾ Dark",
        "to_light": "☀︎ Light",
        "chapters": [
            (1,  "01-architecture.md",                    "General architecture & structure"),
            (2,  "02-security-roles-permissions.md",      "User roles, permissions & isolation"),
            (3,  "03-database-structures.md",             "Database structures (Postgres & ClickHouse)"),
            (4,  "04-deployment-and-seeding.md",          "Initial deployment & seeding"),
            (5,  "05-login-authentication.md",            "Login & authentication"),
            (6,  "06-import-pipeline.md",                 "Package import — manifest, checks, traceability"),
            (7,  "07-backend-routers-and-services.md",    "Backend: routers & services in detail"),
            (8,  "08-filter-pages-and-api.md",            "Filter pages ↔ API"),
            (9,  "09-visualisations.md",                  "Visualisations (chromosome, genome, circos, IGV)"),
            (10, "10-tagging-and-acmg-classification.md", "Variant tagging & semi-automatic ACMG classification"),
            (11, "11-report-and-traceability.md",         "Report & full traceability"),
            (12, "12-hpo-monarch-prioritisation.md",      "HPO, Monarch & variant prioritisation"),
            (13, "13-gene-explorer.md",                   "Gene Explorer & version control"),
            (14, "14-variant-explorer.md",                "Variant Explorer"),
            (15, "15-other-modules-and-admin.md",         "Other modules & administration"),
        ],
    },
]


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def to_html(text):
    return markdown.Markdown(extensions=MARKDOWN_EXTENSIONS).convert(text)


def render_md(path, lang):
    out = to_html(read(path))
    code = lang["code"]
    # Links between chapters, NN-....md(#..), point at that chapter in the same language.
    out = re.sub(r'href="(\d+)-[^"]*\.md(?:#[^"]*)?"', lambda m: f'href="#{code}-hs-{int(m.group(1))}"', out)

    # Every other relative link (another .md, ../) is unlinked, keeping its text, so the page
    # has no dead links.
    def unlink(m):
        href, txt = m.group(1), m.group(2)
        if href.startswith("#") or href.startswith("http"):
            return m.group(0)
        return f'<span class="deadref">{txt}</span>'

    out = re.sub(r'<a href="([^"]+)">(.*?)</a>', unlink, out, flags=re.S)
    # Wide blocks scroll horizontally.
    out = out.replace("<table>", '<div class="scroll"><table>').replace("</table>", "</table></div>")
    out = re.sub(r"<pre>", '<div class="scroll"><pre>', out)
    out = re.sub(r"</pre>", "</pre></div>", out)
    # The "Where in the code" paragraph is a callout.
    out = out.replace(f'<p><strong>{lang["callout"]}', f'<p class="waar"><strong>{lang["callout"]}')
    return out


# ---------- the two languages hold the same manual ----------

def structure(path):
    """What a translation keeps from its original, read from the rendered chapter."""
    html = to_html(read(path))
    prose = re.sub(r"<pre>.*?</pre>", "", html, flags=re.S)
    return {
        "heading levels": re.findall(r"<h([1-6])", html),
        "table rows": [table.count("<tr") for table in re.findall(r"<table>.*?</table>", html, flags=re.S)],
        "code blocks": html.count("<pre>"),
        "paragraphs": len(re.findall(r"<p[ >]", html)),
        "list items": len(re.findall(r"<li[ >]", html)),
        "inline code": set(re.findall(r"<code>(.*?)</code>", prose, flags=re.S)),
        "chapter links": {int(n) for n in re.findall(r'href="(\d+)-[^"]*\.md', html)},
    }


def translation_differences(original_path, translation_path):
    """The ways a translation's structure differs from its original; empty when they match."""
    a, b = structure(original_path), structure(translation_path)
    problems = [
        f"{key}: {a[key]} in {os.path.relpath(original_path, ROOT)}, {b[key]} in the translation"
        for key in ("heading levels", "table rows", "code blocks", "paragraphs", "list items", "chapter links")
        if a[key] != b[key]
    ]
    for span in sorted(a["inline code"] - b["inline code"]):
        problems.append(f"inline code missing from the translation: {htmlmod.unescape(span)}")
    for span in sorted(b["inline code"] - a["inline code"]):
        problems.append(f"inline code only in the translation: {htmlmod.unescape(span)}")
    return problems


def check_languages():
    """Every language has the same chapters as the first, with the same structure."""
    original = LANGUAGES[0]
    problems = []
    for lang in LANGUAGES[1:]:
        if [num for num, _, _ in lang["chapters"]] != [num for num, _, _ in original["chapters"]]:
            problems.append(f"{lang['code']}: the chapter list differs from {original['code']}")
            continue
        pairs = [("README.md", "README.md")] + [
            (a[1], b[1]) for a, b in zip(original["chapters"], lang["chapters"])
        ]
        for original_file, translated_file in pairs:
            original_path = os.path.join(original["dir"], original_file)
            translated_path = os.path.join(lang["dir"], translated_file)
            if not os.path.exists(translated_path):
                problems.append(f"{os.path.relpath(translated_path, ROOT)} is missing")
                continue
            for problem in translation_differences(original_path, translated_path):
                problems.append(f"{os.path.relpath(translated_path, ROOT)}: {problem}")
    return problems


# ---------- page ----------

def only(lang_code, inner):
    """Markup shown only while the page reads in this language."""
    return f'<span data-lang-only="{lang_code}" lang="{lang_code}">{inner}</span>'


def each(key, escape=False):
    """One span per language holding that language's UI string."""
    return "".join(
        only(lang["code"], htmlmod.escape(lang[key]) if escape else lang[key]) for lang in LANGUAGES
    )


def build_article(lang):
    code = lang["code"]
    intro = render_md(os.path.join(lang["dir"], "README.md"), lang)
    sections = [f'<section id="{code}-top" class="intro">\n{intro}\n</section>']
    for num, fn, _ in lang["chapters"]:
        body = render_md(os.path.join(lang["dir"], fn), lang)
        sections.append(f'<section id="{code}-hs-{num}" class="chapter">\n{body}\n</section>')
    return (
        f'<article class="reading" data-lang-only="{code}" lang="{code}">\n'
        f'{"".join(sections)}\n</article>'
    )


def build_nav(lang):
    code = lang["code"]
    items = [
        f'<a class="nav-link" href="#{code}-top" data-target="{code}-top"><span class="n">·</span>'
        f'<span class="t">{lang["intro_nav"]}</span></a>'
    ]
    for num, _, title in lang["chapters"]:
        items.append(
            f'<a class="nav-link" href="#{code}-hs-{num}" data-target="{code}-hs-{num}">'
            f'<span class="n">{num}</span><span class="t">{htmlmod.escape(title)}</span></a>'
        )
    return (
        f'<nav class="nav" data-lang-only="{code}" lang="{code}" aria-label="{lang["nav_label"]}">\n'
        f'    <div class="nav-eyebrow">{lang["contents"]}</div>\n    {"".join(items)}\n  </nav>'
    )


CSS = r"""
<style>
:root{
  --bg:#f4f7f6; --surface:#ffffff; --surface-2:#eef3f1;
  --ink:#16211f; --muted:#5b6b68; --border:#dde5e2;
  --accent:#0b6b5f; --accent-ink:#0a544b; --accent-soft:#e3efec;
  --code-bg:#eef3f1; --shadow:0 1px 3px rgba(16,40,36,.06),0 8px 24px rgba(16,40,36,.05);
  --font-body:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --font-head:"Iowan Old Style","Charter","Bitstream Charter","Sitka Text",Cambria,Georgia,serif;
  --font-mono:ui-monospace,"SF Mono","JetBrains Mono","Cascadia Code",Menlo,Consolas,monospace;
  --sidebar:312px; --measure:46rem;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0d1413; --surface:#141f1d; --surface-2:#1b2724;
    --ink:#e7edeb; --muted:#93a4a0; --border:#263531;
    --accent:#49c9b8; --accent-ink:#6fd6c7; --accent-soft:#14312c;
    --code-bg:#101a18; --shadow:0 1px 3px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.35);
  }
}
:root[data-theme="light"]{
  --bg:#f4f7f6; --surface:#ffffff; --surface-2:#eef3f1; --ink:#16211f; --muted:#5b6b68;
  --border:#dde5e2; --accent:#0b6b5f; --accent-ink:#0a544b; --accent-soft:#e3efec;
  --code-bg:#eef3f1; --shadow:0 1px 3px rgba(16,40,36,.06),0 8px 24px rgba(16,40,36,.05);
}
:root[data-theme="dark"]{
  --bg:#0d1413; --surface:#141f1d; --surface-2:#1b2724; --ink:#e7edeb; --muted:#93a4a0;
  --border:#263531; --accent:#49c9b8; --accent-ink:#6fd6c7; --accent-soft:#14312c;
  --code-bg:#101a18; --shadow:0 1px 3px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.35);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--font-body);
  font-size:17px;line-height:1.65;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
a{color:var(--accent-ink);text-underline-offset:2px}
a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px}

/* ---------- language: only the chosen language shows (Dutch until the script runs) ---------- */
:root:not([data-lang="en"]) [data-lang-only="en"],
:root[data-lang="en"] [data-lang-only="nl"]{display:none!important}

/* ---------- layout ---------- */
.app{display:grid;grid-template-columns:var(--sidebar) minmax(0,1fr);min-height:100vh}
.sidebar{position:sticky;top:0;height:100vh;overflow:hidden;display:flex;flex-direction:column;
  background:var(--surface);border-right:1px solid var(--border)}
.brand{padding:22px 22px 14px;border-bottom:1px solid var(--border)}
.brand .logo{display:flex;align-items:center;gap:10px}
.brand .mark{width:30px;height:30px;flex:0 0 auto;border-radius:8px;background:
  linear-gradient(135deg,var(--accent),color-mix(in oklab,var(--accent) 55%, #000 12%));
  display:grid;place-items:center;color:#fff;font-size:16px}
.brand h1{font-family:var(--font-head);font-size:1.12rem;line-height:1.2;margin:0;font-weight:600;letter-spacing:.1px}
.brand .sub{color:var(--muted);font-size:.78rem;margin-top:6px;line-height:1.4}
.brand-meta{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:12px}
.chip{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border:1px solid var(--border);
  border-radius:999px;background:var(--surface-2);color:var(--muted);font-size:.72rem;font-family:var(--font-mono)}
.chip b{color:var(--accent-ink);font-weight:600}
.navwrap{overflow:auto;padding:12px 12px 6px;flex:1}
.nav-eyebrow{font-size:.68rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);
  padding:6px 10px;font-weight:600}
.nav-link{display:flex;gap:12px;align-items:baseline;padding:8px 10px;border-radius:9px;color:var(--ink);
  text-decoration:none;font-size:.9rem;line-height:1.3}
.nav-link .n{flex:0 0 22px;text-align:right;font-family:var(--font-mono);font-size:.78rem;color:var(--muted);
  font-variant-numeric:tabular-nums}
.nav-link .t{flex:1}
.nav-link:hover{background:var(--surface-2);text-decoration:none}
.nav-link.active{background:var(--accent-soft);color:var(--accent-ink);font-weight:600}
.nav-link.active .n{color:var(--accent-ink)}
.sidebar-foot{border-top:1px solid var(--border);padding:12px 16px;display:flex;align-items:center;
  justify-content:space-between;gap:8px}
.sidebar-foot .ver{color:var(--muted);font-size:.72rem;font-family:var(--font-mono)}

/* ---------- theme and language toggles / topbar ---------- */
.tbtn{appearance:none;border:1px solid var(--border);background:var(--surface-2);color:var(--ink);
  border-radius:8px;padding:6px 10px;font-size:.8rem;cursor:pointer;display:inline-flex;gap:6px;align-items:center}
.tbtn:hover{border-color:var(--accent)}
.langswitch{display:inline-flex;flex:0 0 auto;border:1px solid var(--border);border-radius:8px;overflow:hidden;
  background:var(--surface-2)}
.langswitch button{appearance:none;border:0;background:transparent;color:var(--muted);cursor:pointer;
  font:600 .74rem/1 var(--font-mono);letter-spacing:.06em;padding:7px 9px}
.langswitch button+button{border-left:1px solid var(--border)}
.langswitch button:hover{color:var(--ink)}
.langswitch button[aria-pressed="true"]{background:var(--accent);color:#fff}
:root[data-theme="dark"] .langswitch button[aria-pressed="true"]{color:#0d1413}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]) .langswitch button[aria-pressed="true"]{color:#0d1413}
}
.topbar{display:none}
.scrim{display:none}

/* ---------- content ---------- */
main{min-width:0}
.reading{max-width:calc(var(--measure) + 6rem);margin:0 auto;padding:56px 3rem 120px}
section{scroll-margin-top:20px}
.chapter{padding-top:40px;margin-top:40px;border-top:1px solid var(--border)}
.intro{padding-bottom:8px}
.reading :is(h1,h2,h3,h4){font-family:var(--font-head);text-wrap:balance;line-height:1.22;color:var(--ink)}
.reading h1{font-size:2.05rem;font-weight:600;margin:.2em 0 .5em;letter-spacing:-.01em}
.chapter>h1:first-child{margin-top:0}
.reading h2{font-size:1.4rem;font-weight:600;margin:2em 0 .5em;padding-top:.3em}
.reading h3{font-size:1.12rem;font-weight:600;margin:1.7em 0 .4em;color:var(--accent-ink)}
.reading h4{font-size:1rem;font-weight:700;margin:1.4em 0 .3em}
.reading p,.reading li{max-width:var(--measure)}
.reading p{margin:.7em 0}
.reading ul,.reading ol{margin:.7em 0;padding-left:1.4em}
.reading li{margin:.32em 0}
.reading li::marker{color:var(--accent)}
.reading strong{font-weight:650}
.reading hr{border:0;border-top:1px solid var(--border);margin:2em 0}
.reading blockquote{margin:1.1em 0;padding:.6em 1.1em;border-left:3px solid var(--accent);
  background:var(--accent-soft);border-radius:0 8px 8px 0;color:var(--ink)}
.reading blockquote p{margin:.3em 0}
p.waar{background:var(--surface-2);border:1px solid var(--border);border-left:3px solid var(--accent);
  border-radius:0 8px 8px 0;padding:.6em .9em;font-size:.94rem;max-width:var(--measure)}
p.waar strong{color:var(--accent-ink)}
.deadref{color:var(--muted);font-style:italic}

/* code */
.reading :not(pre)>code{font-family:var(--font-mono);font-size:.86em;background:var(--code-bg);
  border:1px solid var(--border);border-radius:5px;padding:.08em .38em;word-break:break-word}
.reading pre{font-family:var(--font-mono);font-size:.86rem;line-height:1.55;background:var(--code-bg);
  border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin:0}
.reading pre code{background:none;border:0;padding:0;font-size:1em}

/* tables */
.scroll{overflow-x:auto;margin:1.1em 0;border-radius:10px}
.scroll::-webkit-scrollbar{height:8px}
.scroll::-webkit-scrollbar-thumb{background:var(--border);border-radius:8px}
.reading table{border-collapse:collapse;width:100%;font-size:.9rem;background:var(--surface);
  border:1px solid var(--border);border-radius:10px;overflow:hidden}
.reading th,.reading td{text-align:left;padding:9px 13px;border-bottom:1px solid var(--border);
  vertical-align:top}
.reading thead th{background:var(--surface-2);font-weight:650;color:var(--ink);white-space:nowrap;
  font-size:.82rem;letter-spacing:.02em}
.reading tbody tr:last-child td{border-bottom:0}
.reading tbody tr:hover{background:var(--accent-soft)}
.reading td code{white-space:nowrap}

/* ---------- responsive ---------- */
@media (max-width:960px){
  .app{grid-template-columns:1fr}
  .topbar{display:flex;position:sticky;top:0;z-index:40;align-items:center;gap:10px;
    padding:10px 14px;background:color-mix(in oklab,var(--surface) 88%, transparent);
    backdrop-filter:saturate(1.2) blur(8px);border-bottom:1px solid var(--border)}
  .topbar .title{font-family:var(--font-head);font-weight:600;font-size:1rem;flex:1;min-width:0;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .menu{appearance:none;border:1px solid var(--border);background:var(--surface-2);border-radius:8px;
    width:38px;height:34px;display:grid;place-items:center;cursor:pointer;color:var(--ink)}
  .sidebar{position:fixed;z-index:60;left:0;top:0;width:min(86vw,340px);transform:translateX(-100%);
    transition:transform .22s ease}
  .brand-meta .langswitch{display:none}
  body.nav-open .sidebar{transform:none}
  body.nav-open .scrim{display:block;position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.42)}
  .reading{padding:30px 20px 100px}
  section{scroll-margin-top:64px}
  .reading h1{font-size:1.7rem}
}
@media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  .sidebar{transition:none}
}

/* ---------- print (PDF export by the board): the chosen language only ---------- */
@media print{
  :root{--ink:#111;--muted:#444;--border:#bbb;--accent:#0b6b5f;--surface:#fff;--surface-2:#fff;--accent-soft:#fff;--code-bg:#f4f4f4}
  .sidebar,.topbar,.scrim,.sidebar-foot{display:none!important}
  .app{display:block}
  .reading{max-width:none;padding:0}
  .chapter{border-top:0;page-break-before:always}
  a{color:inherit;text-decoration:none}
  .scroll{overflow:visible}
  body{font-size:11pt}
}
</style>
"""

# Runs before the page is drawn, so it never flashes in the other language. The language is,
# in order: the one a link's anchor names (#en-hs-6), the reader's last choice, the first of
# the browser's preferred languages the manual has, Dutch.
EARLY_JS = r"""
<script>
(function(){
  var langs=__LANGS__, root=document.documentElement, pick=null;
  var m=/^#([a-z]{2})-/.exec(location.hash); if(m&&langs.indexOf(m[1])>=0) pick=m[1];
  if(!pick){ try{ var saved=localStorage.getItem('coga-handleiding-lang'); if(langs.indexOf(saved)>=0) pick=saved; }catch(e){} }
  if(!pick){ var pref=(navigator.languages&&navigator.languages.length)?navigator.languages:[navigator.language||''];
    for(var i=0;i<pref.length&&!pick;i++){ var c=String(pref[i]).slice(0,2).toLowerCase(); if(langs.indexOf(c)>=0) pick=c; } }
  root.setAttribute('data-lang',pick||langs[0]);
  root.setAttribute('lang',pick||langs[0]);
  try{var theme=localStorage.getItem('coga-theme'); if(theme) root.setAttribute('data-theme',theme);}catch(e){}
})();
</script>
"""

JS = r"""
<script>
(function(){
  var root=document.documentElement, body=document.body;
  var UI=__UI__;
  function lang(){ return root.getAttribute('data-lang'); }

  // theme toggle: sets data-theme explicitly (overrides prefers-color-scheme either way)
  function effective(){
    var dt=root.getAttribute('data-theme');
    if(dt) return dt;
    return window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light';
  }
  function applyLabels(){
    var ui=UI[lang()], dark=effective()==='dark';
    document.title=ui.title;
    document.querySelectorAll('[data-theme-toggle]').forEach(function(b){
      b.innerHTML = dark ? ui.light : ui.dark;
      b.setAttribute('aria-label', ui.theme);
    });
    document.querySelectorAll('[data-set-lang]').forEach(function(b){
      b.setAttribute('aria-pressed', b.getAttribute('data-set-lang')===lang() ? 'true' : 'false');
    });
  }

  // scrollspy, over the sections of the language on screen
  var links=[].slice.call(document.querySelectorAll('.nav-link'));
  var map={}; links.forEach(function(l){map[l.getAttribute('data-target')]=l;});
  var sections=[].slice.call(document.querySelectorAll('section[id]'));
  var current=null;
  function setActive(id){ current=id; links.forEach(function(l){l.classList.remove('active');});
    if(map[id]){ map[id].classList.add('active');
      var nav=map[id].closest('.navwrap');
      var r=map[id].getBoundingClientRect(), nr=nav.getBoundingClientRect();
      if(r.top<nr.top||r.bottom>nr.bottom){ map[id].scrollIntoView({block:'nearest'}); } } }
  var io=new IntersectionObserver(function(entries){
    entries.forEach(function(en){ if(en.isIntersecting){ setActive(en.target.id); } });
  },{rootMargin:'-15% 0px -70% 0px',threshold:0});
  sections.forEach(function(s){io.observe(s);});

  // language toggle: the same place in the other language, as far into the chapter as before
  function setLang(next){
    if(next===lang()) return;
    var from=current&&document.getElementById(current), progress=0;
    if(from){ var r=from.getBoundingClientRect(); progress=Math.min(1,Math.max(0,-r.top/Math.max(1,r.height))); }
    root.setAttribute('data-lang',next); root.setAttribute('lang',next);
    try{localStorage.setItem('coga-handleiding-lang',next);}catch(e){}
    applyLabels();
    if(!from) return;
    var to=document.getElementById(current.replace(/^[a-z]{2}-/, next+'-'));
    if(!to) return;
    var top=to.getBoundingClientRect().top+window.pageYOffset+progress*to.getBoundingClientRect().height;
    window.scrollTo({top:Math.round(top),behavior:'auto'});
    setActive(to.id);
    if(location.hash){ history.replaceState(null,'','#'+to.id); }
  }

  applyLabels();
  document.addEventListener('click',function(e){
    var l=e.target.closest('[data-set-lang]');
    if(l){ setLang(l.getAttribute('data-set-lang')); return; }
    var t=e.target.closest('[data-theme-toggle]');
    if(t){ var next=effective()==='dark'?'light':'dark'; root.setAttribute('data-theme',next);
      try{localStorage.setItem('coga-theme',next);}catch(e){} applyLabels(); return; }
    var m=e.target.closest('[data-menu]'); if(m){ body.classList.toggle('nav-open'); return; }
    if(e.target.closest('.scrim')){ body.classList.remove('nav-open'); return; }
    var lnk=e.target.closest('.nav-link'); if(lnk){ body.classList.remove('nav-open'); }
  });
})();
</script>
"""


def lang_switch():
    buttons = "".join(
        f'<button type="button" data-set-lang="{lang["code"]}" lang="{lang["code"]}" '
        f'title="{lang["name"]}" aria-pressed="{"true" if i == 0 else "false"}">{lang["code"].upper()}</button>'
        for i, lang in enumerate(LANGUAGES)
    )
    label = " / ".join(lang["language_label"] for lang in LANGUAGES)
    return f'<div class="langswitch" role="group" aria-label="{label}">{buttons}</div>'


def ui_strings():
    """The strings the script swaps when the language changes, as a JavaScript object."""
    entries = []
    for lang in LANGUAGES:
        strings = {
            "title": lang["page_title"],
            "dark": lang["to_dark"],
            "light": lang["to_light"],
            "theme": lang["theme_label"],
        }
        body = ",".join(f"{key}:{js_string(value)}" for key, value in strings.items())
        entries.append(f"{lang['code']}:{{{body}}}")
    return "{" + ",".join(entries) + "}"


def js_string(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def build_page():
    first = LANGUAGES[0]
    chip = "".join(
        only(lang["code"], f'{lang["version"]} <b>{htmlmod.escape(PRODUCT_VERSION or lang["unknown_version"])}</b>')
        for lang in LANGUAGES
    )
    chapter_count = "".join(only(lang["code"], f'{len(lang["chapters"])} {lang["chapters_label"]}') for lang in LANGUAGES)
    theme_button = (
        f'<button class="tbtn" data-theme-toggle aria-label="{first["theme_label"]}">{first["to_dark"]}</button>'
    )

    topbar = (
        '<div class="topbar">'
        '<button class="menu" data-menu aria-label="Menu">☰</button>'
        f'<span class="title">{each("page_title", escape=True)}</span>'
        f"{lang_switch()}"
        f"{theme_button}"
        "</div>"
    )
    sidebar = f"""
<aside class="sidebar" id="sidebar">
  <div class="brand">
    <div class="logo"><span class="mark" aria-hidden="true">🧬</span>
      <h1>{each("brand")}</h1></div>
    <div class="sub">{each("sub")}</div>
    <div class="brand-meta"><span class="chip">{chip}</span>{lang_switch()}</div>
  </div>
  <div class="navwrap">
  {"".join(build_nav(lang) for lang in LANGUAGES)}
  </div>
  <div class="sidebar-foot">
    <span class="ver">{chapter_count}</span>
    {theme_button}
  </div>
</aside>
"""
    langs = "[" + ",".join(js_string(lang["code"]) for lang in LANGUAGES) + "]"
    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{htmlmod.escape(first["page_title"])}</title>
{EARLY_JS.replace("__LANGS__", langs)}
{CSS}
{topbar}
<div class="scrim" aria-hidden="true"></div>
<div class="app">
{sidebar}
<main>
{"".join(build_article(lang) for lang in LANGUAGES)}
</main>
</div>
{JS.replace("__UI__", ui_strings())}
"""


def main():
    problems = check_languages()
    if problems:
        print(
            "The English manual no longer matches the Dutch one; nothing was written.\n"
            "Update the chapter in both languages, then build again:\n  "
            + "\n  ".join(problems),
            file=sys.stderr,
        )
        return 1
    page = build_page()
    with open(OUT, "w", encoding="utf-8") as handle:
        handle.write(page)
    print("written:", OUT)
    print("size:", round(len(page) / 1024, 1), "KB")
    print("sections:", page.count("<section"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
