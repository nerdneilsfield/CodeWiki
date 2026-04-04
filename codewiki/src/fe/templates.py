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
    <link href="https://cdn.jsdelivr.net/npm/daisyui@5" rel="stylesheet" type="text/css" />
    <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
</head>
<body class="min-h-screen p-4 md:p-8" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">
    <div class="card w-full max-w-2xl mx-auto shadow-2xl bg-base-100">
        <div class="bg-primary text-primary-content p-8 text-center rounded-t-2xl">
            <h1 class="text-3xl font-bold mb-2">&#128218; CodeWiki</h1>
            <p class="opacity-90">Generate comprehensive documentation for any GitHub repository</p>
        </div>

        <div class="card-body">
            {% if message %}
            <div class="alert {{ 'alert-success' if message_type == 'success' else 'alert-error' }}">
                <span>{{ message }}</span>
            </div>
            {% endif %}

            <form method="POST" action="/">
                <div class="form-control mb-4">
                    <label class="label" for="repo_url">
                        <span class="label-text font-semibold">GitHub Repository URL:</span>
                    </label>
                    <input
                        type="url"
                        id="repo_url"
                        name="repo_url"
                        class="input input-bordered w-full"
                        placeholder="https://github.com/owner/repository"
                        required
                        value="{{ repo_url or '' }}"
                    >
                </div>

                <div class="form-control mb-6">
                    <label class="label" for="commit_id">
                        <span class="label-text font-semibold">Commit ID (optional):</span>
                    </label>
                    <input
                        type="text"
                        id="commit_id"
                        name="commit_id"
                        class="input input-bordered w-full"
                        placeholder="Enter specific commit hash (defaults to latest)"
                        value="{{ commit_id or '' }}"
                        pattern="[a-f0-9]{4,40}"
                        title="Enter a valid commit hash (4-40 characters, hexadecimal)"
                    >
                </div>

                <button type="submit" class="btn btn-primary w-full">Generate Documentation</button>
            </form>

            {% if recent_jobs %}
            <div class="divider mt-6"></div>
            <h3 class="text-lg font-semibold mb-3">Recent Jobs</h3>
            {% for job in recent_jobs %}
            <div class="card card-compact bg-base-200 mb-3">
                <div class="card-body">
                    <div class="flex justify-between items-center flex-wrap gap-2">
                        <a href="{{ job.repo_url }}" target="_blank" rel="noopener" class="link link-primary font-semibold text-sm">&#128279; {{ job.repo_url }}</a>
                        <span class="badge {{ 'badge-warning' if job.status == 'queued' else 'badge-info' if job.status == 'processing' else 'badge-success' if job.status == 'completed' else 'badge-error' }}">{{ job.status }}</span>
                    </div>
                    <p class="text-sm opacity-70">{{ job.progress }}</p>
                    {% if job.main_model %}
                    <p class="text-xs opacity-50">Generated with: {{ job.main_model }}</p>
                    {% endif %}
                    <div class="card-actions mt-2">
                        <a href="https://deepwiki.com/{{ job.repo_url | replace('https://github.com/', '') }}" target="_blank" rel="noopener" class="btn btn-sm btn-outline">&#127760; DeepWiki</a>
                        <a href="/docs/{{ job.job_id }}" class="btn btn-sm btn-outline btn-primary">View Documentation</a>
                    </div>
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
                    submitButton.innerHTML = '<span class="loading loading-spinner loading-sm"></span> Processing...';
                    setTimeout(function() {
                        isSubmitting = false;
                        submitButton.disabled = false;
                        submitButton.textContent = 'Generate Documentation';
                    }, 10000);
                });
            }
            const recentJobs = document.querySelector('.divider');
            if (recentJobs) {
                const refreshBtn = document.createElement('button');
                refreshBtn.textContent = 'Refresh Status';
                refreshBtn.className = 'btn btn-sm btn-ghost mt-3';
                refreshBtn.onclick = function() { window.location.reload(); };
                const jobsParent = recentJobs.parentNode;
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
<link href="https://cdn.jsdelivr.net/npm/daisyui@5" rel="stylesheet" type="text/css" />
<script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
<link id="hljs-css" rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11.9.0/dist/mermaid.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js" onload="renderMathInElement(document.getElementById('mc')||document.body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false}],throwOnError:false});"></script>
<script>(function(){var t=localStorage.getItem('cw-theme')||(window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');document.documentElement.setAttribute('data-theme',t);if(t==='dark'){document.getElementById('hljs-css').href='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css';}})();</script>
<style type="text/tailwindcss">
@import "tailwindcss";
@plugin "https://esm.sh/@tailwindcss/typography@0.5";
</style>
<style>
/* DOCS_VIEW uses katex auto-render → produces .katex-display/.katex (NOT .math-block/.math-inline) */
.prose pre code.hljs { background: transparent !important; }
.prose .katex code::before, .prose .katex code::after { content: none; }
.prose .mermaid { max-width: none; }
.prose .katex-display { overflow-x: auto; max-width: none; }
.prose table { display: block; max-width: 100%; overflow-x: auto; }
.prose pre:focus-visible { box-shadow: 0 0 0 3px oklch(var(--p)); }
</style>
</head>
<body class="bg-base-100 font-[Inter,system-ui,sans-serif]">
<div class="drawer lg:drawer-open">
  <input id="cw-drawer" type="checkbox" class="drawer-toggle" />
  <div class="drawer-content flex flex-col">
    <header class="navbar bg-base-200 shadow-sm sticky top-0 z-50">
      <div class="flex-none lg:hidden">
        <label for="cw-drawer" class="btn btn-ghost btn-square btn-sm" aria-label="Toggle sidebar">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="inline-block h-5 w-5 stroke-current"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path></svg>
        </label>
      </div>
      <div class="flex-1 px-2">
        <a href="/static-docs/{{ job_id }}/overview.md" class="btn btn-ghost text-primary font-bold text-base normal-case">&#128218; {{ repo_name }}</a>
      </div>
      <div class="flex-none gap-1">
        <a href="/" id="site-home-btn" class="btn btn-ghost btn-square btn-sm" title="Back to main site" aria-label="Back to main site">&#127968;</a>
        <button class="btn btn-ghost btn-square btn-sm" id="theme-btn" title="Toggle theme" aria-label="Toggle light/dark theme">&#127769;</button>
      </div>
    </header>
    <main class="flex justify-center px-4 py-8 lg:px-8">
      <div class="flex gap-8 w-full max-w-6xl items-start">
        <article id="mc" class="prose prose-lg max-w-none flex-1 min-w-0">{{ content | safe }}</article>
        <aside class="hidden xl:block w-56 shrink-0 sticky top-20 max-h-[calc(100vh-6rem)] overflow-y-auto" id="toc">
          <div class="menu-title text-xs uppercase tracking-wider opacity-60">On this page</div>
          <ul id="toc-ul" class="menu menu-sm"></ul>
        </aside>
      </div>
    </main>
  </div>
  <div class="drawer-side z-40">
    <label for="cw-drawer" aria-label="close sidebar" class="drawer-overlay"></label>
    <div class="bg-base-200 min-h-full w-72 p-4">
      <ul class="menu w-full">
        {% if metadata and metadata.generation_info %}
        <li>
          <div class="card card-compact bg-base-100 shadow-sm mb-2">
            <div class="card-body p-3">
              {% if metadata.generation_info.main_model %}<div class="text-xs"><b class="opacity-70">Model:</b> {{ metadata.generation_info.main_model }}</div>{% endif %}
              {% if metadata.generation_info.timestamp %}<div class="text-xs"><b class="opacity-70">Generated:</b> {{ metadata.generation_info.timestamp[:16] }}</div>{% endif %}
              {% if metadata.generation_info.commit_id %}<div class="text-xs"><b class="opacity-70">Commit:</b> {{ metadata.generation_info.commit_id[:8] }}</div>{% endif %}
              {% if metadata.statistics and metadata.statistics.total_components %}<div class="text-xs"><b class="opacity-70">Components:</b> {{ metadata.statistics.total_components }}</div>{% endif %}
              {% if metadata.generation_info.repo_url %}
              <div class="flex gap-2 flex-wrap mt-2">
                <a href="{{ metadata.generation_info.repo_url }}" target="_blank" rel="noopener" class="link link-primary text-xs">&#128279; Repository</a>
                {% if 'github.com' in metadata.generation_info.repo_url %}
                <a href="https://deepwiki.com/{{ metadata.generation_info.repo_url.split('github.com/')[-1] }}" target="_blank" rel="noopener" class="link link-primary text-xs">&#127760; DeepWiki</a>
                {% endif %}
              </div>
              {% endif %}
            </div>
          </div>
        </li>
        {% endif %}
        {% if navigation %}
        <li><a href="/static-docs/{{ job_id }}/overview.md"{% if current_page == 'overview.md' %} class="active"{% endif %}>Overview</a></li>
        {% macro render_nav_item(key, data, depth=0) %}
          {% set has_ch = data.children and data.children|length > 0 %}
          {% if has_ch %}
          <li>
            <details open>
              {% if data.doc_exists is not defined or data.doc_exists %}
              <summary><a href="/static-docs/{{ job_id }}/{{ data.doc_filename }}"{% if current_page == data.doc_filename %} class="active"{% endif %}>{{ key.replace('_', ' ').title() }}</a></summary>
              {% else %}
              <summary class="opacity-50">{{ key.replace('_', ' ').title() }}</summary>
              {% endif %}
              <ul>
                {% for ck, cd in data.children.items() %}{{ render_nav_item(ck, cd, depth + 1) }}{% endfor %}
              </ul>
            </details>
          </li>
          {% else %}
          <li>
            {% if data.doc_exists is not defined or data.doc_exists %}
            <a href="/static-docs/{{ job_id }}/{{ data.doc_filename }}"{% if current_page == data.doc_filename %} class="active"{% endif %}>{{ key.replace('_', ' ').title() }}</a>
            {% else %}
            <span class="opacity-50">{{ key.replace('_', ' ').title() }}</span>
            {% endif %}
          </li>
          {% endif %}
        {% endmacro %}
        {% for sk, sd in navigation.items() %}{{ render_nav_item(sk, sd) }}{% endfor %}
        {% endif %}
      </ul>
    </div>
  </div>
</div>
<button id="btt" class="btn btn-circle btn-primary btn-sm fixed bottom-6 right-6 z-50 hidden shadow-lg" title="Back to top">&#8593;</button>
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
// Sidebar
var drawerCb=document.getElementById('cw-drawer');
if(window.innerWidth>=1024 && localStorage.getItem('cw-sb')==='off'){
  document.querySelector('.drawer').classList.remove('lg:drawer-open');
}
drawerCb.addEventListener('change',function(){
  if(window.innerWidth>=1024){
    localStorage.setItem('cw-sb',drawerCb.checked?'on':'off');
    var d=document.querySelector('.drawer');
    if(drawerCb.checked)d.classList.add('lg:drawer-open');
    else d.classList.remove('lg:drawer-open');
  }
});
document.addEventListener('keydown',function(e){
  if(e.key==='Escape'&&drawerCb.checked){drawerCb.checked=false;drawerCb.dispatchEvent(new Event('change'));}
});
// Resize: restore drawer-open when returning to desktop if not manually closed
window.addEventListener('resize',function(){
  var d=document.querySelector('.drawer');
  if(window.innerWidth>=1024){
    if(localStorage.getItem('cw-sb')!=='off') d.classList.add('lg:drawer-open');
  }
});
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
    var a=document.createElement('a');a.href='#'+h.id;a.textContent=h.textContent;
    if(h.tagName==='H3')a.classList.add('pl-4','text-xs');
    li.appendChild(a);ul.appendChild(li);
  });
  var obs=new IntersectionObserver(function(entries){
    entries.forEach(function(e){var a=ul.querySelector('a[href="#'+e.target.id+'"]');if(a)a.classList.toggle('active',e.isIntersecting);});
  },{rootMargin:'-15% 0% -75% 0%'});
  hs.forEach(function(h){obs.observe(h);});
})();
// Back to top
var btt=document.getElementById('btt');
window.addEventListener('scroll',function(){btt.classList.toggle('hidden',window.scrollY<=300);});
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
      el.innerHTML='<details open><summary class="text-error cursor-pointer">&#9888; Mermaid error</summary><pre class="text-xs mt-2 whitespace-pre-wrap">'+err.message+'</pre><pre class="text-xs opacity-60">'+src.replace(/</g,'&lt;')+'</pre></details>';
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
<link href="https://cdn.jsdelivr.net/npm/daisyui@5" rel="stylesheet" type="text/css" />
<script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
<script>(function(){var t=localStorage.getItem('cw-theme')||(window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');document.documentElement.setAttribute('data-theme',t);})();</script>
</head>
<body class="bg-base-200 flex items-center justify-center min-h-screen p-6">
<div class="card bg-base-100 shadow-xl max-w-md w-full">
  <div class="card-body items-center text-center">
    <div class="text-7xl font-bold text-primary mb-4">404</div>
    <h1 class="text-xl font-semibold mb-2">Page not found</h1>
    <p class="opacity-70 mb-6">The page you&#8217;re looking for doesn&#8217;t exist or has been moved.</p>
    <a href="/" class="btn btn-primary">&#8592; Back to Home</a>
    <p class="text-xs opacity-50 mt-4">If you followed a link inside a documentation page, the file may not have been generated yet.</p>
  </div>
</div>
</body>
</html>"""
