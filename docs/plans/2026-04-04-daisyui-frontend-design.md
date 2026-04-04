# DaisyUI Frontend UI 改造设计规格

**日期:** 2026-04-04
**状态:** Draft

## 目标

将 CodeWiki 三套前端模板的自定义 CSS 替换为 DaisyUI + Tailwind CSS（CDN 引入），统一视觉风格，使界面更专业、美观。

### 不在范围内

- 不引入 Vue/React 等 JS 框架
- 不改变 Python SSR 架构
- 不改动 Highlight.js / Mermaid / KaTeX / MathJax 渲染逻辑
- 不新增 npm/node 构建工具链
- 不改动 FastAPI 路由和后端逻辑

## CDN 引入

所有三套模板统一在 `<head>` 中引入：

```html
<!-- DaisyUI 主包（含 light/dark 内置主题 + 全部组件样式，42kB compressed） -->
<link href="https://cdn.jsdelivr.net/npm/daisyui@5" rel="stylesheet" type="text/css" />
<!-- Tailwind CSS v4 Browser CDN（开发级，适合文档工具场景） -->
<script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
```

> **注意：** `themes.css` 仅提供额外的 30+ 主题变量（cupcake、dracula 等），**不含组件样式**。
> 我们只需 light/dark，主包已内置，无需引入 `themes.css`。

**Typography（prose）方案：** `@tailwindcss/browser@4` 不内置 Typography 插件，`prose` 类默认不可用。
解决方案：在 `<style type="text/tailwindcss">` 中通过 CDN URL 加载插件：

```html
<style type="text/tailwindcss">
@import "tailwindcss";
@plugin "https://esm.sh/@tailwindcss/typography@0.5";
</style>
```

如果 `@plugin` CDN 加载不可行（实现时需验证），则退回备选方案：编写一套轻量自定义 article 样式（~50 行），使用 DaisyUI 的 CSS 变量（`--bc`、`--b2` 等）保持主题一致。

保留现有 CDN：Highlight.js、Mermaid、KaTeX、MathJax。
保留 Google Fonts（Inter + JetBrains Mono）——覆盖 DaisyUI 默认字体，维持当前品牌字体一致性。

## 主题系统

- Light/Dark 双主题，使用 DaisyUI 内置 `light` 和 `dark` 主题
- 通过 `<html data-theme="light|dark">` 切换
- 复用现有 `localStorage('cw-theme')` 逻辑
- 保留系统偏好检测（`prefers-color-scheme`）

## 改造范围

### 模板 1: Static HTML Generator (`static_generator.py`)

当前状态：808 行，内联 `_CSS` 变量（~80 行压缩 CSS，第 33-112 行）+ `_PAGE_TEMPLATE`（`string.Template`）。

**改造点：**

| 现有元素 | CSS class | DaisyUI 替换 |
|---------|-----------|-------------|
| 顶栏 | `.tb` | `navbar bg-base-200 shadow-sm` |
| 顶栏按钮 | `.ib` | `btn btn-ghost btn-square btn-sm` |
| 顶栏 logo | `.tb-logo` | `btn btn-ghost text-primary font-bold` |
| 侧栏容器 | `.sb` | `drawer-side` + `menu bg-base-200` |
| 侧栏导航项 | `a.nv` | `menu` 内的 `<li><a>` |
| 当前页高亮 | `a.nv.on` | `<a class="active">` (DaisyUI menu 内建) |
| 缺失文档项 | `.nv-missing` | `<a class="menu-disabled opacity-50">` |
| 折叠按钮 | `.nvcaret` | `<details>` 或保留自定义 caret + Tailwind 样式 |
| 元信息卡 | `.nav-meta` | `card card-compact bg-base-100` |
| 主内容区 | `article` | `prose prose-lg max-w-none`（Tailwind Typography） |
| TOC 容器 | `.toc` | `menu menu-sm` + sticky positioning |
| TOC 标题 | `.toc-h` | `menu-title` |
| TOC 高亮 | `.toc li.on a` | `<a class="active">` |
| 返回顶部 | `#btt` | `btn btn-circle btn-primary btn-sm fixed` |
| overlay | `.ov` | `drawer-overlay` |

