#!/usr/bin/env python3
"""
HTML templates for the CodeWiki web application.
"""

from .html_sanitizer import sanitize_html


def prepare_docs_content(content: str) -> str:
    """Sanitize rendered docs HTML before it is injected with ``|safe``."""
    return sanitize_html(content)


# Web interface HTML template
WEB_INTERFACE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CodeWiki - GitHub Repository Documentation Generator</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bulma@1.0.4/css/bulma.min.css">
    <script>(function(){var t=localStorage.getItem('cw-theme')||(window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');document.documentElement.setAttribute('data-theme',t);})();</script>
</head>
<body style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 2rem;">
    <div class="card" style="max-width: 640px; margin: 0 auto;">
        <section class="hero is-primary">
            <div class="hero-body has-text-centered">
                <p class="title">&#128218; CodeWiki</p>
                <p class="subtitle">Generate comprehensive documentation for any GitHub repository</p>
            </div>
        </section>

        <div class="card-content">
            {% if message %}
            <div class="notification {{ 'is-success' if message_type == 'success' else 'is-danger' }}">
                {{ message }}
            </div>
            {% endif %}

            <form method="POST" action="/">
                <div class="field">
                    <label class="label" for="repo_url">GitHub Repository URL:</label>
                    <div class="control">
                        <input
                            type="url"
                            id="repo_url"
                            name="repo_url"
                            class="input"
                            placeholder="https://github.com/owner/repository"
                            required
                            value="{{ repo_url or '' }}"
                        >
                    </div>
                </div>

                <div class="field">
                    <label class="label" for="commit_id">Commit ID (optional):</label>
                    <div class="control">
                        <input
                            type="text"
                            id="commit_id"
                            name="commit_id"
                            class="input"
                            placeholder="Enter specific commit hash (defaults to latest)"
                            value="{{ commit_id or '' }}"
                            pattern="[a-f0-9]{4,40}"
                            title="Enter a valid commit hash (4-40 characters, hexadecimal)"
                        >
                    </div>
                </div>

                <div class="field mt-5">
                    <div class="control">
                        <button type="submit" class="button is-primary is-fullwidth">Generate Documentation</button>
                    </div>
                </div>
            </form>

            {% if recent_jobs %}
            <hr>
            <h3 class="title is-5 mb-3">Recent Jobs</h3>
            {% for job in recent_jobs %}
            <div class="box">
                <div class="is-flex is-justify-content-space-between is-align-items-center is-flex-wrap-wrap" style="gap: 0.5rem;">
                    <a href="{{ job.repo_url }}" target="_blank" rel="noopener" class="has-text-link has-text-weight-semibold is-size-7">&#128279; {{ job.repo_url }}</a>
                    <span class="tag {{ 'is-warning' if job.status == 'queued' else 'is-info' if job.status == 'processing' else 'is-success' if job.status == 'completed' else 'is-danger' }}">{{ job.status }}</span>
                </div>
                <p class="is-size-7 has-text-grey mt-2">{{ job.progress }}</p>
                {% if job.main_model %}
                <p class="is-size-7 has-text-grey-light">Generated with: {{ job.main_model }}</p>
                {% endif %}
                <div class="buttons are-small mt-3">
                    <a href="https://deepwiki.com/{{ job.repo_url | replace('https://github.com/', '') }}" target="_blank" rel="noopener" class="button is-small is-outlined">&#127760; DeepWiki</a>
                    <a href="/docs/{{ job.job_id }}" class="button is-small is-outlined is-primary">View Documentation</a>
                </div>
            </div>
            {% endfor %}
            {% endif %}
        </div>
    </div>

    <script>
        let isSubmitting = false;
        document.addEventListener('DOMContentLoaded', function() {
            const form = document.querySelector('form');
            const submitButton = document.querySelector('button[type="submit"]');
            if (form && submitButton) {
                form.addEventListener('submit', function(e) {
                    if (isSubmitting) { e.preventDefault(); return false; }
                    isSubmitting = true;
                    submitButton.disabled = true;
                    submitButton.classList.add('is-loading');
                    setTimeout(function() {
                        isSubmitting = false;
                        submitButton.disabled = false;
                        submitButton.classList.remove('is-loading');
                    }, 10000);
                });
            }
            const jobsHr = document.querySelector('hr');
            if (jobsHr) {
                const refreshBtn = document.createElement('button');
                refreshBtn.textContent = 'Refresh Status';
                refreshBtn.className = 'button is-small is-ghost mt-3';
                refreshBtn.onclick = function() { window.location.reload(); };
                const jobsParent = jobsHr.parentNode;
                jobsParent.appendChild(refreshBtn);
            }
        });
    </script>
</body>
</html>
"""

# HTML template for the documentation pages
DOCS_VIEW_TEMPLATE = """
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }} — {{ repo_name }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:ital,wght@0,400;0,500;1,400&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bulma@1.0.4/css/bulma.min.css">
<link id="hljs-css" rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11.9.0/dist/mermaid.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js" onload="renderMathInElement(document.getElementById('mc')||document.body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false}],throwOnError:false});"></script>
<script>(function(){var t=localStorage.getItem('cw-theme')||(window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');document.documentElement.setAttribute('data-theme',t);if(t==='dark'){document.getElementById('hljs-css').href='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css';}})();</script>
<style>
.cw-wrap{display:flex;min-height:100vh;padding-top:3.25rem;}
.cw-side{position:fixed;top:3.25rem;left:0;width:272px;height:calc(100vh - 3.25rem);overflow-y:auto;background:var(--bulma-scheme-main-bis);border-right:1px solid var(--bulma-border);padding:1rem 0.75rem 3rem;z-index:30;transition:transform .2s;}
.cw-side.off{transform:translateX(-272px);}
.cw-body{margin-left:272px;flex:1;min-width:0;transition:margin-left .2s;}
.cw-body.full{margin-left:0;}
.cw-content{display:flex;gap:2.5rem;max-width:1200px;margin:0 auto;padding:2.5rem 2rem;align-items:flex-start;}
.cw-article{flex:1;min-width:0;max-width:860px;}
.cw-overlay{display:none;position:fixed;inset:0;top:3.25rem;background:rgba(0,0,0,.4);z-index:25;}
.cw-overlay.on{display:block;}
.cw-toc{width:220px;flex-shrink:0;position:sticky;top:calc(3.25rem + 1.5rem);max-height:calc(100vh - 3.25rem - 3rem);overflow-y:auto;display:none;}
@media(min-width:1280px){.cw-toc{display:block;}}
.cw-toc .menu-label{font-size:0.65rem;}
.cw-toc .menu-list a{font-size:0.8rem;padding:0.25em 0.5em;}
.cw-toc .toc-h3 a{padding-left:1.5em;font-size:0.75rem;}
.cw-side .menu-list a{font-size:0.85rem;}
.cw-side .card{margin-bottom:0.75rem;}
.content pre code.hljs{background:transparent!important;}
.content .mermaid{max-width:none;margin:1rem 0;}
.content .math-block{overflow-x:auto;margin:1rem 0;}
.content .math-inline{display:inline;}
.content .math-err{color:var(--bulma-danger);font-style:italic;font-size:0.85em;}
.content pre:focus-visible{box-shadow:0 0 0 3px var(--bulma-link);}
.hljs{background:transparent!important;}
#btt{position:fixed;bottom:1.5rem;right:1.5rem;z-index:100;display:none;}
#btt.on{display:inline-flex;}
@media(max-width:768px){.cw-side{transform:translateX(-272px);}.cw-side.on{transform:translateX(0);}.cw-body{margin-left:0;}.cw-content{padding:1.5rem 1rem;gap:0;}}
@media(min-width:769px){.cw-side{transform:none;}.cw-side.off{transform:translateX(-272px);}}
</style>
</head>
<body style="font-family:Inter,system-ui,sans-serif;">
<nav class="navbar is-fixed-top" role="navigation" aria-label="main navigation">
  <div class="navbar-brand">
    <button class="navbar-burger" id="cw-burger" aria-label="Toggle sidebar" aria-expanded="false">
      <span aria-hidden="true"></span><span aria-hidden="true"></span><span aria-hidden="true"></span><span aria-hidden="true"></span>
    </button>
    <a class="navbar-item has-text-link has-text-weight-bold" href="/static-docs/{{ job_id }}/overview.md">&#128218; {{ repo_name }}</a>
  </div>
  <div class="navbar-end">
    <div class="navbar-item">
      <div class="buttons">
        <a href="/" id="site-home-btn" class="button is-small is-ghost" title="Back to main site" aria-label="Back to main site">&#127968;</a>
        <button class="button is-small is-ghost" id="theme-btn" title="Toggle theme" aria-label="Toggle light/dark theme">&#127769;</button>
      </div>
    </div>
  </div>
</nav>
<div class="cw-overlay" id="cw-overlay"></div>
<div class="cw-wrap">
  <aside class="cw-side" id="cw-side">
    <div class="menu">
      {% if metadata and metadata.generation_info %}
      <div class="card mb-4">
        <div class="card-content" style="padding:0.75rem;">
          {% if metadata.generation_info.main_model %}<div class="is-size-7"><b class="has-text-grey">Model:</b> {{ metadata.generation_info.main_model }}</div>{% endif %}
          {% if metadata.generation_info.timestamp %}<div class="is-size-7"><b class="has-text-grey">Generated:</b> {{ metadata.generation_info.timestamp[:16] }}</div>{% endif %}
          {% if metadata.generation_info.commit_id %}<div class="is-size-7"><b class="has-text-grey">Commit:</b> {{ metadata.generation_info.commit_id[:8] }}</div>{% endif %}
          {% if metadata.statistics and metadata.statistics.total_components %}<div class="is-size-7"><b class="has-text-grey">Components:</b> {{ metadata.statistics.total_components }}</div>{% endif %}
          {% if metadata.generation_info.repo_url %}
          <div class="mt-2" style="display:flex;gap:0.5rem;flex-wrap:wrap;">
            <a href="{{ metadata.generation_info.repo_url }}" target="_blank" rel="noopener" class="has-text-link is-size-7">&#128279; Repository</a>
            {% if 'github.com' in metadata.generation_info.repo_url %}
            <a href="https://deepwiki.com/{{ metadata.generation_info.repo_url.split('github.com/')[-1] }}" target="_blank" rel="noopener" class="has-text-link is-size-7">&#127760; DeepWiki</a>
            {% endif %}
          </div>
          {% endif %}
        </div>
      </div>
      {% endif %}
      {% if navigation %}
      <ul class="menu-list">
        <li><a href="/static-docs/{{ job_id }}/overview.md"{% if current_page == 'overview.md' %} class="is-active"{% endif %}>Overview</a></li>
        {% macro render_nav_item(key, data, depth=0) %}
          {% set has_ch = data.children and data.children|length > 0 %}
          {% if has_ch %}
          <li>
            {% if data.doc_exists is not defined or data.doc_exists %}
            <a href="/static-docs/{{ job_id }}/{{ data.doc_filename }}"{% if current_page == data.doc_filename %} class="is-active"{% endif %}>{{ key.replace('_', ' ').title() }}</a>
            {% else %}
            <span class="has-text-grey-light">{{ key.replace('_', ' ').title() }}</span>
            {% endif %}
            <ul>
              {% for ck, cd in data.children.items() %}{{ render_nav_item(ck, cd, depth + 1) }}{% endfor %}
            </ul>
          </li>
          {% else %}
          <li>
            {% if data.doc_exists is not defined or data.doc_exists %}
            <a href="/static-docs/{{ job_id }}/{{ data.doc_filename }}"{% if current_page == data.doc_filename %} class="is-active"{% endif %}>{{ key.replace('_', ' ').title() }}</a>
            {% else %}
            <span class="has-text-grey-light">{{ key.replace('_', ' ').title() }}</span>
            {% endif %}
          </li>
          {% endif %}
        {% endmacro %}
        {% for sk, sd in navigation.items() %}{{ render_nav_item(sk, sd) }}{% endfor %}
      </ul>
      {% endif %}
    </div>
  </aside>
  <div class="cw-body" id="cw-body">
    <div class="cw-content">
      <article id="mc" class="cw-article content is-medium">{{ content | safe }}</article>
      <aside class="cw-toc" id="toc">
        <div class="menu">
          <p class="menu-label">On this page</p>
          <ul class="menu-list" id="toc-ul"></ul>
        </div>
      </aside>
    </div>
  </div>
</div>
<button id="btt" class="button is-primary is-rounded is-small" title="Back to top">&#8593;</button>
<script>
document.getElementById('site-home-btn').href = window.location.origin + '/';
var _hljsBase='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/';
var html=document.documentElement,themeBtn=document.getElementById('theme-btn');
function curTheme(){return html.getAttribute('data-theme')||(window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');}
function setTheme(t){html.setAttribute('data-theme',t);localStorage.setItem('cw-theme',t);themeBtn.innerHTML=t==='dark'?'&#9728;&#65039;':'&#127769;';document.getElementById('hljs-css').href=_hljsBase+(t==='dark'?'github-dark':'github')+'.min.css';}
setTheme(curTheme());
themeBtn.addEventListener('click',function(){setTheme(curTheme()==='dark'?'light':'dark');});
document.addEventListener('DOMContentLoaded',function(){
  hljs.highlightAll();
  document.querySelectorAll('article pre').forEach(function(pre){
    pre.setAttribute('tabindex','0');
    pre.setAttribute('role','region');
    pre.setAttribute('aria-label','Code block');
  });
});
// Sidebar toggle
var cwSide=document.getElementById('cw-side'),cwBody=document.getElementById('cw-body'),cwOverlay=document.getElementById('cw-overlay'),cwBurger=document.getElementById('cw-burger');
function isMobile(){return window.innerWidth<769;}
function openSidebar(){cwSide.classList.remove('off');cwBody.classList.remove('full');if(isMobile()){cwSide.classList.add('on');cwOverlay.classList.add('on');}localStorage.setItem('cw-sb','on');}
function closeSidebar(){if(isMobile()){cwSide.classList.remove('on');cwOverlay.classList.remove('on');}else{cwSide.classList.add('off');cwBody.classList.add('full');}localStorage.setItem('cw-sb','off');}
function toggleSidebar(){if(isMobile()?cwSide.classList.contains('on'):!cwSide.classList.contains('off')){closeSidebar();}else{openSidebar();}}
cwBurger.addEventListener('click',toggleSidebar);
cwOverlay.addEventListener('click',closeSidebar);
if(!isMobile()&&localStorage.getItem('cw-sb')==='off'){cwSide.classList.add('off');cwBody.classList.add('full');}
document.addEventListener('keydown',function(e){if(e.key==='Escape'){closeSidebar();}});
window.addEventListener('resize',function(){if(!isMobile()){cwOverlay.classList.remove('on');cwSide.classList.remove('on');if(localStorage.getItem('cw-sb')!=='off'){cwSide.classList.remove('off');cwBody.classList.remove('full');}}});
// NOTE: DOCS_VIEW uses katex auto-render (not cwRenderMath), so no MathJax fallback.
// This is consistent with current behavior — not a regression.
// TOC
(function(){
  var mc=document.getElementById('mc'),ul=document.getElementById('toc-ul'),toc=document.getElementById('toc');
  if(!mc||!ul)return;
  var hs=mc.querySelectorAll('h2,h3');
  if(hs.length<2){if(toc)toc.style.display='none';return;}
  hs.forEach(function(h){
    if(!h.id)return;
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
// Back to top
var btt=document.getElementById('btt');
window.addEventListener('scroll',function(){btt.classList.toggle('on',window.scrollY>300);});
btt.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'});});
// Mermaid
async function cwRenderMermaid(){
  var theme=document.documentElement.getAttribute('data-theme')==='dark'?'dark':'default';
  mermaid.initialize({startOnLoad:false,theme:theme,themeVariables:{primaryColor:'#2563eb',lineColor:'#64748b'},flowchart:{htmlLabels:true,curve:'basis'},sequence:{mirrorActors:false,useMaxWidth:true}});
  var els=document.querySelectorAll('.mermaid');
  for(var i=0;i<els.length;i++){
    var el=els[i];
    var src=el.getAttribute('data-mermaid-src');
    if(!src){src=el.textContent.trim();el.setAttribute('data-mermaid-src',src);}
    else{el.textContent=src;}
    try{
      var r=await mermaid.render('mermaid-'+Date.now()+'-'+i,src);
      el.innerHTML=r.svg;
    }catch(err){
      el.innerHTML='<details open><summary class="has-text-danger" style="cursor:pointer;">&#9888; Mermaid error</summary><pre class="is-size-7 mt-2" style="white-space:pre-wrap;">'+err.message+'</pre><pre class="is-size-7 has-text-grey-light">'+src.replace(/</g,'&lt;')+'</pre></details>';
    }
  }
}
document.addEventListener('DOMContentLoaded',cwRenderMermaid);
themeBtn.addEventListener('click',function(){setTimeout(cwRenderMermaid,50);});
</script>
</body>
</html>
"""

NOT_FOUND_TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Page Not Found — CodeWiki</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bulma@1.0.4/css/bulma.min.css">
<script>(function(){var t=localStorage.getItem('cw-theme')||(window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');document.documentElement.setAttribute('data-theme',t);})();</script>
</head>
<body>
<section class="hero is-medium is-bold">
  <div class="hero-body">
    <div class="container has-text-centered">
      <div class="box" style="max-width:480px;margin:0 auto;">
        <p class="title is-1 has-text-primary mb-4">404</p>
        <h1 class="title is-4 mb-2">Page not found</h1>
        <p class="has-text-grey mb-5">The page you&#8217;re looking for doesn&#8217;t exist or has been moved.</p>
        <a href="/" class="button is-primary">&#8592; Back to Home</a>
        <p class="is-size-7 has-text-grey-light mt-4">If you followed a link inside a documentation page, the file may not have been generated yet.</p>
      </div>
    </div>
  </div>
</section>
</body>
</html>"""
