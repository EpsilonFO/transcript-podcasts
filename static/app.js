const fileInput = document.getElementById("file-input");
const dropzone = document.getElementById("dropzone");
const filenameLabel = document.getElementById("filename");
const submitBtn = document.getElementById("submit-btn");
const uploadForm = document.getElementById("upload-form");
const uploadStatus = document.getElementById("upload-status");
const urlForm = document.getElementById("url-form");
const urlInput = document.getElementById("url-input");
const urlSubmitBtn = document.getElementById("url-submit-btn");

const uploadView = document.getElementById("upload-view");
const resultView = document.getElementById("result-view");
const summaryEl = document.getElementById("summary-content");
const docTitleEl = document.getElementById("doc-title");
const docMetaEl = document.getElementById("doc-meta");
const deleteBtn = document.getElementById("delete-btn");
const newBtn = document.getElementById("new-btn");
const conversationsEl = document.getElementById("conversations");
const searchInput = document.getElementById("search-input");

const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");

const SOURCE_ICONS = { audio: "🎙", pdf: "📄", text: "📝" };
const SOURCE_LABELS = { audio: "audio transcrit", pdf: "PDF", text: "texte" };

const state = {
  documentId: null,
  history: [],
};

marked.setOptions({ breaks: true, gfm: true });

function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(text || ""));
}

// Wrap fetch so a 401 (expired session) sends the user back to /login.
const _origFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const res = await _origFetch(...args);
  if (res.status === 401) {
    window.location.assign("/login");
    throw new Error("Non authentifié");
  }
  return res;
};

function setStatus(msg, isError = false) {
  uploadStatus.textContent = msg;
  uploadStatus.classList.toggle("error", isError);
}

function showUploadView() {
  state.documentId = null;
  state.history = [];
  fileInput.value = "";
  filenameLabel.textContent = "";
  submitBtn.disabled = true;
  urlInput.value = "";
  urlSubmitBtn.disabled = true;
  setStatus("");
  resultView.classList.add("hidden");
  uploadView.classList.remove("hidden");
  highlightActive();
}

function showResultView({ documentId, title, summary, sourceKind, textLength, messages }) {
  state.documentId = documentId;
  state.history = messages.map((m) => ({ role: m.role, content: m.content }));

  docTitleEl.textContent = title;
  summaryEl.innerHTML = renderMarkdown(summary);
  docMetaEl.textContent = `Source : ${SOURCE_LABELS[sourceKind] || sourceKind} — ${textLength.toLocaleString("fr-FR")} caractères extraits`;

  chatLog.innerHTML = "";
  for (const msg of state.history) appendBubble(msg.role, msg.content);

  uploadView.classList.add("hidden");
  resultView.classList.remove("hidden");
  highlightActive();
}

function highlightActive() {
  for (const li of conversationsEl.querySelectorAll("li")) {
    li.classList.toggle("active", li.dataset.id === state.documentId);
  }
}

async function refreshConversations() {
  try {
    const q = searchInput.value.trim();
    const url = q ? `/api/documents?q=${encodeURIComponent(q)}` : "/api/documents";
    const res = await fetch(url);
    if (!res.ok) return;
    const items = await res.json();
    conversationsEl.innerHTML = "";
    if (items.length === 0) {
      const li = document.createElement("li");
      li.className = "conv-empty";
      li.textContent = q ? "Aucun résultat" : "Aucune conversation";
      conversationsEl.appendChild(li);
      return;
    }
    for (const item of items) {
      const li = document.createElement("li");
      li.dataset.id = item.document_id;
      li.innerHTML = `
        <span class="conv-icon">${SOURCE_ICONS[item.source_kind] || "📄"}</span>
        <span class="conv-title"></span>
        <button class="conv-delete" title="Supprimer" aria-label="Supprimer cette conversation">🗑</button>
      `;
      li.querySelector(".conv-title").textContent = item.title;
      li.addEventListener("click", (e) => {
        if (e.target.closest(".conv-delete")) return;
        loadConversation(item.document_id);
      });
      li.querySelector(".conv-delete").addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`Supprimer "${item.title}" ?`)) return;
        try {
          const res = await fetch(`/api/documents/${item.document_id}`, { method: "DELETE" });
          if (!res.ok) throw new Error("Suppression échouée");
          if (state.documentId === item.document_id) showUploadView();
          refreshConversations();
        } catch (err) {
          alert(err.message);
        }
      });
      conversationsEl.appendChild(li);
    }
    highlightActive();
  } catch {
    /* ignore */
  }
}