**布局方案：** 使用 DaisyUI `drawer` 组件实现侧栏，响应式行为开箱即用：
- 桌面端：侧栏常驻（`drawer-open` 的 lg 断点）
- 移动端：汉堡按钮触发 drawer

**文章排版：** 使用 Tailwind Typography 的 `prose` 类处理 markdown 渲染后的 HTML，覆盖 h1-h4、p、ul/ol、code、pre、blockquote、table、img。额外覆盖：
- `pre code` 保持 Highlight.js 背景透明
- `.mermaid` 块的 margin/padding
- `.math-block` / `.math-inline` 的间距
- `.math-err` 的错误样式

**删除内容：** 完整删除 `_CSS` 变量（~80 行）。

### 模板 2: Web Interface (`templates.py`)

三个子模板：

#### 2a. `WEB_INTERFACE_TEMPLATE`（提交页面）

| 现有元素 | DaisyUI 替换 |
|---------|-------------|
| `.container` | `card w-full max-w-2xl mx-auto shadow-xl` |
| `.header`（渐变背景） | `card` 内的 hero 区域或保留渐变 + Tailwind |
| `.form-group label` | `label` + `label-text` |
| `.form-group input` | `input input-bordered w-full` |
| `.btn` 提交 | `btn btn-primary` |
| `.alert-success` | `alert alert-success` |
| `.alert-error` | `alert alert-error` |
| `.job-item` | `card card-compact bg-base-200` |
| `.job-status` | `badge badge-warning/info/success/error` |
| `.btn-small` | `btn btn-sm btn-outline` |
| body 渐变背景 | 保留原始渐变 `background: linear-gradient(135deg, #667eea, #764ba2)`（Tailwind 预设色与原色有偏差，用 arbitrary value 或内联样式） |

#### 2b. `DOCS_VIEW_TEMPLATE`（文档查看页面）

与 Static HTML Generator 使用相同的设计语言。当前两者 CSS 已高度重复（顶栏、侧栏、TOC、article 排版完全相同）。

**改造策略：** 共享同一套 DaisyUI 组件类名，两模板的 HTML 结构统一。差异在于：
- Jinja2 vs string.Template 模板引擎语法
- DOCS_VIEW 有 Jinja2 macro（`render_nav_item`），Static 用 Python 函数 `_build_nav_html`
- **数学渲染机制不同**（见下方"数学渲染差异"章节）

**可访问性基线：** `DOCS_VIEW_TEMPLATE` 现有以下增强特性，改造时必须统一保留到所有文档查看模板：
- `article pre` 的 `focus-visible` 样式
- 按钮的 `aria-label` 属性
- `pre` 元素的 `tabindex="0"` + `role="region"` + `aria-label="Code block"`
- Escape 键关闭 sidebar
- 移动端 `article pre` 和 `table` 的 `max-width` 溢出处理

#### 2c. `NOT_FOUND_TEMPLATE`（404 页面）

| 现有元素 | DaisyUI 替换 |
|---------|-------------|
| `.card` | `card bg-base-100 shadow-xl` |
| `.code`（72px 404） | `text-7xl font-bold text-primary` |
| `.btn` | `btn btn-primary` |

### 模板 3: GitHub Pages Template (`viewer_template.html`)

当前状态：804 行，固定左侧栏 320px + 右侧内容区，客户端 `marked.js` 渲染。

**架构特殊性：** 此模板使用 `marked.js` 在客户端渲染 Markdown，HTML 内容通过 `innerHTML` 动态插入 DOM。
`prose` 容器需要包裹动态内容区域（`.markdown-content`），确保 JS 插入后样式仍然生效。

**改造边界：** 仅替换样式，不补齐 TOC/主题切换/返回顶部等功能（当前模板本身没有这些功能）。

