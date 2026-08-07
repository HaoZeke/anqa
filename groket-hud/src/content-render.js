/**
 * Detect and render operator-facing body text (turn prompts, timeline).
 * Pure client-side; no extra packages. Always escapes untrusted text.
 */

/**
 * @param {unknown} s
 * @returns {string}
 */
export function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/**
 * @param {string} text
 * @returns {"json"|"markdown"|"code"|"pathlist"|"plain"}
 */
export function detectContentKind(text) {
  const t = String(text ?? "").trim();
  if (!t) return "plain";
  if (looksLikeJson(t)) return "json";
  // Path listings before markdown — many list_dir dumps match bullet heuristics
  // and would otherwise become oversized markdown list "cards".
  if (looksLikePathListing(t)) return "pathlist";
  if (looksLikeMarkdown(t)) return "markdown";
  if (looksLikeCode(t)) return "code";
  return "plain";
}

/**
 * Dense directory / file listings (list_dir, trees, path dumps).
 * @param {string} t
 */
function looksLikePathListing(t) {
  const lines = String(t)
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((l) => l.replace(/\s+$/, ""))
    .filter((l) => l.trim().length > 0);
  if (lines.length < 4) return false;
  let pathish = 0;
  for (const raw of lines) {
    const item = raw
      .trim()
      .replace(/^\s*[-*+]\s+/, "")
      .replace(/^\s*\d+\.\s+/, "")
      .replace(/^["']|["']$/g, "");
    if (!item || item.length > 240) continue;
    // Paths, dotted files, dirs, simple identifiers from list_dir.
    if (
      item.includes("/") ||
      item.endsWith("/") ||
      item.startsWith(".") ||
      item.startsWith("~") ||
      /\.\w{1,12}$/.test(item) ||
      /^[\w.@+${}[\]()-]+$/.test(item)
    ) {
      pathish += 1;
    }
  }
  return pathish >= Math.max(4, Math.ceil(lines.length * 0.65));
}

/**
 * Compact mono path column (not markdown list cards).
 * @param {string} text
 * @returns {string}
 */
export function renderPathListHtml(text) {
  const lines = String(text ?? "")
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((l) => l.replace(/\s+$/, ""))
    .filter((l) => l.trim().length > 0);
  const items = lines.map((raw) => {
    const item = raw
      .trim()
      .replace(/^\s*[-*+]\s+/, "")
      .replace(/^\s*\d+\.\s+/, "");
    const dir = item.endsWith("/") || raw.trim().endsWith("/");
    const label = item.replace(/\/$/, "");
    const cls = dir ? "is-dir" : "";
    return `<li class="${cls}" title="${escapeHtml(label)}">${escapeHtml(label)}</li>`;
  });
  const meta =
    lines.length >= 8
      ? `<div class="path-meta">${lines.length} entries</div>`
      : "";
  return `<div class="content-block content-pathlist" data-kind="pathlist">${meta}<ul class="path-list">${items.join("")}</ul></div>`;
}

/**
 * @param {string} t
 */
function looksLikeJson(t) {
  if (!(t.startsWith("{") || t.startsWith("["))) return false;
  try {
    JSON.parse(t);
    return true;
  } catch {
    return false;
  }
}

/**
 * Prefer markdown only when structure is clear — avoid mangling plain prose.
 * @param {string} t
 */
function looksLikeMarkdown(t) {
  if (/^#{1,6}\s+\S/m.test(t)) return true;
  if (/```/.test(t)) return true;
  const listHits = (t.match(/^\s*[-*+]\s+\S/gm) || []).length;
  // One bullet is enough when the body is multi-line (agent plans / steps).
  if (listHits >= 2) return true;
  if (listHits >= 1 && t.includes("\n")) return true;
  const olHits = (t.match(/^\s*\d+\.\s+\S/gm) || []).length;
  if (olHits >= 2) return true;
  if (olHits >= 1 && t.includes("\n") && t.split("\n").length >= 3) return true;
  // Task list checkboxes
  if ((t.match(/^\s*[-*+]\s+\[[ xX]\]\s+\S/gm) || []).length >= 1) return true;
  if ((t.match(/\*\*[^*\n]+\*\*/g) || []).length >= 2) return true;
  if (/\[[^\]]+\]\(https?:[^)\s]+\)/.test(t)) return true;
  if ((t.match(/^>\s+\S/gm) || []).length >= 2) return true;
  if (/^\|(.+\|)+$/m.test(t) && t.includes("\n|")) return true;
  return false;
}

/**
 * @param {string} t
 */
function looksLikeCode(t) {
  if (t.split("\n").length < 3) return false;
  const codey =
    /^(import |from |def |class |function |const |let |var |package |fn |pub )/m.test(t) ||
    /^#!\//m.test(t) ||
    /[{};]\s*$/m.test(t);
  return codey && !looksLikeMarkdown(t);
}

/**
 * Pretty JSON with light token spans (escaped).
 * @param {string} text
 * @returns {string}
 */
export function renderJsonHtml(text) {
  let pretty;
  try {
    pretty = JSON.stringify(JSON.parse(text.trim()), null, 2);
  } catch {
    return `<pre class="content-block content-code"><code>${escapeHtml(text)}</code></pre>`;
  }
  const colored = pretty.replace(
    /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g,
    (match, str, isKey, lit) => {
      if (str != null) {
        if (isKey) {
          return `<span class="j-key">${escapeHtml(str)}</span>${escapeHtml(isKey)}`;
        }
        return `<span class="j-str">${escapeHtml(str)}</span>`;
      }
      if (lit != null) return `<span class="j-lit">${escapeHtml(lit)}</span>`;
      return `<span class="j-num">${escapeHtml(match)}</span>`;
    },
  );
  return `<pre class="content-block content-json" data-kind="json"><code>${colored}</code></pre>`;
}

/**
 * Minimal safe markdown → HTML (headings, fences, lists, quotes, hr, tables, inline).
 * @param {string} text
 * @returns {string}
 */
export function renderMarkdownHtml(text) {
  const src = String(text ?? "").replace(/\r\n/g, "\n");
  /** @type {{ type: string, text?: string, lang?: string, body?: string }[]} */
  const parts = [];
  // Fence: ```lang? optional newline, body, closing ```
  const fenceRe = /```([a-zA-Z0-9_+-]*)[ \t]*\n?([\s\S]*?)```/g;
  let m;
  let last = 0;
  while ((m = fenceRe.exec(src)) !== null) {
    if (m.index > last) {
      parts.push({ type: "md", text: src.slice(last, m.index) });
    }
    const body = (m[2] || "").replace(/\n$/, "");
    parts.push({ type: "fence", lang: m[1] || "", body });
    last = m.index + m[0].length;
  }
  if (last < src.length) parts.push({ type: "md", text: src.slice(last) });

  const out = [];
  for (const p of parts) {
    if (p.type === "fence") {
      const lang = (p.lang || "").toLowerCase();
      if (lang === "json" && looksLikeJson(p.body || "")) {
        out.push(renderJsonHtml(p.body || ""));
      } else {
        const langAttr = p.lang ? ` data-lang="${escapeHtml(p.lang)}"` : "";
        out.push(
          `<pre class="content-block content-code md-fence"${langAttr}><code>${escapeHtml(p.body || "")}</code></pre>`,
        );
      }
      continue;
    }
    out.push(renderMarkdownBlocks(p.text || ""));
  }
  return `<div class="content-block content-md" data-kind="markdown">${out.join("")}</div>`;
}

/**
 * @param {string} text
 */
function renderMarkdownBlocks(text) {
  const lines = text.replace(/\n{3,}/g, "\n\n").split("\n");
  const html = [];
  let para = [];
  /** @type {"ul"|"ol"|null} */
  let listType = null;
  /** @type {string[]} */
  let listItems = [];
  /** @type {string[][]} */
  let tableRows = null;

  const flushPara = () => {
    if (!para.length) return;
    const body = para.join("\n").trim();
    if (body) html.push(`<p>${inlineMd(body)}</p>`);
    para = [];
  };
  const flushList = () => {
    if (!listType || !listItems.length) {
      listType = null;
      listItems = [];
      return;
    }
    const tag = listType;
    const cls = listType === "ul" ? ' class="md-ul"' : ' class="md-ol"';
    html.push(
      `<${tag}${cls}>${listItems
        .map((li) => {
          // Task list: - [ ] / - [x]
          const task = /^\[([ xX])\]\s+([\s\S]+)$/.exec(li);
          if (task) {
            const done = task[1].toLowerCase() === "x";
            return `<li class="md-task${done ? " is-done" : ""}"><span class="md-check" aria-hidden="true">${done ? "✓" : ""}</span><span class="md-task-body">${inlineMd(task[2])}</span></li>`;
          }
          return `<li>${inlineMd(li)}</li>`;
        })
        .join("")}</${tag}>`,
    );
    listType = null;
    listItems = [];
  };
  const flushTable = () => {
    if (!tableRows || !tableRows.length) {
      tableRows = null;
      return;
    }
    const [header, ...rest] = tableRows;
    const bodyRows = rest.filter((r) => !r.every((c) => /^:?-+:?$/.test(c.trim())));
    const th = header.map((c) => `<th>${inlineMd(c.trim())}</th>`).join("");
    const trs = bodyRows
      .map((r) => `<tr>${r.map((c) => `<td>${inlineMd(c.trim())}</td>`).join("")}</tr>`)
      .join("");
    html.push(`<div class="md-table-wrap"><table class="md-table"><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table></div>`);
    tableRows = null;
  };

  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+$/, "");

    // Table row
    if (/^\s*\|.+\|\s*$/.test(line) || (/^\s*\|/.test(line) && line.includes("|"))) {
      flushList();
      flushPara();
      const cells = line
        .replace(/^\s*\|/, "")
        .replace(/\|\s*$/, "")
        .split("|")
        .map((c) => c.trim());
      if (!tableRows) tableRows = [];
      tableRows.push(cells);
      continue;
    }
    if (tableRows) flushTable();

    // Horizontal rule
    if (/^(\*\s*){3,}$|^(-\s*){3,}$|^(_\s*){3,}$/.test(line.trim())) {
      flushList();
      flushPara();
      html.push(`<hr class="md-hr" />`);
      continue;
    }

    const h = /^(#{1,4})\s+(.+)$/.exec(line);
    if (h) {
      flushList();
      flushPara();
      const level = Math.min(h[1].length + 2, 6); // h3–h6 in dense UI
      html.push(`<h${level} class="md-h">${inlineMd(h[2].trim())}</h${level}>`);
      continue;
    }

    const bq = /^>\s?(.*)$/.exec(line);
    if (bq) {
      flushList();
      flushPara();
      html.push(`<blockquote class="md-quote">${inlineMd(bq[1])}</blockquote>`);
      continue;
    }

    const ul = /^\s*[-*+]\s+(.+)$/.exec(line);
    if (ul) {
      flushPara();
      if (listType && listType !== "ul") flushList();
      listType = "ul";
      listItems.push(ul[1]);
      continue;
    }

    const ol = /^\s*\d+\.\s+(.+)$/.exec(line);
    if (ol) {
      flushPara();
      if (listType && listType !== "ol") flushList();
      listType = "ol";
      listItems.push(ol[1]);
      continue;
    }

    if (/^\s*$/.test(line)) {
      flushList();
      flushPara();
      continue;
    }

    if (listType) flushList();
    para.push(line);
  }
  flushTable();
  flushList();
  flushPara();
  return html.join("");
}

/**
 * @param {string} text
 */
function inlineMd(text) {
  /** @type {string[]} */
  const slots = [];
  const hold = (html) => {
    const id = `\u0000${slots.length}\u0000`;
    slots.push(html);
    return id;
  };
  let work = String(text ?? "");
  // Inline code first
  work = work.replace(/`([^`\n]+)`/g, (_, inner) =>
    hold(`<code class="md-code">${escapeHtml(inner)}</code>`),
  );
  work = work.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, (_, label, url) =>
    hold(
      `<a class="md-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`,
    ),
  );
  // Bold before italic
  work = work.replace(/\*\*([^*\n]+)\*\*/g, (_, inner) =>
    hold(`<strong>${escapeHtml(inner)}</strong>`),
  );
  work = work.replace(/__([^_\n]+)__/g, (_, inner) =>
    hold(`<strong>${escapeHtml(inner)}</strong>`),
  );
  // Single-asterisk italic (avoid ** leftovers and lone *)
  work = work.replace(/(^|[^*\w])\*([^*\n]+?)\*(?!\*)/g, (_, pre, inner) =>
    `${pre}${hold(`<em>${escapeHtml(inner)}</em>`)}`,
  );
  let out = escapeHtml(work);
  out = out.replace(/\u0000(\d+)\u0000/g, (_, n) => slots[Number(n)] || "");
  // Soft line breaks inside a paragraph
  out = out.replace(/\n/g, "<br>\n");
  return out;
}

/**
 * @param {string} text
 * @returns {string}
 */
export function renderCodeHtml(text) {
  return `<pre class="content-block content-code" data-kind="code"><code>${escapeHtml(text)}</code></pre>`;
}

/**
 * @param {string} text
 * @returns {string}
 */
export function renderPlainHtml(text) {
  return `<div class="content-block content-plain" data-kind="plain">${escapeHtml(text)}</div>`;
}

/**
 * Render body text with kind detection.
 * @param {unknown} text
 * @param {{ kind?: string, className?: string, maxLen?: number, showKind?: boolean }} [opts]
 * @returns {string}
 */
export function renderBodyHtml(text, opts = {}) {
  let raw = String(text ?? "");
  if (!raw.trim()) {
    return `<div class="content-block content-plain muted">—</div>`;
  }
  const maxLen = opts.maxLen;
  let truncated = false;
  if (typeof maxLen === "number" && maxLen > 0 && raw.length > maxLen) {
    raw = raw.slice(0, maxLen);
    truncated = true;
  }
  const kind = opts.kind || detectContentKind(raw);
  let html;
  if (kind === "json") html = renderJsonHtml(raw);
  else if (kind === "pathlist") html = renderPathListHtml(raw);
  else if (kind === "markdown") html = renderMarkdownHtml(raw);
  else if (kind === "code") html = renderCodeHtml(raw);
  else html = renderPlainHtml(raw);

  // Kind badge only for structured types (json/code); hide on plain/md/pathlist.
  const showKind =
    opts.showKind === true ||
    (opts.showKind !== false && (kind === "json" || kind === "code"));
  const badge = showKind
    ? `<span class="content-kind" title="detected content type">${escapeHtml(kind)}</span>`
    : "";
  const more = truncated ? `<div class="content-more muted">…truncated</div>` : "";
  const cls = opts.className ? ` ${opts.className}` : "";
  const kindCls = showKind ? " has-kind" : " no-kind";
  return `<div class="content-wrap${cls}${kindCls}">${badge}${html}${more}</div>`;
}

/**
 * Pretty tool rawInput as JSON when object-like.
 * @param {unknown} raw
 * @returns {string}
 */
export function renderRawInputHtml(raw) {
  if (raw == null) return "";
  if (typeof raw === "string") {
    const t = raw.trim();
    if (!t) return "";
    return renderBodyHtml(t, { className: "content-tool-raw", showKind: false });
  }
  if (typeof raw === "object") {
    try {
      const pretty = JSON.stringify(raw, null, 2);
      return renderBodyHtml(pretty, {
        kind: "json",
        className: "content-tool-raw",
        showKind: true,
      });
    } catch {
      return "";
    }
  }
  return "";
}
