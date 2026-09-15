/* The generated HTML contains all content. File URLs need no fetch or server. */
(() => {
  "use strict";
  const labels = JSON.parse(document.getElementById("manual-ui").textContent);
  const tabs = [...document.querySelectorAll("[data-tab-link]")];
  const languages = [...document.querySelectorAll(".manual-language")];
  const reader = document.getElementById("reader");
  const search = document.getElementById("search");
  const results = document.getElementById("search-results");
  const missing = document.getElementById("not-found");
  const outline = document.getElementById("outline");
  const status = document.getElementById("search-status");
  const inventory = {};
  let current = { locale: "en", key: "vertex-groups", query: "" };
  let debounce;

  for (const language of languages) {
    const locale = language.dataset.locale;
    const chapters = [...language.querySelectorAll(".chapter")];
    inventory[locale] = {
      element: language,
      chapters,
      articles: chapters.flatMap(chapter => [...chapter.querySelectorAll("article")].map(node => ({
        key: node.dataset.article,
        title: node.dataset.title,
        group: node.dataset.group,
        tab: chapter.dataset.tab,
        tabTitle: chapter.dataset.title,
        summary: [...node.children].find(child => child.tagName === "P" && !child.classList.contains("eyebrow"))?.textContent || "",
        text: node.textContent.toLocaleLowerCase(locale),
        node,
      }))),
    };
  }

  function route() {
    const raw = location.hash.slice(1);
    const slash = raw.indexOf("/");
    if (!raw) return { locale: "en", key: "vertex-groups", query: "" };
    if (slash < 0) return { locale: "en", key: "missing", query: "" };
    const locale = raw.slice(0, slash);
    if (!inventory[locale]) return { locale: "en", key: "missing", query: "" };
    const part = raw.slice(slash + 1);
    const question = part.indexOf("?");
    const key = question < 0 ? part : part.slice(0, question);
    const query = new URLSearchParams(question < 0 ? "" : part.slice(question + 1)).get("q") || "";
    return { locale, key, query: query.slice(0, 160) };
  }

  function link(key, locale = current.locale) {
    return "#" + locale + "/" + key;
  }

  function navigate(key, query = "", replace = false) {
    const hash = link(key) + (key === "search" ? "?q=" + encodeURIComponent(query) : "");
    if (replace) {
      history.replaceState(null, "", hash);
      render(false);
    } else if (location.hash !== hash) {
      location.hash = hash;
    } else {
      render(false);
    }
  }

  function buildOutline(chapter, activeKey) {
    outline.replaceChildren();
    const heading = document.createElement("h2");
    heading.textContent = labels[current.locale].outline;
    outline.append(heading);
    const directory = document.createElement("a");
    directory.href = link(chapter.dataset.tab);
    directory.textContent = labels[current.locale].directory;
    if (activeKey === chapter.dataset.tab) directory.setAttribute("aria-current", "page");
    outline.append(directory);
    let group = null;
    for (const article of inventory[current.locale].articles.filter(item => item.tab === chapter.dataset.tab)) {
      if (article.group !== group) {
        const label = document.createElement("h3");
        label.textContent = article.group;
        outline.append(label);
        group = article.group;
      }
      const anchor = document.createElement("a");
      anchor.href = link(article.key);
      anchor.textContent = article.title;
      if (activeKey === article.key) anchor.setAttribute("aria-current", "page");
      outline.append(anchor);
    }
  }

  function searchResults(query) {
    results.replaceChildren();
    const text = labels[current.locale];
    const title = document.createElement("h2");
    title.tabIndex = -1;
    title.textContent = text.results;
    results.append(title);
    const words = query.toLocaleLowerCase(current.locale).trim().split(/\s+/).filter(Boolean);
    const hits = words.length ? inventory[current.locale].articles.filter(article =>
      words.every(word => article.text.includes(word))) : [];
    status.textContent = hits.length + " " + text.tutorials;
    const summary = document.createElement("p");
    summary.textContent = hits.length ? query + " · " + hits.length + " " + text.tutorials : text.none;
    results.append(summary);
    const list = document.createElement("ol");
    list.className = "directory-list";
    for (const hit of hits) {
      const row = document.createElement("li");
      const tag = document.createElement("span");
      tag.className = "result-tag";
      tag.textContent = hit.tabTitle + " / " + hit.group;
      const anchor = document.createElement("a");
      anchor.className = "directory-link";
      anchor.href = link(hit.key);
      anchor.textContent = hit.title;
      const description = document.createElement("p");
      description.textContent = hit.summary;
      row.append(tag, anchor, description);
      list.append(row);
    }
    results.append(list);
  }

  function render(moveFocus = true) {
    clearTimeout(debounce);
    current = route();
    const locale = current.locale;
    const text = labels[locale];
    const model = inventory[locale];
    const active = model.articles.find(item => item.key === current.key);
    const tabKey = active ? active.tab : current.key;
    const chapter = model.chapters.find(item => item.dataset.tab === tabKey);
    for (const language of languages) language.hidden = language.dataset.locale !== locale;
    for (const language of languages) {
      for (const item of language.querySelectorAll(".chapter")) item.hidden = item !== chapter;
      for (const item of language.querySelectorAll("article")) item.hidden = item !== active?.node;
      for (const item of language.querySelectorAll(".overview")) item.hidden = !!active;
    }
    for (const node of document.querySelectorAll("[data-i18n]")) node.textContent = text[node.dataset.i18n];
    document.documentElement.lang = locale;
    const languageButton = document.getElementById("language-button");
    languageButton.textContent = locale === "en" ? "简体中文" : "English";
    languageButton.lang = locale === "en" ? "zh-CN" : "en";
    languageButton.setAttribute("aria-label", locale === "en" ? "切换为简体中文" : "Switch to English");
    search.setAttribute("placeholder", locale === "en" ? "e.g. mirror, normal, LOD" : "例如：镜像、法线、LOD");
    search.value = current.key === "search" ? current.query : "";
    document.getElementById("clear-search").setAttribute("aria-label", text.clear);
    document.getElementById("start-link").href = link("game-start");
    document.getElementById("brand-link").href = link("vertex-groups");
    document.querySelector(".sidebar").setAttribute("aria-label", text.outline);
    outline.setAttribute("aria-label", text.outline);
    let selected = false;
    for (const tab of tabs) {
      const selectedHere = tab.dataset.tabLink === tabKey;
      tab.textContent = model.chapters.find(item => item.dataset.tab === tab.dataset.tabLink).dataset.title;
      tab.href = link(tab.dataset.tabLink);
      tab.setAttribute("aria-selected", String(selectedHere));
      tab.tabIndex = selectedHere ? 0 : -1;
      selected ||= selectedHere;
    }
    if (!selected) tabs[0].tabIndex = 0;
    results.hidden = current.key !== "search";
    missing.hidden = !!chapter || current.key === "search";
    if (chapter) {
      reader.setAttribute("role", "tabpanel");
      reader.setAttribute("aria-labelledby", "tab-" + tabKey);
      buildOutline(chapter, current.key);
      status.textContent = chapter.querySelectorAll("article").length + " " + text.tutorials;
    } else {
      reader.removeAttribute("role");
      reader.removeAttribute("aria-labelledby");
      outline.replaceChildren();
      status.textContent = "";
    }
    if (!results.hidden) searchResults(current.query);
    document.body.classList.toggle("print-tab", !!chapter && !active);
    document.title = (active?.title || chapter?.dataset.title || text.results) + " — Velo Tools 1.7.1";
    if (moveFocus && document.activeElement !== search && !tabs.includes(document.activeElement)) {
      const heading = (active?.node || (chapter?.querySelector(".overview")) || (!results.hidden ? results : missing)).querySelector("h2");
      heading?.focus({ preventScroll: true });
      reader.scrollIntoView({ block: "start" });
    }
  }

  for (const tab of tabs) {
    tab.addEventListener("keydown", event => {
      const index = tabs.indexOf(tab);
      let next;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index + tabs.length - 1) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next === undefined) return;
      event.preventDefault();
      tabs[next].focus();
      navigate(tabs[next].dataset.tabLink);
    });
  }
  search.addEventListener("input", () => {
    clearTimeout(debounce);
    const query = search.value;
    debounce = setTimeout(() => {
      if (!query.trim()) navigate("vertex-groups");
      else navigate("search", query, current.key === "search");
    }, 140);
  });
  search.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      clearTimeout(debounce);
      navigate("vertex-groups");
    }
  });
  document.getElementById("clear-search").addEventListener("click", () => {
    clearTimeout(debounce);
    navigate("vertex-groups");
    search.focus();
  });
  document.getElementById("language-button").addEventListener("click", () => {
    clearTimeout(debounce);
    const other = current.locale === "en" ? "zh-CN" : "en";
    location.hash = link(current.key, other) + (current.key === "search" ? "?q=" + encodeURIComponent(current.query) : "");
  });
  document.getElementById("print-button").addEventListener("click", () => window.print());
  document.querySelector(".skip-link").addEventListener("click", event => {
    event.preventDefault();
    reader.focus();
    reader.scrollIntoView();
  });
  window.addEventListener("hashchange", () => render());
  document.body.classList.add("enhanced");
  render(false);
})();