| 现有元素 | DaisyUI 替换 |
|---------|-------------|
| `.sidebar` | `drawer-side` + `menu bg-base-200` |
| `.content` / `.markdown-content` | `drawer-content` 内的 `prose` |
| `.logo` | `text-xl font-bold text-primary` |
| `.repo-link` | `btn btn-outline btn-sm` |
| `.repo-info` / `.info-row` | `card card-compact bg-base-100`，行内用 Tailwind 排版 |
| `.nav-section` / `.nav-section h3` | `menu-title` |
| `.nav-item` | `menu` 内 `<li><a>` |
| `.nav-item.active` | `<a class="active">` |
| `.nav-subsection`（三级缩进） | `menu` 嵌套 `<ul>` |
| `.loading` + `.loading-spinner` | DaisyUI `loading loading-spinner loading-lg` + flex 容器 |
| `.error` | `alert alert-error` |
| `.mermaid` 容器 | `card bg-base-100 p-4` |
| `@keyframes fadeIn` | Tailwind `animate-` 或保留自定义 |

**布局方案：** 同样采用 DaisyUI `drawer`，替换现有的 fixed sidebar。

## 数学渲染差异

两种文档查看模板的数学渲染机制不同，覆盖样式需分别处理：

| 模板 | 渲染方式 | DOM 中的 CSS 类 |
|------|---------|---------------|
| `static_generator.py` | `cwRenderMath()`：KaTeX 快速路径 + MathJax 异步回退。数学公式在服务端预提取为 `.math-block`/`.math-inline` 元素 | `.math-block`、`.math-inline`、`.math-err`、`.katex-display`、`.katex` |
| `templates.py` DOCS_VIEW | `katex auto-render`：客户端通过 `$$`/`$` 分隔符扫描渲染 | `.katex-display`、`.katex`（无 `.math-block`/`.math-inline`） |
| `viewer_template.html` | KaTeX 手动调用，按 `.math-block`/`.math-inline` CSS 类查找 | `.math-block`、`.math-inline`、`.katex-display`、`.katex` |

## 共性覆盖样式（prose 与 JS 库兼容）

`prose` 类会对子元素施加全局排版样式，与功能性 JS 库产生以下冲突：
- `prose` 给 `code` 元素添加 `::before`/`::after` 反引号装饰，影响 KaTeX 内部 `<code>`
- `prose` 的 `max-width` 限制影响宽 Mermaid 图和大型表格
- Highlight.js 的 `.hljs` span 颜色被 `prose` 的 `color` 覆盖

**策略：** 对 `.mermaid`、`.math-block`、`.math-inline`、`.katex-display` 统一使用 `not-prose` class 包裹，避免 prose 侵入。额外添加覆盖规则：

```css
/* Highlight.js: 保持代码块背景透明，让 prose 控制外层 */
.prose pre code.hljs { background: transparent !important; }

/* prose 内 code 的伪元素装饰：排除 KaTeX 内部 code */
.prose .katex code::before,
.prose .katex code::after { content: none; }

/* Mermaid: 不被 prose max-width 限制 */
.prose .mermaid { max-width: none; }

/* 数学公式：两种渲染路径都需要覆盖 */
.prose .math-block,
.prose .katex-display { overflow-x: auto; max-width: none; }

/* 数学错误提示 */
.prose .math-err { color: oklch(var(--er)); font-style: italic; font-size: 0.85em; }

/* 宽表格溢出 */
.prose table { display: block; max-width: 100%; overflow-x: auto; }
```

## JavaScript 逻辑保留

以下 JS 功能保持不变，仅更新 DOM 选择器以匹配新的 class/id：

1. **主题切换** — `data-theme` 属性 + `localStorage` + Highlight.js 主题联动
2. **侧栏开关** — 采用 DaisyUI drawer 的 hidden checkbox 机制（见下方）
3. **TOC 高亮** — IntersectionObserver 逻辑不变，更新选择器为 DaisyUI menu active class
4. **返回顶部** — 显示/隐藏逻辑不变
5. **Mermaid 渲染** — 完整保留 `cwRenderMermaid()`
6. **数学渲染** — 完整保留 `cwRenderMath()`（仅 static_generator）/ KaTeX auto-render（仅 templates.py）
7. **导航折叠** — 改用 HTML `<details><summary>` 原生折叠，无需额外 JS
8. **Escape 关闭侧栏** — 保留，通过 JS uncheck drawer-toggle checkbox 实现
9. **可访问性** — 保留 `aria-label`、`tabindex`、`role` 属性

