# DaisyUI Frontend UI 改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 CodeWiki 三套前端模板的自定义 CSS 替换为 DaisyUI + Tailwind CSS CDN，统一视觉风格。

**Architecture:** 删除所有内联自定义 CSS，替换为 DaisyUI 组件类 + Tailwind 工具类。使用 DaisyUI drawer 组件作为统一的侧栏布局方案，通过 hidden checkbox 控制开合状态并用 JS 整合 localStorage 持久化。文章排版优先尝试 `@tailwindcss/typography` CDN 插件，备选自定义 prose 样式。

**Tech Stack:** DaisyUI v5 CDN, Tailwind CSS v4 Browser CDN, @tailwindcss/typography

**Spec:** `docs/plans/2026-04-04-daisyui-frontend-design.md`

---

### Task 1: Static HTML Generator — DaisyUI 迁移 (`static_generator.py`)

这是最核心的改造，涉及删除 `_CSS`、重写 `_PAGE_TEMPLATE`、更新 `_build_nav_html`/`_build_meta_html`/`generate()` 中的内联 nav HTML。

**Files:**
- Modify: `codewiki/cli/static_generator.py:33-303` (删除 `_CSS`，重写 `_PAGE_TEMPLATE`)
- Modify: `codewiki/cli/static_generator.py:367-472` (`_build_nav_html`, `_build_meta_html`)
- Modify: `codewiki/cli/static_generator.py:716-776` (`generate()` 中内联 nav HTML)

- [ ] **Step 1: 验证 Typography CDN 可行性**

在本地创建一个最小 HTML 文件，验证 `@tailwindcss/typography` 在 Browser CDN 下是否可用：

```html
<!-- /tmp/daisyui-test.html -->
<!DOCTYPE html>
<html data-theme="light">
<head>
<link href="https://cdn.jsdelivr.net/npm/daisyui@5" rel="stylesheet" type="text/css" />
<script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
<style type="text/tailwindcss">
@import "tailwindcss";
@plugin "https://esm.sh/@tailwindcss/typography@0.5";
</style>
</head>
<body>
<div class="prose prose-lg p-8">
  <h1>Test</h1>
  <p>If this paragraph has prose styling (larger font, comfortable line-height), typography plugin works.</p>
  <pre><code>console.log("code block");</code></pre>
</div>
</body>
</html>
```

Run: 在浏览器中打开此文件，检查 `<p>` 是否有 prose 样式。

**两种写法都要测试：** 先测带 `@import "tailwindcss"` 的版本，如果报错再测不带的版本。记录哪种有效。

如果两种都不生效，改用备选方案——`_PROSE_FALLBACK` 自定义样式（见 Step 2），并从 `<article>` 移除 `prose` class。

- [ ] **Step 2: 删除 `_CSS`，编写新的 `_PAGE_TEMPLATE`**

将 `codewiki/cli/static_generator.py` 中第 33-303 行的 `_CSS` 和 `_PAGE_TEMPLATE` 替换为：

```python
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
```

- [ ] **Step 3: 更新 `_build_meta_html` 输出 DaisyUI card 格式**

替换 `_build_meta_html` 函数的 HTML 输出（约第 432-472 行）：

```python
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
        body += (
            "\n        <div class='flex gap-2 flex-wrap mt-2'>"
            + "".join(link_parts)
            + "</div>"
        )
    return (
        '      <li>\n'
        '        <div class="card card-compact bg-base-100 shadow-sm mb-2">\n'
        '          <div class="card-body p-3">\n'
        + body +
        '\n          </div>\n'
        '        </div>\n'
        '      </li>'
    )
```

- [ ] **Step 4: 更新 `_build_nav_html` 输出 DaisyUI menu `<li>` 格式**

替换 `_build_nav_html` 函数（约第 367-429 行）：

```python
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
                lines.append(f'{indent}    <summary><a href="{href}"{active_cls}>{label}</a></summary>')
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
                lines.append(f'{indent}<li class="disabled"><span class="opacity-50">{label}</span></li>')

    return "\n".join(lines)
```

