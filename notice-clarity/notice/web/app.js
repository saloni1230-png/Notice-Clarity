const $ = (selector) => document.querySelector(selector);
const escape = (value = "") =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const state = {
  me: null,
  org: null,
  view: "home",
  cases: [],
  current: null,
  tab: "facts",
  page: 1,
  quote: "",
  timer: null,
  image: null,
  busy: false,
};
const labels = {
  income_tax: "Income tax",
  gst: "GST",
  bank: "Bank",
  court: "Court",
  unknown: "Category not confirmed",
};
const statuses = {
  queued: "Queued",
  extracting: "Processing",
  review_required: "Review required",
  ready: "Processed",
};
const kinds = {
  issuer: "Issuer",
  reference: "Reference number",
  notice_date: "Notice date",
  deadline: "Date stated in the notice",
  amount: "Stated amount",
  section: "Section or rule",
  recipient: "Recipient",
  request: "Information requested in the notice",
  other: "Other facts",
};
const flags = {
  low_ocr_quality: "Some text could not be read clearly",
  ai_not_configured_extracts_only:
    "AI service is not configured; limited excerpts are available",
  ai_unavailable_extracts_only:
    "AI service or budget is unavailable; view excerpts",
  ambiguous_deadline: "The date format is ambiguous",
  conflicting_deadlines: "Multiple deadlines need confirmation",
  deadline_not_found: "No reply deadline was found",
  unsupported_extraction_rejected: "An unsupported fact was withheld",
  extraction_uncertain: "Extracted facts need confirmation",
  processing_failed: "Some pages could not be processed",
  legal_applicability_requires_review:
    "Legal applicability requires professional review",
  question_requires_review: "This question requires professional review",
  page_text_limit_reached: "This page exceeds the text limit",
  semantic_search_unavailable:
    "Semantic search unavailable; lexical search is available",
};
const paths = {
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  file: "M6 2h8l5 5v15H6z M14 2v6h5 M9 12h7 M9 16h7",
  book: "M12 5C8 2 4 3 2 4v16c4-2 7-1 10 1 3-2 6-3 10-1V4c-3-1-6-2-10 1z M12 5v16",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M9 3l1-2h4l1 2 3 2 2 1 2 4-1 2v3l1 2-2 4-3 1-2 2h-4l-2-2-3-1-2-4 1-2v-3l-1-2 2-4 2-1z",
  upload: "M12 16V3 M7 8l5-5 5 5 M3 15v6h18v-6",
  check: "M4 12l5 5L20 6",
  shield: "M12 2l9 4v6c0 5-9 10-9 10S3 17 3 12V6z M8 12l3 3 5-6",
  arrow: "M5 12h14 M14 7l5 5-5 5",
  clock: "M12 8v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
};
const icon = (name) =>
  `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.file}"/></svg>`;
const dateText = (value) =>
  value
    ? new Intl.DateTimeFormat("en-IN", {
        day: "numeric",
        month: "short",
        year: "numeric",
      }).format(new Date(value))
    : "—";
