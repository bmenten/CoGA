# Bouwt docs/handleiding/coga-handleiding.html uit de Markdown-hoofdstukken.
# Vereist het Python-pakket "markdown": pip install markdown
# Draai:  python docs/handleiding/build_site.py
#!/usr/bin/env python
import os, re, html as htmlmod
import markdown

DOCS = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(DOCS, "coga-handleiding.html")

CHAPTERS = [
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
]

def render_md(path):
    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "attr_list"])
    text = open(path, encoding="utf-8").read()
    out = md.convert(text)
    # cross-hoofdstuk links NN-...md(#..) -> #hs-N
    out = re.sub(r'href="(\d+)-[^"]*\.md(?:#[^"]*)?"', lambda m: f'href="#hs-{int(m.group(1))}"', out)
    # overige relatieve/externe .md- of ../-links -> ontlink (behoud tekst) zodat er geen dode links zijn
    def unlink(m):
        href, txt = m.group(1), m.group(2)
        if href.startswith("#") or href.startswith("http"):
            return m.group(0)
        return f'<span class="deadref">{txt}</span>'
    out = re.sub(r'<a href="([^"]+)">(.*?)</a>', unlink, out, flags=re.S)
    # brede blokken in horizontale scroll-container
    out = out.replace("<table>", '<div class="scroll"><table>').replace("</table>", "</table></div>")
    out = re.sub(r"<pre>", '<div class="scroll"><pre>', out)
    out = re.sub(r"</pre>", "</pre></div>", out)
    # "Waar in de code"-alinea als callout
    out = out.replace("<p><strong>Waar in de code", '<p class="waar"><strong>Waar in de code')
    return out

intro_html = render_md(os.path.join(DOCS, "README.md"))
# in de intro de eigen "Inhoudstabel"-tabel weglaten (de zijbalk + kaarten dekken dat) -> laat staan, is nuttig.

sections = [f'<section id="top" class="intro">\n{intro_html}\n</section>']
navitems = ['<a class="nav-link" href="#top" data-target="top"><span class="n">·</span><span class="t">Introductie &amp; inhoudstabel</span></a>']
for num, fn, title in CHAPTERS:
    body = render_md(os.path.join(DOCS, fn))
    sections.append(f'<section id="hs-{num}" class="chapter">\n{body}\n</section>')
    navitems.append(
        f'<a class="nav-link" href="#hs-{num}" data-target="hs-{num}">'
        f'<span class="n">{num}</span><span class="t">{htmlmod.escape(title)}</span></a>'
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
.chip{display:inline-flex;align-items:center;gap:6px;margin-top:12px;padding:3px 9px;border:1px solid var(--border);
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

/* ---------- theme toggle / topbar ---------- */
.tbtn{appearance:none;border:1px solid var(--border);background:var(--surface-2);color:var(--ink);
  border-radius:8px;padding:6px 10px;font-size:.8rem;cursor:pointer;display:inline-flex;gap:6px;align-items:center}
.tbtn:hover{border-color:var(--accent)}
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
  .topbar{display:flex;position:sticky;top:0;z-index:40;align-items:center;gap:12px;
    padding:10px 14px;background:color-mix(in oklab,var(--surface) 88%, transparent);
    backdrop-filter:saturate(1.2) blur(8px);border-bottom:1px solid var(--border)}
  .topbar .title{font-family:var(--font-head);font-weight:600;font-size:1rem;flex:1;min-width:0;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .menu{appearance:none;border:1px solid var(--border);background:var(--surface-2);border-radius:8px;
    width:38px;height:34px;display:grid;place-items:center;cursor:pointer;color:var(--ink)}
  .sidebar{position:fixed;z-index:60;left:0;top:0;width:min(86vw,340px);transform:translateX(-100%);
    transition:transform .22s ease}
  body.nav-open .sidebar{transform:none}
  body.nav-open .scrim{display:block;position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.42)}
  .reading{padding:30px 20px 100px}
  .reading h1{font-size:1.7rem}
}
@media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  .sidebar{transition:none}
}