- [ ] **Step 5: 更新 `generate()` 方法中的内联 nav HTML**

在 `generate()` 方法中（约第 716-776 行），将 overview 和 guide pages 的 nav HTML 从 `.nv`/`.nvcaret`/`.nvsub` 类改为 DaisyUI `<li><a>` 格式：

```python
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
                    nav_html += f'        <li>\n'
                    nav_html += f'          <details open>\n'
                    nav_html += f'            <summary><a href="{guide_html}"{guide_cls}>{label}</a></summary>\n'
                    nav_html += f'            <ul>\n'
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
                    nav_html += f'            </ul>\n'
                    nav_html += f'          </details>\n'
                    nav_html += f'        </li>\n'
                else:
                    nav_html += f'        <li><a href="{guide_html}"{guide_cls}>{label}</a></li>\n'
```

- [ ] **Step 6: 更新 `generate()` 中的 `safe_substitute` 调用**

替换 `page = _PAGE_TEMPLATE.safe_substitute(...)` 调用，移除 `css` 参数，添加新参数：

```python
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
            leftover = _re.findall(r'\$\{[a-z_]+\}', page)
            if leftover:
                logger.warning(f"Un-substituted placeholders in {html_name}: {leftover}")
```

- [ ] **Step 7: 运行现有测试，验证辅助函数不受影响**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/test_static_generator_corner_cases.py tests/test_build_static_command.py -v`

Expected: 全部 PASS（这些测试测的是 `_extract_math_blocks`、`_fix_markdown_links`、`_rewrite_md_to_html_links`、`_resolve_nav_hrefs` 等辅助函数和 CLI 命令，不依赖 HTML 结构）。

> **注意：** `_extract_math_blocks` 中 math-block/math-inline 的 HTML 输出现在包含 `not-prose` class（见 Step 7b），相关测试中的 `assert` 如有精确匹配需同步更新。

- [ ] **Step 7b: 为 mermaid 和 math 元素添加 `not-prose` class**

在 `_extract_math_blocks` 函数中，修改 HTML 输出以包含 `not-prose`：

```python
# In _display():
protected.append((ph, f'<div class="math-block not-prose">\\[{escaped}\\]</div>'))

# In _inline():
protected.append((ph, f'<span class="math-inline not-prose">\\({escaped}\\)</span>'))
```

在 `_markdown_to_static_html` 函数中，修改 mermaid 转换输出：

```python
def _mermaid(m: re.Match) -> str:
    code = html_module.unescape(m.group(1))
    return f'<div class="mermaid not-prose">{code}</div>'
```

- [ ] **Step 8: 手动 smoke test**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m codewiki build-static <任意已有 docs 目录>`

在浏览器中打开生成的 `index.html`，验证：
1. DaisyUI 组件样式加载正常（navbar、drawer、menu、card）
2. Light/Dark 主题切换正常
3. 侧栏导航正常展开/折叠
4. 代码高亮（Highlight.js）正常
5. Mermaid 图表渲染正常
6. 数学公式（KaTeX）渲染正常
7. TOC 目录高亮正常
8. 响应式：缩小窗口时侧栏变为 drawer

- [ ] **Step 9: Commit**

```bash
git add codewiki/cli/static_generator.py
git commit -m "feat(fe): migrate static HTML generator to DaisyUI + Tailwind CSS CDN

Replace custom inline CSS with DaisyUI drawer, navbar, menu, card
components. Add prose compatibility overrides for hljs/katex/mermaid.
Sidebar now uses DaisyUI checkbox drawer with localStorage persistence."
```

---

### Task 2: Web Interface Template — `WEB_INTERFACE_TEMPLATE` DaisyUI 迁移

**Files:**
- Modify: `codewiki/src/fe/templates.py:15-329` (`WEB_INTERFACE_TEMPLATE`)

- [ ] **Step 1: 重写 `WEB_INTERFACE_TEMPLATE`**

替换 `codewiki/src/fe/templates.py` 中 `WEB_INTERFACE_TEMPLATE`（第 15-329 行）为：

