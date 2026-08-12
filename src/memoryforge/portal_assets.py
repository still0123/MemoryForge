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
    <input id="global-search" type="search" placeholder="搜索标题、路径或正文…">
    <kbd>⌘K</kbd></label>
  <button id="menu-button" class="tool" type="button" aria-expanded="false">导航</button>
</header>
<div class="shell">
  <nav id="primary-nav" class="primary-nav" aria-label="一级导航">
    <a href="#home">首页</a>
    <a href="#knowledge">我的知识</a>
    <a href="#add-source">添加来源</a>
    <a href="#updates">知识更新</a>
    <a href="#jobs">后台任务</a>
    <a href="#system">系统状态</a>
  </nav>
  <main id="app" tabindex="-1" aria-live="polite">
    <p class="empty">正在读取知识库…</p>
  </main>
</div>
</body>
</html>
"""

APP_CSS = """
:root{color-scheme:light;--bg:#f6f7f5;--panel:#fff;--soft:#eef2ef;--text:#202522;
--muted:#69736e;--line:#dce2de;--accent:#19715f;--accent-soft:#ddf2eb;--link:#315fbd;
--warn:#9a6700;--danger:#b42318;--code:#f3f5f4}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#101311;--panel:#171b19;
--soft:#202622;--text:#e7ece9;--muted:#9aa59f;--line:#303833;--accent:#69cbb5;
--accent-soft:#183a32;--link:#9ab5ff;--warn:#e3b341;--danger:#ff9b92;--code:#0d100e}}
*{box-sizing:border-box}[hidden]{display:none!important}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);
color:var(--text);font:15px/1.65 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
button,input{font:inherit;color:inherit}a{color:var(--link)}button{cursor:pointer}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
clip:rect(0,0,0,0);white-space:nowrap;border:0}
.topbar{position:sticky;top:0;z-index:20;display:grid;grid-template-columns:230px minmax(260px,680px) auto;
align-items:center;gap:24px;min-height:64px;padding:9px 22px;background:color-mix(in srgb,var(--bg) 91%,transparent);
border-bottom:1px solid var(--line);backdrop-filter:blur(16px)}
.brand{display:flex;align-items:center;gap:10px;color:var(--text);font-weight:700;text-decoration:none}
.brand-mark{display:grid;width:34px;height:34px;place-items:center;color:#fff;background:var(--accent);
border-radius:9px;font-size:12px;letter-spacing:.06em}.brand small{display:block;color:var(--muted);
font-size:10px;font-weight:500;letter-spacing:.08em;text-transform:uppercase}
.search{display:flex;align-items:center;height:40px;padding:0 10px;background:var(--panel);
border:1px solid var(--line);border-radius:9px}.search:focus-within{border-color:var(--accent)}
.search input{flex:1;min-width:0;background:transparent;border:0;outline:0}
kbd{padding:2px 6px;color:var(--muted);background:var(--soft);border:1px solid var(--line);
border-radius:5px;font-size:11px}.tool{display:none;padding:7px 10px;background:var(--panel);
border:1px solid var(--line);border-radius:8px}
.shell{display:grid;grid-template-columns:230px minmax(0,1060px);gap:44px;max-width:1400px;margin:auto;padding:0 24px}
.primary-nav{position:sticky;top:64px;align-self:start;height:calc(100vh - 64px);padding:28px 8px;
border-right:1px solid var(--line)}.primary-nav a{display:block;margin:2px 0;padding:9px 11px;
color:var(--muted);border-radius:7px;text-decoration:none}.primary-nav a:hover,.primary-nav a:focus-visible{
color:var(--accent);background:var(--accent-soft);outline:0}
#app{min-width:0;padding:42px 0 80px;outline:0}.page-head{margin-bottom:25px}.eyebrow{color:var(--accent);
font-size:11px;font-weight:700;letter-spacing:.1em}.page-head h1{margin:6px 0 8px;font-size:34px;
line-height:1.2;letter-spacing:-.03em}.page-head p{max-width:760px;margin:0;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.card{min-width:0;padding:15px;background:var(--panel);border:1px solid var(--line);border-radius:10px}
a.card{display:block;color:var(--text);text-decoration:none}a.card:hover{border-color:var(--accent);
box-shadow:0 7px 22px color-mix(in srgb,var(--text) 8%,transparent)}.card h2,.card h3{margin:0 0 5px;
font-size:16px}.card p{margin:4px 0;color:var(--muted)}.metric{display:block;margin-top:3px;
font-size:25px;font-weight:650}.meta{color:var(--muted);font-size:12px}.section{margin-top:32px}
.section-title{display:flex;align-items:end;justify-content:space-between;gap:18px;margin-bottom:12px}
.section-title h2{margin:0;font-size:20px}.section-title p{margin:0;color:var(--muted)}
.warning{padding:12px 14px;color:var(--warn);background:color-mix(in srgb,var(--warn) 10%,var(--panel));
border:1px solid color-mix(in srgb,var(--warn) 30%,var(--line));border-radius:8px}
.empty{padding:28px;color:var(--muted);background:var(--panel);border:1px dashed var(--line);border-radius:9px}
.badge{display:inline-flex;padding:3px 8px;color:var(--accent);background:var(--accent-soft);
border-radius:999px;font-size:11px;font-weight:650}.badge.warn{color:var(--warn);
background:color-mix(in srgb,var(--warn) 12%,var(--panel))}
.breadcrumbs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:15px;color:var(--muted);font-size:12px}
.breadcrumbs a{color:var(--muted);text-decoration:none}.breadcrumbs a:hover{color:var(--accent)}
.breadcrumbs a:not(:last-child)::after{content:"/";margin-left:6px;color:var(--line)}
.reader-layout{display:grid;grid-template-columns:minmax(0,780px) 240px;gap:45px;align-items:start}
.reader-title{margin:8px 0 10px;font-size:36px;line-height:1.18;letter-spacing:-.035em}
.summary{margin:18px 0 24px;padding:13px 15px;background:var(--accent-soft);
border-left:3px solid var(--accent);border-radius:0 7px 7px 0}.page-path{display:block;margin:8px 0;
color:var(--muted);font-size:11px;overflow-wrap:anywhere}
.markdown{font-size:16px;line-height:1.8}.markdown>h1{display:none}.markdown h2,.markdown h3,.markdown h4{
scroll-margin-top:82px;line-height:1.35}.markdown h2{margin:1.7em 0 .55em;padding-bottom:7px;
border-bottom:1px solid var(--line);font-size:23px}.markdown h3{margin:1.5em 0 .5em;font-size:19px}
.markdown pre{padding:14px;background:var(--code);border:1px solid var(--line);border-radius:8px;overflow:auto}
.markdown :not(pre)>code{padding:2px 5px;background:var(--code);border:1px solid var(--line);
border-radius:4px;font-size:.88em}code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.markdown blockquote{margin-left:0;padding-left:12px;color:var(--muted);border-left:3px solid var(--line)}
.rail{position:sticky;top:88px;max-height:calc(100vh - 112px);overflow:auto}.rail h2{margin:0 0 8px;
color:var(--muted);font-size:11px;letter-spacing:.06em}.rail a{display:block;margin:6px 0;
color:var(--muted);font-size:12px;line-height:1.4;text-decoration:none}.rail a:hover{color:var(--accent)}
.rail .depth-3{padding-left:10px}details{margin-top:12px;background:var(--panel);
border:1px solid var(--line);border-radius:8px}summary{padding:10px 12px;cursor:pointer}
.detail-body{padding:0 12px 12px}.relation{display:block;margin:6px 0;padding:8px 9px;
color:var(--text);background:var(--soft);border-radius:7px;text-decoration:none}.relation small{display:block;
color:var(--muted)}.pagination{display:flex;gap:8px;margin-top:18px}.pagination button{padding:8px 12px;
background:var(--panel);border:1px solid var(--line);border-radius:7px}.pagination button:disabled{opacity:.45;
cursor:default}.source-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;
padding:12px 0;border-bottom:1px solid var(--line)}.source-row:last-child{border:0}
.actions-row{display:flex;flex-wrap:wrap;gap:9px;margin:18px 0}.button{display:inline-flex;
align-items:center;justify-content:center;padding:9px 13px;color:var(--text);background:var(--panel);
border:1px solid var(--line);border-radius:8px;text-decoration:none}.button.primary{color:#fff;
background:var(--accent);border-color:var(--accent)}.button.danger{color:var(--danger);
border-color:color-mix(in srgb,var(--danger) 40%,var(--line))}.button:disabled{opacity:.45}
.form{display:grid;gap:12px;max-width:720px;padding:18px;background:var(--panel);
border:1px solid var(--line);border-radius:10px}.form label{display:grid;gap:5px;font-weight:600}
.form input,.form select,.form textarea{width:100%;padding:9px 10px;background:var(--bg);
border:1px solid var(--line);border-radius:7px}.form textarea{min-height:90px;resize:vertical}
.checkbox{display:flex!important;grid-template-columns:auto 1fr;align-items:center;font-weight:400!important}
.checkbox input{width:auto}.job-progress{width:100%;height:8px;accent-color:var(--accent)}
.diff{max-height:420px;padding:12px;background:var(--code);border:1px solid var(--line);
border-radius:8px;overflow:auto;white-space:pre-wrap}.error{color:var(--danger)}
.choice-list{display:grid;gap:8px}.choice{display:flex;justify-content:space-between;gap:12px;
padding:10px;background:var(--soft);border:0;border-radius:7px;text-align:left}
@media(max-width:960px){.reader-layout{grid-template-columns:minmax(0,1fr)}.rail{position:static;
max-height:none;order:2}.shell{gap:28px}}
@media(max-width:720px){.topbar{grid-template-columns:auto minmax(0,1fr) auto;gap:9px;padding:8px 12px}
.brand>span:last-child,kbd{display:none}.tool{display:block}.shell{display:block;padding:0 16px}
.primary-nav{display:none}.nav-open .primary-nav{position:fixed;z-index:30;inset:64px 0 0;display:block;
width:100%;height:auto;padding:18px;background:var(--bg);border:0}.nav-open{overflow:hidden}
#app{padding-top:28px}.page-head h1,.reader-title{font-size:29px}.grid{grid-template-columns:1fr}}
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
function heading(kicker,title,description){
  return element("header",{class:"page-head"},[
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
    element("p",{text:`${project.page_count} 页 · ${project.module_count} 个模块`}),
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
    api(`/api/pages?kind=${encodeURIComponent(kind)}&offset=${offset}&limit=${limit}`)
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
    const data=await api(`/api/pages?q=${encodeURIComponent(value)}&offset=${offset}&limit=${limit}`,
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
  document.body.classList.remove("nav-open");
  menu.setAttribute("aria-expanded","false")
}
function fail(error){
  show(element("p",{class:"empty error",text:error?.message||"知识库暂时不可用。"}))
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
    element("p",{text:`${project.page_count} 页 · ${project.module_count} 个模块`}),
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
  const nodes=[
    heading("知识入口","你的本地知识库","查看知识、添加来源、审核更新或直接提问。"),
    element("div",{class:"actions-row"},[
      element("a",{class:"button primary",href:"#add-source",text:"添加来源"}),
      element("a",{class:"button",href:"#ask",text:"提问"})
    ]),
    element("div",{class:"grid"},metrics),
    section("最近打开","仅保存页面路径",recentGrid())
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
    summary.code_pages?metric("代码知识",summary.code_pages,"#sources=code"):null,
    summary.conversation_pages?metric("AI 会话",summary.conversation_pages,"#sources=conversation"):null,
    summary.feishu_pages?metric("飞书资料",summary.feishu_pages,"#sources=feishu"):null,
    summary.note_pages?metric("文件、网页和笔记",summary.note_pages,"#sources=note"):null
  ].filter(Boolean);
  show([
    heading("我的知识","知识总览","按用户可识别的项目和来源类型浏览。"),
    element("div",{class:"grid"},categories),
    section("代码仓库",`${projects.items.length} 个`,
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
  nodes.push(section("项目概览","项目入口与同步信息",pageGrid(data.overview,"还没有项目概览。")));
  nodes.push(section("模块","确定性代码模块",pageGrid(data.modules,"还没有模块页。")));
  nodes.push(section("代码文件","固定 Commit 的代码知识",pageGrid(data.files,"还没有代码文件页。")));
  nodes.push(section("相关资料","确定性相关知识",pageGrid(data.related,"还没有相关资料。")));
  show(nodes)
}
const sourceNames={code:"代码知识",conversation:"AI 会话",feishu:"飞书资料",note:"文件、网页和笔记"};
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
  const limit=50;
  const [sources,pages]=await Promise.all([
    api(`/api/sources?kind=${encodeURIComponent(kind)}&offset=${offset}&limit=${limit}`),
    api(`/api/pages?kind=${encodeURIComponent(kind)}&offset=${offset}&limit=${limit}`)
  ]);
  const sourceList=sources.items.length?
    element("div",{class:"card"},sources.items.map(sourceRow)):
    empty(`还没有${sourceNames[kind]||"此类"}来源。`);
  show([
    heading("我的知识",sourceNames[kind]||"知识来源","当前版本和已应用页面。"),
    section("当前来源",`${sources.total} 个`,sourceList),
    section("已应用页面",`${pages.total} 页`,pageGrid(pages.items,"还没有已应用页面。")),
    pagination(offset,limit,Math.max(sources.total,pages.total),next=>renderSources(kind,next))
  ])
}
function sourcePayload(kind,value,selectionId){
  if(kind==="repository"||kind==="codex"||kind==="file"||kind==="folder"){
    return selectionId&&kind==="codex"?{kind,selection_id:selectionId}:{kind,path:value}
  }
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
    ["repository","代码仓库"],["codex","AI 会话"],["feishu","飞书文档"],
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
      repository:"本地 Git 仓库路径",codex:"Codex rollout JSONL 路径",
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
  return element("a",{class:"card",href:"#job="+encodeURIComponent(job.id)},[
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
  return element("a",{class:"card",href:"#update="+encodeURIComponent(update.id)},[
    element("h3",{text:update.name}),
    element("p",{text:`新增 ${counts.create} · 修改 ${counts.update} · 删除 ${counts.delete}`}),
    element("small",{class:"meta",text:update.status})
  ])
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
  const update=await api("/api/updates/"+encodeURIComponent(id));
  const pages=update.pages.map(page=>element("details",{},[
    element("summary",{text:`${page.action} · ${page.title}`}),
    element("div",{class:"detail-body"},[
      element("small",{class:"meta",text:`引用 ${page.citation_count} 条`}),
      element("pre",{class:"diff",text:page.diff||"无文本差异"})
    ])
  ]));
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
    element("div",{class:"grid"},update.sources.map(source=>
      element("article",{class:"card"},[
        element("h3",{text:source.name}),
        element("p",{text:`版本 ${source.version} · ${source.privacy}`})
      ])
    )),
    ...pages,
    update.warnings.length?element("p",{class:"warning",text:update.warnings.join("；")}):null,
    element("div",{class:"actions-row"},[reject,approve])
  ].filter(Boolean))
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
  const body=element("div",{class:"markdown"});body.innerHTML=data.html;
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
    ...data.structure.filter(item=>item.level<=3).map((item,index)=>
      element("a",{href:"#section-"+index,text:item.title,class:item.level===3?"depth-3":""})),
    relationGroup("直接关联",data.related.direct),
    relationGroup("精确提及",data.related.exact_mentions),
    relationGroup("同项目",data.related.same_project)
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
      `/api/pages?q=${encodeURIComponent(value)}&offset=${offset}&limit=${limit}`,
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
    else if(hash.startsWith("#sources="))await renderSources(decodeURIComponent(hash.slice(9)));
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
