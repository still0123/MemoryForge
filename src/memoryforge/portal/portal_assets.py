"""Same-origin assets for the local dynamic portal."""

# ruff: noqa: E501

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MemoryForge 本地知识门户</title>
<link rel="stylesheet" href="/app.css">
<script src="/app.js" defer></script>
</head>
<body>
<header class="topbar">
  <a class="brand" href="#home"><span class="brand-mark">MF</span>
    <span>MemoryForge<small>本地知识门户</small></span></a>
  <label class="search"><span class="sr-only">全局搜索</span>
    <svg class="search-icon" viewBox="0 0 16 16" aria-hidden="true"><circle cx="6.8" cy="6.8" r="4.6"/><path d="M10.4 10.4 14 14"/></svg>
    <input id="global-search" type="search" placeholder="搜索标题、路径或正文…">
    <kbd>⌘K</kbd></label>
  <button id="menu-button" class="tool" type="button" aria-expanded="false">导航</button>
</header>
<div class="shell">
  <nav id="primary-nav" class="primary-nav" aria-label="一级导航">
    <div class="nav-links">
      <a href="#home"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 6.7 8 2.4l5.5 4.3v6.1a.8.8 0 0 1-.8.8H9.4v-4h-2.8v4H3.3a.8.8 0 0 1-.8-.8Z"/></svg>首页</a>
      <a href="#knowledge"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 2.2h7.2L13 5v8.8a.6.6 0 0 1-.6.6H3a.6.6 0 0 1-.6-.6V2.8a.6.6 0 0 1 .6-.6Z"/><path d="M5.2 7.4h5.2M5.2 10h3.4" class="stroke"/></svg>我的知识</a>
      <a href="#add-source"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.6v10.8M2.6 8h10.8"/></svg>添加来源</a>
      <a href="#updates"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.3a5.7 5.7 0 1 1-5.4 7.5"/><path d="m2.3 6.5.3 3.3 3.2-.9" class="stroke"/></svg>知识更新</a>
      <a href="#jobs"><svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.4" y="3" width="11.2" height="10.6" rx="1.4"/><path d="M5.4 1.8v2.6M10.6 1.8v2.6M2.4 6.6h11.2" class="stroke"/></svg>后台任务</a>
      <a href="#system"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.8a6.2 6.2 0 0 1 6.2 6.2A6.2 6.2 0 0 1 8 14.2 6.2 6.2 0 0 1 1.8 8 6.2 6.2 0 0 1 8 1.8Z"/><circle cx="8" cy="8" r="1.9"/><path d="m10.6 5.4-1.4 1.4" class="stroke"/></svg>系统状态</a>
    </div>
    <div class="nav-foot"><strong>Local-first</strong>资料默认留在本机，发布前必须人工审核。</div>
  </nav>
  <main id="app" tabindex="-1" aria-live="polite">
    <p class="empty">正在读取知识库…</p>
  </main>
