"""
Static HTML generator.

Converts a docs directory (containing .md files + module_tree.json) into
a fully pre-rendered static website — one .html file per .md file, with
an inline sidebar, all internal links rewritten to .html, and no runtime
markdown rendering.
"""

from __future__ import annotations

import html as _html
import os
import re
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from jinja2 import Template

from codewiki.src.utils import (
    file_manager,
    module_doc_filename,
    find_module_doc,
    _normalize_for_match,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Layout CSS — compact inline styles for sidebar + article + overrides
# Uses Bulma CSS variables for theme consistency
# ──────────────────────────────────────────────────────────────────────────────

_CSS = """
/* ── Theme overrides ── */
:root,[data-theme=light]{
  --bulma-scheme-main:#fff;--bulma-scheme-main-bis:#f8fafc;--bulma-scheme-main-ter:#f1f5f9;
  --bulma-border:#e2e8f0;--bulma-border-weak:#f1f5f9;
  --bulma-text:#1e293b;--bulma-text-weak:#64748b;--bulma-text-strong:#0f172a;
  --bulma-link:#2563eb;--bulma-link-hover:#1d4ed8;
  --bulma-primary:#2563eb;--bulma-primary-light:#eff6ff;
  --bulma-code:#e11d48;--bulma-code-background:#f8fafc;
  --bulma-pre-background:#f8fafc;
  --cw-nav-active-bg:#eff6ff;--cw-nav-active-text:#1d4ed8;
  --cw-nav-hover-bg:#f1f5f9;
  --cw-meta-bg:#f8fafc;
}
[data-theme=dark]{
  --bulma-scheme-main:#0f172a;--bulma-scheme-main-bis:#1e293b;--bulma-scheme-main-ter:#253047;
  --bulma-border:#334155;--bulma-border-weak:#253047;
  --bulma-text:#e2e8f0;--bulma-text-weak:#94a3b8;--bulma-text-strong:#f8fafc;
  --bulma-link:#60a5fa;--bulma-link-hover:#93c5fd;
  --bulma-primary:#60a5fa;--bulma-primary-light:#1e3a5f;
  --bulma-code:#f472b6;--bulma-code-background:#1e293b;
  --bulma-pre-background:#162032;
  --cw-nav-active-bg:#1e3a5f;--cw-nav-active-text:#60a5fa;
  --cw-nav-hover-bg:#253047;
  --cw-meta-bg:#253047;
}
/* ── Navbar ── */
.navbar{background:var(--bulma-scheme-main)!important;border-bottom:1px solid var(--bulma-border)!important;box-shadow:none!important;}
.navbar-item,.navbar-burger{color:var(--bulma-text)!important;}
.navbar-burger:hover{background:var(--cw-nav-hover-bg)!important;}
/* ── Layout ── */
.cw-wrap{display:flex;min-height:100vh;padding-top:3.25rem;}
.cw-side{position:fixed;top:3.25rem;left:0;width:272px;height:calc(100vh - 3.25rem);overflow-y:auto;background:var(--bulma-scheme-main-bis);border-right:1px solid var(--bulma-border);padding:1rem 0.75rem 3rem;z-index:30;transition:transform .2s;}
.cw-side.off{transform:translateX(-272px);}
.cw-body{margin-left:272px;flex:1;min-width:0;transition:margin-left .2s;}
.cw-body.full{margin-left:0;}
.cw-content{max-width:900px;margin:0 auto;padding:2.5rem 2rem;}
.cw-overlay{display:none;position:fixed;inset:0;top:3.25rem;background:rgba(0,0,0,.35);z-index:25;backdrop-filter:blur(2px);}
.cw-overlay.on{display:block;}
/* ── Sidebar nav ── */
.cw-side .menu-list a{font-size:0.84rem;border-radius:6px;color:var(--bulma-text-weak);padding:0.4em 0.75em;transition:all .15s;}
.cw-side .menu-list a:hover{background:var(--cw-nav-hover-bg);color:var(--bulma-text);}
.cw-side .menu-list a.is-active{background:var(--cw-nav-active-bg)!important;color:var(--cw-nav-active-text)!important;font-weight:600;}
.cw-side .menu-list ul{border-left:1px solid var(--bulma-border);margin-left:0.75em;padding-left:0;}
.cw-side .menu-label{color:var(--bulma-text-weak);font-size:0.7rem;letter-spacing:0.06em;margin-top:1.2em;}
/* ── Meta card ── */
.cw-side .card{background:var(--cw-meta-bg);border:1px solid var(--bulma-border);box-shadow:none;border-radius:8px;margin-bottom:0.75rem;}
.cw-side .card b{color:var(--bulma-text-weak);}
/* ── TOC dropdown ── */
#toc-dropdown .dropdown-content{background:var(--bulma-scheme-main);border:1px solid var(--bulma-border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.12);}
#toc-dropdown .menu-list a{padding:0.3em 0.6em;border-radius:4px;font-size:0.82rem;color:var(--bulma-text-weak);}
#toc-dropdown .menu-list a:hover{background:var(--cw-nav-hover-bg);color:var(--bulma-text);}
#toc-dropdown .menu-list a.is-active{color:var(--cw-nav-active-text);font-weight:600;}
#toc-dropdown .toc-h3 a{padding-left:1.2em;font-size:0.78rem;}
/* ── Article content ── */
.content{color:var(--bulma-text);line-height:1.75;}
.content h1{font-size:1.85rem;font-weight:700;border-bottom:2px solid var(--bulma-border);padding-bottom:0.4rem;margin-bottom:1.2rem;}
.content h2{font-size:1.4rem;font-weight:600;margin-top:2.4rem;padding-top:1.2rem;border-top:1px solid var(--bulma-border-weak);}
.content h3{font-size:1.15rem;font-weight:600;margin-top:1.8rem;color:var(--bulma-text-strong);}
.content h4{font-size:1rem;font-weight:600;color:var(--bulma-text-strong);}
.content p,.content li{color:var(--bulma-text);font-size:0.95rem;}
.content a{color:var(--bulma-link);text-decoration:none;border-bottom:1px solid transparent;transition:border-color .15s;}
.content a:hover{border-bottom-color:var(--bulma-link);}
.content code{background:var(--bulma-code-background);color:var(--bulma-code);padding:0.15em 0.4em;border-radius:4px;font-size:0.85em;font-family:'JetBrains Mono',Consolas,monospace;}
.content pre{background:var(--bulma-pre-background);border:1px solid var(--bulma-border);border-radius:8px;padding:1rem 1.2rem;overflow-x:auto;}
.content pre code{background:none;color:inherit;padding:0;font-size:0.87em;}
.content blockquote{border-left:3px solid var(--bulma-link);background:var(--bulma-primary-light);padding:0.75rem 1rem;border-radius:0 6px 6px 0;color:var(--bulma-text);}
.content table{border-collapse:collapse;width:100%;}
.content th{background:var(--bulma-scheme-main-bis);font-weight:600;text-align:left;}
.content th,.content td{border:1px solid var(--bulma-border);padding:0.6rem 0.8rem;font-size:0.9rem;}
.content img{border-radius:8px;max-width:100%;}
.content hr{border:none;border-top:1px solid var(--bulma-border);margin:2rem 0;}
/* hljs/mermaid/math overrides */
.content pre code.hljs{background:transparent!important;}
.content .mermaid{max-width:none;margin:1.5rem 0;padding:1rem;background:var(--bulma-scheme-main);border:1px solid var(--bulma-border);border-radius:8px;}
.content .math-block{overflow-x:auto;margin:1rem 0;}
.content .math-inline{display:inline;}
.content .math-err{color:var(--bulma-danger);font-style:italic;font-size:0.85em;}
.content pre:focus-visible{box-shadow:0 0 0 3px color-mix(in srgb,var(--bulma-link) 30%,transparent);}
.hljs{background:transparent!important;}
/* ── Back to top ── */
#btt{position:fixed;bottom:1.5rem;right:1.5rem;z-index:100;display:none;box-shadow:0 2px 8px rgba(0,0,0,.15);width:36px;height:36px;font-size:14px;}
#btt.on{display:inline-flex;}
/* ── Responsive ── */
@media(max-width:768px){
  .cw-side{transform:translateX(-272px);}
  .cw-side.on{transform:translateX(0);}
  .cw-body{margin-left:0;}
  .cw-content{padding:1.5rem 1rem;}
}
@media(min-width:769px){
  .cw-side{transform:none;}
  .cw-side.off{transform:translateX(-272px);}
}
""".strip()

# ──────────────────────────────────────────────────────────────────────────────
# Page template (Jinja2 — nav/meta logic lives in the template itself)
# ──────────────────────────────────────────────────────────────────────────────

_PAGE_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:ital,wght@0,400;0,500;1,400&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bulma@1.0.4/css/bulma.min.css">
<link id="hljs-css" rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11.9.0/dist/mermaid.min.js"></script>
<script>(function(){var t=localStorage.getItem('cw-theme')||(window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');document.documentElement.setAttribute('data-theme',t);if(t==='dark'){document.getElementById('hljs-css').href='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css';}})();</script>
<style>
body{font-family:'Inter',system-ui,-apple-system,sans-serif;}
{{ css }}
</style>
</head>
<body>
<nav class="navbar is-fixed-top has-shadow" role="navigation" aria-label="main navigation">
  <div class="navbar-brand">
    <button class="navbar-burger" id="sb-toggle" aria-label="Toggle sidebar" aria-expanded="false">
      <span aria-hidden="true"></span><span aria-hidden="true"></span><span aria-hidden="true"></span><span aria-hidden="true"></span>
    </button>
    <a href="index.html" class="navbar-item has-text-link has-text-weight-bold">&#128218; {{ repo_name }}</a>
  </div>
  <div class="navbar-end">
    <div class="navbar-item">
      <div class="buttons are-small">
        {%- if not hide_repo_links and metadata and metadata.generation_info and metadata.generation_info.repo_url %}
        <a href="{{ metadata.generation_info.repo_url }}" target="_blank" rel="noopener" class="button is-light is-small" title="Repository">&#128279; Repo</a>
        {%- if 'github.com' in metadata.generation_info.repo_url %}
        <a href="https://deepwiki.com/{{ metadata.generation_info.repo_url.split('github.com/')[-1] }}" target="_blank" rel="noopener" class="button is-light is-small" title="DeepWiki">&#127760; DeepWiki</a>
        {%- endif %}
        {%- endif %}
        <div class="dropdown is-right is-hoverable" id="toc-dropdown">
          <div class="dropdown-trigger">
            <button class="button is-light is-small" aria-haspopup="true" aria-controls="toc-menu" title="Table of Contents">&#128209; TOC</button>
          </div>
          <div class="dropdown-menu" id="toc-menu" role="menu" style="min-width:220px;">
            <div class="dropdown-content" style="max-height:60vh;overflow-y:auto;padding:0.5rem;">
              <ul class="menu-list" id="toc-ul" style="font-size:0.85rem;"></ul>
            </div>
          </div>
        </div>
        <a href="/" id="site-home-btn" class="button is-light is-small" title="Back to main site" aria-label="Back to main site">&#127968;</a>
        <button class="button is-light is-small" id="theme-btn" title="Toggle theme" aria-label="Toggle theme">&#127769;</button>
      </div>
    </div>
  </div>
</nav>
<div class="cw-wrap">
  <aside class="cw-side" id="sb">
    {%- if metadata and metadata.generation_info %}
    <div class="card" style="margin-bottom:0.75rem;">
      <div class="card-content" style="padding:0.75rem;">
        {%- if metadata.generation_info.main_model %}
        <p class="is-size-7"><b>Model:</b> {{ metadata.generation_info.main_model }}</p>
        {%- endif %}
        {%- if metadata.generation_info.timestamp %}
        <p class="is-size-7"><b>Generated:</b> {{ metadata.generation_info.timestamp[:16] }}</p>
        {%- endif %}
        {%- if metadata.generation_info.commit_id %}
        <p class="is-size-7"><b>Commit:</b> {{ metadata.generation_info.commit_id[:8] }}</p>
        {%- endif %}
        {%- if metadata.statistics and metadata.statistics.total_components %}
        <p class="is-size-7"><b>Components:</b> {{ metadata.statistics.total_components }}</p>
        {%- endif %}
        {%- if not hide_repo_links and metadata.generation_info.repo_url %}
        <p class="is-size-7" style="margin-top:0.5rem">
          <a href="{{ metadata.generation_info.repo_url }}" target="_blank" rel="noopener">&#128279; Repository</a>
          {%- if 'github.com' in metadata.generation_info.repo_url %}
          &middot; <a href="https://deepwiki.com/{{ metadata.generation_info.repo_url.split('github.com/')[-1] }}" target="_blank" rel="noopener">&#127760; DeepWiki</a>
          {%- endif %}
        </p>
        {%- endif %}
      </div>
    </div>
    {%- endif %}
    <aside class="menu">
      <ul class="menu-list">
        <li><a href="index.html"{% if current_page in ('overview.html', 'index.html') %} class="is-active"{% endif %}>{{ nav_labels.get('overview', 'Overview') }}</a></li>
        {%- for guide in guides %}
        {%- if guide.sub_pages %}
        <li>
          <a href="{{ guide.href }}"{% if current_page == guide.href %} class="is-active"{% endif %}>{{ guide.label }}</a>
          <ul>
            {%- for sub in guide.sub_pages %}
            <li><a href="{{ sub.href }}"{% if current_page == sub.href %} class="is-active"{% endif %}>{{ sub.label }}</a></li>
            {%- endfor %}
          </ul>
        </li>
        {%- else %}
        <li><a href="{{ guide.href }}"{% if current_page == guide.href %} class="is-active"{% endif %}>{{ guide.label }}</a></li>
        {%- endif %}
        {%- endfor %}
        {%- if module_tree %}
        {{ render_nav(module_tree, current_page, resolved_hrefs, [], nav_labels) }}
        {%- endif %}
      </ul>
    </aside>
  </aside>
  <div class="cw-overlay" id="ov"></div>
  <main class="cw-body" id="body">
    <div class="cw-content">
      <article id="mc" class="cw-article content">
{{ content }}
      </article>
    </div>
  </main>
</div>
<button id="btt" class="button is-primary is-rounded" title="Back to top">&#8679;</button>
<script>
document.getElementById('site-home-btn').href=window.location.origin+'/';
var html=document.documentElement,themeBtn=document.getElementById('theme-btn');
function curTheme(){return html.getAttribute('data-theme')||(window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');}
var _hljsBase='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/';
function setTheme(t){html.setAttribute('data-theme',t);localStorage.setItem('cw-theme',t);themeBtn.innerHTML=t==='dark'?'&#9728;&#65039;':'&#127769;';document.getElementById('hljs-css').href=_hljsBase+(t==='dark'?'github-dark':'github')+'.min.css';}
setTheme(curTheme());
themeBtn.addEventListener('click',function(){setTheme(curTheme()==='dark'?'light':'dark');});
document.addEventListener('DOMContentLoaded',function(){
  hljs.highlightAll();
  document.querySelectorAll('article pre').forEach(function(pre){
    pre.setAttribute('tabindex','0');pre.setAttribute('role','region');pre.setAttribute('aria-label','Code block');
  });
});
var sb=document.getElementById('sb'),body=document.getElementById('body'),ov=document.getElementById('ov'),burger=document.getElementById('sb-toggle');
function isMob(){return window.innerWidth<769;}
function sbShow(){if(isMob()){sb.classList.add('on');ov.classList.add('on');}else{sb.classList.remove('off');body.classList.remove('full');}}
function sbHide(){if(isMob()){sb.classList.remove('on');ov.classList.remove('on');}else{sb.classList.add('off');body.classList.add('full');}}
if(!isMob()&&localStorage.getItem('cw-sb')==='off'){sb.classList.add('off');body.classList.add('full');}
burger.addEventListener('click',function(){
  if(isMob()){if(sb.classList.contains('on'))sbHide();else sbShow();}
  else{if(sb.classList.contains('off')){sbShow();localStorage.setItem('cw-sb','on');}else{sbHide();localStorage.setItem('cw-sb','off');}}
});
ov.addEventListener('click',sbHide);
document.addEventListener('keydown',function(e){if(e.key==='Escape')sbHide();});
window.addEventListener('resize',function(){if(!isMob()){ov.classList.remove('on');sb.classList.remove('on');if(localStorage.getItem('cw-sb')!=='off')sbShow();}else{sb.classList.remove('off');body.classList.remove('full');}});
(function(){
  var mc=document.getElementById('mc'),ul=document.getElementById('toc-ul'),tocDrop=document.getElementById('toc-dropdown');
  if(!mc||!ul)return;
  var hs=mc.querySelectorAll('h2,h3');
  if(hs.length<2){if(tocDrop)tocDrop.style.display='none';return;}
  hs.forEach(function(h,i){
    if(!h.id)h.id='h-'+i;
    var li=document.createElement('li');
    li.className=h.tagName==='H3'?'toc-h3':'';
    var a=document.createElement('a');a.href='#'+h.id;a.textContent=h.textContent;
    li.appendChild(a);ul.appendChild(li);
  });
  var obs=new IntersectionObserver(function(entries){
    entries.forEach(function(e){var a=ul.querySelector('a[href="#'+e.target.id+'"]');if(a)a.classList.toggle('is-active',e.isIntersecting);});
  },{rootMargin:'-15% 0% -75% 0%'});
  hs.forEach(function(h){obs.observe(h);});
})();
var btt=document.getElementById('btt');
window.addEventListener('scroll',function(){btt.classList.toggle('on',window.scrollY>300);});
btt.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'});});
async function cwRenderMermaid(){
  var theme=document.documentElement.getAttribute('data-theme')==='dark'?'dark':'default';
  mermaid.initialize({startOnLoad:false,theme:theme,themeVariables:{primaryColor:'#2563eb',lineColor:'#64748b'},flowchart:{htmlLabels:true,curve:'basis'},sequence:{mirrorActors:false,useMaxWidth:true}});
  var els=document.querySelectorAll('.mermaid');
  for(var i=0;i<els.length;i++){
    var el=els[i];
    var src=el.getAttribute('data-mermaid-src');
    if(!src){src=el.textContent.trim();el.setAttribute('data-mermaid-src',src);}
    else{el.textContent=src;}
    try{var r=await mermaid.render('mermaid-'+Date.now()+'-'+i,src);el.innerHTML=r.svg;}
    catch(err){el.innerHTML='<details open><summary style="color:var(--bulma-danger);cursor:pointer">&#9888; Mermaid error</summary><pre style="font-size:12px;margin-top:8px;white-space:pre-wrap">'+err.message+'</pre><pre style="font-size:11px;opacity:.6">'+src.replace(/</g,'&lt;')+'</pre></details>';}
  }
}
document.addEventListener('DOMContentLoaded',cwRenderMermaid);
themeBtn.addEventListener('click',function(){setTimeout(cwRenderMermaid,50);});
var _mjReady=null;
function _loadMathJax(){
  if(!_mjReady){window.MathJax={tex:{packages:{'[+]':['ams','newcommand']}},svg:{fontCache:'global'},startup:{typeset:false}};_mjReady=new Promise(function(res,rej){var s=document.createElement('script');s.src='https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js';s.onload=function(){MathJax.startup.promise.then(res,rej);};s.onerror=rej;document.head.appendChild(s);});}
  return _mjReady;
}
async function cwRenderMath(root){
  if(typeof katex==='undefined')return;
  if(!root||typeof root.querySelectorAll!=='function')root=document.getElementById('mc')||document.body;
  var failed=[];
  root.querySelectorAll('.math-block,.math-inline').forEach(function(el){
    if(el.dataset.mathDone)return;var disp=el.classList.contains('math-block');
    var src=el.textContent.trim().slice(2,-2).trim();el.dataset.mathSrc=src;el.dataset.mathDone='1';
    try{el.innerHTML=katex.renderToString(src,{displayMode:disp,throwOnError:true,output:'html'});}catch(e){failed.push([el,disp]);}
  });
  if(!failed.length)return;
  try{await _loadMathJax();for(var i=0;i<failed.length;i++){var el=failed[i][0],disp=failed[i][1];try{var node=await MathJax.tex2svgPromise(el.dataset.mathSrc,{display:disp});el.innerHTML='';el.appendChild(node);}catch(e2){el.innerHTML='<code class="math-err" title="'+el.dataset.mathSrc.replace(/"/g,'&#34;')+'">'+el.dataset.mathSrc+'</code>';}}}
  catch(loadErr){failed.forEach(function(p){p[0].innerHTML='<code class="math-err">'+p[0].dataset.mathSrc+'</code>';});}
}
document.addEventListener('DOMContentLoaded',cwRenderMath);
</script>
</body>
</html>
""")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _resolve_nav_hrefs(
    module_tree: Dict[str, Any],
    docs_dir: str,
    parent_path: Optional[list[str]] = None,
) -> Dict[str, Optional[str]]:
    """Pre-compute ``module_key → actual_html_filename`` for every tree node.

    Uses ``find_module_doc`` (with suffix fallback) so that modules whose
    .md file was generated under a different tree prefix are still found.
    Returns a flat dict keyed by ``"/".join(module_path)``.
    """
    result: Dict[str, Optional[str]] = {}
    base = parent_path or []
    for key, data in module_tree.items():
        module_path = base + [key]
        map_key = "/".join(module_path)
        doc_filename = data.get("_doc_filename")
        if doc_filename:
            found_path = os.path.join(docs_dir, doc_filename)
            result[map_key] = (
                doc_filename.replace(".md", ".html") if os.path.exists(found_path) else None
            )
        else:
            found = find_module_doc(docs_dir, module_path)
            if found:
                result[map_key] = os.path.basename(found).replace(".md", ".html")
            else:
                result[map_key] = None
        children = data.get("children") or {}
        if children:
            result.update(_resolve_nav_hrefs(children, docs_dir, module_path))
    return result


def _extract_h1_titles(md_files: list) -> Dict[str, str]:
    """Extract the first Markdown H1 heading from each file.

    Returns ``{filename_stem: h1_text}`` — e.g. ``{"cli": "CLI 传输与事件流"}``.
    The H1 in generated docs is already in the target language, so this gives
    us localized nav labels without extra LLM calls.
    """
    _H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
    titles: Dict[str, str] = {}
    for md_path in md_files:
        try:
            # Only read the first 500 bytes — H1 is always at the top
            with open(md_path, "r", encoding="utf-8") as f:
                head = f.read(500)
            m = _H1_RE.search(head)
            if m:
                titles[md_path.stem] = m.group(1).strip()
        except OSError:
            pass
    return titles


def _render_nav(
    module_tree: Dict[str, Any],
    current_page: str,
    resolved_hrefs: Dict[str, Optional[str]],
    parent_path: list[str],
    nav_labels: Dict[str, str],
) -> str:
    """Recursively build sidebar nav HTML from the module tree (called from Jinja2 template)."""
    lines: list[str] = []

    for key, data in module_tree.items():
        module_path = parent_path + [key]
        map_key = "/".join(module_path)
        href = resolved_hrefs.get(map_key)
        has_page = href is not None
        if not href:
            href = data.get("_doc_filename", module_doc_filename(module_path)).replace(
                ".md", ".html"
            )
        is_active = _normalize_for_match(current_page) == _normalize_for_match(href)
        active = ' class="is-active"' if is_active else ""
        children = data.get("children") or {}
        doc_stem = href.removesuffix(".html") if href else ""
        label = nav_labels.get(doc_stem, key.replace("_", " ").title())

        if children:
            lines.append("<li>")
            if has_page:
                lines.append(f'  <a href="{href}"{active}>{label}</a>')
            else:
                lines.append(
                    f'  <span style="opacity:.5;padding:0.5em 0.75em;display:block">{label}</span>'
                )
            lines.append("  <ul>")
            lines.append(
                _render_nav(children, current_page, resolved_hrefs, module_path, nav_labels)
            )
            lines.append("  </ul>")
            lines.append("</li>")
        else:
            if has_page:
                lines.append(f'<li><a href="{href}"{active}>{label}</a></li>')
            else:
                lines.append(
                    f'<li><span style="opacity:.5;padding:0.5em 0.75em;display:block">{label}</span></li>'
                )

    return "\n".join(lines)


def _rewrite_md_to_html_links(html: str) -> str:
    """Replace href="something.md" with href="something.html" in rendered HTML."""

    def _replace(m: re.Match) -> str:
        href = m.group(1)
        if href.startswith("http") or href.startswith("#"):
            return f'href="{href}"'

        suffix = ""
        split_at = len(href)
        for marker in ("#", "?"):
            idx = href.find(marker)
            if idx != -1 and idx < split_at:
                split_at = idx
        if split_at != len(href):
            suffix = href[split_at:]
            href = href[:split_at]

        if href.endswith(".md"):
            href = re.sub(r"\.md$", ".html", href) + suffix
        else:
            href = href + suffix
        return f'href="{href}"'

    return re.sub(r'href="([^"]*)"', _replace, html)


def _fix_markdown_links(content: str) -> str:
    """Percent-encode spaces in markdown link URLs so the parser handles them."""

    def _fix_url(m: re.Match) -> str:
        text, url = m.group(1), m.group(2)
        if " " in url:
            url = url.replace(" ", "%20")
        return f"[{text}]({url})"

    return re.sub(r"\[([^\]]*)\]\(([^)]*)\)", _fix_url, content)


# Pre-compiled regex for server-side math delimiter normalisation.
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_LATEX_CMD_RE = re.compile(r"\\[A-Za-z]")  # detects real LaTeX commands
_DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_MATH_RE = re.compile(r"\$(?!\s)([^$\n]+?)\$(?!\$)")
# Backslash-delimited math: \[...\] and \(...\) — generated directly by some LLMs.
# Must be extracted before markdown-it, which otherwise escapes \[ → [ per CommonMark.
_DISPLAY_MATH_BK_RE = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)
_INLINE_MATH_BK_RE = re.compile(r"\\\((.+?)\\\)")


def _extract_math_blocks(content: str) -> tuple[str, list[tuple[str, str]]]:
    """Extract math blocks BEFORE markdown rendering to prevent markdown-it from
    escaping LaTeX delimiters (\\[ → [, \\( → () and double-backslashes (\\\\ → \\).

    Each block is replaced with an all-alphanumeric placeholder that markdown-it
    leaves untouched.  The companion list maps placeholder → HTML replacement with
    properly escaped content for the browser / KaTeX auto-render.

    Segments containing CJK characters are skipped so Chinese prose enclosed by
    dollar signs is never forwarded to KaTeX.

    $$...$$ is processed first to avoid partial matches with $...$.
    """

    protected: list[tuple[str, str]] = []

    def _norm_backslash(s: str) -> str:
        r"""Normalize ``\\cmd`` → ``\cmd`` for LaTeX commands.

        LLMs double backslashes for markdown escaping (``\\text`` means
        ``\text``).  We normalise ``\\`` followed by a letter to a single
        ``\``, but keep standalone ``\\`` (LaTeX line-break).
        """
        # \\<letter> → \<letter>  (e.g. \\text → \text, \\mathcal → \mathcal)
        # But keep \\\\ (literal double-backslash = LaTeX line-break)
        return re.sub(r"\\\\(?=[A-Za-z])", r"\\", s)

    def _is_cjk_prose(inner: str) -> bool:
        """Return True only when CJK chars appear in non-math content.

        ``$100$`` next to Chinese text → skip (CJK prose with dollar signs).
        ``$\\text{中文}$`` → real math (has LaTeX cmd), extract.
        """
        return bool(_CJK_RE.search(inner)) and not _LATEX_CMD_RE.search(inner)

    def _display(m: re.Match) -> str:
        inner = m.group(1)
        if _is_cjk_prose(inner):
            return m.group(0)
        idx = len(protected)
        ph = f"CWIKIMD{idx:06d}"
        normed = _norm_backslash(inner)
        # HTML-escape so & < > are safe in the DOM; KaTeX reads textContent
        # which the browser decodes back to the original LaTeX characters.
        escaped = _html.escape(normed, quote=False)
        protected.append((ph, f'<div class="math-block not-prose">\\[{escaped}\\]</div>'))
        return ph

    _PURE_NUMERIC_RE = re.compile(r"^[\d,.\s%+\-*/=]+$")

    def _inline(m: re.Match) -> str:
        inner = m.group(1)
        if _is_cjk_prose(inner):
            return m.group(0)
        # Only skip CJK-adjacent pure-numeric content like 价格$100$元.
        # Anything with letters (e.g. $O(W)$, $T_a$) is real math.
        if _PURE_NUMERIC_RE.match(inner):
            before = m.string[: m.start()].rstrip()
            after = m.string[m.end() :].lstrip()
            if (before and _CJK_RE.search(before[-1])) or (after and _CJK_RE.search(after[0])):
                return m.group(0)
        idx = len(protected)
        ph = f"CWIKIMI{idx:06d}"
        normed = _norm_backslash(inner)
        escaped = _html.escape(normed, quote=False)
        protected.append((ph, f'<span class="math-inline not-prose">\\({escaped}\\)</span>'))
        return ph

    content = _DISPLAY_MATH_RE.sub(_display, content)
    content = _INLINE_MATH_RE.sub(_inline, content)
    # Also extract backslash-delimited math that LLMs may write directly.
    # Process after $-delimited so placeholders from above are never re-matched.
    content = _DISPLAY_MATH_BK_RE.sub(_display, content)
    content = _INLINE_MATH_BK_RE.sub(_inline, content)
    return content, protected


def _restore_math_blocks(html: str, protected: list[tuple[str, str]]) -> str:
    """Restore protected math blocks after markdown rendering.

    markdown-it may wrap a block-level placeholder in a ``<p>`` tag; we strip
    that wrapper when restoring display-math divs.
    """
    for ph, math_html in protected:
        html = html.replace(f"<p>{ph}</p>", math_html)
        html = html.replace(ph, math_html)
    return html


# Lazy-initialised markdown parser (avoids top-level import of markdown_it
# which may not be installed in every environment).
_md_parser = None


def _get_md_parser():
    global _md_parser
    if _md_parser is None:
        from markdown_it import MarkdownIt

        _md_parser = MarkdownIt().enable("table").enable("strikethrough")
    return _md_parser


def _markdown_to_static_html(content: str) -> str:
    """Convert markdown to HTML for static output (no base_url rewriting needed)."""
    # Fix spaces only (no base_url — links will be rewritten to .html afterwards)
    content = _fix_markdown_links(content)
    # Extract math blocks BEFORE markdown rendering so markdown-it cannot escape
    # the LaTeX delimiters (\[ → [, \( → () or strip double-backslashes (\\ → \).
    content, protected_math = _extract_math_blocks(content)
    html = _get_md_parser().render(content)
    # Restore math blocks as raw HTML with proper KaTeX delimiters.
    html = _restore_math_blocks(html, protected_math)

    # Handle mermaid fences
    import html as html_module

    mermaid_re = re.compile(r'<pre><code class="language-mermaid">(.*?)</code></pre>', re.DOTALL)

    def _mermaid(m: re.Match) -> str:
        code = html_module.unescape(m.group(1))
        return f'<div class="mermaid not-prose">{code}</div>'

    html = mermaid_re.sub(_mermaid, html)

    # Rewrite .md links to .html
    html = _rewrite_md_to_html_links(html)
    return html


# ──────────────────────────────────────────────────────────────────────────────
# Main generator
# ──────────────────────────────────────────────────────────────────────────────


class StaticHTMLGenerator:
    """
    Generates a fully pre-rendered static website from a docs directory.

    For every ``<module>.md`` file found in *docs_dir* a corresponding
    ``<module>.html`` is written.  ``overview.md`` additionally produces
    ``index.html`` (the GitHub Pages root page).
    """

    def generate(self, docs_dir: Path, hide_repo_links: bool = False) -> list[str]:
        """
        Generate static HTML files in *docs_dir*.

        Args:
            docs_dir: Directory containing the generated Markdown files.
            hide_repo_links: When True, omit Repository and DeepWiki links
                from the sidebar metadata panel.

        Returns a list of filenames that were written.
        """
        docs_dir = docs_dir.resolve()
        logger.debug(f"Static HTML generator: docs_dir={docs_dir}")

        # Load shared data
        module_tree: Dict[str, Any] = {}
        mt_path = docs_dir / "module_tree.json"
        if mt_path.exists():
            try:
                module_tree = file_manager.load_json(str(mt_path)) or {}
            except Exception as e:
                logger.warning(f"Could not load module_tree.json: {e}")

        metadata: Optional[Dict[str, Any]] = None
        meta_path = docs_dir / "metadata.json"
        if meta_path.exists():
            try:
                metadata = file_manager.load_json(str(meta_path))
            except Exception as e:
                logger.warning(f"Could not load metadata.json: {e}")

        repo_name = docs_dir.parent.name or "Docs"

        # Collect all .md files
        md_files = sorted(f for f in docs_dir.glob("*.md") if not f.name.startswith("_"))
        if not md_files:
            logger.warning(f"No .md files found in {docs_dir}")
            return []

        # Pre-compute module_path → actual HTML filename using fuzzy matching,
        # so the sidebar links point to the right files even when the tree
        # structure changed between runs (different path prefixes).
        resolved_hrefs = _resolve_nav_hrefs(module_tree, str(docs_dir)) if module_tree else {}

        # Build filename → H1 title map for localized nav labels.
        # Each generated .md file's first heading is already in the target language.
        h1_titles = _extract_h1_titles(md_files)

        written: list[str] = []

        for md_path in md_files:
            stem = md_path.stem  # e.g. "overview", "auth"
            html_name = f"{stem}.html"  # e.g. "overview.html"

            # Render markdown → HTML
            try:
                md_content = file_manager.load_text(str(md_path))
            except Exception as e:
                logger.warning(f"Skipping {md_path.name}: {e}")
                continue

            content_html = _markdown_to_static_html(md_content)

            # Extract title from first H1 or fall back to stem
            title_match = re.search(r"<h1[^>]*>(.*?)</h1>", content_html, re.IGNORECASE | re.DOTALL)
            title = (
                re.sub(r"<[^>]+>", "", title_match.group(1)).strip()
                if title_match
                else stem.replace("_", " ").title()
            )

            # Build guide pages data for template
            _GUIDE_FALLBACK_LABELS = {
                "guide-getting-started": "Get Started",
                "guide-beginners-guide": "Beginner's Guide",
                "guide-build-and-organization": "Build & Code Organization",
                "guide-core-algorithms": "Core Algorithms",
            }
            guides: list[Dict[str, Any]] = []
            for slug, fallback_label in _GUIDE_FALLBACK_LABELS.items():
                md_file = docs_dir / f"{slug}.md"
                if not md_file.exists():
                    continue
                guide: Dict[str, Any] = {
                    "href": slug + ".html",
                    "label": h1_titles.get(slug, fallback_label),
                    "sub_pages": [],
                }
                sub_prefix = slug + "-"
                for sub_file in sorted(
                    f
                    for f in os.listdir(str(docs_dir))
                    if f.startswith(sub_prefix) and f.endswith(".md")
                ):
                    sub_stem = sub_file.removesuffix(".md")
                    sub_label = h1_titles.get(sub_stem)
                    if not sub_label:
                        raw = sub_file[len(sub_prefix) : -3]
                        m = re.match(r"^(\d+)-(.+)$", raw)
                        if m:
                            sub_label = f"{int(m.group(1))}. {m.group(2).replace('-', ' ').title()}"
                        else:
                            sub_label = raw.replace("-", " ").title()
                    guide["sub_pages"].append(
                        {"href": sub_file.replace(".md", ".html"), "label": sub_label}
                    )
                guides.append(guide)

            page = _PAGE_TEMPLATE.render(
                title=title,
                css=_CSS,
                repo_name=repo_name,
                metadata=metadata,
                hide_repo_links=hide_repo_links,
                current_page=html_name,
                nav_labels=h1_titles,
                guides=guides,
                module_tree=module_tree,
                resolved_hrefs=resolved_hrefs,
                render_nav=_render_nav,
                content=content_html,
            )

            out_path = docs_dir / html_name
            # Strip lone surrogates that can appear in LLM-generated content
            # (e.g. invalid Unicode from source code snippets).
            out_path.write_bytes(page.encode("utf-8", errors="replace"))
            written.append(html_name)
            logger.info(f"  ✓ {html_name}")

            # overview.md → also write index.html
            if stem == "overview":
                index_path = docs_dir / "index.html"
                index_path.write_bytes(page.encode("utf-8", errors="replace"))
                written.append("index.html")
                logger.info(f"  ✓ index.html (copy of overview.html)")

        return written
