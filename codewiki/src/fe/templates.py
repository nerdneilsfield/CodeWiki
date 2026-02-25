#!/usr/bin/env python3
"""
HTML templates for the CodeWiki web application.
"""

# Web interface HTML template
WEB_INTERFACE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CodeWiki - GitHub Repository Documentation Generator</title>
    <style>
        :root {
            --primary-color: #2563eb;
            --secondary-color: #f1f5f9;
            --text-color: #334155;
            --border-color: #e2e8f0;
            --success-color: #10b981;
            --warning-color: #f59e0b;
            --error-color: #ef4444;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: var(--text-color);
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 800px;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
            overflow: hidden;
        }
        
        .header {
            background: var(--primary-color);
            color: white;
            padding: 2rem;
            text-align: center;
        }
        
        .header h1 {
            font-size: 2.5rem;
            margin-bottom: 0.5rem;
            font-weight: 700;
        }
        
        .header p {
            font-size: 1.1rem;
            opacity: 0.9;
        }
        
        .content {
            padding: 2rem;
        }
        
        .form-group {
            margin-bottom: 1.5rem;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 0.5rem;
            font-weight: 600;
            color: var(--text-color);
        }
        
        .form-group input {
            width: 100%;
            padding: 0.75rem 1rem;
            border: 2px solid var(--border-color);
            border-radius: 8px;
            font-size: 1rem;
            transition: border-color 0.2s ease;
        }
        
        .form-group input:focus {
            outline: none;
            border-color: var(--primary-color);
        }
        
        .btn {
            display: inline-block;
            padding: 0.75rem 2rem;
            background: var(--primary-color);
            color: white;
            text-decoration: none;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 1rem;
            font-weight: 600;
            transition: all 0.2s ease;
        }
        
        .btn:hover {
            background: #1d4ed8;
            transform: translateY(-1px);
        }
        
        .btn:disabled {
            background: #94a3b8;
            cursor: not-allowed;
            transform: none;
        }
        
        .alert {
            padding: 1rem;
            border-radius: 8px;
            margin-bottom: 1rem;
        }
        
        .alert-success {
            background: #dcfce7;
            color: #166534;
            border: 1px solid #bbf7d0;
        }
        
        .alert-error {
            background: #fef2f2;
            color: #991b1b;
            border: 1px solid #fecaca;
        }
        
        .recent-jobs {
            margin-top: 2rem;
            border-top: 1px solid var(--border-color);
            padding-top: 2rem;
        }
        
        .job-item {
            background: var(--secondary-color);
            padding: 1rem;
            border-radius: 8px;
            margin-bottom: 1rem;
        }
        
        .job-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.5rem;
        }
        
        .job-url {
            font-weight: 600;
            color: var(--primary-color);
        }
        
        .job-status {
            padding: 0.25rem 0.75rem;
            border-radius: 16px;
            font-size: 0.875rem;
            font-weight: 600;
        }
        
        .status-queued {
            background: #fef3c7;
            color: #92400e;
        }
        
        .status-processing {
            background: #dbeafe;
            color: #1e40af;
        }
        
        .status-completed {
            background: #dcfce7;
            color: #166534;
        }
        
        .status-failed {
            background: #fef2f2;
            color: #991b1b;
        }
        
        .job-progress {
            font-size: 0.875rem;
            color: #64748b;
            margin-top: 0.25rem;
        }
        
        .job-actions {
            margin-top: 0.5rem;
        }
        
        .btn-small {
            padding: 0.5rem 1rem;
            font-size: 0.875rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📚 CodeWiki</h1>
            <p>Generate comprehensive documentation for any GitHub repository</p>
        </div>
        
        <div class="content">
            {% if message %}
            <div class="alert alert-{{ message_type }}">
                {{ message }}
            </div>
            {% endif %}
            
            <form method="POST" action="/">
                <div class="form-group">
                    <label for="repo_url">GitHub Repository URL:</label>
                    <input 
                        type="url" 
                        id="repo_url" 
                        name="repo_url" 
                        placeholder="https://github.com/owner/repository"
                        required
                        value="{{ repo_url or '' }}"
                    >
                </div>
                
                <div class="form-group">
                    <label for="commit_id">Commit ID (optional):</label>
                    <input 
                        type="text" 
                        id="commit_id" 
                        name="commit_id" 
                        placeholder="Enter specific commit hash (defaults to latest)"
                        value="{{ commit_id or '' }}"
                        pattern="[a-f0-9]{4,40}"
                        title="Enter a valid commit hash (4-40 characters, hexadecimal)"
                    >
                </div>
                
                <button type="submit" class="btn">Generate Documentation</button>
            </form>
            
            {% if recent_jobs %}
            <div class="recent-jobs">
                <h3>Recent Jobs</h3>
                {% for job in recent_jobs %}
                <div class="job-item">
                    <div class="job-header">
                        <a href="{{ job.repo_url }}" target="_blank" rel="noopener" class="job-url">🔗 {{ job.repo_url }}</a>
                        <div class="job-status status-{{ job.status }}">{{ job.status }}</div>
                    </div>
                    <div class="job-progress">{{ job.progress }}</div>
                    {% if job.main_model %}
                    <div class="job-model" style="font-size: 0.75rem; color: #64748b; margin-top: 0.25rem;">
                        Generated with: {{ job.main_model }}
                    </div>
                    {% endif %}
                    <div class="job-actions">
                        <a href="https://deepwiki.com/{{ job.repo_url | replace('https://github.com/', '') }}" target="_blank" rel="noopener" class="btn btn-small">🌐 DeepWiki</a>
                        <a href="/docs/{{ job.job_id }}" class="btn btn-small">View Documentation</a>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% endif %}
        </div>
    </div>
    
    <script>
        // Form submission protection
        let isSubmitting = false;
        
        document.addEventListener('DOMContentLoaded', function() {
            const form = document.querySelector('form');
            const submitButton = document.querySelector('button[type="submit"]');
            
            if (form && submitButton) {
                form.addEventListener('submit', function(e) {
                    if (isSubmitting) {
                        e.preventDefault();
                        return false;
                    }
                    
                    isSubmitting = true;
                    submitButton.disabled = true;
                    submitButton.textContent = 'Processing...';
                    
                    // Re-enable after 10 seconds as a failsafe
                    setTimeout(function() {
                        isSubmitting = false;
                        submitButton.disabled = false;
                        submitButton.textContent = 'Generate Documentation';
                    }, 10000);
                });
            }
            
            // Optional: Add manual refresh button instead of auto-refresh
            const refreshButton = document.createElement('button');
            refreshButton.textContent = 'Refresh Status';
            refreshButton.className = 'btn btn-small';
            refreshButton.style.marginTop = '1rem';
            refreshButton.onclick = function() {
                window.location.reload();
            };
            
            const recentJobsSection = document.querySelector('.recent-jobs');
            if (recentJobsSection) {
                recentJobsSection.appendChild(refreshButton);
            }
        });
    </script>