</div>
</body>
</html>
"""

APP_CSS = """
:root{color-scheme:light;--bg:#f5f8ff;--panel:#fff;--soft:#edf4ff;--text:#1f2329;
--muted:#69758c;--line:#d9e4f5;--accent:#1664ff;--accent-strong:#0b55e8;
--accent-soft:#e8f1ff;--link:#1664ff;--warn:#a65f00;--danger:#d93026;--code:#f5f8ff;
--mint:#18a980;--mint-soft:#e2f6ef;--amber:#b45309;--amber-soft:#fdf1e3;
--shadow:0 1px 2px rgba(22,100,255,.04),0 10px 34px rgba(22,100,255,.07);
--shadow-hover:0 2px 5px rgba(22,100,255,.08),0 18px 42px rgba(22,100,255,.13)}
*{box-sizing:border-box}[hidden]{display:none!important}html{scroll-behavior:smooth}body{margin:0;
background:radial-gradient(circle at 78% -10%,color-mix(in srgb,var(--accent-soft) 55%,transparent),transparent 31rem),var(--bg);
color:var(--text);font:15px/1.65 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
font-synthesis:none;-webkit-font-smoothing:antialiased}
::selection{background:color-mix(in srgb,var(--accent) 20%,transparent)}
*{scrollbar-width:thin;scrollbar-color:color-mix(in srgb,var(--accent) 28%,transparent) transparent}
*::-webkit-scrollbar{width:8px;height:8px}
*::-webkit-scrollbar-thumb{background:color-mix(in srgb,var(--accent) 26%,transparent);border-radius:8px}
*::-webkit-scrollbar-track{background:transparent}
button,input,select,textarea{font:inherit;color:inherit}a{color:var(--link)}
button{cursor:pointer}a,button,input,select,textarea{transition:border-color .16s ease,box-shadow .16s ease,
background-color .16s ease,color .16s ease,transform .16s ease}
:focus-visible{outline:2px solid color-mix(in srgb,var(--accent) 60%,transparent);outline-offset:2px}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
clip:rect(0,0,0,0);white-space:nowrap;border:0}
.topbar{position:sticky;top:0;z-index:20;display:grid;grid-template-columns:230px minmax(260px,680px) auto;
align-items:center;gap:24px;min-height:68px;padding:10px 24px;background:color-mix(in srgb,var(--bg) 82%,transparent);
border-bottom:1px solid color-mix(in srgb,var(--line) 78%,transparent);backdrop-filter:blur(20px) saturate(1.25)}
.brand{display:flex;align-items:center;gap:11px;color:var(--text);font-weight:760;text-decoration:none;
letter-spacing:-.015em}.brand-mark{display:grid;width:38px;height:38px;place-items:center;color:#fff;
background:linear-gradient(145deg,var(--accent),var(--accent-strong));border-radius:11px;font-size:11px;
letter-spacing:.08em;box-shadow:0 7px 18px color-mix(in srgb,var(--accent) 25%,transparent)}
.brand small{display:block;margin-top:-2px;color:var(--muted);font-size:9px;font-weight:650;
letter-spacing:.1em;text-transform:uppercase}
.search{display:flex;align-items:center;gap:8px;height:42px;padding:0 12px;background:color-mix(in srgb,var(--panel) 92%,transparent);
border:1px solid var(--line);border-radius:11px;box-shadow:0 1px 2px rgba(0,0,0,.025)}
.search-icon{flex:none;width:15px;height:15px;fill:none;stroke:var(--muted);stroke-width:1.7;
stroke-linecap:round;stroke-linejoin:round}
.search:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 14%,transparent)}
.search:focus-within .search-icon{stroke:var(--accent)}
.search input{flex:1;min-width:0;background:transparent;border:0;outline:0}
kbd{padding:2px 6px;color:var(--muted);background:var(--soft);border:1px solid var(--line);
border-radius:5px;font-size:11px}.tool{display:none;padding:7px 10px;background:var(--panel);
border:1px solid var(--line);border-radius:8px}
.shell{display:grid;grid-template-columns:224px minmax(0,1080px);gap:40px;max-width:1420px;margin:auto;padding:0 28px}
.primary-nav{position:sticky;top:68px;align-self:start;height:calc(100vh - 68px);display:flex;
flex-direction:column;padding:26px 14px 22px 0;border-right:1px solid var(--line)}
.primary-nav .nav-links{display:grid;gap:2px}
.primary-nav a{display:flex;align-items:center;gap:11px;margin:2px 0;padding:9px 12px;color:var(--muted);
border-radius:10px;text-decoration:none;font-weight:570;font-size:14px}
.primary-nav svg{flex:none;width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.55;
stroke-linecap:round;stroke-linejoin:round;opacity:.9}
.primary-nav a:hover,.primary-nav a:focus-visible{color:var(--accent);background:var(--accent-soft);outline:0}
.primary-nav a[aria-current=page]{color:var(--accent-strong);background:var(--accent-soft);
font-weight:700;box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--accent) 16%,transparent)}
.primary-nav a[aria-current=page] svg{color:var(--accent);filter:drop-shadow(0 2px 5px color-mix(in srgb,var(--accent) 30%,transparent))}
.nav-foot{margin-top:auto;padding:14px 12px 0;color:var(--muted);font-size:11px;line-height:1.5;
border-top:1px dashed var(--line)}
.nav-foot strong{display:block;color:var(--mint);font-size:10px;letter-spacing:.12em;text-transform:uppercase}
#app{min-width:0;padding:44px 0 96px;outline:0}.page-head{margin-bottom:28px}.eyebrow{display:inline-flex;
align-items:center;gap:7px;color:var(--accent);font-size:10px;font-weight:800;letter-spacing:.13em;
text-transform:uppercase}.eyebrow::before{content:"";width:17px;height:2px;background:var(--accent);border-radius:2px}
.page-head h1{margin:7px 0 9px;font-size:36px;line-height:1.15;letter-spacing:-.045em}
.page-head p{max-width:720px;margin:0;color:var(--muted);font-size:15px}
.hero{position:relative;overflow:hidden;margin-bottom:22px;padding:34px 36px 30px;
background:linear-gradient(132deg,color-mix(in srgb,var(--accent-soft) 88%,var(--panel)),var(--panel) 63%);
border:1px solid color-mix(in srgb,var(--accent) 20%,var(--line));border-radius:20px;box-shadow:var(--shadow)}
.hero::before{content:"";position:absolute;right:-70px;top:-90px;width:290px;height:290px;border-radius:50%;
background:radial-gradient(circle,color-mix(in srgb,var(--accent) 12%,transparent),transparent 68%)}
.hero::after{content:"MF";position:absolute;right:27px;bottom:-31px;color:color-mix(in srgb,var(--accent) 7%,transparent);
font-size:122px;font-weight:900;letter-spacing:-.09em;line-height:1}
.hero .page-head{position:relative;z-index:1;margin-bottom:20px}
.hero .page-head h1{font-size:40px}.hero .actions-row{position:relative;z-index:1;margin:0}
.hero-chips{position:relative;z-index:1;display:flex;flex-wrap:wrap;gap:8px;margin:0 0 22px}
.hero-chip{display:inline-flex;align-items:center;gap:7px;padding:5px 12px;color:var(--accent-strong);
background:color-mix(in srgb,var(--panel) 88%,transparent);border:1px solid color-mix(in srgb,var(--accent) 18%,var(--line));
border-radius:999px;font-size:12px;font-weight:650}
.hero-chip::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--mint);
box-shadow:0 0 0 3px color-mix(in srgb,var(--mint) 20%,transparent)}
.hero-chip.mint{color:#0e7d5f}.hero-chip.amber{color:var(--amber)}.hero-chip.amber::before{background:#e5a13c;
box-shadow:0 0 0 3px color-mix(in srgb,#e5a13c 22%,transparent)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:15px}
.card{position:relative;min-width:0;padding:18px;background:color-mix(in srgb,var(--panel) 96%,transparent);
border:1px solid var(--line);border-radius:14px;box-shadow:0 1px 2px rgba(22,100,255,.03)}
a.card{display:block;color:var(--text);text-decoration:none}
a.card::before{content:"";position:absolute;left:-1px;top:14px;bottom:14px;width:3px;border-radius:3px;
background:var(--accent);opacity:0;transition:opacity .16s ease}
a.card:hover{border-color:color-mix(in srgb,var(--accent) 48%,var(--line));
box-shadow:var(--shadow-hover);transform:translateY(-2px)}
a.card:hover::before{opacity:1}
a.project-card::before{background:var(--mint)}a.update-card::before{background:#e5a13c}
a.job-card::before{background:#8e97a8}
.update-card{cursor:pointer}.card-action{display:block;margin-top:12px;
color:var(--accent);font-weight:700}.card h2,.card h3{margin:0 0 6px;
font-size:16px;letter-spacing:-.015em}.card p{margin:5px 0;color:var(--muted)}
.metric-card{position:relative;overflow:hidden;padding:20px 20px 18px}
.metric-card::before{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:var(--accent)}
.metric-label{display:block;color:var(--muted);font-size:11px;font-weight:650;letter-spacing:.02em}
.metric{display:block;margin-top:6px;font-size:29px;font-weight:780;letter-spacing:-.04em;
font-variant-numeric:tabular-nums;color:var(--text)}
a.metric-card .metric{color:var(--accent-strong)}
.page-card{display:flex!important;min-height:150px;flex-direction:column}.page-card p{flex:1;margin:8px 0 16px}
.module-tree{display:grid;gap:8px}.module-node{margin:0}.module-node summary{color:var(--text);background:var(--soft)}
.module-children{display:grid;gap:6px;padding:8px 12px 12px}.module-link{display:block;padding:10px 12px;
color:var(--text);background:var(--panel);border:1px solid var(--line);border-radius:9px;text-decoration:none}
.module-link:hover{border-color:var(--accent);background:var(--accent-soft)}.module-link strong{display:block}
.module-link small{display:block;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card-top,.card-footer{display:flex;align-items:center;justify-content:space-between;gap:12px}
.card-top h3{margin:0}.kind-chip{flex:none;padding:3px 9px;color:var(--accent);background:var(--accent-soft);
border:1px solid color-mix(in srgb,var(--accent) 14%,transparent);border-radius:999px;font-size:10px;
font-weight:750}.open-page{flex:none;color:var(--accent);font-size:12px;
font-weight:750}.page-card:hover .open-page{color:var(--accent-strong);transform:translateX(2px)}
.meta{color:var(--muted);font-size:12px}.section{margin-top:40px}
.section-title{display:flex;align-items:end;justify-content:space-between;gap:18px;margin-bottom:14px;
padding-bottom:10px;border-bottom:1px solid color-mix(in srgb,var(--line) 70%,transparent)}
.section-title h2{margin:0;font-size:20px;letter-spacing:-.025em}.section-title p{margin:0;color:var(--muted);font-size:12px}
.warning{padding:12px 14px;color:var(--warn);background:color-mix(in srgb,var(--warn) 10%,var(--panel));
border:1px solid color-mix(in srgb,var(--warn) 30%,var(--line));border-radius:10px}
.empty{padding:38px 24px;color:var(--muted);background:color-mix(in srgb,var(--panel) 70%,transparent);
border:1.5px dashed color-mix(in srgb,var(--accent) 26%,var(--line));border-radius:14px;text-align:center}
.empty::before{display:block;margin-bottom:8px;color:color-mix(in srgb,var(--accent) 40%,transparent);
font-size:22px;content:"◇"}
.badge{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;color:var(--accent);background:var(--accent-soft);
border:1px solid color-mix(in srgb,var(--accent) 14%,transparent);border-radius:999px;font-size:11px;font-weight:650}
.badge::before{content:"";width:5px;height:5px;border-radius:50%;background:currentColor;opacity:.75}
.badge.warn{color:var(--warn);background:color-mix(in srgb,var(--warn) 12%,var(--panel));
border-color:color-mix(in srgb,var(--warn) 26%,transparent)}
.breadcrumbs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:15px;color:var(--muted);font-size:12px}
.breadcrumbs a{color:var(--muted);text-decoration:none}.breadcrumbs a:hover{color:var(--accent)}
.breadcrumbs a:not(:last-child)::after{content:"/";margin-left:6px;color:var(--line)}
.reader-layout{display:grid;grid-template-columns:minmax(0,780px) 240px;gap:45px;align-items:start}
.reader-title{margin:8px 0 10px;font-size:36px;line-height:1.18;letter-spacing:-.035em}
.summary{margin:18px 0 26px;padding:14px 18px;color:#27313f;background:linear-gradient(120deg,var(--accent-soft),color-mix(in srgb,var(--mint-soft) 40%,var(--accent-soft)));
border-left:3px solid var(--accent);border-radius:0 10px 10px 0;font-size:15px}
.page-path{display:block;margin:8px 0;padding:4px 10px;color:var(--muted);font-size:11px;
background:var(--soft);border-radius:6px;width:fit-content;max-width:100%;overflow-wrap:anywhere}
.markdown{font-size:16px;line-height:1.8}.markdown>h1{display:none}.markdown h2,.markdown h3,.markdown h4{
scroll-margin-top:82px;line-height:1.35}.markdown h2{margin:1.7em 0 .55em;padding-bottom:7px;
border-bottom:1px solid var(--line);font-size:23px}.markdown h3{margin:1.5em 0 .5em;font-size:19px}
.markdown pre{padding:14px 16px;background:var(--code);border:1px solid var(--line);
border-radius:10px;overflow:auto;font-size:13.5px}
.markdown :not(pre)>code{padding:2px 6px;background:var(--code);border:1px solid var(--line);
border-radius:5px;font-size:.88em}code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.markdown blockquote{margin-left:0;padding:10px 14px;color:var(--muted);background:var(--soft);
border-left:3px solid var(--accent);border-radius:0 8px 8px 0}
.markdown table{border-collapse:collapse;width:100%;margin:1.2em 0;font-size:14px}
.markdown th{text-align:left;padding:8px 12px;color:var(--accent-strong);background:var(--accent-soft);
border:1px solid var(--line);font-size:13px;letter-spacing:.02em}
.markdown td{padding:8px 12px;border:1px solid var(--line)}
.markdown tr:nth-child(2n) td{background:color-mix(in srgb,var(--soft) 45%,transparent)}
.markdown img{max-width:100%;border-radius:10px}
.markdown hr{border:0;border-top:1px solid var(--line);margin:2em 0}
.rail{position:sticky;top:88px;max-height:calc(100vh - 112px);overflow:auto;padding-left:16px;
border-left:2px solid var(--line)}
.rail h2{margin:0 0 10px;color:var(--muted);font-size:11px;letter-spacing:.09em;text-transform:uppercase}
.rail a{display:block;margin:7px 0;padding-left:10px;color:var(--muted);font-size:12px;line-height:1.45;
text-decoration:none;border-left:2px solid transparent}
.rail a:hover{color:var(--accent);border-left-color:var(--accent)}
.rail .depth-3{padding-left:22px}
details{margin-top:12px;background:var(--panel);
border:1px solid var(--line);border-radius:12px;box-shadow:0 1px 2px rgba(22,100,255,.03)}
details[open]{border-color:color-mix(in srgb,var(--accent) 22%,var(--line));box-shadow:var(--shadow)}
summary{padding:12px 15px;cursor:pointer;font-weight:620}summary:hover{color:var(--accent)}
summary::marker{color:var(--accent)}
.detail-body{padding:0 15px 14px}.relation{display:block;margin:6px 0;padding:10px 12px;
color:var(--text);background:var(--soft);border-radius:8px;text-decoration:none}
.relation:hover{background:var(--accent-soft)}
.relation small{display:block;color:var(--muted)}.related-groups{display:grid;gap:12px}
.relation-group{position:relative;padding:16px;overflow:hidden;background:var(--panel);
border:1px solid var(--line);border-radius:12px}
.relation-group::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;
background:linear-gradient(var(--accent),var(--mint))}
.relation-group h3{margin:0 0 2px;font-size:14px}.relation-group p{margin:0}
.relation-list{display:grid;gap:6px;margin-top:10px}
.pagination{display:flex;gap:8px;margin-top:18px}.pagination button{padding:8px 14px;
background:var(--panel);border:1px solid var(--line);border-radius:8px;font-weight:600}
.pagination button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
.pagination button:disabled{opacity:.45;cursor:default}
.source-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;
padding:12px 0;border-bottom:1px solid color-mix(in srgb,var(--line) 60%,transparent)}
.source-row:last-child{border:0}
.actions-row{display:flex;flex-wrap:wrap;gap:10px;margin:20px 0}.review-actions{align-items:center;margin-top:0}
.review-actions .meta{flex:1 1 100%}
.button{display:inline-flex;align-items:center;justify-content:center;padding:9px 17px;color:var(--text);
background:var(--panel);border:1px solid var(--line);border-radius:10px;text-decoration:none;font-weight:650}
.button:hover{border-color:var(--accent);background:var(--accent-soft);transform:translateY(-1px)}
.button.primary{color:#fff;background:linear-gradient(145deg,var(--accent),var(--accent-strong));
border-color:var(--accent);box-shadow:0 7px 18px color-mix(in srgb,var(--accent) 22%,transparent)}
.button.primary:hover{color:#fff;background:var(--accent-strong);box-shadow:0 9px 22px color-mix(in srgb,var(--accent) 30%,transparent)}
.button.danger{color:var(--danger);border-color:color-mix(in srgb,var(--danger) 40%,var(--line))}
.button.danger:hover{background:color-mix(in srgb,var(--danger) 8%,var(--panel))}
.button:disabled{opacity:.45;transform:none}
.form{display:grid;gap:15px;max-width:760px;padding:24px;background:var(--panel);
border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}.form label{display:grid;gap:6px;
font-size:13px;font-weight:650}.form input,.form select,.form textarea{width:100%;padding:10px 12px;
background:var(--bg);border:1px solid var(--line);border-radius:10px;outline:0}
.form input:focus,.form select:focus,.form textarea:focus{border-color:var(--accent);
box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 13%,transparent)}
.form textarea{min-height:100px;resize:vertical}
.checkbox{display:flex!important;grid-template-columns:auto 1fr;align-items:center;font-weight:400!important}
.checkbox input{width:auto}.job-progress{width:100%;height:8px;accent-color:var(--accent)}
.diff{max-height:420px;padding:13px 15px;background:var(--code);border:1px solid var(--line);
border-radius:10px;overflow:auto;white-space:pre-wrap;line-height:1.55;font-size:13px}.error{color:var(--danger)}
.choice-list{display:grid;gap:8px}.choice{display:flex;justify-content:space-between;gap:12px;
padding:11px 13px;background:var(--soft);border:1px solid transparent;border-radius:9px;text-align:left}
.choice:hover{border-color:var(--accent);background:var(--accent-soft)}
@media(max-width:960px){.reader-layout{grid-template-columns:minmax(0,1fr)}.rail{position:static;
max-height:none;order:2}.shell{gap:28px}}
@media(max-width:720px){.topbar{grid-template-columns:auto minmax(0,1fr) auto;gap:9px;padding:8px 12px}
.brand>span:last-child,kbd{display:none}.tool{display:block}.shell{display:block;padding:0 16px}
.primary-nav{display:none}.nav-open .primary-nav{position:fixed;z-index:30;inset:68px 0 0;display:flex;
width:100%;height:auto;padding:18px;background:var(--bg);border:0}.nav-open{overflow:hidden}
#app{padding-top:28px}.page-head h1,.reader-title{font-size:29px}.hero{padding:23px 20px}
.hero .page-head h1{font-size:31px}.grid{grid-template-columns:1fr}}
"""

APP_JS = r"""
const app=document.getElementById("app");
const search=document.getElementById("global-search");
const menu=document.getElementById("menu-button");
let requestController=null;
let searchTimer=null;

function element(tag,attrs={},children=[]){
  const node=document.createElement(tag);
  for(const [key,value] of Object.entries(attrs)){
    if(value===null||value===undefined)continue;
    if(key==="class")node.className=value;
    else if(key==="text")node.textContent=String(value);
    else node.setAttribute(key,String(value));
  }
  for(const child of Array.isArray(children)?children:[children]){
    if(child instanceof Node)node.append(child);
    else if(child!==null&&child!==undefined)node.append(document.createTextNode(String(child)));
  }
  return node
}
function heading(kicker,title,description,className=""){
  return element("header",{class:"page-head "+className},[
    element("span",{class:"eyebrow",text:kicker}),
    element("h1",{text:title}),
    element("p",{text:description})
  ])
}
async function api(path,signal){
  const response=await fetch(path,{signal});
  if(!response.ok)throw new Error("request failed");
  return response.json()
}
function show(nodes,focus=true){
  app.replaceChildren(...(Array.isArray(nodes)?nodes:[nodes]));
  if(focus)app.focus({preventScroll:true});
  document.body.classList.remove("nav-open");
  menu.setAttribute("aria-expanded","false")
}
function fail(){
  show(element("p",{class:"empty",text:"知识库暂时不可用，请检查 Workspace 状态。"}))
}
function metric(label,value,route){
  const content=[
    element("span",{class:"meta",text:label}),
    element("strong",{class:"metric",text:value})
  ];
  return route?element("a",{class:"card",href:route},content):element("article",{class:"card"},content)
}
function pageCard(page){
  const meta=[page.kind,page.updated,page.status].filter(Boolean).join(" · ");
  return element("a",{class:"card",href:"#page="+encodeURIComponent(page.path)},[
    element("h3",{text:page.title}),
    element("p",{text:page.summary||"暂无摘要"}),
    element("small",{class:"meta",text:meta})
  ])
}
function section(title,description,content){
  return element("section",{class:"section"},[
    element("div",{class:"section-title"},[
      element("h2",{text:title}),element("p",{text:description})
    ]),content
  ])
}
function empty(text){return element("p",{class:"empty",text})}
function recentPaths(){
  try{
    const value=JSON.parse(localStorage.getItem("memoryforge-recent-pages")||"[]");
    return Array.isArray(value)?value.filter(item=>typeof item==="string").slice(0,8):[]
  }catch(_){return[]}
}
function rememberPage(path){
  try{
    const paths=[path,...recentPaths().filter(item=>item!==path)].slice(0,8);
    localStorage.setItem("memoryforge-recent-pages",JSON.stringify(paths))
  }catch(_){}
}
function recentGrid(){
  const paths=recentPaths();
  if(!paths.length)return empty("还没有最近打开的页面。");
  return element("div",{class:"grid"},paths.map(path=>
    element("a",{class:"card",href:"#page="+encodeURIComponent(path)},[
      element("h3",{text:path.split("/").pop().replace(/\.md$/,"")}),
      element("small",{class:"meta",text:path})
    ])
  ))
}
function projectCard(project){
  return element("a",{class:"card",href:"#project="+encodeURIComponent(project.repository_id)},[
    element("h3",{text:project.name}),
    element("p",{text:`${project.knowledge_page_count??project.page_count} 个知识页 · ${project.evidence_file_count??0} 个源码证据`}),
    element("small",{class:"meta",text:
      `${project.languages.join(" / ")||"语言未识别"} · Commit ${project.commit||"未同步"}`})
  ])
}
async function renderHome(){
  const [summary,projects]=await Promise.all([api("/api/summary"),api("/api/projects")]);
  const metrics=[
    metric("已应用页面",summary.applied_page_count,null),
    summary.projects?metric("项目",summary.projects,"#projects"):null,
    summary.code_pages?metric("代码知识",summary.code_pages,"#sources=code"):null,
    summary.conversation_pages?metric("AI 会话",summary.conversation_pages,"#sources=conversation"):null,
    summary.feishu_pages?metric("飞书资料",summary.feishu_pages,"#sources=feishu"):null,
    summary.note_pages?metric("其他知识",summary.note_pages,"#sources=note"):null
  ].filter(Boolean);
  const nodes=[
    heading("知识入口","你的本地知识库","先找项目和真实数据源，再按需阅读页面与依据。"),
    element("div",{class:"grid"},metrics),
    section("最近打开","仅在当前浏览器保存页面路径",recentGrid())
  ];
  if(projects.items.length){
    nodes.push(section("项目","按 Git repository 整理",
      element("div",{class:"grid"},projects.items.map(projectCard))))
  }
  if(summary.pending_sources){
    nodes.push(element("p",{class:"warning",text:`有 ${summary.pending_sources} 个当前来源尚未应用。`}))
  }
  show(nodes)
}
async function renderProjects(){
  const data=await api("/api/projects");
  show([
    heading("项目","项目目录","每个项目对应一个已登记的 Git repository。"),
    data.items.length?element("div",{class:"grid"},data.items.map(projectCard)):empty("还没有已应用的项目知识。")
  ])
}
function pageGrid(items,message){
  return items.length?element("div",{class:"grid"},items.map(pageCard)):empty(message)
}
async function renderProject(id){
  const data=await api("/api/project?id="+encodeURIComponent(id));
  const nodes=[
    heading("项目",data.name,
      `${data.page_count} 页 · ${data.module_count} 个模块 · Commit ${data.commit||"未同步"}`),
    element("div",{class:"grid"},[
      metric("页面",data.page_count,null),metric("模块",data.module_count,null),
      metric("语言",data.languages.join(" / ")||"未识别",null)
    ])
  ];
  nodes.push(section("项目概览","项目入口与同步信息",pageGrid(data.overview,"还没有项目概览。")));
  nodes.push(section("模块","确定性代码模块",pageGrid(data.modules,"还没有已应用的模块页。")));
  nodes.push(section("代码文件","固定 Commit 的代码知识",pageGrid(data.files,"还没有已应用的代码文件页。")));
  nodes.push(section("相关资料","精确提及该项目的飞书资料、AI 会话和笔记",
    pageGrid(data.related,"还没有确定性相关资料。")));
  show(nodes)
}
const sourceNames={code:"代码知识",conversation:"AI 会话",feishu:"飞书资料",note:"其他知识"};
async function renderSources(kind,offset=0){
  const limit=50;
  const [sources,pages]=await Promise.all([
    api(`/api/sources?kind=${encodeURIComponent(kind)}&offset=${offset}&limit=${limit}`),
    api(`/api/pages?view=published&kind=${encodeURIComponent(kind)}&offset=${offset}&limit=${limit}`)
  ]);
  const sourceList=sources.items.length?element("div",{class:"card"},sources.items.map(item=>
    element("div",{class:"source-row"},[
      element("div",{},[element("strong",{text:item.name}),
        element("div",{class:"meta",text:`${item.updated} · ${item.page_count} 页`})]),
      element("span",{class:item.status==="待应用"?"badge warn":"badge",text:item.status})
    ])
  )):empty(`还没有${sourceNames[kind]||"此类"}来源。`);
  show([
    heading("数据源",sourceNames[kind]||"知识来源","只显示当前版本和已应用页面，不混入历史来源总数。"),
    section("当前来源",`${sources.total} 个`,sourceList),
    section("已应用页面",`${pages.total} 页`,pageGrid(pages.items,"还没有已应用页面。")),
    pagination(offset,limit,Math.max(sources.total,pages.total),next=>renderSources(kind,next))
  ])
}
function pagination(offset,limit,total,onPage){
  const previous=element("button",{type:"button",text:"上一页"});
  const next=element("button",{type:"button",text:"下一页"});
  previous.disabled=offset===0;next.disabled=offset+limit>=total;
  previous.addEventListener("click",()=>onPage(Math.max(0,offset-limit)));
  next.addEventListener("click",()=>onPage(offset+limit));
  return element("div",{class:"pagination"},[previous,next])
}
function breadcrumbs(items){
  return element("nav",{class:"breadcrumbs","aria-label":"面包屑"},items.map(item=>
    element("a",{href:item.route.startsWith("#page=")?
      "#page="+encodeURIComponent(item.route.slice(6)):item.route,text:item.label})
  ))
}
function relationGroup(title,items){
  if(!items.length)return null;
  return element("details",{},[
    element("summary",{text:`${title} · ${items.length}`}),
    element("div",{class:"detail-body"},items.map(item=>
      element("a",{class:"relation",href:"#page="+encodeURIComponent(item.path)},[
        element("strong",{text:item.title}),
        element("small",{text:`${item.relationship} · ${item.detail}`})
      ])
    ))
  ])
}
function sourceDetails(items){
  if(!items.length)return null;
  return element("details",{},[
    element("summary",{text:`来源详情 · ${items.length}`}),
    element("div",{class:"detail-body"},items.map(item=>
      element("div",{class:"source-row"},[
        element("span",{text:item.name}),
        element("small",{class:"meta",text:`${item.status} · 版本 ${item.current_version}`})
      ])
    ))
  ])
}
async function renderPage(path){
  const data=await api("/api/page?path="+encodeURIComponent(path));
  rememberPage(path);
  const body=element("div",{class:"markdown"});
  body.innerHTML=data.html;
  const content=element("article",{},[
    breadcrumbs(data.breadcrumbs),
    element("span",{class:data.status==="未验证会话记忆"?"badge warn":"badge",text:data.status}),
    element("h1",{class:"reader-title",text:data.title}),
    element("code",{class:"page-path",text:data.path}),
    data.summary?element("p",{class:"summary",text:data.summary}):null,
    body,sourceDetails(data.sources)
  ].filter(Boolean));
  const railItems=[
    element("h2",{text:"本页目录"}),
    ...data.structure.filter(item=>item.level<=3).map((item,index)=>{
      const link=element("a",{href:"#section-"+index,text:item.title,
        class:item.level===3?"depth-3":""});
      return link
    }),
    relationGroup("直接关联",data.related.direct),
    relationGroup("精确提及",data.related.exact_mentions),
    relationGroup("同项目",data.related.same_project)
  ].filter(Boolean);
  show(element("div",{class:"reader-layout"},[content,element("aside",{class:"rail"},railItems)]));
  const headings=[...body.querySelectorAll("h2,h3")];
  headings.forEach((item,index)=>item.id="section-"+index)
}
async function renderSystem(){
  const data=await api("/api/summary");
  show([
    heading("只读诊断","系统状态","这里才展示历史来源、Workspace Commit 和待处理数量。"),
    element("div",{class:"grid"},[
      metric("当前来源",data.current_source_count,null),
      metric("历史 Source identity",data.source_identity_count,null),
      metric("已应用页面",data.applied_page_count,null),
      metric("待应用来源",data.pending_sources,null)
    ]),
    section("Workspace Commit","所有 Wiki 页面都从这个固定 Commit 读取",
      element("pre",{class:"card",text:data.workspace_commit}))
  ])
}
async function renderSearch(value,offset=0){
  if(requestController)requestController.abort();
  requestController=new AbortController();
  const limit=50;
  try{
    const data=await api(`/api/pages?view=published&q=${encodeURIComponent(value)}&offset=${offset}&limit=${limit}`,
      requestController.signal);
    show([
      heading("全局搜索",`“${value}”`,`${data.total} 个匹配页面`),
      pageGrid(data.items,"没有匹配的知识页面。"),
      pagination(offset,limit,data.total,next=>renderSearch(value,next))
    ],false)
  }catch(error){if(error.name!=="AbortError")fail()}
}
async function route(){
  const hash=location.hash||"#home";
  try{
    if(hash==="#home")await renderHome();
    else if(hash==="#projects")await renderProjects();
    else if(hash.startsWith("#project="))await renderProject(decodeURIComponent(hash.slice(9)));
    else if(hash.startsWith("#sources="))await renderSources(decodeURIComponent(hash.slice(9)));
    else if(hash.startsWith("#page="))await renderPage(decodeURIComponent(hash.slice(6)));
    else if(hash==="#system")await renderSystem();
    else location.hash="#home"
  }catch(error){if(error.name!=="AbortError")fail()}
}
search.addEventListener("input",event=>{
  clearTimeout(searchTimer);
  const value=event.target.value.trim();
  if(!value)return;
  searchTimer=setTimeout(()=>renderSearch(value),200)
});
search.addEventListener("keydown",event=>{
  if(event.key==="Enter"&&search.value.trim()){event.preventDefault();renderSearch(search.value.trim())}
});
document.addEventListener("keydown",event=>{
  if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="k"){
    event.preventDefault();search.focus()
  }
  if(event.key==="Escape"){
    document.body.classList.remove("nav-open");menu.setAttribute("aria-expanded","false")
  }
});
menu.addEventListener("click",()=>{
  const active=document.body.classList.toggle("nav-open");
  menu.setAttribute("aria-expanded",String(active))
});
window.addEventListener("hashchange",route);
route();
"""

CONTROL_JS = r"""
const app=document.getElementById("app");
const search=document.getElementById("global-search");
const menu=document.getElementById("menu-button");
let csrfToken="";
let requestController=null;
let searchTimer=null;
let jobTimer=null;

function element(tag,attrs={},children=[]){
  const node=document.createElement(tag);
  for(const [key,value] of Object.entries(attrs)){
    if(value===null||value===undefined)continue;
    if(key==="class")node.className=value;
    else if(key==="text")node.textContent=String(value);
    else node.setAttribute(key,String(value));
  }
  for(const child of Array.isArray(children)?children:[children]){
    if(child instanceof Node)node.append(child);
    else if(child!==null&&child!==undefined)node.append(document.createTextNode(String(child)));
  }
  return node
}
function heading(kicker,title,description){
  return element("header",{class:"page-head"},[
    element("span",{class:"eyebrow",text:kicker}),
    element("h1",{text:title}),
    element("p",{text:description})
  ])
}
async function api(path,{signal}={}){
  const response=await fetch(path,{signal});
  const data=await response.json();
  if(!response.ok)throw new Error(data.user_message||"请求失败");
  return data
}
async function post(path,payload){
  const response=await fetch(path,{
    method:"POST",
    headers:{
      "Content-Type":"application/json",
      "X-MemoryForge-CSRF":csrfToken
    },
    body:JSON.stringify(payload)
  });
  const data=await response.json();
  if(!response.ok)throw new Error(data.user_message||"操作失败");
  return data
}
async function postUpload(file,kind,publicValue,confirmPublic){
  const body=new FormData();
  body.append("kind",kind);body.append("file",file);
  body.append("public",String(publicValue));
  body.append("confirm_public",String(confirmPublic));
  const response=await fetch("/api/sources",{
    method:"POST",headers:{"X-MemoryForge-CSRF":csrfToken},body
  });
  const data=await response.json();
  if(!response.ok)throw new Error(data.user_message||"上传失败");
  return data
}
function show(nodes,focus=true){
  clearTimeout(jobTimer);
  app.replaceChildren(...(Array.isArray(nodes)?nodes:[nodes]));
  if(focus)app.focus({preventScroll:true});
  updateNav();
  document.body.classList.remove("nav-open");
  menu.setAttribute("aria-expanded","false")
}
function updateNav(){
  const hash=location.hash||"#home";
  document.querySelectorAll("#primary-nav a").forEach(link=>{
    const target=link.getAttribute("href")||"";
    const active=target==="#home"?hash==="#home":
      target==="#knowledge"?hash==="#knowledge"||hash.startsWith("#project=")||
        hash.startsWith("#projects")||hash.startsWith("#sources=")||hash.startsWith("#page="):
      target==="#updates"?hash==="#updates"||hash.startsWith("#update="):
      target==="#jobs"?hash==="#jobs"||hash.startsWith("#job="):
      target==="#system"?hash===target||hash.startsWith("#source-manager="):
      hash===target;
    if(active)link.setAttribute("aria-current","page");else link.removeAttribute("aria-current")
  })
}
function fail(error){
  show(element("p",{class:"empty error",text:error?.message||"知识库暂时不可用。"}))
}
function metric(label,value,route){
  const content=[
    element("span",{class:"metric-label",text:label}),
    element("strong",{class:"metric",text:value})
  ];
  return route?element("a",{class:"card metric-card",href:route},content):
    element("article",{class:"card metric-card"},content)
}
const pageTypeNames={
  project:"项目概览",code_module:"代码模块",code_file:"代码文件",
  conversation:"AI 会话",feishu:"飞书资料",note:"文件、网页和笔记"
};
const knowledgeTypes={
  conversation:{title:"AI 会话",description:"从对话中沉淀的结论、方案和排查记录。"},
  feishu:{title:"飞书资料",description:"从飞书文档整理出的可追溯知识。"},
  note:{title:"文件、网页和笔记",description:"导入的本地资料和网页笔记。"}
};
function displayPageTitle(page){
  if(page.relative_path)return page.relative_path;
  if(page.module_path)return page.module_path;
  return String(page.title||"未命名页面").replace(/^Code(?: module)?:\s*/i,"")
}
function pageMeta(page){
  const label=pageTypeNames[page.template]||pageTypeNames[page.kind]||"知识页面";
  return [label,page.project?.name,page.status==="未验证会话记忆"?"未验证":null]
    .filter(Boolean).join(" · ")
}
function pageCard(page){
  return element("a",{class:"card page-card",href:"#page="+encodeURIComponent(page.path)},[
    element("div",{class:"card-top"},[
      element("h3",{text:displayPageTitle(page)}),
      element("span",{class:"kind-chip",text:pageTypeNames[page.template]||pageTypeNames[page.kind]||"Wiki"})
    ]),
    element("p",{text:page.summary||"暂无摘要"}),
    element("div",{class:"card-footer"},[
      element("small",{class:"meta",text:pageMeta(page)}),
      element("span",{class:"open-page",text:"查看 Wiki 全文 →"})
    ])
  ])
}
function knowledgeCard(title,count,description,route){
  return element("a",{class:"card page-card",href:route},[
    element("h3",{text:title}),
    element("p",{text:description}),
    element("small",{class:"meta",text:`${count} 页可阅读知识`})
  ])
}
function section(title,description,content){
  return element("section",{class:"section"},[
    element("div",{class:"section-title"},[
      element("h2",{text:title}),element("p",{text:description})
    ]),content
  ])
}
function empty(text){return element("p",{class:"empty",text})}
function recentPaths(){
  try{
    const value=JSON.parse(localStorage.getItem("memoryforge-recent-pages")||"[]");
    return Array.isArray(value)?value.filter(item=>typeof item==="string").slice(0,8):[]
  }catch(_){return[]}
}
function rememberPage(path){
  try{
    const paths=[path,...recentPaths().filter(item=>item!==path)].slice(0,8);
    localStorage.setItem("memoryforge-recent-pages",JSON.stringify(paths))
  }catch(_){}
}
function recentGrid(){
  const paths=recentPaths();
  if(!paths.length)return empty("还没有最近打开的页面。");
  return element("div",{class:"grid"},paths.map(path=>
    element("a",{class:"card",href:"#page="+encodeURIComponent(path)},[
      element("h3",{text:path.split("/").pop().replace(/\.md$/,"")}),
      element("small",{class:"meta",text:"继续阅读"})
    ])
  ))
}
function projectCard(project){
  return element("a",{class:"card project-card",href:"#project="+encodeURIComponent(project.repository_id)},[
    element("h3",{text:project.name}),
    element("p",{text:`${project.knowledge_page_count??project.page_count} 个知识页 · ${project.evidence_file_count??0} 个源码证据`}),
    element("small",{class:"meta",text:
      `${project.languages.join(" / ")||"语言未识别"} · Commit ${project.commit||"未同步"}`})
  ])
}
function askForm(){
  const question=element("textarea",{placeholder:"从已应用知识中提问…","aria-label":"问题"});
  const submit=element("button",{class:"button primary",type:"submit",text:"提问"});
  const result=element("div");
  const form=element("form",{class:"form"},[
    element("label",{},["问题",question]),submit,result
  ]);
  form.addEventListener("submit",async event=>{
    event.preventDefault();submit.disabled=true;result.textContent="正在检索…";
    try{
      const data=await post("/api/ask",{question:question.value});
      result.replaceChildren(
        element("p",{text:data.answer}),
        ...data.citations.map(item=>element("small",{class:"meta",text:item.section||"依据"}))
      )
    }catch(error){result.textContent=error.message}
    finally{submit.disabled=false}
  });
  return form
}
async function renderHome(){
  const [summary,projects]=await Promise.all([api("/api/summary"),api("/api/projects")]);
  const metrics=[
    metric("已应用页面",summary.applied_page_count,"#knowledge"),
    summary.projects?metric("项目",summary.projects,"#knowledge"):null,
    summary.pending_updates?metric("等待审核",summary.pending_updates,"#updates"):null,
    summary.active_jobs?metric("运行中任务",summary.active_jobs,"#jobs"):null,
    summary.failed_jobs?metric("失败任务",summary.failed_jobs,"#jobs"):null
  ].filter(Boolean);
  const hero=element("section",{class:"hero"},[
    heading("知识入口","你的本地知识库","从项目、AI 会话和资料中找到已经沉淀的知识。"),
    element("div",{class:"hero-chips"},[
      element("span",{class:"hero-chip",text:`${summary.applied_page_count} 页已应用知识`}),
      summary.projects?element("span",{class:"hero-chip mint",text:`${summary.projects} 个项目`}):null,
      summary.pending_updates?element("span",{class:"hero-chip amber",text:`${summary.pending_updates} 个更新待审核`}):null,
      element("span",{class:"hero-chip",text:"本地运行 · 资料不出本机"})
    ].filter(Boolean)),
    element("div",{class:"actions-row"},[
      element("a",{class:"button primary",href:"#knowledge",text:"浏览我的知识"}),
      element("a",{class:"button",href:"#add-source",text:"添加来源"}),
      element("a",{class:"button",href:"#ask",text:"提问"})
    ])
  ]);
  const nodes=[
    hero,
    element("div",{class:"grid"},metrics),
    section("最近阅读","浏览记录只保存在本机浏览器",recentGrid())
  ];
  if(projects.items.length){
    nodes.push(section("项目","按 Git repository 整理",
      element("div",{class:"grid"},projects.items.map(projectCard))))
  }
  show(nodes)
}
async function renderKnowledge(){
  const [summary,projects]=await Promise.all([api("/api/summary"),api("/api/projects")]);
  const categories=[
    summary.code_pages?knowledgeCard("项目与代码",summary.code_pages,"先读项目概览和模块；再用搜索定位具体文件。","#projects"):null,
    summary.conversation_pages?knowledgeCard("AI 会话",summary.conversation_pages,knowledgeTypes.conversation.description,"#sources=conversation"):null,
    summary.feishu_pages?knowledgeCard("飞书资料",summary.feishu_pages,knowledgeTypes.feishu.description,"#sources=feishu"):null,
    summary.note_pages?knowledgeCard("文件、网页和笔记",summary.note_pages,knowledgeTypes.note.description,"#sources=note"):null
  ].filter(Boolean);
  show([
    heading("我的知识","从哪里开始","先按用途选知识类型，再打开页面查看结论、依据和关联。"),
    element("div",{class:"grid"},categories),
    section("项目",`${projects.items.length} 个已应用代码仓库`,
      projects.items.length?element("div",{class:"grid"},projects.items.map(projectCard)):
        empty("还没有已应用的代码仓库。"))
  ])
}
async function renderProjects(){
  const data=await api("/api/projects");
  show([
    heading("项目","项目目录","每个项目对应一个已登记的 Git repository。"),
    data.items.length?element("div",{class:"grid"},data.items.map(projectCard)):empty("还没有项目。")
  ])
}
function pageGrid(items,message){
  return items.length?element("div",{class:"grid"},items.map(pageCard)):empty(message)
}
async function renderProject(id){
  const data=await api("/api/project?id="+encodeURIComponent(id));
  const nodes=[
    heading("项目",data.name,
      `${data.page_count} 页 · ${data.module_count} 个模块 · Commit ${data.commit||"未同步"}`),
    element("div",{class:"grid"},[
      metric("页面",data.page_count,null),metric("模块",data.module_count,null),
      metric("语言",data.languages.join(" / ")||"未识别",null)
    ])
  ];
  if(data.overview.length)nodes.push(section("先读这里","项目是什么、入口在哪里、模块如何协作",pageGrid(data.overview,"还没有项目概览。")));
  nodes.push(section("模块树","按项目层级浏览；源码文件只在需要时打开。",
    data.module_tree?.length?moduleTree(data.module_tree):pageGrid(data.modules,"还没有模块页。")));
  nodes.push(section("相关资料","会话、飞书资料和笔记如何关联到这个项目",pageGrid(data.related,"还没有相关资料。")));
  if(data.file_count){
    nodes.push(section("代码文件","源码证据按需打开，不混入项目知识页。",element("div",{},[
      element("div",{class:"actions-row"},[
        element("a",{class:"button primary",href:"#evidence="+encodeURIComponent(data.repository_id),text:"搜索源码证据"})
      ]),
      element("details",{},[
        element("summary",{text:`查看 ${Math.min(data.files.length,data.file_count)} 个代码文件示例（共 ${data.file_count} 个）`} ),
        element("div",{class:"detail-body"},pageGrid(data.files,"还没有代码文件页。"))
      ])
    ])));
  }
  show(nodes)
}
const sourceNames={code:"代码知识",conversation:"AI 会话",feishu:"飞书资料",note:"文件、网页和笔记"};
function moduleTree(nodes){
  const root=element("div",{class:"module-tree"});
  for(const node of nodes){
    const link=element("a",{class:"module-link",href:"#page="+encodeURIComponent(node.path)},[
      element("strong",{text:displayPageTitle(node)}),
      element("small",{class:"meta",text:node.summary||"代码模块"})
    ]);
    if(node.children?.length){
      root.append(element("details",{class:"module-node"},[
        element("summary",{text:`${displayPageTitle(node)} · ${node.children.length} 个子模块`}),
        element("div",{class:"module-children"},[link,moduleTree(node.children)])
      ]));
    }else root.append(link)
  }
  return root
}
async function renderEvidence(projectId,query="",offset=0){
  const limit=50;
  const params=new URLSearchParams({view:"evidence",kind:"code",project:projectId,q:query,offset:String(offset),limit:String(limit)});
  const data=await api("/api/pages?"+params.toString());
  const input=element("input",{type:"search",placeholder:"文件名、符号或路径",value:query});
  const form=element("form",{class:"form"},[
    element("label",{},[element("span",{text:"搜索源码证据"}),input]),
    element("button",{class:"button primary",type:"submit",text:"搜索"})
  ]);
  form.addEventListener("submit",event=>{
    event.preventDefault();
    renderEvidence(projectId,input.value.trim(),0)
  });
  show([
    heading("项目证据","源码证据",`${data.total} 个匹配文件；仅展示当前 Commit 的代码文件。`),
    form,
    section("匹配文件",`${data.total} 个`,pageGrid(data.items,"没有匹配的源码证据。")),
    pagination(offset,limit,data.total,next=>renderEvidence(projectId,query,next))
  ])
}
function sourceRow(item){
  const body=element("div",{class:"detail-body"});
  const details=element("details",{},[
    element("summary",{},[
      element("strong",{text:item.name}),
      element("small",{class:"meta",text:` · ${item.updated} · ${item.status}`})
    ]),
    body
  ]);
  details.addEventListener("toggle",async()=>{
    if(!details.open||body.dataset.loaded)return;
    body.dataset.loaded="true";
    try{
      const data=await api("/api/source?ref="+encodeURIComponent(item.ref));
      const copy=element("button",{class:"button",type:"button",text:"复制完整 ID"});
      copy.addEventListener("click",()=>navigator.clipboard.writeText(data.source_id));
      const actions=[copy];
      if(data.refreshable){
        const refresh=element("button",{class:"button",type:"button",text:"刷新来源"});
        refresh.addEventListener("click",async()=>{
          const job=await post(`/api/sources/${data.source_id}/refresh`,{});
          location.hash="#job="+encodeURIComponent(job.id)
        });
        actions.push(refresh)
      }
      body.replaceChildren(
        element("p",{text:`版本 ${data.current_version} · ${data.page_count} 页`}),
        element("code",{text:data.source_id}),
        element("div",{class:"actions-row"},actions)
      )
    }catch(error){body.textContent=error.message}
  });
  return details
}
async function renderSources(kind,offset=0){
  if(kind==="code"){await renderProjects();return}
  const limit=50;
  const pages=await api(`/api/pages?view=published&kind=${encodeURIComponent(kind)}&offset=${offset}&limit=${limit}`);
  const type=knowledgeTypes[kind]||{title:sourceNames[kind]||"知识",description:"打开页面查看内容和关联。"};
  show([
    heading("我的知识",type.title,type.description+" 打开一页后可继续查看关联知识。"),
    section("可阅读知识",`${pages.total} 页`,pageGrid(pages.items,`还没有${type.title}页面。`)),
    pagination(offset,limit,pages.total,next=>renderSources(kind,next))
  ])
}
async function renderSourceManager(kind,offset=0){
  const limit=50;
  const sources=await api(`/api/sources?kind=${encodeURIComponent(kind)}&offset=${offset}&limit=${limit}`);
  const sourceList=sources.items.length?
    element("div",{class:"card"},sources.items.map(sourceRow)):
    empty(`还没有${sourceNames[kind]||"此类"}来源。`);
  show([
    heading("来源管理",sourceNames[kind]||"知识来源","这里用于刷新来源、查看版本或复制内部 ID；阅读知识请回到“我的知识”。"),
    section("已登记来源",`${sources.total} 个`,sourceList),
    pagination(offset,limit,sources.total,next=>renderSourceManager(kind,next))
  ])
}
function sourcePayload(kind,value,selectionId){
  if(kind==="repository"||kind==="codex"||kind==="file"||kind==="folder"){
    return selectionId&&kind==="codex"?{kind,selection_id:selectionId}:{kind,path:value}
  }
  if(kind==="repository_url")return{kind,url:value};
  if(kind==="feishu")return{kind,document:value};
  return{kind,url:value}
}
function renderPreview(target,data){
  const rows=Object.entries(data).filter(([key,value])=>
    !["workspace_commit","kind","sessions","selection_id"].includes(key)&&value!==null);
  target.replaceChildren(element("article",{class:"card"},rows.map(([key,value])=>
    element("p",{},[
      element("strong",{text:key+"："}),
      Array.isArray(value)?value.join(" / "):String(value)
    ])
  )))
}
async function renderAddSource(){
  let selectionId="";
  let previewed=false;
  const kind=element("select");
  [
    ["repository","本地 Git 仓库"],["repository_url","Git 仓库链接（HTTPS）"],
    ["codex","AI 会话"],["feishu","飞书文档"],
    ["file","文件"],["folder","文件夹"],["web","网页"],["github","GitHub 讨论"]
  ].forEach(([value,label])=>kind.append(element("option",{value,text:label})));
  const value=element("input",{placeholder:"本地路径"});
  const fileInput=element("input",{type:"file",accept:".md,.markdown,.txt,.jsonl"});
  const publicInput=element("input",{type:"checkbox"});
  const confirmPublic=element("input",{type:"checkbox"});
  const preview=element("div");
  const submit=element("button",{class:"button primary",type:"submit",text:"开始处理"});
  const scan=element("button",{class:"button",type:"button",text:"扫描未收录 Codex 会话"});
  const choices=element("div",{class:"choice-list"});
  function updateFields(){
    selectionId="";previewed=false;preview.textContent="";choices.textContent="";
    const selected=kind.value;
    value.placeholder={
      repository:"本地 Git 仓库路径",repository_url:"HTTPS Git 链接，例如 https://github.com/owner/repo.git",
      codex:"Codex rollout JSONL 路径",
      feishu:"飞书文档或 Wiki 链接",file:"Markdown/TXT 文件路径",
      folder:"本地文件夹路径",web:"公开网页 URL",github:"GitHub Issue/PR URL"
    }[selected];
    scan.hidden=selected!=="codex";
    fileInput.hidden=!["file","codex"].includes(selected);
    publicInput.disabled=selected==="codex"||selected==="feishu";
    confirmPublic.disabled=publicInput.disabled
  }
  kind.addEventListener("change",updateFields);updateFields();
  fileInput.addEventListener("change",()=>{
    const file=fileInput.files[0];
    if(!file)return;
    previewed=true;selectionId="";value.value="";
    renderPreview(preview,{title:file.name,size_bytes:file.size,privacy:"local_only"})
  });
  const previewButton=element("button",{class:"button",type:"button",text:"预览来源"});
  previewButton.addEventListener("click",async()=>{
    try{
      const data=await post("/api/sources/preview",sourcePayload(kind.value,value.value,selectionId));
      renderPreview(preview,data);previewed=true
    }catch(error){preview.textContent=error.message}
  });
  scan.addEventListener("click",async()=>{
    try{
      const data=await post("/api/sources/preview",{kind:"codex_scan"});
      choices.replaceChildren(...data.sessions.map(item=>{
        const button=element("button",{class:"choice",type:"button"},[
          element("span",{text:item.title}),
          element("small",{class:"meta",text:item.updated})
        ]);
        button.addEventListener("click",()=>{
          selectionId=item.selection_id;value.value=item.title;previewed=true;
          renderPreview(preview,item)
        });
        return button
      }));
      if(!data.sessions.length)choices.append(empty("没有发现可选会话。"))
    }catch(error){choices.textContent=error.message}
  });
  const form=element("form",{class:"form"},[
    element("label",{},["来源类型",kind]),
    element("label",{},["来源",value]),
    element("label",{},["或上传小文件",fileInput]),
    scan,choices,
    element("label",{class:"checkbox"},[publicInput,"这是可公开资料"]),
    element("label",{class:"checkbox"},[confirmPublic,"再次确认允许公开"]),
    element("div",{class:"actions-row"},[previewButton,submit]),
    preview
  ]);
  form.addEventListener("submit",async event=>{
    event.preventDefault();
    if(!previewed){preview.textContent="请先预览来源。";return}
    submit.disabled=true;
    try{
      if(fileInput.files[0]){
        const job=await postUpload(
          fileInput.files[0],kind.value,publicInput.checked,confirmPublic.checked);
        location.hash="#job="+encodeURIComponent(job.id);return
      }
      const payload=sourcePayload(kind.value,value.value,selectionId);
      payload.public=publicInput.checked;payload.confirm_public=confirmPublic.checked;
      const job=await post("/api/sources",payload);
      location.hash="#job="+encodeURIComponent(job.id)
    }catch(error){preview.textContent=error.message;submit.disabled=false}
  });
  show([
    heading("添加来源","三步完成","选择来源、确认隐私、开始处理。"),
    form
  ])
}
function jobCard(job){
  const status={queued:"等待",running:"运行中",waiting_review:"等待审核",
    completed:"完成",failed:"失败",cancelled:"已取消"}[job.status]||job.status;
  return element("a",{class:"card job-card",href:"#job="+encodeURIComponent(job.id)},[
    element("h3",{text:job.name}),
    element("p",{text:`${status} · ${job.stage}`}),
    element("small",{class:"meta",text:job.message})
  ])
}
async function renderJobs(){
  const data=await api("/api/jobs");
  show([
    heading("后台任务","本地任务","导入、刷新、编译和应用按顺序执行。"),
    data.items.length?element("div",{class:"grid"},data.items.map(jobCard)):empty("还没有后台任务。")
  ])
}
async function renderJob(id){
  const job=await api("/api/jobs/"+encodeURIComponent(id));
  const nodes=[
    heading("后台任务",job.name,job.message),
    element("progress",{class:"job-progress",max:"100",value:job.progress}),
    element("div",{class:"grid"},[
      metric("状态",job.status,null),metric("阶段",job.stage,null),
      metric("耗时",job.duration_seconds+" 秒",null)
    ])
  ];
  if(job.error_code)nodes.push(element("p",{class:"error",text:`错误：${job.error_code}`}));
  if(job.changeset_ids?.length){
    nodes.push(element("div",{class:"actions-row"},job.changeset_ids.map(update=>
      element("a",{class:"button primary",href:"#update="+encodeURIComponent(update),text:"查看知识更新"})
    )))
  }
  if(job.status==="queued"){
    const cancel=element("button",{class:"button danger",type:"button",text:"取消任务"});
    cancel.addEventListener("click",async()=>{
      await post(`/api/jobs/${encodeURIComponent(id)}/cancel`,{});renderJob(id)
    });
    nodes.push(cancel)
  }
  show(nodes);
  if(["queued","running"].includes(job.status)&&location.hash==="#job="+encodeURIComponent(id)){
    jobTimer=setTimeout(()=>renderJob(id).catch(fail),1000)
  }
}
function updateCard(update){
  const counts=update.counts;
  const card=element("a",{class:"card update-card",href:"#update="+encodeURIComponent(update.id)},[
    element("h3",{text:update.name}),
    element("p",{text:`新增 ${counts.create} · 修改 ${counts.update} · 删除 ${counts.delete}`}),
    element("small",{class:"meta",text:update.status}),
    element("small",{class:"card-action",text:"查看并审核 →"})
  ]);
  card.addEventListener("click",event=>{
    if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
    event.preventDefault();openUpdate(update.id)
  });
  return card
}
function openUpdate(id){
  const target="#update="+encodeURIComponent(id);
  if(location.hash!==target)history.pushState(null,"",target);
  renderUpdate(id).catch(fail)
}
function lazyUpdateSection(label,build){
  const details=element("details",{},[element("summary",{text:label})]);
  details.addEventListener("toggle",async()=>{
    if(!details.open||details.dataset.loaded)return;
    details.dataset.loaded="true";
    const loading=element("p",{class:"detail-body meta",text:"正在载入…"});
    details.append(loading);
    try{loading.replaceWith(await build())}catch(error){loading.replaceWith(
      element("p",{class:"detail-body error",text:error.message||"载入失败。"})
    )}
  });
  return details
}
async function renderUpdates(){
  const data=await api("/api/updates");
  show([
    heading("知识更新","等待审核","更新不会自动进入正式 Wiki。"),
    data.items.length?element("div",{class:"grid"},data.items.map(updateCard)):
      empty("没有等待审核的知识更新。")
  ])
}
async function renderUpdate(id){
  show([
    heading("知识更新","正在打开审核…","正在载入本次变更。")
  ]);
  const update=await api("/api/updates/"+encodeURIComponent(id)+"?view=summary");
  const reject=element("button",{class:"button danger",type:"button",text:"拒绝"});
  const approve=element("button",{class:"button primary",type:"button",text:"批准并应用"});
  reject.addEventListener("click",async()=>{
    await post(`/api/updates/${encodeURIComponent(id)}/reject`,{});location.hash="#updates"
  });
  approve.addEventListener("click",async()=>{
    approve.disabled=true;
    try{
      const job=await post(`/api/updates/${encodeURIComponent(id)}/approve-and-apply`,{});
      location.hash="#job="+encodeURIComponent(job.id)
    }catch(error){approve.disabled=false;fail(error)}
  });
  show([
    heading("知识更新",update.name,
      `新增 ${update.counts.create} · 修改 ${update.counts.update} · 删除 ${update.counts.delete}`),
    element("div",{class:"actions-row review-actions"},[
      element("span",{class:"meta",text:"确认后才会写入正式 Wiki。"}),reject,approve
    ]),
    lazyUpdateSection(`涉及来源 · ${update.sources.length} 个`,()=>
      element("div",{class:"grid detail-body"},update.sources.map(source=>
        element("article",{class:"card"},[
          element("h3",{text:source.name}),
          element("p",{text:`版本 ${source.version} · ${source.privacy}`})
        ])
      ))
    ),
    lazyUpdateSection(`知识页改动 · ${update.page_count} 项`,async()=>{
      const data=await api("/api/updates/"+encodeURIComponent(id)+"/pages");
      return element("div",{class:"detail-body"},data.items.map(page=>
        element("details",{class:"update-page"},[
          element("summary",{text:`${page.action} · ${page.title}`}),
          element("div",{class:"detail-body"},[
            element("small",{class:"meta",text:`引用 ${page.citation_count} 条`}),
            element("pre",{class:"diff",text:page.diff||"无文本差异"})
          ])
        ])
      ))
    }),
    update.warnings.length?element("p",{class:"warning",text:update.warnings.join("；")}):null,
  ].filter(Boolean))
}
function breadcrumbs(items){
  return element("nav",{class:"breadcrumbs","aria-label":"面包屑"},items.map(item=>
    element("a",{href:item.route.startsWith("#page=")?
      "#page="+encodeURIComponent(item.route.slice(6)):item.route,text:item.label})
  ))
}
function relationGroup(title,description,items){
  if(!items.length)return null;
  return element("div",{class:"relation-group"},[
    element("h3",{text:title}),
    element("p",{class:"meta",text:description}),
    element("div",{class:"relation-list"},items.slice(0,6).map(item=>
      element("a",{class:"relation",href:"#page="+encodeURIComponent(item.path)},[
        element("strong",{text:displayPageTitle(item)}),
        element("small",{text:item.relationship==="精确提及"?"这页明确提到了它":item.relationship==="同项目"?"同一个项目中的知识":"页面之间有明确链接"})
      ])
    ))
  ])
}
function relatedKnowledge(related){
  const groups=[
    relationGroup("直接关联","页面通过链接相连。",related.direct),
    relationGroup("提到的代码或模块","页面文本中明确提到。",related.exact_mentions),
    relationGroup("同一项目","来自同一个代码项目。",related.same_project)
  ].filter(Boolean);
  return groups.length?section("继续探索","这些页面与当前内容有确定性关联。",element("div",{class:"related-groups"},groups)):null
}
function sourceDetails(items){
  if(!items.length)return null;
  return element("details",{},[
    element("summary",{text:`来源详情 · ${items.length}`}),
    element("div",{class:"detail-body"},items.map(sourceOriginal))
  ])
}
function sourceOriginal(item){
  const text=element("pre",{class:"diff",hidden:""});
  const action=element("button",{class:"button",type:"button",text:"查看来源原文"});
  let offset=0;
  action.addEventListener("click",async()=>{
    action.disabled=true;
    try{
      const data=await api(`/api/source-text?source_id=${encodeURIComponent(item.source_id)}&source_version=${item.source_version}&offset=${offset}&limit=4000`);
      text.hidden=false;text.append(document.createTextNode(data.text));
      offset=data.offset+data.limit;
      if(data.has_more){action.textContent="继续加载";action.disabled=false}else action.remove()
    }catch(error){action.textContent=error.message}
  });
  return element("div",{},[
    element("div",{class:"source-row"},[
      element("div",{},[
        element("span",{text:item.name}),
        element("div",{class:"meta",text:`${item.status} · 版本 ${item.source_version}`})
      ]),
      action
    ]),
    text
  ])
}
async function renderPage(path){
  const data=await api("/api/page?path="+encodeURIComponent(path));
  rememberPage(path);
  const body=element("div",{class:"markdown"});body.innerHTML=data.html;
  const content=element("article",{},[
    breadcrumbs(data.breadcrumbs),
    element("span",{class:data.status==="未验证会话记忆"?"badge warn":"badge",text:data.status}),
    element("h1",{class:"reader-title",text:displayPageTitle(data)}),
    data.summary?element("p",{class:"summary",text:data.summary}):null,
    body,relatedKnowledge(data.related),sourceDetails(data.sources)
  ].filter(Boolean));
  const railItems=[
    element("h2",{text:"本页目录"}),
    ...data.structure.filter(item=>item.level<=3).map((item,index)=>
      element("a",{href:"#section-"+index,text:item.title,class:item.level===3?"depth-3":""}))
  ].filter(Boolean);
  show(element("div",{class:"reader-layout"},[content,element("aside",{class:"rail"},railItems)]));
  [...body.querySelectorAll("h2,h3")].forEach((item,index)=>item.id="section-"+index)
}
async function renderSystem(){
  const [data,automation]=await Promise.all([api("/api/summary"),api("/api/automation")]);
  const enabled=element("input",{type:"checkbox"});enabled.checked=automation.enabled;
  const interval=element("input",{type:"number",min:"5",value:automation.interval_minutes});
  const types={};
  ["code","codex","feishu"].forEach(type=>{
    types[type]=element("input",{type:"checkbox"});types[type].checked=automation.types.includes(type)
  });
  const automationForm=element("form",{class:"form"},[
    element("label",{class:"checkbox"},[enabled,"开启自动更新"]),
    element("label",{},["间隔（分钟）",interval]),
    ...Object.entries(types).map(([type,input])=>
      element("label",{class:"checkbox"},[input,type])),
    element("button",{class:"button primary",type:"submit",text:"保存自动更新"})
  ]);
  automationForm.addEventListener("submit",async event=>{
    event.preventDefault();
    try{
      await post("/api/automation",{
        enabled:enabled.checked,
        interval_minutes:Number(interval.value),
        types:Object.entries(types).filter(([,input])=>input.checked).map(([type])=>type)
      });
      renderSystem()
    }catch(error){fail(error)}
  });
  show([
    heading("系统状态","诊断与自动更新","审计信息默认集中在这里。"),
    element("div",{class:"grid"},[
      metric("当前来源",data.current_source_count,null),
      metric("历史 Source identity",data.source_identity_count,null),
      metric("已应用页面",data.applied_page_count,null),
      metric("待应用来源",data.pending_sources,null)
    ]),
    section("Workspace Commit","固定 Commit 阅读",
      element("pre",{class:"card",text:data.workspace_commit})),
    section("来源管理","查看来源版本、刷新或复制内部 ID。",element("div",{class:"actions-row"},[
      element("a",{class:"button",href:"#source-manager=code",text:"代码来源"}),
      element("a",{class:"button",href:"#source-manager=conversation",text:"AI 会话来源"}),
      element("a",{class:"button",href:"#source-manager=feishu",text:"飞书来源"}),
      element("a",{class:"button",href:"#source-manager=note",text:"其他来源"})
    ])),
    section("自动更新",
      automation.supported?
        `${automation.enabled?"已开启":"已关闭"} · 下次 ${automation.next_run||"尚未运行"}`:
        "当前平台不支持 launchd",
      automationForm)
  ])
}
async function renderSearch(value,offset=0){
  if(requestController)requestController.abort();
  requestController=new AbortController();
  const limit=50;
  try{
    const data=await api(
      `/api/pages?view=published&q=${encodeURIComponent(value)}&offset=${offset}&limit=${limit}`,
      {signal:requestController.signal}
    );
    show([
      heading("全局搜索",`“${value}”`,`${data.total} 个匹配页面`),
      pageGrid(data.items,"没有匹配的知识页面。"),
      pagination(offset,limit,data.total,next=>renderSearch(value,next))
    ],false)
  }catch(error){if(error.name!=="AbortError")fail(error)}
}
function pagination(offset,limit,total,onPage){
  const previous=element("button",{type:"button",text:"上一页"});
  const next=element("button",{type:"button",text:"下一页"});
  previous.disabled=offset===0;next.disabled=offset+limit>=total;
  previous.addEventListener("click",()=>onPage(Math.max(0,offset-limit)));
  next.addEventListener("click",()=>onPage(offset+limit));
  return element("div",{class:"pagination"},[previous,next])
}
async function route(){
  const hash=location.hash||"#home";
  try{
    if(hash==="#home")await renderHome();
    else if(hash==="#knowledge")await renderKnowledge();
    else if(hash==="#projects")await renderProjects();
    else if(hash.startsWith("#project="))await renderProject(decodeURIComponent(hash.slice(9)));
    else if(hash.startsWith("#evidence="))await renderEvidence(decodeURIComponent(hash.slice(10)));
    else if(hash.startsWith("#sources="))await renderSources(decodeURIComponent(hash.slice(9)));
    else if(hash.startsWith("#source-manager="))await renderSourceManager(decodeURIComponent(hash.slice(16)));
    else if(hash==="#add-source")await renderAddSource();
    else if(hash==="#updates")await renderUpdates();
    else if(hash.startsWith("#update="))await renderUpdate(decodeURIComponent(hash.slice(8)));
    else if(hash==="#jobs")await renderJobs();
    else if(hash.startsWith("#job="))await renderJob(decodeURIComponent(hash.slice(5)));
    else if(hash==="#ask")show([heading("提问","从 Wiki 获取答案","只读取已应用知识。"),askForm()]);
    else if(hash.startsWith("#page="))await renderPage(decodeURIComponent(hash.slice(6)));
    else if(hash==="#system")await renderSystem();
    else location.hash="#home"
  }catch(error){if(error.name!=="AbortError")fail(error)}
}
search.addEventListener("input",event=>{
  clearTimeout(searchTimer);
  const value=event.target.value.trim();
  if(!value)return;
  searchTimer=setTimeout(()=>renderSearch(value),200)
});
search.addEventListener("keydown",event=>{
  if(event.key==="Enter"&&search.value.trim()){event.preventDefault();renderSearch(search.value.trim())}
});
document.addEventListener("keydown",event=>{
  if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="k"){
    event.preventDefault();search.focus()
  }
  if(event.key==="Escape"){
    document.body.classList.remove("nav-open");menu.setAttribute("aria-expanded","false")
  }
});
menu.addEventListener("click",()=>{
  const active=document.body.classList.toggle("nav-open");
  menu.setAttribute("aria-expanded",String(active))
});
window.addEventListener("hashchange",route);
api("/api/session").then(session=>{csrfToken=session.csrf_token;route()}).catch(fail);
"""
