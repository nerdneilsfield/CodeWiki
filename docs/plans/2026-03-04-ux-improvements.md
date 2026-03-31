# UX Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix four user-facing UX/accessibility issues identified in the Codex audit review.

**Architecture:** All changes are inline CSS/JS edits inside `codewiki/src/fe/templates.py` (Python string constants `DOCS_VIEW_TEMPLATE` and `WEB_INTERFACE_TEMPLATE`), plus a FastAPI exception handler registration in `web_app.py`. No new files are needed except a `NOT_FOUND_TEMPLATE` constant added to `templates.py`.

**Tech Stack:** FastAPI exception handler, inline CSS (CSS variables already in place), vanilla JS (no frameworks), Highlight.js 11.9.0 (CDN).

---

## Issue Registry (from Codex review)

| # | Severity | Issue |
|---|----------|-------|
| UX-1 | High | 404 page is blank gray with no guidance or recovery CTA |
| UX-2 | Medium | Mobile (375px) horizontal overflow — `<pre>` and tables scroll the whole page |
| UX-3 | Medium | Scrollable `<pre>` blocks not keyboard-focusable; `tabindex="0"` missing |
| UX-4 | Low | Sidebar not dismissible with `Escape` key |

*(Color contrast is deferred — hljs github theme is borderline at 4.49:1; acceptable until a full theme audit is done.)*

---

## Key Files

- **`codewiki/src/fe/templates.py`** — All HTML/CSS/JS lives here as Python string constants.
  - `WEB_INTERFACE_TEMPLATE` (line 7): main landing page
  - `DOCS_VIEW_TEMPLATE` (line 324): docs viewer
- **`codewiki/src/fe/web_app.py`** — FastAPI `app` instance; register exception handler here.

---

## Task 1: Custom 404 Page (UX-1)

**Files:**
- Modify: `codewiki/src/fe/templates.py` — add `NOT_FOUND_TEMPLATE` constant after line 567
- Modify: `codewiki/src/fe/web_app.py` — register exception handler

### Step 1: Add `NOT_FOUND_TEMPLATE` to templates.py

Append after the closing `"""` of `DOCS_VIEW_TEMPLATE` (after line 567):

```python
NOT_FOUND_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Page Not Found — CodeWiki</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box;}
body{font-family:'Inter',system-ui,sans-serif;background:#f8fafc;color:#1e293b;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px;}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:48px 40px;max-width:440px;width:100%;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.06);}
.code{font-size:72px;font-weight:700;color:#2563eb;line-height:1;margin-bottom:16px;}
h1{font-size:1.4rem;font-weight:600;margin-bottom:10px;}
p{color:#475569;line-height:1.6;margin-bottom:28px;}
.btn{display:inline-block;background:#2563eb;color:#fff;text-decoration:none;padding:10px 24px;border-radius:6px;font-weight:500;font-size:15px;transition:background .15s;}
.btn:hover{background:#1d4ed8;}
.hint{margin-top:20px;font-size:13px;color:#94a3b8;}
</style>
</head>
<body>
<div class="card">
  <div class="code">404</div>
  <h1>Page not found</h1>
  <p>The page you're looking for doesn't exist or has been moved.</p>
  <a href="/" class="btn">&#8592; Back to Home</a>
  <p class="hint">If you followed a link inside a documentation page, the file may not have been generated yet.</p>
</div>
</body>
</html>"""
```

### Step 2: Register exception handler in web_app.py

In `codewiki/src/fe/web_app.py`:

**Add import** at the top (after `from fastapi.responses import HTMLResponse`):
```python
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .templates import NOT_FOUND_TEMPLATE
```

**Add handler** after `app = FastAPI(...)` block (before "Initialize components"):
```python
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return HTMLResponse(content=NOT_FOUND_TEMPLATE, status_code=404)
    raise exc
```

### Step 3: Verify manually

Start server and navigate to `/nonexistent-path` — you should see the styled 404 page, not a blank gray screen.

### Step 4: Commit

```bash
git add codewiki/src/fe/templates.py codewiki/src/fe/web_app.py
git commit -m "feat(ux): Add styled 404 page with home CTA"
```

---

## Task 2: Mobile Overflow Fix (UX-2)

**Files:**
- Modify: `codewiki/src/fe/templates.py` — update `DOCS_VIEW_TEMPLATE` CSS

