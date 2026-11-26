document.addEventListener("DOMContentLoaded", () => {
  const NORMAL_CHATS_KEY = "llmagent_normal_chats_v1";
  const NORMAL_OPEN_TABS_KEY = "llmagent_normal_open_tabs_v1";
  const NORMAL_ACTIVE_ID_KEY = "llmagent_normal_active_id_v1";

  let chats = [];
  let openTabIds = [];
  let activeChatId = null;
  let isGenerating = false;
  let abortController = null;
  let attachedImageBase64 = null;
  let isWebEnabled = true;
  let isAnonEnabled = false;
  let isCloudEnabled = false;
  let userScrolledUp = false;

  const sidebar = document.getElementById("chat-sidebar");
  const btnToggleSidebar = document.getElementById("btn-toggle-sidebar");
  const btnNewChat = document.getElementById("btn-new-chat");
  const btnHeaderNew = document.getElementById("btn-header-new");
  const btnClearChat = document.getElementById("btn-clear-chat");
  const historySearch = document.getElementById("history-search");
  const historyList = document.getElementById("history-list");
  const activeChatTitle = document.getElementById("active-chat-title");
  const messagesScroll = document.getElementById("messages-scroll");
  const messagesContainer = document.getElementById("messages-container");

  const chatTextarea = document.getElementById("chat-textarea");
  const btnSend = document.getElementById("btn-send");
  const btnStop = document.getElementById("btn-stop");
  const imageInput = document.getElementById("image-input");
  const btnUploadImage = document.getElementById("btn-upload-image");
  const imagePreviewBox = document.getElementById("image-preview-box");
  const imagePreviewImg = document.getElementById("image-preview-img");
  const btnCancelImage = document.getElementById("btn-cancel-image");

  const btnToggleWeb = document.getElementById("btn-toggle-web");
  const btnToggleAnon = document.getElementById("btn-toggle-anon");
  const btnToggleCloud = document.getElementById("btn-toggle-cloud");
  const modelDot = document.getElementById("model-dot");
  const modelNameLabel = document.getElementById("model-name-label");

  function makeId() {
    return `chat_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
  }

  function extractText(content) {
    if (typeof content === "string") return content;
    if (Array.isArray(content)) {
      const txt = content.find(p => p && p.type === "text");
      return txt ? (txt.text || "") : "Imagen adjunta";
    }
    return "";
  }

  function extractImage(content) {
    if (Array.isArray(content)) {
      const img = content.find(p => p && p.type === "image_url" && p.image_url);
      return img ? img.image_url.url : null;
    }
    return null;
  }

  function deriveTitle(text) {
    const clean = (text || "").replace(/\s+/g, " ").trim();
    if (!clean) return "Nuevo chat";
    return clean.length > 38 ? clean.slice(0, 38) + "…" : clean;
  }

  function formatTime(ts) {
    try {
      return new Date(ts || Date.now()).toLocaleString("es-ES", {
        day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit"
      });
    } catch {
      return "";
    }
  }

  function saveStore() {
    try {
      localStorage.setItem(NORMAL_CHATS_KEY, JSON.stringify(chats));
      localStorage.setItem(NORMAL_OPEN_TABS_KEY, JSON.stringify(openTabIds));
      if (activeChatId) localStorage.setItem(NORMAL_ACTIVE_ID_KEY, activeChatId);
    } catch (e) {
      console.warn("Error guardando historial:", e);
    }
  }

  function loadStore() {
    try {
      const raw = JSON.parse(localStorage.getItem(NORMAL_CHATS_KEY) || "[]");
      chats = Array.isArray(raw) ? raw.filter(c => c && c.id) : [];
    } catch {
      chats = [];
    }
    try {
      const tabs = JSON.parse(localStorage.getItem(NORMAL_OPEN_TABS_KEY) || "[]");
      openTabIds = Array.isArray(tabs) ? tabs.filter(id => chats.some(c => c.id === id)) : [];
    } catch {
      openTabIds = [];
    }
    const savedActive = localStorage.getItem(NORMAL_ACTIVE_ID_KEY);
    if (chats.length === 0) {
      const first = { id: makeId(), title: "Nuevo chat", createdAt: Date.now(), updatedAt: Date.now(), messages: [] };
      chats.push(first);
      openTabIds = [first.id];
      activeChatId = first.id;
      saveStore();
    } else {
      if (openTabIds.length === 0) openTabIds = [chats[0].id];
      activeChatId = (savedActive && chats.some(c => c.id === savedActive)) ? savedActive : openTabIds[0];
      if (!openTabIds.includes(activeChatId)) openTabIds.push(activeChatId);
    }
  }

  function getActiveChat() {
    let c = chats.find(x => x.id === activeChatId);
    if (!c) {
      loadStore();
      c = chats.find(x => x.id === activeChatId) || chats[0];
    }
    return c;
  }

  function createNewChat() {
    if (isGenerating) return;
    const c = { id: makeId(), title: "Nuevo chat", createdAt: Date.now(), updatedAt: Date.now(), messages: [] };
    chats.unshift(c);
    openTabIds.push(c.id);
    activeChatId = c.id;
    saveStore();
    renderHistory();
    renderMessages();
    chatTextarea.focus();
  }

  function deleteChat(id) {
    if (isGenerating && id === activeChatId) return;
    chats = chats.filter(c => c.id !== id);
    openTabIds = openTabIds.filter(x => x !== id);
    if (chats.length === 0) {
      createNewChat();
      return;
    }
    if (openTabIds.length === 0) openTabIds = [chats[0].id];
    if (activeChatId === id) activeChatId = openTabIds[0];
    saveStore();
    renderHistory();
    renderMessages();
  }

  function renderHistory() {
    const q = (historySearch.value || "").trim().toLowerCase();
    const sorted = [...chats]
      .filter(c => !q || (c.title || "").toLowerCase().includes(q))
      .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));

    historyList.innerHTML = "";
    if (sorted.length === 0) {
      historyList.innerHTML = `<div class="history-empty">Sin conversaciones</div>`;
      return;
    }

    sorted.forEach(c => {
      const item = document.createElement("div");
      item.className = `history-item${c.id === activeChatId ? " active" : ""}`;

      const meta = document.createElement("div");
      meta.className = "history-meta";

      const title = document.createElement("div");
      title.className = "history-title";
      title.textContent = c.title || "Nuevo chat";

      const count = (c.messages || []).length;
      const sub = document.createElement("div");
      sub.className = "history-sub";
      sub.textContent = `${count} msg · ${formatTime(c.updatedAt || c.createdAt)}`;

      meta.appendChild(title);
      meta.appendChild(sub);
      item.appendChild(meta);

      const del = document.createElement("button");
      del.type = "button";
      del.className = "history-del";
      del.title = "Eliminar chat";
      del.innerHTML = `<svg viewBox="0 0 24 24" class="ico" style="width:13px;height:13px"><path d="M4 7h16"/><path d="M9 7V5h6v2"/><path d="m6 7 1 13h10l1-13"/></svg>`;
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteChat(c.id);
      });
      item.appendChild(del);

      item.addEventListener("click", () => {
        if (isGenerating) return;
        activeChatId = c.id;
        if (!openTabIds.includes(c.id)) openTabIds.push(c.id);
        saveStore();
        renderHistory();
        renderMessages();
        chatTextarea.focus();
      });

      historyList.appendChild(item);
    });
  }

  function addCopyButtons(container) {
    container.querySelectorAll("pre").forEach(pre => {
      if (pre.querySelector(".code-copy-btn")) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "code-copy-btn";
      btn.textContent = "Copiar";
      btn.addEventListener("click", () => {
        const code = pre.querySelector("code");
        const txt = code ? code.innerText : pre.innerText;
        navigator.clipboard?.writeText(txt).then(() => {
          btn.textContent = "¡Copiado!";
          setTimeout(() => { btn.textContent = "Copiar"; }, 1800);
        });
      });
      pre.appendChild(btn);
    });
  }

  function renderBubble(bubble, thinking, content) {
    let html = "";
    if (thinking && thinking.trim()) {
      const safeThink = thinking.replace(/</g, "&lt;").replace(/>/g, "&gt;");
      html += `<details class="think-box"><summary>Razonamiento</summary><div class="think-body">${safeThink}</div></details>`;
    }
    html += marked.parse(content || "");
    bubble.innerHTML = html;
    addCopyButtons(bubble);
  }

  function appendMsg(role, content, imgSrc = null) {
    const wrap = document.createElement("div");
    wrap.className = `chat-msg ${role}`;
    const imgHtml = imgSrc ? `<div class="msg-attachment"><img src="${imgSrc}" alt="Imagen adjunta"></div>` : "";
    wrap.innerHTML = `
      <div class="msg-avatar">${role === "user" ? "TÚ" : "A"}</div>
      <div class="msg-bubble">
        ${imgHtml}
        <div class="msg-bubble-content">${role === "user" ? marked.parse(content || "") : ""}</div>
      </div>
    `;
    messagesContainer.appendChild(wrap);
    if (!userScrolledUp) {
      messagesScroll.scrollTop = messagesScroll.scrollHeight;
    }
    return wrap.querySelector(".msg-bubble-content");
  }

  function renderMessages() {
    const chat = getActiveChat();
    activeChatTitle.textContent = chat.title || "Nuevo chat";
    messagesContainer.innerHTML = "";

    if (!chat.messages || chat.messages.length === 0) {
      const welcome = document.createElement("div");
      welcome.className = "welcome-chat";
      welcome.innerHTML = `
        <h2>Chat Normal</h2>
        <p>Conversación independiente con historial persistente. Pregunta lo que quieras, analiza ideas o adjunta imágenes.</p>
        <div class="starter-grid">
          <button class="starter-pill" data-prompt="Explícame un tema complejo de forma sencilla y con ejemplos.">Explicar un tema</button>
          <button class="starter-pill" data-prompt="Ayúdame a resumir o mejorar un texto.">Redactar o resumir</button>
          <button class="starter-pill" data-prompt="Compara ventajas y desventajas de dos tecnologías o enfoques.">Comparar opciones</button>
        </div>
      `;
      welcome.querySelectorAll(".starter-pill").forEach(btn => {
        btn.addEventListener("click", () => {
          chatTextarea.value = btn.dataset.prompt;
          sendMessage();
        });
      });
      messagesContainer.appendChild(welcome);
      return;
    }

    chat.messages.forEach(m => {
      const text = extractText(m.content);
      const img = m.imgSrc || extractImage(m.content);
      const bubble = appendMsg(m.role, m.role === "assistant" ? "" : text, img);
      if (m.role === "assistant") {
        renderBubble(bubble, m.thinking || "", text);
      }
    });
    messagesScroll.scrollTop = messagesScroll.scrollHeight;
  }

  async function sendMessage() {
    if (isGenerating) return;
    const text = chatTextarea.value.trim();
    if (!text && !attachedImageBase64) return;

    const welcomeEl = messagesContainer.querySelector(".welcome-chat");
    if (welcomeEl) welcomeEl.remove();

    const chat = getActiveChat();
    let historyItem;
    if (attachedImageBase64) {
      appendMsg("user", text || "Analiza esta imagen adjunta.", attachedImageBase64);
      historyItem = {
        role: "user",
        content: [
          { type: "text", text: text || "Analiza esta imagen." },
          { type: "image_url", image_url: { url: attachedImageBase64 } }
        ]
      };
      attachedImageBase64 = null;
      imageInput.value = "";
      imagePreviewBox.classList.add("hidden");
    } else {
      appendMsg("user", text);
      historyItem = { role: "user", content: text };
    }

    if ((!chat.messages || chat.messages.length === 0) && (!chat.title || chat.title === "Nuevo chat")) {
      chat.title = deriveTitle(text || "Imagen adjunta");
      activeChatTitle.textContent = chat.title;
    }

    chat.messages.push(historyItem);
    chat.updatedAt = Date.now();
    saveStore();
    renderHistory();

    chatTextarea.value = "";
    chatTextarea.style.height = "auto";
    userScrolledUp = false;

    isGenerating = true;
    btnSend.classList.add("hidden");
    btnStop.classList.remove("hidden");
    abortController = new AbortController();

    const bubble = appendMsg("assistant", "");
    bubble.innerHTML = `<div class="thinking-loader"><span>Pensando respuesta...</span></div>`;

    let fullContent = "";
    let fullThinking = "";

    try {
      const apiMessages = chat.messages.map(m => ({ role: m.role, content: m.content }));
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: abortController.signal,
        body: JSON.stringify({
          messages: apiMessages,
          mode: "chat",
          web_search: isWebEnabled,
          cloud_workers: isCloudEnabled,
          active_file: null,
          project_files: [],
          workspace_path: "",
          max_tokens: 16384,
          temperature: 0.5,
          stream: true
        })
      });

      if (!res.ok) {
        const errTxt = await res.text();
        throw new Error(errTxt || `HTTP ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let sseBuf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        sseBuf += decoder.decode(value, { stream: true });
        const lines = sseBuf.split(/\r?\n/);
        sseBuf = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw || raw === "[DONE]") continue;
          try {
            const parsed = JSON.parse(raw);
            if (parsed.error) throw new Error(parsed.error.message || JSON.stringify(parsed.error));
            const choice = parsed.choices && parsed.choices[0];
            const deltaObj = choice ? (choice.delta || {}) : {};
            const delta = deltaObj.content || "";
            const reasoning = deltaObj.reasoning_content || deltaObj.reasoning || "";

            if (reasoning) fullThinking += reasoning;
            if (delta) {
              // Manejar etiquetas <think>...</think> inline si llegan en content
              fullContent += delta;
            }

            // Extraer <think> si el modelo lo embebe en content
            let displayThinking = fullThinking;
            let displayContent = fullContent;
            const thinkMatch = displayContent.match(/<think>([\s\S]*?)(<\/think>|$)/i);
            if (thinkMatch) {
              displayThinking = (displayThinking ? displayThinking + "\n" : "") + thinkMatch[1];
              displayContent = displayContent.replace(/<think>[\s\S]*?(<\/think>|$)/gi, "").trimStart();
            }

            if (delta || reasoning) {
              renderBubble(bubble, displayThinking, displayContent);
              if (!userScrolledUp) {
                messagesScroll.scrollTop = messagesScroll.scrollHeight;
              }
            }
          } catch (e) {
            if (e.message && !e.message.includes("JSON")) throw e;
          }
        }
      }

      let finalThinking = fullThinking;
      let finalContent = fullContent;
      const thinkMatch = finalContent.match(/<think>([\s\S]*?)(<\/think>|$)/i);
      if (thinkMatch) {
        finalThinking = (finalThinking ? finalThinking + "\n" : "") + thinkMatch[1];
        finalContent = finalContent.replace(/<think>[\s\S]*?(<\/think>|$)/gi, "").trimStart();
      }

      chat.messages.push({ role: "assistant", content: finalContent, thinking: finalThinking });
      chat.updatedAt = Date.now();
      saveStore();
      renderHistory();
    } catch (err) {
      if (err.name === "AbortError") {
        if (!fullContent.trim()) {
          bubble.innerHTML = `<span style="color:var(--faint)">Generación detenida.</span>`;
        }
      } else {
        bubble.innerHTML += `<div class="msg-error">Error: ${err.message}</div>`;
      }
    } finally {
      isGenerating = false;
      btnSend.classList.remove("hidden");
      btnStop.classList.add("hidden");
    }
  }

  // Event listeners
  btnToggleSidebar.addEventListener("click", () => sidebar.classList.toggle("collapsed"));
  btnNewChat.addEventListener("click", createNewChat);
  btnHeaderNew.addEventListener("click", createNewChat);
  historySearch.addEventListener("input", renderHistory);

  btnClearChat.addEventListener("click", () => {
    if (isGenerating) return;
    const chat = getActiveChat();
    if (!confirm(`¿Limpiar la conversación "${chat.title}"?`)) return;
    chat.messages = [];
    chat.title = "Nuevo chat";
    chat.updatedAt = Date.now();
    saveStore();
    renderHistory();
    renderMessages();
  });

  btnSend.addEventListener("click", sendMessage);
  btnStop.addEventListener("click", () => {
    if (abortController) abortController.abort();
    fetch("/api/chat/abort", { method: "POST" }).catch(() => {});
  });

  chatTextarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  chatTextarea.addEventListener("input", () => {
    chatTextarea.style.height = "auto";
    chatTextarea.style.height = Math.min(chatTextarea.scrollHeight, 180) + "px";
  });

  messagesScroll.addEventListener("scroll", () => {
    const dist = messagesScroll.scrollHeight - messagesScroll.scrollTop - messagesScroll.clientHeight;
    userScrolledUp = dist > 80;
  });

  // Imagen adjunta
  btnUploadImage.addEventListener("click", () => imageInput.click());
  imageInput.addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      attachedImageBase64 = ev.target.result;
      imagePreviewImg.src = attachedImageBase64;
      imagePreviewBox.classList.remove("hidden");
    };
    reader.readAsDataURL(f);
  });
  btnCancelImage.addEventListener("click", () => {
    attachedImageBase64 = null;
    imageInput.value = "";
    imagePreviewBox.classList.add("hidden");
  });
  chatTextarea.addEventListener("paste", (e) => {
    const items = (e.clipboardData || e.originalEvent.clipboardData).items;
    for (const item of items) {
      if (item.type.indexOf("image") === 0) {
        const reader = new FileReader();
        reader.onload = (ev) => {
          attachedImageBase64 = ev.target.result;
          imagePreviewImg.src = attachedImageBase64;
          imagePreviewBox.classList.remove("hidden");
        };
        reader.readAsDataURL(item.getAsFile());
        break;
      }
    }
  });

  // Toggles
  btnToggleWeb.addEventListener("click", () => {
    isWebEnabled = !isWebEnabled;
    btnToggleWeb.classList.toggle("off", !isWebEnabled);
    btnToggleWeb.querySelector("span").textContent = isWebEnabled ? "Web ON" : "Web OFF";
  });

  btnToggleAnon.addEventListener("click", async () => {
    isAnonEnabled = !isAnonEnabled;
    btnToggleAnon.classList.toggle("off", !isAnonEnabled);
    btnToggleAnon.querySelector("span").textContent = isAnonEnabled ? "Anon ON" : "Anon OFF";
    try {
      await fetch("/api/proxy/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: isAnonEnabled })
      });
    } catch {}
  });

  btnToggleCloud.addEventListener("click", async () => {
    isCloudEnabled = !isCloudEnabled;
    btnToggleCloud.classList.toggle("off", !isCloudEnabled);
    btnToggleCloud.querySelector("span").textContent = isCloudEnabled ? "Cloud ON" : "Cloud OFF";
    try {
      await fetch("/api/cloud/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: isCloudEnabled })
      });
    } catch {}
  });

  async function syncStatus() {
    try {
      const [stRes, pxRes, clRes] = await Promise.all([
        fetch("/api/status").then(r => r.json()).catch(() => null),
        fetch("/api/proxy/config").then(r => r.json()).catch(() => null),
        fetch("/api/cloud/config").then(r => r.json()).catch(() => null)
      ]);
      if (stRes) {
        modelDot.classList.toggle("ok", !!stRes.model_loaded);
        modelNameLabel.textContent = stRes.current_model ? stRes.current_model.replace(/\.gguf$/i, "") : "Sin modelo";
      }
      if (pxRes) {
        isAnonEnabled = !!pxRes.enabled;
        btnToggleAnon.classList.toggle("off", !isAnonEnabled);
        btnToggleAnon.querySelector("span").textContent = isAnonEnabled ? "Anon ON" : "Anon OFF";
      }
      if (clRes) {
        isCloudEnabled = !!clRes.enabled;
        btnToggleCloud.classList.toggle("off", !isCloudEnabled);
        btnToggleCloud.querySelector("span").textContent = isCloudEnabled ? "Cloud ON" : "Cloud OFF";
      }
    } catch {}
  }

  window.addEventListener("storage", (e) => {
    if (e.key === NORMAL_CHATS_KEY || e.key === NORMAL_OPEN_TABS_KEY || e.key === NORMAL_ACTIVE_ID_KEY) {
      if (isGenerating) return;
      loadStore();
      renderHistory();
      renderMessages();
    }
  });

  loadStore();
  renderHistory();
  renderMessages();
  syncStatus();
  chatTextarea.focus();
});