```python
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
```

- [ ] **Step 2: 运行相关测试**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/ -k "web_app or template" -v`

Expected: PASS（模板测试主要测渲染是否成功，不断言 HTML class 名）。

- [ ] **Step 3: Commit**

```bash
git add codewiki/src/fe/templates.py
git commit -m "feat(fe): migrate WEB_INTERFACE_TEMPLATE to DaisyUI

Replace custom CSS with DaisyUI card, form, alert, badge components.
Preserve gradient background and form validation logic."
```

---

### Task 3: Docs View Template — `DOCS_VIEW_TEMPLATE` DaisyUI 迁移

**Files:**
- Modify: `codewiki/src/fe/templates.py:332-593` (`DOCS_VIEW_TEMPLATE`)

- [ ] **Step 1: 重写 `DOCS_VIEW_TEMPLATE`**

替换 `codewiki/src/fe/templates.py` 中 `DOCS_VIEW_TEMPLATE`（第 332-593 行）为：

```python
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
        <li><a href="/static-docs/{{ job_id }}/overview.md" class="{% if current_page == 'overview.md' %}active{% endif %}">Overview</a></li>
        {% macro render_nav_item(key, data, depth=0) %}
          {% set has_ch = data.children and data.children|length > 0 %}
          {% if has_ch %}
          <li>
            <details open>
              {% if data.doc_exists is not defined or data.doc_exists %}
              <summary><a href="/static-docs/{{ job_id }}/{{ data.doc_filename }}" class="{% if current_page == data.doc_filename %}active{% endif %}">{{ key.replace('_', ' ').title() }}</a></summary>
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
            <a href="/static-docs/{{ job_id }}/{{ data.doc_filename }}" class="{% if current_page == data.doc_filename %}active{% endif %}">{{ key.replace('_', ' ').title() }}</a>
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
```

- [ ] **Step 2: 运行测试**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/ -k "web_app or template or route" -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add codewiki/src/fe/templates.py
git commit -m "feat(fe): migrate DOCS_VIEW_TEMPLATE to DaisyUI

Replace custom CSS with DaisyUI drawer, navbar, menu, card, prose.
Preserve KaTeX auto-render, Mermaid, hljs, and a11y features."
```

---

### Task 4: Not Found Template — `NOT_FOUND_TEMPLATE` DaisyUI 迁移

**Files:**
- Modify: `codewiki/src/fe/templates.py:595-624` (`NOT_FOUND_TEMPLATE`)

- [ ] **Step 1: 重写 `NOT_FOUND_TEMPLATE`**

替换 `NOT_FOUND_TEMPLATE`：

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add codewiki/src/fe/templates.py
git commit -m "feat(fe): migrate NOT_FOUND_TEMPLATE to DaisyUI

Replace custom 404 page with DaisyUI card component."
```

---

### Task 5: GitHub Pages Template — `viewer_template.html` DaisyUI 迁移

**Files:**
- Modify: `codewiki/templates/github_pages/viewer_template.html` (完整重写)

- [ ] **Step 1: 重写 `viewer_template.html`**

替换整个 `codewiki/templates/github_pages/viewer_template.html` 文件。这个模板使用 `marked.js` 客户端渲染，不需要 TOC/主题切换/返回顶部（当前没有这些功能，本次不添加）。

```html
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{TITLE}}</title>
    <!-- CSS first, JS after (consistent CDN order across all templates) -->
    <link href="https://cdn.jsdelivr.net/npm/daisyui@5" rel="stylesheet" type="text/css" />
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
    <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@11.9.0/dist/mermaid.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/marked@11.0.0/marked.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
    <style type="text/tailwindcss">
    @import "tailwindcss";
    @plugin "https://esm.sh/@tailwindcss/typography@0.5";
    </style>
    <style>
    .prose pre code.hljs { background: transparent !important; }
    .prose .katex code::before, .prose .katex code::after { content: none; }
    .prose .mermaid { max-width: none; }
    .prose .math-block, .prose .katex-display { overflow-x: auto; max-width: none; }
    .prose .math-err { color: oklch(var(--er)); font-style: italic; font-size: 0.85em; }
    .prose table { display: block; max-width: 100%; overflow-x: auto; }
    </style>
