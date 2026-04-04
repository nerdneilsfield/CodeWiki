"""
Static HTML generator.

Converts a docs directory (containing .md files + module_tree.json) into
a fully pre-rendered static website — one .html file per .md file, with
an inline sidebar, all internal links rewritten to .html, and no runtime
markdown rendering.
"""

from __future__ import annotations

import os
import re
import logging
from pathlib import Path
from string import Template
from typing import Dict, Any, Optional

from codewiki.src.utils import (
    file_manager,
    module_doc_filename,
    find_module_doc,
    _normalize_for_match,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Override CSS — fixes prose/hljs/katex/mermaid conflicts (inlined per page)
# ──────────────────────────────────────────────────────────────────────────────

_OVERRIDE_CSS = """
/* Highlight.js: keep code bg transparent, let prose control outer */
.prose pre code.hljs { background: transparent !important; }
/* prose code pseudo-elements: exclude KaTeX internals */
.prose .katex code::before,
.prose .katex code::after { content: none; }
/* Mermaid: not constrained by prose max-width */
.prose .mermaid { max-width: none; }
/* Math: both rendering paths */
.prose .math-block,
.prose .katex-display { overflow-x: auto; max-width: none; }
/* Math error */
.prose .math-err { color: oklch(var(--er)); font-style: italic; font-size: 0.85em; }
/* Wide tables */
.prose table { display: block; max-width: 100%; overflow-x: auto; }
/* pre focus (a11y) */
.prose pre:focus-visible { box-shadow: 0 0 0 3px oklch(var(--p)); }
""".strip()

# Fallback prose styles if @tailwindcss/typography CDN fails.
# Uses DaisyUI CSS variables for theme consistency.
# NOTE: When using fallback, remove "prose prose-lg" from <article> class
# and use only "cw-article" to avoid conflicting with any residual prose rules.
_PROSE_FALLBACK = """
.cw-article h1{font-size:1.9rem;font-weight:700;border-bottom:2px solid oklch(var(--bc)/.2);padding-bottom:.4rem;margin-bottom:1.2rem;line-height:1.3;}
.cw-article h2{font-size:1.45rem;font-weight:600;margin-top:2.2rem;margin-bottom:.7rem;border-bottom:1px solid oklch(var(--bc)/.15);padding-bottom:.2rem;}
.cw-article h3{font-size:1.15rem;font-weight:600;margin-top:1.8rem;margin-bottom:.5rem;}
.cw-article h4{font-size:1rem;font-weight:600;margin-top:1.4rem;margin-bottom:.4rem;}
.cw-article p{margin-bottom:1rem;color:oklch(var(--bc)/.7);}
.cw-article ul,.cw-article ol{margin-bottom:1rem;padding-left:1.6rem;}
.cw-article li{margin-bottom:.3rem;color:oklch(var(--bc)/.7);}
.cw-article a{color:oklch(var(--p));}
.cw-article a:hover{text-decoration:underline;}
.cw-article code{font-family:'JetBrains Mono',Consolas,monospace;font-size:.82em;background:oklch(var(--b2));padding:.15em .4em;border-radius:4px;}
.cw-article pre{background:oklch(var(--b2));border:1px solid oklch(var(--bc)/.15);border-radius:8px;padding:1rem 1.2rem;overflow-x:auto;margin-bottom:1.2rem;}
.cw-article pre code{background:none;padding:0;font-size:.87em;}
.cw-article blockquote{border-left:4px solid oklch(var(--p));padding:.5rem 1rem;margin-bottom:1rem;color:oklch(var(--bc)/.5);background:oklch(var(--p)/.1);border-radius:0 6px 6px 0;}
.cw-article table{width:100%;border-collapse:collapse;margin-bottom:1rem;}
.cw-article th,.cw-article td{border:1px solid oklch(var(--bc)/.15);padding:.6rem .8rem;text-align:left;}
.cw-article th{background:oklch(var(--b2));font-weight:600;}
.cw-article img{max-width:100%;border-radius:6px;}
""".strip()

# Determined by Step 1 verification. Override via env var CODEWIKI_TYPOGRAPHY_CDN=0.
_TYPOGRAPHY_CDN_WORKS = os.environ.get("CODEWIKI_TYPOGRAPHY_CDN", "1") != "0"

# ──────────────────────────────────────────────────────────────────────────────
# Page template (uses string.Template — $var substitution, no brace escaping)
# ──────────────────────────────────────────────────────────────────────────────

_PAGE_TEMPLATE = Template(r"""\
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:ital,wght@0,400;0,500;1,400&display=swap" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/daisyui@5" rel="stylesheet" type="text/css" />
<script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
<link id="hljs-css" rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11.9.0/dist/mermaid.min.js"></script>
<script>(function(){var t=localStorage.getItem('cw-theme')||(window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');document.documentElement.setAttribute('data-theme',t);if(t==='dark'){document.getElementById('hljs-css').href='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css';}})();</script>
${typography_style}
<style>
${override_css}
</style>
</head>
<body class="bg-base-100 font-[Inter,system-ui,sans-serif]">
<div class="drawer lg:drawer-open">
  <input id="cw-drawer" type="checkbox" class="drawer-toggle" />
  <div class="drawer-content flex flex-col">
    <!-- Navbar -->
    <header class="navbar bg-base-200 shadow-sm sticky top-0 z-50">
      <div class="flex-none lg:hidden">
        <label for="cw-drawer" class="btn btn-ghost btn-square btn-sm" aria-label="Toggle sidebar">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="inline-block h-5 w-5 stroke-current"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path></svg>
        </label>
      </div>
      <div class="flex-1 px-2">
        <a href="index.html" class="btn btn-ghost text-primary font-bold text-base normal-case">&#128218; ${repo_name}</a>
      </div>
      <div class="flex-none gap-1">
        <a href="/" id="site-home-btn" class="btn btn-ghost btn-square btn-sm" title="Back to main site" aria-label="Back to main site">&#127968;</a>
        <button class="btn btn-ghost btn-square btn-sm" id="theme-btn" title="Toggle theme" aria-label="Toggle light/dark theme">&#127769;</button>
      </div>
    </header>
    <!-- Main content -->
    <main class="flex justify-center px-4 py-8 lg:px-8">
      <div class="flex gap-8 w-full max-w-6xl items-start">
        <article id="mc" class="${article_class} max-w-none flex-1 min-w-0">
${content}
        </article>
        <aside class="hidden xl:block w-56 shrink-0 sticky top-20 max-h-[calc(100vh-6rem)] overflow-y-auto" id="toc">
          <div class="menu-title text-xs uppercase tracking-wider opacity-60">On this page</div>
          <ul id="toc-ul" class="menu menu-sm"></ul>
        </aside>
      </div>
    </main>
  </div>
  <!-- Sidebar -->
  <div class="drawer-side z-40">
    <label for="cw-drawer" aria-label="close sidebar" class="drawer-overlay"></label>
    <div class="bg-base-200 min-h-full w-72 p-4">
${meta_html}
      <ul class="menu w-full">
${nav_html}
      </ul>
    </div>
  </div>
</div>
<button id="btt" class="btn btn-circle btn-primary btn-sm fixed bottom-6 right-6 z-50 hidden shadow-lg" title="Back to top">&#8679;</button>
<script>
// Site home button
document.getElementById('site-home-btn').href = window.location.origin + '/';
// Theme
var html=document.documentElement,themeBtn=document.getElementById('theme-btn');
function curTheme(){return html.getAttribute('data-theme')||(window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');}
var _hljsBase='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/';
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
// Sidebar persistence (desktop only — mobile uses drawer-toggle natively)
var drawerCb=document.getElementById('cw-drawer');
if(window.innerWidth>=1024 && localStorage.getItem('cw-sb')==='off'){
  // lg:drawer-open forces open; override by removing the class
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
// Escape key closes sidebar
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
// TOC
(function(){
  var mc=document.getElementById('mc'),ul=document.getElementById('toc-ul'),toc=document.getElementById('toc');
  if(!mc||!ul)return;
  var hs=mc.querySelectorAll('h2,h3');
  if(hs.length<2){if(toc)toc.style.display='none';return;}
  hs.forEach(function(h,i){
    if(!h.id)h.id='h-'+i;
    var li=document.createElement('li');
    var a=document.createElement('a');a.href='#'+h.id;a.textContent=h.textContent;
    if(h.tagName==='H3')a.classList.add('pl-4','text-xs');
    li.appendChild(a);ul.appendChild(li);
  });
  var obs=new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      var a=ul.querySelector('a[href="#'+e.target.id+'"]');
      if(a)a.classList.toggle('active',e.isIntersecting);
    });
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
// Math: KaTeX fast path + MathJax async fallback
var _mjReady=null;
function _loadMathJax(){
  if(!_mjReady){
    window.MathJax={tex:{packages:{'[+]':['ams','newcommand']}},svg:{fontCache:'global'},startup:{typeset:false}};
    _mjReady=new Promise(function(res,rej){
      var s=document.createElement('script');
      s.src='https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js';
      s.onload=function(){MathJax.startup.promise.then(res,rej);};
      s.onerror=rej;
      document.head.appendChild(s);
    });
  }
  return _mjReady;
}
async function cwRenderMath(root){
  if(typeof katex==='undefined')return;
  if(!root||typeof root.querySelectorAll!=='function')
    root=document.getElementById('mc')||document.body;
  var failed=[];
  root.querySelectorAll('.math-block,.math-inline').forEach(function(el){
    if(el.dataset.mathDone)return;
    var disp=el.classList.contains('math-block');
    var src=el.textContent.trim().slice(2,-2).trim();
    el.dataset.mathSrc=src;
    el.dataset.mathDone='1';
    try{
      el.innerHTML=katex.renderToString(src,{displayMode:disp,throwOnError:true,output:'html'});
    }catch(e){
      failed.push([el,disp]);
    }
  });
  if(!failed.length)return;
  try{
    await _loadMathJax();
    for(var i=0;i<failed.length;i++){
      var el=failed[i][0],disp=failed[i][1];
      try{
        var node=await MathJax.tex2svgPromise(el.dataset.mathSrc,{display:disp});
        el.innerHTML='';el.appendChild(node);
      }catch(e2){
        el.innerHTML='<code class="math-err" title="'+
          el.dataset.mathSrc.replace(/"/g,'&#34;')+'">'+
          el.dataset.mathSrc+'</code>';
      }
    }
  }catch(loadErr){
    failed.forEach(function(p){
      var el=p[0];
      el.innerHTML='<code class="math-err">'+el.dataset.mathSrc+'</code>';
    });
  }
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


def _build_nav_html(
    module_tree: Dict[str, Any],
    current_html: str,
    depth: int = 0,
    resolved_hrefs: Optional[Dict[str, Optional[str]]] = None,
    parent_path: Optional[list[str]] = None,
    h1_titles: Optional[Dict[str, str]] = None,
) -> str:
    """Recursively build sidebar nav HTML from the module tree as DaisyUI menu items."""
    lines: list[str] = []
    indent = "  " * (depth + 3)
    base_path = parent_path or []
    titles = h1_titles or {}

    for key, data in module_tree.items():
        module_path = base_path + [key]
        map_key = "/".join(module_path)
        href = (resolved_hrefs or {}).get(map_key)
        has_page = href is not None
        if not href:
            href = data.get("_doc_filename", module_doc_filename(module_path)).replace(
                ".md", ".html"
            )
        is_active = _normalize_for_match(current_html) == _normalize_for_match(href)
        active_cls = ' class="active"' if is_active else ""
        children = data.get("children") or {}

        doc_stem = href.removesuffix(".html") if href else ""
        label = titles.get(doc_stem, key.replace("_", " ").title())

        if children:
            lines.append(f"{indent}<li>")
            lines.append(f"{indent}  <details open>")
            if has_page:
                lines.append(
                    f'{indent}    <summary><a href="{href}"{active_cls}>{label}</a></summary>'
                )
            else:
                lines.append(f'{indent}    <summary class="opacity-50">{label}</summary>')
            lines.append(f"{indent}    <ul>")
            lines.append(
                _build_nav_html(
                    children, current_html, depth + 1, resolved_hrefs, module_path, titles
                )
            )
            lines.append(f"{indent}    </ul>")
            lines.append(f"{indent}  </details>")
            lines.append(f"{indent}</li>")
        else:
            if has_page:
                lines.append(f'{indent}<li><a href="{href}"{active_cls}>{label}</a></li>')
            else:
                lines.append(
                    f'{indent}<li class="disabled"><span class="opacity-50">{label}</span></li>'
                )

    return "\n".join(lines)


def _build_meta_html(metadata: Optional[Dict[str, Any]], hide_repo_links: bool = False) -> str:
    if not metadata:
        return ""
    gi = metadata.get("generation_info", {})
    st = metadata.get("statistics", {})
    parts = []
    if gi.get("main_model"):
        parts.append(f"<b class='opacity-70'>Model:</b> {gi['main_model']}")
    if gi.get("timestamp"):
        parts.append(f"<b class='opacity-70'>Generated:</b> {gi['timestamp'][:16]}")
    if gi.get("commit_id"):
        parts.append(f"<b class='opacity-70'>Commit:</b> {gi['commit_id'][:8]}")
    if st.get("total_components"):
        parts.append(f"<b class='opacity-70'>Components:</b> {st['total_components']}")

    link_parts = []
    if not hide_repo_links:
        repo_url = gi.get("repo_url")
        if repo_url:
            link_parts.append(
                f'<a href="{repo_url}" target="_blank" rel="noopener" '
                f'class="link link-primary text-xs">&#128279; Repository</a>'
            )
            if "github.com" in repo_url:
                slug = repo_url.split("github.com/")[-1]
                link_parts.append(
                    f'<a href="https://deepwiki.com/{slug}" target="_blank" rel="noopener" '
                    f'class="link link-primary text-xs">&#127760; DeepWiki</a>'
                )

    if not parts and not link_parts:
        return ""

    body = "\n".join(f"        <div class='text-xs leading-relaxed'>{p}</div>" for p in parts)
    if link_parts:
        body += "\n        <div class='flex gap-2 flex-wrap mt-2'>" + "".join(link_parts) + "</div>"
    return (
        "      <li>\n"
        '        <div class="card card-compact bg-base-100 shadow-sm mb-2">\n'
        '          <div class="card-body p-3">\n' + body + "\n          </div>\n"
        "        </div>\n"
        "      </li>"
    )


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
_DISPLAY_MATH_RE = re.compile(r"\$\$([^$]+?)\$\$", re.DOTALL)
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
    import html as _html

    protected: list[tuple[str, str]] = []

    def _display(m: re.Match) -> str:
        inner = m.group(1)
        if _CJK_RE.search(inner):
            return m.group(0)
        idx = len(protected)
        ph = f"CWIKIMD{idx:06d}"
        # HTML-escape so & < > are safe in the DOM; KaTeX reads textContent
        # which the browser decodes back to the original LaTeX characters.
        escaped = _html.escape(inner, quote=False)
        protected.append((ph, f'<div class="math-block not-prose">\\[{escaped}\\]</div>'))
        return ph

    def _inline(m: re.Match) -> str:
        inner = m.group(1)
        if _CJK_RE.search(inner):
            return m.group(0)
        before = m.string[: m.start()].rstrip()
        after = m.string[m.end() :].lstrip()
        if (before and _CJK_RE.search(before[-1])) or (after and _CJK_RE.search(after[0])):
            return m.group(0)
        idx = len(protected)
        ph = f"CWIKIMI{idx:06d}"
        escaped = _html.escape(inner, quote=False)
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
        meta_html = _build_meta_html(metadata, hide_repo_links=hide_repo_links)

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

            # Build sidebar for this page
            # 1. Overview
            ov_cls = ' class="active"' if html_name in ("overview.html", "index.html") else ""
            ov_label = h1_titles.get("overview", "Overview")
            nav_html = f'        <li><a href="index.html"{ov_cls}>{ov_label}</a></li>\n'

            # 2. Guide pages
            _GUIDE_FALLBACK_LABELS = {
                "guide-getting-started": "Get Started",
                "guide-beginners-guide": "Beginner's Guide",
                "guide-build-and-organization": "Build & Code Organization",
                "guide-core-algorithms": "Core Algorithms",
            }
            for slug, fallback_label in _GUIDE_FALLBACK_LABELS.items():
                md_file = docs_dir / f"{slug}.md"
                if not md_file.exists():
                    continue
                guide_html = slug + ".html"
                guide_cls = ' class="active"' if html_name == guide_html else ""
                label = h1_titles.get(slug, fallback_label)

                # Sub-pages for multi-page guides
                sub_prefix = slug + "-"
                sub_pages = sorted(
                    [
                        f
                        for f in os.listdir(str(docs_dir))
                        if f.startswith(sub_prefix) and f.endswith(".md")
                    ]
                )
                if sub_pages:
                    nav_html += f"        <li>\n"
                    nav_html += f"          <details open>\n"
                    nav_html += f'            <summary><a href="{guide_html}"{guide_cls}>{label}</a></summary>\n'
                    nav_html += f"            <ul>\n"
                    for sub_file in sub_pages:
                        sub_html = sub_file.replace(".md", ".html")
                        sub_stem = sub_file.removesuffix(".md")
                        sub_label = h1_titles.get(sub_stem)
                        if not sub_label:
                            raw = sub_file[len(sub_prefix) : -3]
                            m = re.match(r"^(\d+)-(.+)$", raw)
                            if m:
                                sub_label = (
                                    f"{int(m.group(1))}. {m.group(2).replace('-', ' ').title()}"
                                )
                            else:
                                sub_label = raw.replace("-", " ").title()
                        sub_cls = ' class="active"' if html_name == sub_html else ""
                        nav_html += f'              <li><a href="{sub_html}"{sub_cls}>{sub_label}</a></li>\n'
                    nav_html += f"            </ul>\n"
                    nav_html += f"          </details>\n"
                    nav_html += f"        </li>\n"
                else:
                    nav_html += f'        <li><a href="{guide_html}"{guide_cls}>{label}</a></li>\n'

            # 3. Module tree (only when present)
            if module_tree:
                nav_html += _build_nav_html(
                    module_tree, html_name, resolved_hrefs=resolved_hrefs, h1_titles=h1_titles
                )

            # Determine typography style block and article class
            if _TYPOGRAPHY_CDN_WORKS:
                typography_style = '<style type="text/tailwindcss">\n@import "tailwindcss";\n@plugin "https://esm.sh/@tailwindcss/typography@0.5";\n</style>'
                article_class = "prose prose-lg"
            else:
                typography_style = f"<style>\n{_PROSE_FALLBACK}\n</style>"
                article_class = "cw-article"

            page = _PAGE_TEMPLATE.safe_substitute(
                title=title,
                override_css=_OVERRIDE_CSS,
                typography_style=typography_style,
                article_class=article_class,
                repo_name=repo_name,
                meta_html=meta_html,
                nav_html=nav_html,
                content=content_html,
            )

            # Sanity check: safe_substitute silently ignores missing placeholders.
            # Verify no un-substituted ${...} remain in output.
            import re as _re

            leftover = _re.findall(r"\$\{[a-z_]+\}", page)
            if leftover:
                logger.warning(f"Un-substituted placeholders in {html_name}: {leftover}")

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