let searchDebounce = null;
searchInput.addEventListener("input", () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(refreshConversations, 200);
});

async function loadConversation(documentId) {
  try {
    const res = await fetch(`/api/documents/${documentId}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "Erreur");
    }
    const data = await res.json();
    showResultView({
      documentId: data.document_id,
      title: data.title,
      summary: data.summary,
      sourceKind: data.source_kind,
      textLength: data.text_length,
      messages: data.messages,
    });
  } catch (err) {
    setStatus(err.message, true);
  }
}

fileInput.addEventListener("change", () => {
  const file = fileInput.files?.[0];
  filenameLabel.textContent = file ? file.name : "";
  submitBtn.disabled = !file;
});

["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);
dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer?.files?.[0];
  if (file) {
    const dt = new DataTransfer();
    dt.items.add(file);
    fileInput.files = dt.files;
    fileInput.dispatchEvent(new Event("change"));
  }
});

async function processResponseAndShow(res) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Erreur serveur");
  }
  const data = await res.json();
  showResultView({
    documentId: data.document_id,
    title: data.title,
    summary: data.summary,
    sourceKind: data.source_kind,
    textLength: data.text_length,
    messages: [],
  });
  chatInput.focus();
  refreshConversations();
}

uploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = fileInput.files?.[0];
  if (!file) return;

  submitBtn.disabled = true;
  urlSubmitBtn.disabled = true;
  const isAudio = file.type.startsWith("audio/") ||
    /\.(mp3|wav|m4a|flac|ogg|webm|mp4|mpeg|mpga)$/i.test(file.name);
  setStatus(isAudio ? "Transcription puis résumé en cours… (peut prendre une minute)" : "Résumé en cours…");

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch("/api/process", { method: "POST", body: formData });
    await processResponseAndShow(res);
  } catch (err) {
    setStatus(err.message, true);
    submitBtn.disabled = false;
    urlSubmitBtn.disabled = !urlInput.value.trim();
  }
});

urlInput.addEventListener("input", () => {
  urlSubmitBtn.disabled = !urlInput.value.trim();
});

urlForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!url) return;

  urlSubmitBtn.disabled = true;
  submitBtn.disabled = true;
  setStatus("Téléchargement, transcription et résumé en cours… (peut prendre quelques minutes)");

  try {
    const res = await fetch("/api/process-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    await processResponseAndShow(res);
  } catch (err) {
    setStatus(err.message, true);
    urlSubmitBtn.disabled = false;
    submitBtn.disabled = !fileInput.files?.[0];
  }
});

newBtn.addEventListener("click", showUploadView);

deleteBtn.addEventListener("click", async () => {
  if (!state.documentId) return;
  if (!confirm("Supprimer cette conversation ?")) return;
  try {
    const res = await fetch(`/api/documents/${state.documentId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Suppression échouée");
    showUploadView();
    refreshConversations();
  } catch (err) {
    alert(err.message);
  }
});

function appendBubble(role, content, opts = {}) {
  const el = document.createElement("div");
  el.className = `bubble ${role}`;
  if (opts.thinking) el.classList.add("thinking");
  if (role === "assistant" && !opts.thinking) {
    el.classList.add("markdown");
    el.innerHTML = renderMarkdown(content);
  } else {
    el.textContent = content;
  }
  chatLog.appendChild(el);
  chatLog.scrollTop = chatLog.scrollHeight;
  return el;
}

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!state.documentId) return;
  const text = chatInput.value.trim();
  if (!text) return;

  chatInput.value = "";
  appendBubble("user", text);
  state.history.push({ role: "user", content: text });
  const thinking = appendBubble("assistant", "…", { thinking: true });

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        document_id: state.documentId,
        messages: state.history,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "Erreur serveur");
    }
    const data = await res.json();
    thinking.classList.remove("thinking");
    thinking.classList.add("markdown");
    thinking.innerHTML = renderMarkdown(data.reply);
    state.history.push({ role: "assistant", content: data.reply });
  } catch (err) {
    thinking.classList.remove("thinking");
    thinking.classList.add("error");
    thinking.textContent = `Erreur : ${err.message}`;
    state.history.pop();
  }
});

refreshConversations();