</head>
<body class="bg-base-100">
    <div class="drawer lg:drawer-open">
        <input id="gp-drawer" type="checkbox" class="drawer-toggle" />
        <div class="drawer-content flex flex-col">
            <!-- Navbar (mobile only) -->
            <header class="navbar bg-base-200 shadow-sm lg:hidden">
                <div class="flex-none">
                    <label for="gp-drawer" class="btn btn-ghost btn-square btn-sm" aria-label="Open sidebar">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="inline-block h-5 w-5 stroke-current"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path></svg>
                    </label>
                </div>
                <div class="flex-1 px-2">
                    <span class="font-bold text-primary">&#128218; {{TITLE}}</span>
                </div>
            </header>
            <!-- Main content -->
            <main class="p-6 lg:p-10 max-w-4xl mx-auto w-full">
                <div id="loading" class="flex flex-col items-center justify-center min-h-[400px]">
                    <span class="loading loading-spinner loading-lg text-primary"></span>
                    <p class="mt-4 opacity-60">Loading documentation...</p>
                </div>
                <div id="content" class="prose prose-lg max-w-none" style="display: none;"></div>
            </main>
        </div>
        <!-- Sidebar -->
        <div class="drawer-side z-40">
            <label for="gp-drawer" aria-label="close sidebar" class="drawer-overlay"></label>
            <div class="bg-base-200 min-h-full w-80 p-4">
                <div class="text-xl font-bold text-primary mb-4">&#128218; {{TITLE}}</div>
                {{REPO_LINK}}
                <div id="repo-info" class="card card-compact bg-base-100 shadow-sm mb-4" style="display: {{SHOW_INFO}};">
                    <div class="card-body p-3">
                        <h4 class="text-xs font-semibold uppercase tracking-wider opacity-60 mb-2">Generation Info</h4>
                        <div id="info-content" class="text-xs leading-relaxed">{{INFO_CONTENT}}</div>
                    </div>
                </div>
                <ul class="menu w-full">
                    <li><a class="active" data-file="overview.md">&#128196; Overview</a></li>
                </ul>
                <ul id="navigation" class="menu w-full"></ul>
            </div>
        </div>
    </div>

    <script>
        const CONFIG = {{CONFIG_JSON}};
        const MODULE_TREE = {{MODULE_TREE_JSON}};
        const METADATA = {{METADATA_JSON}};
        const DOCS_BASE_PATH = '{{DOCS_BASE_PATH}}';
        const GUIDE_PAGES = {{GUIDE_PAGES_JSON}};

        marked.setOptions({ breaks: true, gfm: true, headerIds: true, mangle: false });

        mermaid.initialize({
            startOnLoad: false, theme: 'default',
            themeVariables: { primaryColor: '#2563eb', primaryTextColor: '#334155', primaryBorderColor: '#e2e8f0', lineColor: '#64748b', secondaryColor: '#f1f5f9', tertiaryColor: '#f8fafc' },
            flowchart: { htmlLabels: true, curve: 'basis' },
            sequence: { mirrorActors: true, useMaxWidth: true }
        });

        document.addEventListener('DOMContentLoaded', function() {
            buildNavigation();
            loadDocument('overview.md');
        });

        function buildNavigation() {
            const nav = document.getElementById('navigation');
            let html = '';

            if (GUIDE_PAGES && GUIDE_PAGES.length > 0) {
                for (const guide of GUIDE_PAGES) {
                    if (guide.subPages && guide.subPages.length > 0) {
                        html += `<li><details open>`;
                        html += `<summary><a data-file="${guide.slug}.md">&#128214; ${guide.label}</a></summary>`;
                        html += `<ul>`;
                        for (const sub of guide.subPages) {
                            html += `<li><a data-file="${sub.slug}.md">${sub.label}</a></li>`;
                        }
                        html += `</ul></details></li>`;
                    } else {
                        html += `<li><a data-file="${guide.slug}.md">&#128214; ${guide.label}</a></li>`;
                    }
                }
            }

            if (MODULE_TREE) {
                for (const [key, data] of Object.entries(MODULE_TREE)) {
                    html += buildNavItem(key, data, 0);
                }
            }

            nav.innerHTML = html;

            // Click handlers for all menu items
            document.querySelectorAll('[data-file]').forEach(item => {
                item.addEventListener('click', function(e) {
                    e.preventDefault();
                    const file = this.getAttribute('data-file');
                    if (file) {
                        loadDocument(file);
                        document.querySelectorAll('[data-file]').forEach(i => i.classList.remove('active'));
                        this.classList.add('active');
                    }
                });
            });
        }

        function buildNavItem(key, data, depth) {
            const fileName = `${key}.md`;
            let html = '';

            if (data.children && Object.keys(data.children).length > 0) {
                html += `<li><details open>`;
                if (data.components && data.components.length > 0) {
                    html += `<summary><a data-file="${fileName}">${formatNavTitle(key)}</a></summary>`;
                } else {
                    html += `<summary>${formatNavTitle(key)}</summary>`;
                }
                html += `<ul>`;
                for (const [childKey, childData] of Object.entries(data.children)) {
                    html += buildNavItem(childKey, childData, depth + 1);
                }
                html += `</ul></details></li>`;
            } else {
                html += `<li><a data-file="${fileName}">${formatNavTitle(key)}</a></li>`;
            }

            return html;
        }

        function formatNavTitle(key) {
            return key.replace(/_/g, ' ').split(' ').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
        }

        async function loadDocument(filename) {
            const loading = document.getElementById('loading');
            const content = document.getElementById('content');
            loading.style.display = 'flex';
            content.style.display = 'none';
            try {
                const docPath = DOCS_BASE_PATH ? `${DOCS_BASE_PATH}/${filename}` : filename;
                const response = await fetch(docPath);
                if (!response.ok) throw new Error(`Failed to load ${filename}`);
                const markdown = await response.text();
                const html = await renderMarkdown(markdown);
                content.innerHTML = html;
                loading.style.display = 'none';
                content.style.display = 'block';
                await renderMermaidDiagrams();
                await cwRenderMath(content);
                setupMarkdownLinks();
                window.scrollTo(0, 0);
            } catch (error) {
                console.error('Error loading document:', error);
                showError(`Failed to load document: ${filename}`);
            }
        }

        function setupMarkdownLinks() {
            const content = document.getElementById('content');
            const oldListener = content._markdownLinkListener;
            if (oldListener) content.removeEventListener('click', oldListener);
            const listener = function(e) {
                const link = e.target.closest('a');
                if (!link) return;
                const href = link.getAttribute('href');
                if (!href) return;
                const mdMatch = href.match(/([^\/]*\.md(?:#.*)?$)/i);
                if (mdMatch) {
                    e.preventDefault();
                    e.stopPropagation();
                    const filename = mdMatch[1].split('#')[0];
                    loadDocument(filename);
                    document.querySelectorAll('[data-file]').forEach(item => {
                        if (item.getAttribute('data-file') === filename) {
                            document.querySelectorAll('[data-file]').forEach(i => i.classList.remove('active'));
                            item.classList.add('active');
                        }
                    });
                }
            };
            content._markdownLinkListener = listener;
            content.addEventListener('click', listener);
        }

        // Hybrid math rendering: KaTeX fast path + MathJax async fallback
        var _mjReady = null;
        function _loadMathJax() {
            if (!_mjReady) {
                window.MathJax = { tex: { packages: {'[+]': ['ams', 'newcommand']} }, svg: { fontCache: 'global' }, startup: { typeset: false } };
                _mjReady = new Promise(function(res, rej) {
                    var s = document.createElement('script');
                    s.src = 'https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js';
                    s.onload = function() { MathJax.startup.promise.then(res, rej); };
                    s.onerror = rej;
                    document.head.appendChild(s);
                });
            }
            return _mjReady;
        }
        async function cwRenderMath(root) {
            if (typeof katex === 'undefined') return;
            root = root || document.body;
            var failed = [];
            root.querySelectorAll('.math-block, .math-inline').forEach(function(el) {
                if (el.dataset.mathDone) return;
                var disp = el.classList.contains('math-block');
                var src = el.textContent.trim().slice(2, -2).trim();
                el.dataset.mathSrc = src;
                el.dataset.mathDone = '1';
                try {
                    el.innerHTML = katex.renderToString(src, { displayMode: disp, throwOnError: true, output: 'html' });
                } catch(e) { failed.push([el, disp]); }
            });
            if (!failed.length) return;
            try {
                await _loadMathJax();
                for (var i = 0; i < failed.length; i++) {
                    var el = failed[i][0], disp = failed[i][1];
                    try {
                        var node = await MathJax.tex2svgPromise(el.dataset.mathSrc, {display: disp});
                        el.innerHTML = ''; el.appendChild(node);
                    } catch(e2) {
                        el.innerHTML = '<code class="math-err" title="' + el.dataset.mathSrc.replace(/"/g, '&#34;') + '">' + el.dataset.mathSrc + '</code>';
                    }
                }
            } catch(loadErr) {
                failed.forEach(function(p) { p[0].innerHTML = '<code class="math-err">' + p[0].dataset.mathSrc + '</code>'; });
            }
        }

        function extractMathBlocks(text) {
            const CJK_RE = /[\u4e00-\u9fff\u3400-\u4dbf]/;
            const blocks = [];
            function escape(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
            text = text.replace(/\$\$([^$]+?)\$\$/gs, function(match, inner) {
                if (CJK_RE.test(inner)) return match;
                const ph = 'CWIKIMD' + String(blocks.length).padStart(6, '0');
                blocks.push([ph, '<div class="math-block not-prose">\\[' + escape(inner) + '\\]</div>']);
                return ph;
            });
            text = text.replace(/\$(?!\s)([^$\n]+?)\$(?!\$)/g, function(match, inner) {
                if (CJK_RE.test(inner)) return match;
                const ph = 'CWIKIMI' + String(blocks.length).padStart(6, '0');
                blocks.push([ph, '<span class="math-inline not-prose">\\(' + escape(inner) + '\\)</span>']);
                return ph;
            });
            text = text.replace(/\\\[([\s\S]+?)\\\]/g, function(match, inner) {
                if (CJK_RE.test(inner)) return match;
                const ph = 'CWIKIMD' + String(blocks.length).padStart(6, '0');
                blocks.push([ph, '<div class="math-block not-prose">\\[' + escape(inner) + '\\]</div>']);
                return ph;
            });
            text = text.replace(/\\\((.+?)\\\)/g, function(match, inner) {
                if (CJK_RE.test(inner)) return match;
                const ph = 'CWIKIMI' + String(blocks.length).padStart(6, '0');
                blocks.push([ph, '<span class="math-inline not-prose">\\(' + escape(inner) + '\\)</span>']);
                return ph;
            });
            return [text, blocks];
        }

        function restoreMathBlocks(html, blocks) {
            for (const [ph, mathHtml] of blocks) {
                html = html.replace('<p>' + ph + '</p>', mathHtml);
                html = html.replace(ph, mathHtml);
            }
            return html;
        }

        async function renderMarkdown(markdown) {
            const [textWithPhs, protectedMath] = extractMathBlocks(markdown);
            let html = marked.parse(textWithPhs);
            html = restoreMathBlocks(html, protectedMath);
            html = html.replace(
                /<pre><code class="language-mermaid">([\s\S]*?)<\/code><\/pre>/g,
                (match, code) => {
                    const decoded = code.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
                    return `<div class="mermaid not-prose">${decoded}</div>`;
                }
            );
            return html;
        }

        async function renderMermaidDiagrams() {
            const elements = document.querySelectorAll('.mermaid');
            for (let i = 0; i < elements.length; i++) {
                const el = elements[i];
                const id = `mermaid-${Date.now()}-${i}`;
                el.id = id;
                try {
                    const { svg } = await mermaid.render(id + '-svg', el.textContent);
                    el.innerHTML = svg;
                } catch (error) {
                    console.error('Mermaid rendering error:', error);
                    var src = el.textContent.replace(/</g, '&lt;');
                    el.innerHTML = `<details open><summary class="text-error cursor-pointer">&#9888; Mermaid error</summary><pre class="text-xs mt-2 whitespace-pre-wrap">${error.message}</pre><pre class="text-xs opacity-60">${src}</pre></details>`;
                }
            }
        }

        function showError(message) {
            const loading = document.getElementById('loading');
            const content = document.getElementById('content');
            loading.style.display = 'none';
            content.style.display = 'block';
            content.innerHTML = `<div class="alert alert-error"><span>&#9888;&#65039; ${message}</span></div>`;
        }

        // Escape key closes sidebar (consistent with other templates)
        document.addEventListener('keydown', function(e) {
            var cb = document.getElementById('gp-drawer');
            if (e.key === 'Escape' && cb.checked) { cb.checked = false; }
        });

        // a11y: make code blocks keyboard-focusable after content loads
        const _origLoadDoc = loadDocument;
        loadDocument = async function(filename) {
            await _origLoadDoc(filename);
            document.querySelectorAll('#content pre').forEach(function(pre) {
                pre.setAttribute('tabindex', '0');
                pre.setAttribute('role', 'region');
                pre.setAttribute('aria-label', 'Code block');
            });
        };
    </script>
</body>
</html>
```

- [ ] **Step 2: 运行测试**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/test_cli_html_generator.py -v`

Expected: `test_generate_renders_template_with_loaded_docs_data` 需要更新断言——原测试使用简化模板只检查 placeholder 替换，应该仍然 PASS。

- [ ] **Step 3: Commit**

```bash
git add codewiki/templates/github_pages/viewer_template.html
git commit -m "feat(fe): migrate GitHub Pages viewer template to DaisyUI

Replace custom sidebar/content CSS with DaisyUI drawer, menu, card.
Preserve client-side marked.js rendering, mermaid, and math support."
```

---

### Task 6: 测试更新与全局验证

**Files:**
- Modify: `tests/test_cli_html_generator.py` (如果 Task 5 导致失败)
- No new test files needed（现有辅助函数测试不依赖 HTML 结构）

- [ ] **Step 1: 运行全量测试**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pytest tests/ -v --tb=short 2>&1 | head -80`

检查是否有因 HTML 结构变更导致的失败。

- [ ] **Step 2: 修复失败的测试（如有）**

如果 `test_cli_html_generator.py` 中的 `test_generate_renders_template_with_loaded_docs_data` 失败，原因是测试使用了自定义简化模板（第 34-48 行），不受我们的 viewer_template.html 改动影响。但如果有其他测试依赖 HTML 结构断言，在此步骤修复。

- [ ] **Step 3: 运行 type check**

Run: `cd /home/dengqi/Source/langs/python/CodeWiki && python -m pyright codewiki/cli/static_generator.py codewiki/src/fe/templates.py --level basic 2>&1 | tail -5`

Expected: 无新增 error。

- [ ] **Step 4: 端到端 smoke test**

1. **Static generator:**
   ```bash
   cd /home/dengqi/Source/langs/python/CodeWiki
   python -m codewiki build-static <docs 目录>
   ```
   在浏览器中打开生成的 HTML，逐项验证 spec 验收标准。

2. **Web interface:** 启动 FastAPI 服务，访问首页和文档页，验证 DaisyUI 样式。

3. **GitHub Pages:** 检查生成的 viewer HTML 在浏览器中正常工作。

- [ ] **Step 5: Commit 测试修复（如有）**

```bash
git add tests/
git commit -m "test: update assertions for DaisyUI HTML structure"
```