</body>
</html>
"""

# HTML template for the documentation pages
DOCS_VIEW_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }} — {{ repo_name }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:ital,wght@0,400;0,500;1,400&display=swap" rel="stylesheet">
<link id="hljs-css" rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11.9.0/dist/mermaid.min.js"></script>
<script>(function(){var t=localStorage.getItem('cw-theme')||(window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');document.documentElement.setAttribute('data-theme',t);if(t==='dark'){document.getElementById('hljs-css').href='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css';}})();</script>
<style>
:root{
  --bg:#fff;--bg2:#f8fafc;--bg3:#f1f5f9;--bg-code:#f1f5f9;--bg-pre:#f8fafc;
  --text:#1e293b;--text2:#475569;--text3:#64748b;
  --primary:#2563eb;--primary-h:#1d4ed8;--primary-lt:#eff6ff;
  --border:#e2e8f0;--shadow:rgba(0,0,0,.05);
  --sb-w:272px;--tb-h:56px;--r:6px;--tr:.18s ease;
}
[data-theme=dark]{
  --bg:#0f172a;--bg2:#1e293b;--bg3:#253047;--bg-code:#1e293b;--bg-pre:#162032;
  --text:#e2e8f0;--text2:#cbd5e1;--text3:#94a3b8;
  --primary:#60a5fa;--primary-h:#93c5fd;--primary-lt:#1e3a5f;
  --border:#334155;--shadow:rgba(0,0,0,.3);
}
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box;}
html{scroll-behavior:smooth;}
body{font-family:'Inter',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);line-height:1.7;font-size:15px;transition:background var(--tr),color var(--tr);}
a{color:var(--primary);text-decoration:none;}
a:hover{text-decoration:underline;}
/* topbar */
.tb{position:fixed;top:0;left:0;right:0;height:var(--tb-h);background:var(--bg2);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;padding:0 16px;z-index:200;box-shadow:0 1px 4px var(--shadow);}
.tb-logo{font-size:15px;font-weight:700;color:var(--primary);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-decoration:none;}
.tb-logo:hover{opacity:.85;text-decoration:none;}
.ib{width:34px;height:34px;display:flex;align-items:center;justify-content:center;border:1px solid var(--border);border-radius:var(--r);background:var(--bg);color:var(--text);cursor:pointer;font-size:14px;transition:var(--tr);flex-shrink:0;-webkit-appearance:none;appearance:none;}
.ib:hover{background:var(--bg3);border-color:var(--primary);}
/* overlay */
.ov{display:none;position:fixed;inset:0;top:var(--tb-h);background:rgba(0,0,0,.45);z-index:150;}
.ov.on{display:block;}
/* sidebar */
.sb{position:fixed;top:var(--tb-h);left:0;width:var(--sb-w);height:calc(100vh - var(--tb-h));background:var(--bg2);border-right:1px solid var(--border);overflow-y:auto;z-index:160;transition:transform var(--tr);padding:14px 10px 60px;}
.sb.off{transform:translateX(calc(-1 * var(--sb-w)));}
/* layout */
.layout{display:flex;padding-top:var(--tb-h);transition:padding-left var(--tr);}
.layout.sbon{padding-left:var(--sb-w);}
/* main */
.main{flex:1;min-width:0;display:flex;justify-content:center;}
.cw{width:100%;max-width:1200px;padding:44px 48px;display:flex;gap:44px;align-items:flex-start;}
article{flex:1;min-width:0;max-width:860px;}
/* toc */
.toc{width:220px;flex-shrink:0;position:sticky;top:calc(var(--tb-h) + 20px);max-height:calc(100vh - var(--tb-h) - 40px);overflow-y:auto;display:none;}
@media(min-width:1280px){.toc{display:block;}}
.toc-h{font-size:11px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid var(--border);}
.toc ul{list-style:none;}
.toc li a{font-size:12.5px;color:var(--text3);display:block;padding:3px 0 3px 12px;border-left:2px solid transparent;transition:var(--tr);text-decoration:none;}
.toc li a:hover{color:var(--primary);}
.toc li.on a{color:var(--primary);border-left-color:var(--primary);}
.toc li.h3 a{padding-left:24px;font-size:12px;}
/* nav */
.nav-meta{font-size:11px;color:var(--text3);line-height:1.6;padding:9px 12px;background:var(--bg);border:1px solid var(--border);border-radius:var(--r);margin-bottom:12px;}
.nav-meta b{color:var(--text2);}
.nav-row{display:flex;align-items:center;}
a.nv{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block;padding:6px 10px;border-radius:var(--r);color:var(--text2);font-size:13.5px;transition:var(--tr);text-decoration:none;}
a.nv:hover{background:var(--bg3);color:var(--primary);}
a.nv.on{background:var(--primary-lt);color:var(--primary);font-weight:600;}
.nvcaret{width:24px;height:28px;display:flex;align-items:center;justify-content:center;border:none;background:none;color:var(--text3);cursor:pointer;font-size:12px;border-radius:4px;transition:transform var(--tr);flex-shrink:0;}
.nvcaret:hover{color:var(--primary);}
.nvcaret.open{transform:rotate(90deg);}
.nvsub{overflow:hidden;}
/* markdown */
article h1{font-size:1.9rem;font-weight:700;border-bottom:2px solid var(--border);padding-bottom:.4rem;margin-bottom:1.2rem;line-height:1.3;}
article h2{font-size:1.45rem;font-weight:600;margin-top:2.2rem;margin-bottom:.7rem;border-bottom:1px solid var(--border);padding-bottom:.2rem;}
article h3{font-size:1.15rem;font-weight:600;margin-top:1.8rem;margin-bottom:.5rem;}
article h4{font-size:1rem;font-weight:600;margin-top:1.4rem;margin-bottom:.4rem;}
article p{margin-bottom:1rem;color:var(--text2);}
article ul,article ol{margin-bottom:1rem;padding-left:1.6rem;}
article li{margin-bottom:.3rem;color:var(--text2);}
article a{color:var(--primary);}
article a:hover{text-decoration:underline;}
article code{font-family:'JetBrains Mono',Consolas,monospace;font-size:.82em;background:var(--bg-code);padding:.15em .4em;border-radius:4px;color:var(--text);}
article pre{background:var(--bg-pre);border:1px solid var(--border);border-radius:8px;padding:1rem 1.2rem;overflow-x:auto;margin-bottom:1.2rem;}
article pre code{background:none;padding:0;font-size:.87em;}
article blockquote{border-left:4px solid var(--primary);padding:.5rem 1rem;margin-bottom:1rem;color:var(--text3);background:var(--primary-lt);border-radius:0 var(--r) var(--r) 0;}
article table{width:100%;border-collapse:collapse;margin-bottom:1rem;}
article th,article td{border:1px solid var(--border);padding:.6rem .8rem;text-align:left;}
article th{background:var(--bg2);font-weight:600;}
article img{max-width:100%;border-radius:var(--r);}
.mermaid{margin:1rem 0;}
.hljs{background:transparent!important;}
/* back to top */
#btt{position:fixed;bottom:24px;right:24px;width:40px;height:40px;background:var(--primary);color:#fff;border:none;border-radius:50%;font-size:16px;cursor:pointer;display:none;align-items:center;justify-content:center;box-shadow:0 4px 12px var(--shadow);z-index:100;transition:var(--tr);}
#btt:hover{background:var(--primary-h);transform:translateY(-2px);}
#btt.on{display:flex;}
/* responsive */
@media(max-width:767px){.cw{padding:24px 18px;gap:0;}}
@media(min-width:768px){.sb{transform:none;}.sb.off{transform:translateX(calc(-1 * var(--sb-w)));}}
</style>
</head>
<body>
<header class="tb">
  <button class="ib" id="sb-toggle" title="Toggle sidebar">☰</button>
  <a href="/static-docs/{{ job_id }}/overview.md" class="tb-logo">📚 {{ repo_name }}</a>
  <a href="/" id="site-home-btn" class="ib" title="Back to main site">&#127968;</a>
  <button class="ib" id="theme-btn" title="Toggle theme">&#127769;</button>
</header>
<div class="ov" id="ov"></div>
<div class="layout" id="layout">
  <nav class="sb" id="sb">
    {% if metadata and metadata.generation_info %}
    <div class="nav-meta">
      {% if metadata.generation_info.main_model %}<b>Model:</b> {{ metadata.generation_info.main_model }}<br>{% endif %}
      {% if metadata.generation_info.timestamp %}<b>Generated:</b> {{ metadata.generation_info.timestamp[:16] }}<br>{% endif %}
      {% if metadata.generation_info.commit_id %}<b>Commit:</b> {{ metadata.generation_info.commit_id[:8] }}<br>{% endif %}
      {% if metadata.statistics and metadata.statistics.total_components %}<b>Components:</b> {{ metadata.statistics.total_components }}<br>{% endif %}
      {% if metadata.generation_info.repo_url %}
      <div style="margin-top:6px;display:flex;gap:8px;flex-wrap:wrap;">
        <a href="{{ metadata.generation_info.repo_url }}" target="_blank" rel="noopener">&#128279; Repository</a>
        {% if 'github.com' in metadata.generation_info.repo_url %}
        <a href="https://deepwiki.com/{{ metadata.generation_info.repo_url.split('github.com/')[-1] }}" target="_blank" rel="noopener">&#127760; DeepWiki</a>
        {% endif %}
      </div>
      {% endif %}
    </div>
    {% endif %}
    {% if navigation %}
    <div class="nav-row">
      <a href="/static-docs/{{ job_id }}/overview.md" class="nv {% if current_page == 'overview.md' %}on{% endif %}">Overview</a>
    </div>
    {% macro render_nav_item(key, data, depth=0) %}
      {% set has_ch = data.children and data.children|length > 0 %}
      {% set nk = (key ~ '-d' ~ depth) | replace('/', '-') | replace('.', '-') | replace(' ', '-') %}
      <div>
        <div class="nav-row" style="padding-left:{{ depth * 12 }}px;">
          <a href="/static-docs/{{ job_id }}/{{ key }}.md"
             class="nv {% if current_page == key + '.md' %}on{% endif %}">{{ key.replace('_', ' ').title() }}</a>
          {% if has_ch %}<button class="nvcaret" data-nav="{{ nk }}" aria-label="Toggle">›</button>{% endif %}
        </div>
        {% if has_ch %}
        <div class="nvsub" data-nav-sub="{{ nk }}">
          {% for ck, cd in data.children.items() %}{{ render_nav_item(ck, cd, depth + 1) }}{% endfor %}
        </div>
        {% endif %}
      </div>
    {% endmacro %}
    {% for sk, sd in navigation.items() %}{{ render_nav_item(sk, sd) }}{% endfor %}
    {% endif %}
  </nav>
  <main class="main">
    <div class="cw">
      <article id="mc">{{ content | safe }}</article>
      <div class="toc" id="toc">
        <div class="toc-h">On this page</div>
        <ul id="toc-ul"></ul>
      </div>
    </div>
  </main>
</div>
<button id="btt" title="Back to top">↑</button>
<script>
// Site home button — navigate to the origin root regardless of subpath
document.getElementById('site-home-btn').href = window.location.origin + '/';
// Theme + hljs theme switch
var _hljsBase='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/';
var html=document.documentElement,themeBtn=document.getElementById('theme-btn');
function curTheme(){return html.getAttribute('data-theme')||(window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');}
function setTheme(t){html.setAttribute('data-theme',t);localStorage.setItem('cw-theme',t);themeBtn.innerHTML=t==='dark'?'&#9728;&#65039;':'&#127769;';document.getElementById('hljs-css').href=_hljsBase+(t==='dark'?'github-dark':'github')+'.min.css';}
setTheme(curTheme());
themeBtn.addEventListener('click',function(){setTheme(curTheme()==='dark'?'light':'dark');});
document.addEventListener('DOMContentLoaded',function(){hljs.highlightAll();});
// Sidebar
var sb=document.getElementById('sb'),layout=document.getElementById('layout'),ov=document.getElementById('ov');
function isMob(){return window.innerWidth<768;}
function sbShow(){sb.classList.remove('off');layout.classList.add('sbon');if(isMob())ov.classList.add('on');}
function sbHide(){sb.classList.add('off');layout.classList.remove('sbon');ov.classList.remove('on');}
if(isMob()){sbHide();}else{if(localStorage.getItem('cw-sb')==='off')sbHide();else sbShow();}
document.getElementById('sb-toggle').addEventListener('click',function(){
  if(sb.classList.contains('off')){sbShow();if(!isMob())localStorage.setItem('cw-sb','on');}
  else{sbHide();if(!isMob())localStorage.setItem('cw-sb','off');}
});
ov.addEventListener('click',sbHide);
window.addEventListener('resize',function(){if(!isMob()){ov.classList.remove('on');if(localStorage.getItem('cw-sb')!=='off')sbShow();}else sbHide();});
// Nav collapse — hierarchy visible by default; caret toggles manual collapse
document.querySelectorAll('.nvcaret').forEach(function(c){
  var key=c.getAttribute('data-nav'),sub=document.querySelector('[data-nav-sub="'+key+'"]');
  if(!sub)return;
  c.classList.add('open');
  c.addEventListener('click',function(){var h=sub.style.display==='none';sub.style.display=h?'':'none';c.classList.toggle('open',h);});
});
// TOC
(function(){
  var mc=document.getElementById('mc'),ul=document.getElementById('toc-ul'),toc=document.getElementById('toc');
  if(!mc||!ul)return;
  var hs=mc.querySelectorAll('h2,h3');
  if(hs.length<2){if(toc)toc.style.display='none';return;}
  hs.forEach(function(h,i){
    if(!h.id)h.id='h-'+i;
    var li=document.createElement('li');li.className=h.tagName==='H3'?'h3':'';
    var a=document.createElement('a');a.href='#'+h.id;a.textContent=h.textContent;
    li.appendChild(a);ul.appendChild(li);
  });
  var obs=new IntersectionObserver(function(entries){
    entries.forEach(function(e){var a=ul.querySelector('a[href="#'+e.target.id+'"]');if(a)a.closest('li').classList.toggle('on',e.isIntersecting);});
  },{rootMargin:'-15% 0% -75% 0%'});
  hs.forEach(function(h){obs.observe(h);});
})();
// Back to top
var btt=document.getElementById('btt');
window.addEventListener('scroll',function(){btt.classList.toggle('on',window.scrollY>300);});
btt.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'});});
// Mermaid — startOnLoad:false + manual render with error handling + theme-aware re-render
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
      el.innerHTML='<details open><summary style="color:#e11d48;cursor:pointer">&#9888; Mermaid error (click to expand)</summary><pre style="font-size:12px;margin-top:8px;white-space:pre-wrap">'+err.message+'</pre><pre style="font-size:11px;color:var(--tx2)">'+src.replace(/</g,'&lt;')+'</pre></details>';
    }
  }
}
document.addEventListener('DOMContentLoaded',cwRenderMermaid);
themeBtn.addEventListener('click',function(){setTimeout(cwRenderMermaid,50);});
</script>
</body>
</html>
"""