function toast(message, error = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = error ? "error" : "";
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 6500);
}
async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.me?.csrf) headers.set("X-CSRF-Token", state.me.csrf);
  if (state.org) headers.set("X-Org-ID", state.org);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    let detail;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = "Could not connect to the server.";
    }
    if (response.status === 401 && state.me) {
      state.me = null;
      state.org = null;
      render();
    }
    throw new Error(
      typeof detail === "string"
        ? detail
        : "Check the information and try again.",
    );
  }
  return options.blob ? response.blob() : response.json();
}
function closeModal() {
  $("#modal").close();
  $("#modal").replaceChildren();
}
function modal(title, body) {
  const el = $("#modal");
  el.innerHTML = `<div class="dialog-top"><h2 id="modal-title">${escape(title)}</h2><button class="close" data-action="close" aria-label="Close">×</button></div>${body}`;
  if (!el.open) el.showModal();
}
function shell(content) {
  const email = state.me?.email || "Your private workspace";
  return `<aside class="sidebar"><a class="brand" href="/"><img src="/assets/mark.svg" alt=""><div><strong>Notice Clarity</strong><small>YOUR NOTICE WORKSPACE</small></div></a><div class="side-label">Your workspace</div><nav class="nav" aria-label="Main navigation">${[
    ["home", "grid", "My notices"],
    ["sources", "book", "Official sources"],
    ["settings", "settings", "Account and team"],
  ]
    .map(
      ([view, i, label]) =>
        `<button data-action="nav" data-view="${view}" class="${state.view === view ? "active" : ""}">${icon(i)}${label}</button>`,
    )
    .join(
      "",
    )}</nav><div class="side-bottom"><div class="side-note">${icon("shield")}<strong>Understand. Then take the next step.</strong>Inspect the evidence behind each fact. Have a professional review any legal response.</div><div class="account"><div class="avatar">${escape(state.me?.email?.[0]?.toUpperCase() || "N")}</div><span title="${escape(email)}">${escape(email)}</span></div></div></aside><div class="shell"><header class="topbar"><div><span>Clarity for your next step.</span><div class="mobile-brand"><img src="/assets/mark.svg" alt="">Notice Clarity</div><small> &nbsp; / &nbsp; ${state.view === "home" ? "My notices" : state.view === "sources" ? "Official sources" : state.view === "case" ? "Case workspace" : "Account and team"}</small></div><div class="top-actions"><span class="pill">English workspace</span>${state.me?.orgs?.length > 1 ? `<select class="select-org" id="org-select" aria-label="Choose workspace">${state.me.orgs.map((o) => `<option value="${o.id}" ${o.id === state.org ? "selected" : ""}>${escape(o.name)} · ${o.role}</option>`).join("")}</select>` : ""}<button class="text-btn" data-action="help">Help</button><button class="button small ${state.me ? "secondary" : ""}" data-action="${state.me ? "logout" : "login"}">${state.me ? "Sign out" : "Sign in"}</button></div></header><main class="main" id="main">${state.me && !state.me.verified ? '<div class="notice-banner">Open the verification link in your email. <button class="text-btn" data-action="resend">Resend link</button></div>' : ""}${content}</main></div>`;
}
function home() {
  const total = state.cases.length,
    processing = state.cases.filter((c) =>
      ["queued", "extracting"].includes(c.status),
    ).length;
  const reviews = state.cases.filter(
    (c) => c.status === "review_required",
  ).length;
  return `<div class="eyebrow">A LITTLE CLARITY. A CONFIDENT NEXT STEP.</div><h1>A notice arrived? Let's make sense of it.</h1><p class="sub">Income tax, GST, banking or court notices, with clear language, document evidence, and organized next steps.</p><section class="hero" aria-label="Upload notice"><div class="intro-card"><div class="eyebrow">Your notice. Your control.</div><h2>From complex wording<br>to a clear next step.</h2><p>What it says, what is missing, and where professional review is needed, all in one place.</p><button class="small-link" data-action="sample">See an example &nbsp; ↗</button></div><div class="upload-box" id="dropzone"><div><div class="upload-icon">${icon("upload")}</div><h3>Add your notice here</h3><p>Drag a file here or choose one from your device</p><button class="button" data-action="pick">${icon("upload")} Upload notice</button><input type="file" id="file-input" accept="application/pdf,image/png,image/jpeg" hidden aria-label="Choose a notice file"><small>PDF, JPG, PNG · Maximum 20 MB / 80 pages</small><small>Your file stays in your workspace. If AI is enabled, selected text is sent to the configured provider.</small></div></div></section><section class="metrics" aria-label="Workspace status">${[
    [total, "Notices on this page", "file"],
    [processing, "Processing", "clock"],
    [reviews, "Need review", "shield"],
  ]
    .map(
      ([n, t, i]) =>
        `<div class="metric"><div class="metric-icon">${icon(i)}</div><div><strong>${n.toString().padStart(2, "0")}</strong><span>${t}</span></div></div>`,
    )
    .join(
      "",
    )}</section><div class="section-head"><div><h2>Recent notices</h2><p class="sub">Open a case to see its facts, evidence, and tasks.</p></div><button class="text-btn" data-action="refresh">Refresh ↻</button></div><section class="card case-list">${state.cases.length ? state.cases.map((c) => `<button class="case-row" data-action="case" data-id="${c.id}"><span class="file-icon">${icon("file")}</span><span><span class="case-title">${escape(c.title)}</span><span class="case-meta">${escape(labels[c.category])} &nbsp;·&nbsp; ${c.pages} pages</span></span><span class="case-date">${dateText(c.created)}</span><span class="pill ${c.status === "review_required" ? "warn" : ""}">${escape(statuses[c.status] || c.status)}</span><span>›</span></button>`).join("") : '<div class="empty">Your notices will appear here. Add your first notice to get started.</div>'}</section><div class="how">${[
    [
      "01",
      "Add a notice",
      "Direct text extraction for digital PDFs; OCR for scanned pages.",
    ],
    [
      "02",
      "Understand with evidence",
      "Select a fact and inspect the text on the original page.",
    ],
    [
      "03",
      "Organize your next steps",
      "Add context, organize tasks, and arrange professional review.",
    ],
  ]
    .map(
      ([n, t, d]) =>
        `<div class="step"><b>${n}</b><div><strong>${t}</strong>${d}</div></div>`,
    )
    .join(
      "",
    )}</div><footer class="footer"><span>Notice Clarity is an independent service, unaffiliated with any government department.</span><button class="text-btn" data-action="privacy">Privacy and data</button></footer>`;
}
function answerLabel(answer) {
  if (answer.mode === "verified_ai") return "AI explanation · evidence and support checked";
  if (answer.answer_type === "document_facts") return "Document facts · original evidence linked";
  return "No supported answer found";
}
function engineNote(answer) {
  if (answer.mode === "verified_ai") return "Document-based AI explanation; professional review is still required.";
  if (answer.fallback_reason === "not_configured") return "AI is not configured on this server. Verified document facts are available below.";
  return "The model explanation was unavailable or failed evidence checks. Verified document facts are shown below.";
}
function explanationBlock(c) {
  const summary = c.explanation;
  if (!summary) return "";
  if (summary.status === "queued")
    return '<div class="notice-banner green loading">Preparing an evidence-based English summary...</div>';
  const answer = summary.answer;
  const missing = (answer.missing_context || [])
    .map((text) => `<p class="notice-banner">${escape(text)}</p>`)
    .join("");
  const sourceClaims = (answer.claims || [])
    .filter((claim) => claim.basis === "official_source")
    .map(
      (claim) =>
        `<div class="claim"><span class="tag">Official-source statement · applicability requires review</span><p>${escape(claim.text)}</p><blockquote>${escape(claim.source_quote)}</blockquote><a href="${escape(answer.sources?.[claim.source_id]?.url || "#")}" target="_blank" rel="noopener noreferrer">Official sources ↗</a></div>`,
    )
    .join("");
  return `${missing}${sourceClaims}<h3>Document summary</h3><p class="sub">${engineNote(answer)}</p>${summary.stale ? '<p class="notice-banner">This summary used earlier context or source approvals. Reanalyze the notice to refresh its summary.</p>' : ""}${(answer.claims || []).map((claim, index) => (claim.basis === "official_source" ? "" : `<div class="claim">${escape(claim.text)}${claim.evidence ? `<div><button class="evidence" data-action="cite-summary" data-index="${index}">Page ${claim.evidence.page} evidence ↗</button></div>` : ""}</div>`)).join("")}<p class="case-meta">Pages supporting this summary: ${(answer.coverage?.pages || []).join(", ") || "None"} / total ${answer.coverage?.total || c.page_count}. ${answer.coverage?.facts_considered || 0} extracted facts considered. Inspect the original evidence before responding.</p><hr>`;
}
function factsTab(c) {
  const prefix = explanationBlock(c);
  return `${prefix}<h3>What the document says</h3><p class="sub">These are statements in the notice, not conclusions about their legal validity.</p><div class="facts">${c.facts.length ? c.facts.map((f, index) => `<article class="fact"><div class="fact-label"><span>${escape(kinds[f.kind] || f.kind)}</span><span>Page ${f.evidence.page}</span></div><strong class="fact-value">${escape(f.value)}</strong><button class="evidence" data-action="cite-fact" data-index="${index}">View page evidence ↗</button><div><small>${f.confidence < 0.8 ? "Extracted text: verify before relying on it" : "quote verified; meaning still needs review"}${f.date_iso ? " · " + escape(f.date_iso) : ""}</small></div></article>`).join("") : '<div class="empty">No verified facts yet. Read the page text or wait for processing to finish.</div>'}</div>`;
}
function contextTab(c) {
  return `<h3>Add some context</h3><p class="sub">A notice does not contain all the context. Your information is kept separate from document facts.</p><form id="context-form" class="form-grid"><label>When did you receive the notice?<input type="date" name="received_on" value="${escape(c.context.received_on || "")}" max="${new Date().toISOString().slice(0, 10)}"></label><label>State / relevant office<input name="jurisdiction" maxlength="100" value="${escape(c.context.jurisdiction)}" placeholder="Example: Delhi, income tax office"></label><label>Have you already replied?<select name="already_replied"><option value="">Not sure / prefer not to say</option><option value="true" ${c.context.already_replied === true ? "selected" : ""}>Yes</option><option value="false" ${c.context.already_replied === false ? "selected" : ""}>No</option></select></label><label>What other context should we know?<textarea name="notes" maxlength="3000" placeholder="A previous reply, relevant period, or your concerns...">${escape(c.context.notes)}</textarea></label><button class="button">Save context</button></form>`;
}
function actionsTab(c) {
  return `<h3>Your next steps</h3><p class="sub">Check off completed tasks. Ambiguous dates are never scheduled automatically.</p>${c.actions.map((a) => `<label class="check-row ${a.done ? "done" : ""}"><input type="checkbox" data-action-id="${a.id}" ${a.done ? "checked" : ""}><span>${escape(a.title)}</span></label>`).join("")}<form id="action-form" class="inline-form"><input name="title" required minlength="3" maxlength="300" placeholder="Add a task" aria-label="New task"><button class="button small">Add +</button></form>`;
}
function qaTab(c) {
  return `<h3>Ask about this notice</h3><p class="sub">Answers are limited to available pages and approved sources. Missing evidence is stated explicitly.</p><form id="question-form" class="question-input"><label class="hidden" for="question">Your question</label><textarea id="question" name="question" required minlength="3" maxlength="1200" placeholder="What does this notice ask me to provide?"></textarea><button class="button">Ask ↗</button></form><div class="suggestions">${["Where is the reply deadline stated?", "What amount is stated?", "Which documents are requested?"].map((q) => `<button data-action="suggest" data-question="${escape(q)}">${q}</button>`).join("")}</div>${c.questions.map((q) => `<article class="question-card"><h3>${escape(q.question)}</h3>${q.stale ? '<p class="notice-banner">This answer was generated using earlier context or source approvals.</p>' : ""}${q.status === "queued" ? '<p class="sub loading">Finding supporting evidence...</p>' : q.status === "failed" ? '<p class="notice-banner">Processing failed. Submit a new question or ask a professional to review the notice.</p>' : `<span class="tag">${answerLabel(q.answer)}</span>${(q.answer.claims || []).map((claim, index) => `<div class="claim">${escape(claim.text)}${claim.basis === "document" ? `<div><button class="evidence" data-action="cite-answer" data-qid="${q.id}" data-index="${index}">Page ${claim.evidence.page} evidence ↗</button></div>` : `<blockquote>${escape(claim.source_quote)}</blockquote><a href="${escape(q.answer.sources?.[claim.source_id]?.url || "#")}" target="_blank" rel="noopener noreferrer">Official sources ↗</a>`}</div>`).join("")}${(q.answer.missing_context || []).map((m) => `<p class="notice-banner">${escape(m)}</p>`).join("")}${q.answer.professional_review ? '<p class="case-meta">This question has been flagged for professional review.</p>' : ""}`}</article>`).join("")}`;
}
function reviewTab(c) {
  const role = state.me.orgs.find((o) => o.id === state.org)?.role;
  return `<h3>Professional review</h3><p class="sub">Only a workspace reviewer can record a review. Add a qualified accountant or lawyer after your organization verifies their credentials.</p><span class="pill ${c.review?.status !== "resolved" ? "warn" : ""}">${c.review?.status === "resolved" ? "Review recorded" : "Awaiting review"}</span><div class="notice-banner">${(c.review?.reasons || []).map((r) => escape(flags[r] || r)).join(" · ")}</div>${c.review?.note ? `<p class="review-note">${escape(c.review.note)}</p>` : ""}${role === "reviewer" ? `<form id="review-form" class="form-grid"><label>Review notes<textarea name="note" required minlength="10" maxlength="5000" placeholder="Explain the evidence, dates, applicable rules, and next steps."></textarea></label><label>Status<select name="status"><option value="resolved">Record review and close</option><option value="open">Review is still pending</option></select></label><button class="button">Record review</button></form>` : '<button class="button secondary" data-action="nav" data-view="settings">Manage team</button>'}`;
}
function caseView() {
  const c = state.current;
  if (!c) return '<div class="empty loading">Opening case...</div>';
  const progress = c.pages.filter((p) =>
    ["done", "failed"].includes(p.status),
  ).length;
  return `<button class="back" data-action="nav" data-view="home">← All notices</button><div class="case-heading"><div><div class="eyebrow">YOUR CASE WORKSPACE</div><h1>${escape(c.title)}</h1><span class="pill ${c.status === "review_required" ? "warn" : ""}">${escape(statuses[c.status] || c.status)}</span> <span class="case-meta">${escape(labels[c.category])} · ${c.page_count} pages · ${dateText(c.created)}</span></div><div class="case-controls"><button class="button secondary small" data-action="export">Export ↓</button>${["owner", "admin"].includes(state.me.orgs.find(o => o.id === state.org)?.role) ? '<button class="button secondary small" data-action="reprocess">Reanalyze</button>' : ""}<button class="text-btn" data-action="delete">Delete</button></div></div>${["queued", "extracting"].includes(c.status) ? `<div class="notice-banner green loading">Processing pages · ${progress} / ${c.page_count} complete<div class="progress"><span id="progress"></span></div></div>` : ""}${c.flags.length ? `<div class="notice-banner">${c.flags.map((f) => escape(flags[f] || f)).join(" · ")}</div>` : ""}<div class="workspace"><section class="card viewer" aria-label="Original notice"><div class="viewer-tools"><strong>Original document</strong><div><button data-action="prev-page" aria-label="Previous page">‹</button> &nbsp; <span id="page-label">${state.page} / ${c.page_count}</span> &nbsp; <button data-action="next-page" aria-label="Next page">›</button></div></div><div class="page-image-wrap" tabindex="0" aria-label="Document page image"><div class="page-image"><img id="page-image" alt="Notice page ${state.page}"><div id="overlay" class="overlay"></div></div></div><div class="page-caption" id="page-caption">Loading page...</div><details><summary class="page-caption">View extracted text</summary><pre class="ocr-text" id="page-text" tabindex="0" aria-label="Full extracted text"></pre></details></section><section class="card"><nav class="tabs" aria-label="Case sections">${[
    ["facts", "Facts"],
    ["context", "Context"],
    ["actions", "Next steps"],
    ["qa", "Questions"],
    ["review", "Review"],
  ]
    .map(
      ([id, title]) =>
        `<button data-action="tab" data-tab="${id}" class="${state.tab === id ? "active" : ""}" aria-pressed="${state.tab === id}">${title}</button>`,
    )
    .join(
      "",
    )}</nav><div id="tab-content">${{ facts: factsTab, context: contextTab, actions: actionsTab, qa: qaTab, review: reviewTab }[state.tab](c)}</div></section></div><footer class="footer"><span>Scheduled deletion: ${dateText(c.expires)} · version ${escape(c.pipeline_version)}</span><span>AI budget reserved today: $${c.budget.reserved_usd.toFixed(3)} / $${c.budget.daily_usd.toFixed(2)}</span></footer>`;
}
function render() {
  $("#app").innerHTML = shell(
    state.view === "home"
      ? home()
      : state.view === "case"
        ? caseView()
        : '<div class="empty loading">Loading...</div>',
  );
  if (state.view === "case" && state.current) {
    const el = $("#progress");
    if (el)
      el.style.width = `${(state.current.pages.filter((p) => ["done", "failed"].includes(p.status)).length / state.current.page_count) * 100}%`;
    showPage().catch((error) => toast(error.message, true));
  }
}
async function showPage() {
  if (!state.current) return;
  const id = state.current.id,
    number = state.page;
  const [data, blob] = await Promise.all([
    api(
      `/api/cases/${id}/pages/${number}?quote=${encodeURIComponent(state.quote)}`,
    ).catch(() => ({
      text: "This page is still being processed.",
      quality: 0,
      method: "pending",
      boxes: [],
    })),
    api(`/api/cases/${id}/pages/${number}/image`, { blob: true }),
  ]);
  if (state.current?.id !== id || state.page !== number || !$("#page-image"))
    return;
  if (state.image) URL.revokeObjectURL(state.image);
  state.image = URL.createObjectURL(blob);
  $("#page-image").src = state.image;
  $("#page-image").alt = `Notice page ${number}`;
  $("#page-label").textContent = `${number} / ${state.current.page_count}`;
  $("#page-text").textContent = data.text;
  $("#page-caption").textContent =
    `${data.method === "ocr" ? "OCR extraction" : data.method === "digital" ? "PDF extraction" : "Processing pending"} · quality indicator ${Math.round(data.quality * 100)}%${state.quote ? " · Highlights show approximate word positions only; Read the exact quote below." : ""}`;
  $("#overlay").replaceChildren();
  for (const box of data.boxes) {
    const el = document.createElement("span");
    el.className = "highlight";
    el.style.left = `${box[0] * 100}%`;
    el.style.top = `${box[1] * 100}%`;
    el.style.width = `${(box[2] - box[0]) * 100}%`;
    el.style.height = `${(box[3] - box[1]) * 100}%`;
    $("#overlay").append(el);
  }
  if (state.quote) {
    $("#page-text").textContent =
      `Selected quote:\n${state.quote}\n\nFull text:\n${data.text}`;
    $("#page-text").parentElement.open = true;
  }
}
async function loadHome() {
  state.cases = state.me?.verified ? await api("/api/cases") : [];
  state.view = "home";
  state.current = null;
  render();
  schedule();
}
async function openCase(id) {
  if (!state.me) return authModal("login");
  state.view = "case";
  state.current = await api(`/api/cases/${id}`);
  state.page = 1;
  state.quote = "";
  state.tab = "facts";
  render();
  schedule();
  window.scrollTo(0, 0);
}
function schedule() {
  clearTimeout(state.timer);
  state.timer = setTimeout(async () => {
    try {
      if (state.view === "case" && state.current) {
        const next = await api(`/api/cases/${state.current.id}`);
        if (
          JSON.stringify(next) !== JSON.stringify(state.current) &&
          !["INPUT", "TEXTAREA", "SELECT"].includes(
            document.activeElement?.tagName,
          )
        ) {
          state.current = next;
          render();
        }
      } else if (
        state.view === "home" &&
        state.me?.verified &&
        state.cases.some((c) => ["queued", "extracting"].includes(c.status))
      ) {
        state.cases = await api("/api/cases");
        render();
      }
    } catch (error) {
      toast(error.message, true);
    }
    if (state.view === "case" || state.view === "home") schedule();
  }, 4000);
}
async function uploadFile(file) {
  if (!state.me) return authModal("register");
  if (!state.me.verified) return toast("Verify your email first.", true);
  if (file.size > 20 * 1024 * 1024)
    return toast("Choose a file of 20 MB or less.", true);
  if (state.busy) return;
  state.busy = true;
  toast("Uploading file...");
  try {
    const form = new FormData();
    form.append("file", file);
    const result = await api("/api/cases", { method: "POST", body: form });
    toast(
      result.deduplicated
        ? "Reused this document's existing processing results."
        : "Notice uploaded. Its pages are being processed.",
    );
    await openCase(result.id);
  } finally {
    state.busy = false;
  }
}
function authModal(mode) {
  const titles = {
    login: "Sign in to your workspace",
    register: "Create your workspace",
    recover: "Reset your password",
  };
  modal(
    titles[mode],
    `<div class="auth-panel"><p class="sub">Your notices and tasks in one private workspace.</p><form id="auth-form" class="form-grid" data-mode="${mode}"><label>Email<input name="email" type="email" autocomplete="email" required maxlength="254"></label>${mode !== "recover" ? `<label>Password<input name="password" aria-label="Password" type="password" required minlength="12" maxlength="128" autocomplete="${mode === "register" ? "new-password" : "current-password"}"><span class="case-meta">At least 12 characters</span></label>` : ""}<button class="button">${mode === "register" ? "Create account" : mode === "recover" ? "Send reset link" : "Sign in"}</button></form><div class="auth-links"><button class="text-btn" data-action="${mode === "register" ? "login" : "register"}">${mode === "register" ? "Already registered? Sign in" : "Create a new account"}</button><button class="text-btn" data-action="recover">Forgot password?</button></div><p class="privacy">This service helps you understand notices. Have a qualified professional review any legal response.</p></div>`,
  );
}
async function loadSources() {
  state.view = "sources";
  render();
  let rows = state.me?.verified ? await api("/api/sources") : [];
  $("#main").innerHTML =
    `<div class="eyebrow">EVIDENCE, WITH A VERSION</div><h1>Official sources</h1><p class="sub">Each source change creates a versioned snapshot. Only current, professionally approved snapshots can support official-source statements.</p><section class="card">${rows.length ? rows.map((s) => `<article class="source"><a href="${escape(s.url)}" target="_blank" rel="noopener noreferrer">${escape(s.title)} ↗</a> <span class="pill ${s.status === "approved" ? "" : "warn"}">${s.status === "approved" ? "Approved" : "Review pending"}</span><p>${escape(s.url)}</p><p>Snapshot: ${dateText(s.fetched)} · Effective period: ${escape(s.valid_from || "Unspecified")} to ${escape(s.valid_to || "Unspecified")} · approval: ${dateText(s.approved_until)}</p><p>SHA-256: ${escape(s.digest)}</p></article>`).join("") : '<div class="empty">No sources have been ingested and approved. The app does not use general model knowledge to supply legal interpretations.</div>'}</section><h2 class="section-head">Official portals to check directly</h2><div class="card privacy"><p><a href="https://www.incometax.gov.in/iec/foportal/help/all-topics/e-filing-services/authenticate-noticeorder-issued-itd-UM" target="_blank" rel="noopener noreferrer">Income tax: authenticate a notice or order ↗</a></p><p><a href="https://www.gst.gov.in/" target="_blank" rel="noopener noreferrer">GST Portal ↗</a></p><p><a href="https://services.ecourts.gov.in/ecourtindia_v6/" target="_blank" rel="noopener noreferrer">eCourts: Case status ↗</a></p><p>These links help you check official portals. They are not approved legal knowledge snapshots.</p></div>`;
}
async function loadSettings() {
  if (!state.me) return authModal("login");
  state.view = "settings";
  render();
  const role = state.me.orgs.find((o) => o.id === state.org)?.role;
  const [sessions, members] = await Promise.all([
    api("/api/auth/sessions"),
    ["owner", "admin"].includes(role)
      ? api("/api/org/members")
      : Promise.resolve([]),
  ]);
  $("#main").innerHTML =
    `<div class="eyebrow">ACCOUNT & ACCESS</div><h1>Account and team</h1><p class="sub">${escape(state.me.email)} · Role: ${escape(role)}</p><div class="settings-grid"><section class="card"><h2>Active sessions</h2>${sessions.map((s) => `<div class="row"><span>${dateText(s.created)} ${s.current ? " · This device" : ""}<br><small>Expires: ${dateText(s.expires)}</small></span><button class="text-btn" data-action="revoke" data-id="${s.id}">Remove</button></div>`).join("")}</section><section class="card"><h2>Workspace team</h2>${members.length ? members.map((m) => `<div class="row"><span>${escape(m.email)}<br><small>${escape(m.role)}</small></span>${m.role !== "owner" && role === "owner" ? `<button class="text-btn" data-action="remove-member" data-id="${m.user_id}">Remove</button>` : ""}</div>`).join("") : '<p class="sub">Contact your workspace owner to add team members.</p>'}${role === "owner" ? `<form id="member-form" class="form-grid"><label>Email of an existing verified account<input name="email" type="email" required></label><label>Role<select name="role"><option value="member">Member · can view and update all cases</option><option value="reviewer">Reviewer · can record professional reviews</option><option value="admin">Admin · can delete cases and view the audit log</option></select></label><p class="privacy">This member will have access to every case in this workspace. Verify professional qualifications before assigning the reviewer role.</p><button class="button">Add member</button></form>` : ""}</section><section class="card"><h2>Data and access</h2><p class="sub">Export or delete a case from its workspace. Cases expire automatically according to your organization's retention policy.</p><button class="button secondary" data-action="privacy">Read data policy</button>${["owner", "admin"].includes(role) ? '<button class="text-btn" data-action="audit">Download audit log</button>' : ""}</section></div>`;
}
async function download(path, name) {
  const blob = await api(path, { blob: true });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
document.addEventListener("click", async (event) => {
  const el = event.target.closest("[data-action]");
  if (!el) return;
  const action = el.dataset.action;
  try {
    if (["login", "register", "recover"].includes(action))
      return authModal(action);
    if (action === "close") return closeModal();
    if (action === "nav") {
      clearTimeout(state.timer);
      state.view = el.dataset.view;
      return state.view === "home"
        ? await loadHome()
        : state.view === "sources"
          ? await loadSources()
          : await loadSettings();
    }
    if (action === "pick") {
      if (!state.me) return authModal("register");
      return $("#file-input").click();
    }
    if (action === "refresh") return await loadHome();
    if (action === "case") return await openCase(el.dataset.id);
    if (action === "tab") {
      state.tab = el.dataset.tab;
      render();
      return;
    }
    if (action === "prev-page" || action === "next-page") {
      state.page = Math.max(
        1,
        Math.min(
          state.current.page_count,
          state.page + (action === "prev-page" ? -1 : 1),
        ),
      );
      state.quote = "";
      return await showPage();
    }
    if (action === "cite-summary") {
      const claim =
        state.current.explanation.answer.claims[Number(el.dataset.index)];
      state.page = claim.evidence.page;
      state.quote = claim.evidence.quote;
      return await showPage();
    }
    if (action === "cite-fact") {
      const fact = state.current.facts[Number(el.dataset.index)];
      state.page = fact.evidence.page;
      state.quote = fact.evidence.quote;
      await showPage();
      return $(".viewer").scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
    if (action === "cite-answer") {
      const q = state.current.questions.find((q) => q.id === el.dataset.qid);
      const cite = q.answer.claims[Number(el.dataset.index)].evidence;
      state.page = cite.page;
      state.quote = cite.quote;
      return await showPage();
    }
    if (action === "suggest") {
      $("#question").value = el.dataset.question;
      $("#question").focus();
      return;
    }
    if (action === "logout") {
      await api("/api/auth/logout", { method: "POST" });
      state.me = null;
      state.org = null;
      return await loadHome();
    }
    if (action === "resend") {
      await api("/api/auth/resend", { method: "POST" });
      return toast("A verification link is being emailed.");
    }
    if (action === "sample")
      return modal(
        "An example, not an official notice",
        `<p class="sub">The example PDF contains a clear reply date and amount. Upload it to try the complete evidence and workspace flow.</p><a class="button" href="/assets/sample.pdf" download="example-notice.pdf">Download example PDF ↓</a><p class="privacy">This is a synthetic test document, not an official notice or legal advice.</p>`,
      );
    if (action === "reprocess") {
      await api(`/api/cases/${state.current.id}/reprocess`, { method: "POST" });
      toast("Reanalysis started. Completed model chunks will be reused when possible.");
      return await openCase(state.current.id);
    }
    if (action === "export")
      return await download(
        `/api/cases/${state.current.id}/export`,
        "notice-workspace.json",
      );
    if (action === "delete")
      return modal(
        "Delete this case?",
        `<p>This case will immediately become inaccessible. A background worker deletes the original file and extracted data. Other active cases referencing the same file retain their data.</p><button class="button danger" data-action="confirm-delete">Yes, delete this case</button>`,
      );
    if (action === "confirm-delete") {
      await api(`/api/cases/${state.current.id}`, { method: "DELETE" });
      closeModal();
      toast("Case access has been removed. Data deletion is queued.");
      return await loadHome();
    }
    if (action === "revoke") {
      await api(`/api/auth/sessions/${el.dataset.id}`, { method: "DELETE" });
      try {
        state.me = await api("/api/me");
        await loadSettings();
      } catch {
        state.me = null;
        await loadHome();
      }
      return;
    }
    if (action === "remove-member") {
      await api(`/api/org/members/${el.dataset.id}`, { method: "DELETE" });
      return await loadSettings();
    }
    if (action === "audit")
      return await download("/api/org/audit", "audit-log.json");
    if (action === "privacy")
      return modal(
        "Privacy and data",
        `<div class="privacy"><p>Authorized team members can see every case in your workspace. Other workspaces cannot access them.</p><p>If AI is enabled, selected page text, your question, and supplied context are sent to the configured provider. Ask the deployment operator about provider retention and processing policies.</p><p>The scheduled deletion date appears in each case. Deletion revokes access immediately; a maintenance worker removes the file and extracted data. Backup copies expire according to the operator's published policy.</p><p>The audit log stores user and case identifiers and access events for up to 365 days. It does not contain document text.</p><p>Before deployment, the operator must publish a privacy contact, hosting region, provider terms, backup expiry, and the organization's data policy.</p></div>`,
      );
    if (action === "help")
      return modal(
        "How to use Notice Clarity",
        `<ol class="privacy"><li>Create an account. If this server requires email verification, open the link sent to your email.</li><li>Upload a PDF or a clear photo. Unlock password-protected PDFs before uploading.</li><li>Select extracted facts and inspect their evidence on the original page. OCR indicators do not guarantee accurate extraction.</li><li>Add the receipt date and your context. Ask questions and create a task list.</li><li>Have any legal interpretation or response checked by your accountant or lawyer. Assign a verified professional the reviewer role.</li></ol>`,
      );
  } catch (error) {
    toast(error.message, true);
  }
});
document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!form.id.endsWith("-form")) return;
  event.preventDefault();
  const button = form.querySelector("button[type=submit],button:not([type])");
  if (button) button.disabled = true;
  const data = Object.fromEntries(new FormData(form));
  try {
    if (form.id === "auth-form") {
      const mode = form.dataset.mode;
      const result = await api(`/api/auth/${mode}`, {
        method: "POST",
        body: data,
      });
      if (mode === "recover") {
        closeModal();
        return toast(result.message);
      }
      state.me = await api("/api/me");
      state.org = state.me.orgs[0]?.id;
      closeModal();
      await loadHome();
      if (!state.me.verified)
        toast("Account created. Open the verification link in your email.");
    }
    if (form.id === "context-form") {
      data.received_on = data.received_on || null;
      data.already_replied =
        data.already_replied === "" ? null : data.already_replied === "true";
      await api(`/api/cases/${state.current.id}/context`, {
        method: "PATCH",
        body: data,
      });
      toast("Context saved. The notice summary is being refreshed.");
      state.current = await api(`/api/cases/${state.current.id}`);
      render();
      schedule();
    }
    if (form.id === "action-form") {
      await api(`/api/cases/${state.current.id}/actions`, {
        method: "POST",
        body: data,
      });
      state.current = await api(`/api/cases/${state.current.id}`);
      render();
    }
    if (form.id === "question-form") {
      await api(`/api/cases/${state.current.id}/questions`, {
        method: "POST",
        body: data,
      });
      state.current = await api(`/api/cases/${state.current.id}`);
      render();
      schedule();
    }
    if (form.id === "review-form") {
      await api(`/api/cases/${state.current.id}/review`, {
        method: "POST",
        body: data,
      });
      state.current = await api(`/api/cases/${state.current.id}`);
      render();
      toast("Review recorded.");
    }
    if (form.id === "member-form") {
      await api("/api/org/members", { method: "POST", body: data });
      toast("Team updated.");
      await loadSettings();
    }
    if (form.id === "reset-form") {
      await api("/api/auth/reset", {
        method: "POST",
        body: { token: state.resetToken, password: data.password },
      });
      state.resetToken = null;
      closeModal();
      toast("Password changed. Sign in again.");
      authModal("login");
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (button) button.disabled = false;
  }
});
document.addEventListener("change", async (event) => {
  try {
    if (event.target.id === "file-input" && event.target.files[0])
      await uploadFile(event.target.files[0]);
    if (event.target.id === "org-select") {
      state.org = event.target.value;
      await loadHome();
    }
    if (event.target.dataset.actionId)
      await api(
        `/api/cases/${state.current.id}/actions/${event.target.dataset.actionId}`,
        { method: "PATCH", body: { done: event.target.checked } },
      );
  } catch (error) {
    toast(error.message, true);
  }
});
document.addEventListener("dragover", (event) => {
  if (event.target.closest("#dropzone")) {
    event.preventDefault();
    $("#dropzone").classList.add("drag");
  }
});
document.addEventListener("dragleave", (event) => {
  if (event.target.closest("#dropzone"))
    $("#dropzone").classList.remove("drag");
});
document.addEventListener("drop", async (event) => {
  if (event.target.closest("#dropzone")) {
    event.preventDefault();
    $("#dropzone").classList.remove("drag");
    if (event.dataTransfer.files[0])
      try {
        await uploadFile(event.dataTransfer.files[0]);
      } catch (error) {
        toast(error.message, true);
      }
  }
});
async function init() {
  try {
    state.me = await api("/api/me");
    state.org = state.me.orgs[0]?.id;
  } catch {
    state.me = null;
  }
  await loadHome();
  const params = new URLSearchParams(location.hash.slice(1));
  const verify = params.get("verify"),
    reset = params.get("reset");
  history.replaceState(null, "", location.pathname);
  if (verify) {
    try {
      await api("/api/auth/verify", {
        method: "POST",
        body: { token: verify },
      });
      state.me = await api("/api/me");
      await loadHome();
      toast("Your email has been verified.");
    } catch (error) {
      toast(error.message, true);
    }
  }
  if (reset) {
    state.resetToken = reset;
    modal(
      "Choose a new password",
      `<form id="reset-form" class="form-grid"><label>New password<input name="password" type="password" required minlength="12" maxlength="128" autocomplete="new-password"></label><button class="button">Change password</button></form>`,
    );
  }
}
init().catch((error) => toast(error.message, true));
