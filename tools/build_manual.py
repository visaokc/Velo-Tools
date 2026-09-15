"""Build the offline bilingual handbook from the two canonical Markdown files.

Only the documented Markdown subset is accepted. No package install, network
request or arbitrary HTML execution is needed to build or read the handbook.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TABS = ("vertex-groups", "mesh", "weights", "materials", "game")
LOCALES = ("en", "zh-CN")
REQUIRED = {
    "en": ("Steps", "Small example", "Check and recover"),
    "zh-CN": ("操作步骤", "小案例", "检查与排错"),
}
UI = {
    "en": {
        "title": "Tutorial handbook", "tagline": "Find the button. Understand the task.",
        "intro": "Practical steps, small examples and checks, organized like the add-on.",
        "start": "Start here", "search": "Search every tutorial", "clear": "Clear search",
        "outline": "In this tab", "directory": "Tab directory", "read": "Read tutorial",
        "back": "Back to directory", "previous": "Previous", "next": "Next",
        "print": "Print / PDF", "tutorials": "tutorials", "results": "Search results",
        "none": "No tutorials found. Try a button name, a task, or another language.",
        "missing": "This tutorial link was not found.", "home": "Choose a tab to continue.",
        "noscript": "JavaScript is off. All tutorials remain readable below; use your browser's Find command.",
        "skip": "Skip to tutorial", "footer": "Velo Tools 1.7.1 · Offline handbook · No accounts, analytics or network dependencies.",
        "note": "Examples are illustrative. Use identities from your own extraction.",
    },
    "zh-CN": {
        "title": "教程手册", "tagline": "找到按钮，也弄懂怎么用。",
        "intro": "按插件位置组织，用操作步骤、小案例和检查方法讲清每项功能。",
        "start": "新手入门", "search": "搜索全部教程", "clear": "清空搜索",
        "outline": "本 Tab 目录", "directory": "功能目录", "read": "阅读教程",
        "back": "返回功能目录", "previous": "上一篇", "next": "下一篇",
        "print": "打印 / PDF", "tutorials": "篇教程", "results": "搜索结果",
        "none": "没有找到教程。试试按钮名、要做的事情，或切换语言。",
        "missing": "没有找到这个教程链接。", "home": "请选择一个 Tab 继续。",
        "noscript": "JavaScript 已关闭，下方仍可阅读全部教程，也可用浏览器查找。",
        "skip": "跳到教程正文", "footer": "Velo Tools 1.7.1 · 离线教程手册 · 无账号、统计或联网依赖。",
        "note": "案例用于理解；真实身份请使用自己的提取数据。",
    },
}


@dataclass
class Article:
    key: str
    title: str
    group: str
    tab: str
    lines: list[str] = field(default_factory=list)

    @property
    def summary(self):
        return next(line.strip() for line in self.lines if line.strip())


@dataclass
class Chapter:
    key: str
    title: str
    lines: list[str] = field(default_factory=list)
    articles: list[Article] = field(default_factory=list)


def parse(source: str, locale: str) -> list[Chapter]:
    if "\ufffd" in source or source.startswith("\ufeff"):
        raise ValueError(f"{locale}: invalid UTF-8 text or BOM")
    chapters, chapter, article, pending, group = [], None, None, None, ""
    in_directory = False
    for line in source.splitlines():
        if line == "<!-- directory:start -->":
            in_directory = True
            continue
        if line == "<!-- directory:end -->":
            in_directory = False
            continue
        if in_directory:
            continue
        anchor = re.fullmatch(r'<a id="([a-z0-9-]+)"></a>', line)
        if anchor:
            pending = anchor[1]
            article = None
            continue
        if line.startswith("## "):
            if not pending:
                raise ValueError("Every tab needs a stable anchor")
            chapter = Chapter(pending, line[3:])
            chapters.append(chapter)
            pending, article, group = None, None, ""
        elif line.startswith("### "):
            group, article = line[4:], None
        elif line.startswith("#### "):
            if not pending or chapter is None or not group:
                raise ValueError("Every tutorial needs a tab, group and stable anchor")
            article = Article(pending, line[5:], group, chapter.key)
            chapter.articles.append(article)
            pending = None
        elif article is not None:
            article.lines.append(line)
        elif chapter is not None and not group:
            chapter.lines.append(line)
    if tuple(c.key for c in chapters) != TABS:
        raise ValueError(f"{locale}: tab order must match the add-on")
    identifiers = [c.key for c in chapters]
    for chapter in chapters:
        if not chapter.articles:
            raise ValueError(f"Empty tab: {chapter.key}")
        for article in chapter.articles:
            identifiers.append(article.key)
            body = "\n".join(article.lines)
            for heading in REQUIRED[locale]:
                if f"##### {heading}" not in body:
                    raise ValueError(f"{locale}/{article.key}: missing {heading}")
            minimum = 350 if locale == "en" else 200
            if not re.search(r"^1\. ", body, re.M) or len(body) < minimum:
                raise ValueError(f"{locale}/{article.key}: incomplete tutorial")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate stable IDs")
    for target in re.findall(r"\]\(#([^)]*)\)", source):
        if target not in identifiers:
            raise ValueError(f"{locale}: broken local link #{target}")
    return chapters


def inline(value: str, locale: str) -> str:
    value = escape(value)
    value = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", value)

    def link(match):
        label, href = match.groups()
        if href.startswith("#"):
            href = f"#{locale}/{href[1:]}"
        elif not (href.startswith("https://") or href in {
            "user-manual.en.md", "user-manual.zh-CN.md", "manual.html"
        }):
            raise ValueError(f"Unsupported link: {href}")
        return f'<a href="{href}">{label}</a>'

    return re.sub(r"\[([^]]+)\]\(([^)]+)\)", link, value)


def render_markdown(lines: list[str], locale: str) -> str:
    result, index = [], 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("##### "):
            result.append(f"<h3>{inline(line[6:], locale)}</h3>")
            index += 1
        elif line.startswith("|"):
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [c.strip() for c in lines[index].strip().strip("|").split("|")]
                rows.append(cells)
                index += 1
            if len(rows) < 2 or not all(re.fullmatch(r":?-+:?", c) for c in rows[1]):
                raise ValueError("Unsupported table")
            if any(len(row) != len(rows[0]) for row in rows[2:]):
                raise ValueError("Table column count mismatch")
            head = "".join(f'<th scope="col">{inline(c, locale)}</th>' for c in rows[0])
            body = "".join("<tr>" + "".join(f"<td>{inline(c, locale)}</td>" for c in row) + "</tr>"
                           for row in rows[2:])
            result.append(f'<div class="table-scroll" tabindex="0" role="region" aria-label="{escape(UI[locale]["title"])}"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
        elif re.match(r"^\d+\. ", line) or line.startswith("- "):
            ordered = bool(re.match(r"^\d+\. ", line))
            pattern = r"^\d+\. " if ordered else r"^- "
            items = []
            while index < len(lines) and re.match(pattern, lines[index].strip()):
                items.append(f"<li>{inline(re.sub(pattern, '', lines[index].strip()), locale)}</li>")
                index += 1
            tag = "ol" if ordered else "ul"
            result.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
        else:
            paragraph = []
            while index < len(lines) and lines[index].strip():
                if re.match(r"^(##### |\||\d+\. |- )", lines[index].strip()):
                    break
                paragraph.append(lines[index].strip())
                index += 1
            if not paragraph:
                raise ValueError(f"Unsupported Markdown: {line}")
            result.append(f"<p>{inline(' '.join(paragraph), locale)}</p>")
    return "\n".join(result)


def with_directories(source: str, chapters: list[Chapter]) -> str:
    source = re.sub(r"\n<!-- directory:start -->.*?<!-- directory:end -->\n", "", source, flags=re.S)
    for chapter in chapters:
        rows = ["<!-- directory:start -->"]
        for article in chapter.articles:
            rows.append(f"- [{article.title}](#{article.key})")
        rows.append("<!-- directory:end -->")
        pattern = rf'(<a id="{chapter.key}"></a>\n## [^\n]+\n.*?)(?=\n### )'
        source, count = re.subn(pattern, lambda m: m[1].rstrip() + "\n\n" + "\n".join(rows) + "\n", source, flags=re.S)
        if count != 1:
            raise ValueError(f"Cannot locate tab: {chapter.key}")
    return source.rstrip() + "\n"


def render_language(locale: str, chapters: list[Chapter]) -> str:
    text = UI[locale]
    output = [f'<section class="manual-language" lang="{locale}" data-locale="{locale}">']
    for chapter in chapters:
        tab = chapter.key
        output.append(f'<section class="chapter" data-tab="{tab}" data-title="{escape(chapter.title)}">')
        output.append(f'<div class="overview" data-view="{tab}"><p class="eyebrow">{text["directory"]}</p><h2 tabindex="-1">{escape(chapter.title)}</h2>')
        output.append(render_markdown(chapter.lines, locale))
        output.append(f'<p class="chapter-count">{len(chapter.articles)} {text["tutorials"]}</p>')
        current_group = None
        for article in chapter.articles:
            if article.group != current_group:
                if current_group is not None:
                    output.append("</ol>")
                output.append(f'<h3 class="group-title">{escape(article.group)}</h3><ol class="directory-list">')
                current_group = article.group
            output.append(f'<li><a class="directory-link" href="#{locale}/{article.key}"><span>{escape(article.title)}</span><span aria-hidden="true">↗</span></a><p>{inline(article.summary, locale)}</p></li>')
        output.append("</ol></div>")
        for number, article in enumerate(chapter.articles):
            output.append(f'<article data-article="{article.key}" data-title="{escape(article.title)}" data-group="{escape(article.group)}">')
            output.append(f'<a class="back-link" href="#{locale}/{tab}">← {text["back"]}</a><p class="eyebrow">{escape(chapter.title)} / {escape(article.group)}</p>')
            output.append(f'<h2 tabindex="-1">{escape(article.title)}</h2>')
            output.append(render_markdown(article.lines, locale))
            output.append(f'<nav class="article-paging" aria-label="{text["title"]}">')
            if number > 0:
                previous = chapter.articles[number - 1]
                output.append(f'<a href="#{locale}/{previous.key}"><small>← {text["previous"]}</small>{escape(previous.title)}</a>')
            if number + 1 < len(chapter.articles):
                following = chapter.articles[number + 1]
                output.append(f'<a href="#{locale}/{following.key}"><small>{text["next"]} →</small>{escape(following.title)}</a>')
            output.append("</nav></article>")
        output.append("</section>")
    output.append("</section>")
    return "\n".join(output)


def build(check=False):
    sources, manuals = {}, {}
    for locale in LOCALES:
        path = DOCS / f"user-manual.{locale}.md"
        sources[locale] = path.read_text(encoding="utf-8")
        manuals[locale] = parse(sources[locale], locale)
    schema = lambda chapters: [(c.key, [(a.key, a.tab) for a in c.articles]) for c in chapters]
    if schema(manuals["en"]) != schema(manuals["zh-CN"]):
        raise ValueError("English/Chinese tutorial ID or ordering mismatch")
    for locale in LOCALES:
        expected = with_directories(sources[locale], manuals[locale])
        path = DOCS / f"user-manual.{locale}.md"
        if check:
            if path.read_text(encoding="utf-8") != expected:
                raise ValueError(f"Stale Markdown directory: {path.name}")
        else:
            path.write_text(expected, encoding="utf-8", newline="\n")
    import json
    labels = json.dumps(UI, ensure_ascii=False).replace("<", "\\u003c")
    style = (DOCS / "manual.css").read_text(encoding="utf-8")
    script = (DOCS / "manual.js").read_text(encoding="utf-8")
    languages = "\n".join(render_language(locale, manuals[locale]) for locale in LOCALES)
    tabs = "".join(f'<a role="tab" id="tab-{c.key}" data-tab-link="{c.key}" aria-controls="reader" aria-selected="{str(i == 0).lower()}" tabindex="{0 if i == 0 else -1}" href="#en/{c.key}">{escape(c.title)}</a>'
                   for i, c in enumerate(manuals["en"]))
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light">
<meta name="description" content="The complete Velo Tools 1.7.1 bilingual tutorial handbook, organized into five add-on tabs.">
<title>Velo Tools 1.7.1 — Tutorial handbook</title>
<style>{style}</style>
</head>
<body>
<a class="skip-link" href="#reader" data-i18n="skip">Skip to tutorial</a>
<header class="site-header">
 <div class="brand-row"><a class="brand" href="#en/vertex-groups" id="brand-link"><span class="brand-mark" aria-hidden="true">V</span><span>VELO TOOLS <small data-i18n="title">Tutorial handbook</small></span></a>
 <div class="header-actions"><span class="version">1.7.1</span><a id="start-link" href="#en/game-start" data-i18n="start">Start here</a><button id="language-button" type="button" lang="zh-CN" aria-label="切换为简体中文">简体中文</button><button id="print-button" type="button" data-i18n="print">Print / PDF</button></div></div>
 <div class="tab-row" role="tablist" aria-label="Velo Tools">{tabs}</div>
</header>
<div class="workspace">
 <aside class="sidebar" aria-label="Tutorial navigation">
  <label for="search" data-i18n="search">Search every tutorial</label>
  <div class="search-row"><input id="search" type="search" maxlength="160" autocomplete="off" spellcheck="false"><button id="clear-search" type="button" aria-label="Clear search">×</button></div>
  <p class="search-status" id="search-status" role="status" aria-live="polite"></p>
  <nav id="outline" aria-label="In this tab"></nav>
  <p class="sidebar-note" data-i18n="note">Examples are illustrative. Use identities from your own extraction.</p>
 </aside>
 <main id="reader" tabindex="-1" role="tabpanel" aria-labelledby="tab-vertex-groups">
  <noscript><p>JavaScript is off. All tutorials remain readable below. / JavaScript 已关闭，仍可阅读下方全部教程。</p></noscript>
  <div class="reader-top"><span data-i18n="tagline">Find the button. Understand the task.</span><span class="offline-badge">OFFLINE READY</span></div>
  <section id="search-results" hidden></section>
  <section id="not-found" hidden><h2 tabindex="-1" data-i18n="missing">This tutorial link was not found.</h2><p data-i18n="home">Choose a tab to continue.</p></section>
  {languages}
 </main>
</div>
<footer data-i18n="footer">Velo Tools 1.7.1 · Offline handbook · No accounts, analytics or network dependencies.</footer>
<script id="manual-ui" type="application/json">{labels}</script>
<script>{script}</script>
</body>
</html>
"""
    destination = DOCS / "manual.html"
    if check:
        if not destination.exists() or destination.read_text(encoding="utf-8") != document:
            raise ValueError("Generated manual.html is stale; run tools/build_manual.py")
    else:
        destination.write_text(document, encoding="utf-8", newline="\n")
    count = sum(len(c.articles) for c in manuals["en"])
    print(f"MANUAL_OK: {count} tutorials x 2 languages; 5 tabs; links and parity checked")
    return manuals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Reject stale generated output without writing")
    build(parser.parse_args().check)