### Drawer 侧栏整合方案

**选择：DaisyUI checkbox 机制为基础，JS 操控 `checked` 属性整合 localStorage 持久化。**

HTML 结构（以 static_generator 为例）：

```html
<div class="drawer lg:drawer-open">
  <input id="cw-drawer" type="checkbox" class="drawer-toggle" />
  <div class="drawer-content">
    <!-- navbar + article + toc -->
  </div>
  <div class="drawer-side">
    <label for="cw-drawer" aria-label="close sidebar" class="drawer-overlay"></label>
    <ul class="menu bg-base-200 min-h-full w-72 p-4">
      <!-- nav items -->
    </ul>
  </div>
</div>
```

JS 整合逻辑：
- **桌面端**：`lg:drawer-open` 使侧栏默认展开。用户点击切换按钮时，通过 JS 操作 checkbox `checked` 属性，并写入 `localStorage('cw-sb')`。
- **移动端**：checkbox 默认 unchecked（侧栏隐藏）。汉堡按钮用 `<label for="cw-drawer">` 触发。点击 `drawer-overlay` 自动关闭（DaisyUI 内建）。
- **Escape 键**：`keydown` 监听器 uncheck checkbox 并 `focus()` 回汉堡按钮。

> **注意：** DaisyUI CDN 版不包含 `is-drawer-open:`/`is-drawer-close:` variant class（官方 CDN 文档明确说明）。
> 不使用这些 variant，改用 JS 读取 checkbox 状态来动态添加/移除辅助 class。

## 文件改动清单

| 文件 | 类型 | 改动 |
|------|------|------|
| `codewiki/cli/static_generator.py` | 核心 | 删除 `_CSS`，重写 `_PAGE_TEMPLATE` HTML 结构 |
| `codewiki/src/fe/templates.py` | 核心 | 重写三个模板的 HTML/CSS |
| `codewiki/templates/github_pages/viewer_template.html` | 核心 | 重写 HTML 结构和内联 CSS |
| `codewiki/cli/static_generator.py` `_build_nav_html()` | 适配 | 输出的 HTML class 改为 DaisyUI menu 格式 |
| `codewiki/cli/static_generator.py` `_build_meta_html()` | 适配 | 输出改为 DaisyUI card 格式 |
| `codewiki/src/fe/templates.py` Jinja2 macro | 适配 | `render_nav_item` 输出改为 menu 格式 |
| 相关测试文件 | 适配 | 更新依赖 HTML 结构断言的测试用例 |

## 风险与缓解

| 风险 | 严重度 | 缓解措施 |
|------|--------|---------|
| Tailwind Play CDN 不推荐用于生产 | 低 | CodeWiki 是文档工具，非高流量 SaaS；CDN 有全球缓存 |
| `@tailwindcss/typography` 在 Browser CDN 下不可用 | 高 | 优先尝试 `@plugin` CDN 加载；失败则用自定义 article 样式 + DaisyUI 变量 |
| `prose` 与 Highlight.js 样式冲突 | 中 | `not-prose` 包裹 + 覆盖 CSS；实际测试验证 |
| `prose` 与 KaTeX/MathJax 冲突 | 中 | `.math-block`/`.math-inline`/`.katex-display` 用 `not-prose` 包裹 |
| DaisyUI CDN 不含 `is-drawer-open/close` variant | 低 | 不使用 variant，用 JS 读取 checkbox 状态控制 |
| 三套模板改造期间视觉不一致 | 低 | 按模板逐个改造，每个改完后验证 |

## 验收标准

1. 三套模板全部使用 DaisyUI 组件，无残留自定义 CSS（覆盖样式除外）
2. Light/Dark 主题切换正常，与 Highlight.js 主题联动
3. Mermaid 图表、KaTeX/MathJax 数学公式渲染正常
4. 响应式布局：移动端侧栏可收起，桌面端常驻
5. TOC 目录高亮、返回顶部、导航折叠等交互功能正常
6. 404 页面风格统一
7. `codewiki build-static` 生成的 HTML 可独立打开，无外部依赖问题（CDN 除外）