**Problem:** On 375px screens, wide `<pre>` blocks and `<table>` elements can overflow the viewport horizontally, forcing a full-page scroll rather than a contained element scroll.

### Step 1: Locate the mobile CSS block

In `DOCS_VIEW_TEMPLATE`, find line 425:
```css
@media(max-width:767px){.cw{padding:24px 18px;gap:0;}}
```

### Step 2: Replace with expanded mobile block

```css
@media(max-width:767px){
  .cw{padding:24px 18px;gap:0;}
  article pre{max-width:calc(100vw - 36px);}
  article table{display:block;max-width:calc(100vw - 36px);overflow-x:auto;}
  body{overflow-x:hidden;}
}
```

**Why these values:**
- `calc(100vw - 36px)` = viewport minus 18px padding on each side (from `.cw` mobile padding)
- `display:block` on `table` is required for `overflow-x:auto` to work on tables
- `body{overflow-x:hidden}` prevents the viewport itself from expanding

### Step 3: Verify manually

Open browser DevTools → device emulation → 375px width, load a docs page with a wide code block. The `<pre>` should scroll horizontally within itself; the page should not scroll horizontally.

### Step 4: Commit

```bash
git add codewiki/src/fe/templates.py
git commit -m "fix(ux): Prevent mobile horizontal overflow for pre and table elements"
```

---

## Task 3: Keyboard Accessibility for Code Blocks + Escape Sidebar (UX-3, UX-4)

**Files:**
- Modify: `codewiki/src/fe/templates.py` — update `DOCS_VIEW_TEMPLATE` CSS and JS

Two issues fixed in one task because both are small JS additions + focus styles.

### Step 1: Add focus styles to CSS

In `DOCS_VIEW_TEMPLATE`, find:
```css
article pre{background:var(--bg-pre);border:1px solid var(--border);border-radius:8px;padding:1rem 1.2rem;overflow-x:auto;margin-bottom:1.2rem;}
```

Replace with:
```css
article pre{background:var(--bg-pre);border:1px solid var(--border);border-radius:8px;padding:1rem 1.2rem;overflow-x:auto;margin-bottom:1.2rem;outline:none;}
article pre:focus-visible{box-shadow:0 0 0 3px var(--primary);border-color:var(--primary);}
```

**Why `outline:none` + `box-shadow` focus ring:** Removes the default jagged browser outline and replaces it with a theme-aware ring using the existing `--primary` variable.

### Step 2: Add JS to make pre elements keyboard-focusable

In `DOCS_VIEW_TEMPLATE`, locate the `DOMContentLoaded` listener that calls `hljs.highlightAll()` (line 503):
```javascript
document.addEventListener('DOMContentLoaded',function(){hljs.highlightAll();});
```

Replace with:
```javascript
document.addEventListener('DOMContentLoaded',function(){
  hljs.highlightAll();
  // Make horizontally scrollable code blocks keyboard-focusable
  document.querySelectorAll('article pre').forEach(function(pre){
    pre.setAttribute('tabindex','0');
    pre.setAttribute('role','region');
    pre.setAttribute('aria-label','Code block');
  });
});
```

**Why `role="region"` + `aria-label`:** Screen readers announce "Code block, region" when focused, signalling that keyboard scroll is available inside.

### Step 3: Add Escape key handler for sidebar

In `DOCS_VIEW_TEMPLATE`, locate the overlay click listener (line 514):
```javascript
ov.addEventListener('click',sbHide);
```

Add the Escape handler immediately after:
```javascript
ov.addEventListener('click',sbHide);
document.addEventListener('keydown',function(e){
  if(e.key==='Escape'&&!sb.classList.contains('off')){
    sbHide();
    document.getElementById('sb-toggle').focus();
  }
});
```

**Why return focus to toggle:** After Escape dismisses the sidebar, focus should return to the element that opened it — this is the standard keyboard pattern per ARIA authoring practices.

### Step 4: Verify manually

1. Tab into a code block → it should be focusable with a visible blue ring
2. Open sidebar → press `Escape` → sidebar closes, focus returns to the ☰ button

### Step 5: Commit

```bash
git add codewiki/src/fe/templates.py
git commit -m "fix(a11y): Make code blocks keyboard-focusable; dismiss sidebar with Escape"
```

---

## Execution Order

```
Task 1 (404 page) → Task 2 (mobile overflow) → Task 3 (keyboard/escape)
All tasks are independent — can also be parallelized if using subagent-driven execution.
```