/* ---------- print (voor PDF-export door het board) ---------- */
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

JS = r"""
<script>
(function(){
  var root=document.documentElement, body=document.body;
  // thema-toggle: zet expliciet data-theme (overschrijft prefers-color-scheme in beide richtingen)
  function effective(){
    var dt=root.getAttribute('data-theme');
    if(dt) return dt;
    return window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light';
  }
  function applyLabel(){
    var d=effective()==='dark';
    document.querySelectorAll('[data-theme-toggle]').forEach(function(b){
      b.innerHTML = d ? '☀︎ Licht' : '☾ Donker';
    });
  }
  try{var saved=localStorage.getItem('coga-theme'); if(saved) root.setAttribute('data-theme',saved);}catch(e){}
  applyLabel();
  document.addEventListener('click',function(e){
    var t=e.target.closest('[data-theme-toggle]');
    if(t){ var next=effective()==='dark'?'light':'dark'; root.setAttribute('data-theme',next);
      try{localStorage.setItem('coga-theme',next);}catch(e){} applyLabel(); return; }
    var m=e.target.closest('[data-menu]'); if(m){ body.classList.toggle('nav-open'); return; }
    if(e.target.closest('.scrim')){ body.classList.remove('nav-open'); return; }
    var lnk=e.target.closest('.nav-link'); if(lnk){ body.classList.remove('nav-open'); }
  });
  // scrollspy
  var links=[].slice.call(document.querySelectorAll('.nav-link'));
  var map={}; links.forEach(function(l){map[l.getAttribute('data-target')]=l;});
  var sections=[].slice.call(document.querySelectorAll('section[id]'));
  function setActive(id){ links.forEach(function(l){l.classList.remove('active');});
    if(map[id]){ map[id].classList.add('active');
      var nav=map[id].closest('.navwrap');
      var r=map[id].getBoundingClientRect(), nr=nav.getBoundingClientRect();
      if(r.top<nr.top||r.bottom>nr.bottom){ map[id].scrollIntoView({block:'nearest'}); } } }
  var io=new IntersectionObserver(function(entries){
    entries.forEach(function(en){ if(en.isIntersecting){ setActive(en.target.id); } });
  },{rootMargin:'-15% 0px -70% 0px',threshold:0});
  sections.forEach(function(s){io.observe(s);});
})();
</script>
"""

title = "CoGA — Codebase-handleiding"
topbar = ('<div class="topbar">'
          '<button class="menu" data-menu aria-label="Menu">☰</button>'
          '<span class="title">CoGA — Codebase-handleiding</span>'
          '<button class="tbtn" data-theme-toggle aria-label="Wissel thema">☾ Donker</button>'
          '</div>')

sidebar = f"""
<aside class="sidebar" id="sidebar">
  <div class="brand">
    <div class="logo"><span class="mark" aria-hidden="true">🧬</span>
      <h1>CoGA<br>Codebase-handleiding</h1></div>
    <div class="sub">Volledige technische uitleg voor het review board — explainability, traceability &amp; veiligheid.</div>
    <span class="chip">versie <b>0.1.0</b> · commit <b>6641228</b></span>
  </div>
  <nav class="navwrap" aria-label="Hoofdstukken">
    <div class="nav-eyebrow">Inhoud</div>
    {''.join(navitems)}
  </nav>
  <div class="sidebar-foot">
    <span class="ver">15 hoofdstukken</span>
    <button class="tbtn" data-theme-toggle aria-label="Wissel thema">☾ Donker</button>
  </div>
</aside>
"""

page = f"""<title>CoGA — Codebase-handleiding</title>
{CSS}
{topbar}
<div class="scrim" aria-hidden="true"></div>
<div class="app">
{sidebar}
<main>
  <article class="reading">
    {''.join(sections)}
  </article>
</main>
</div>
{JS}
"""

open(OUT, "w", encoding="utf-8").write(page)
print("geschreven:", OUT)
print("grootte:", round(len(page)/1024,1), "KB")
print("secties:", page.count('<section'))
