// LLMAGENT - MONACO EDITOR + GGUF ENGINE
document.addEventListener("DOMContentLoaded", () => {
  // Monaco Editor State
  let monacoEditor = null;
  let openTabs = new Map(); // path -> { model, isModified }
  let activeFilePath = null;
  let currentWorkspacePath = "";

  // Agente State
  let currentMode = "agent"; // "agent" (crear apps/archivos) o "chat" (pregunta normal)
  let isWebSearchEnabled = true; // Búsqueda web RAG activa por defecto
  let agentHistory = [];
  let isGenerating = false;
  let generatingMode = null;
  let abortController = null;
  let currentAttachedImageBase64 = null;
  let lastAssistantMsgContent = "";
  let lastAssistantThinking = "";
  let lastAssistantOrchestratorSteps = [];
  let lastAssistantMsgDiv = null;
  let continueMode = null;
  let userScrolledUp = false; // Permite al usuario subir a leer mientras se genera sin ser bajado a la fuerza

  // Normal Chat Multi-Tab & Persistent History State (compartido con /static/chat.html)
  const NORMAL_CHATS_KEY = "llmagent_normal_chats_v1";
  const NORMAL_OPEN_TABS_KEY = "llmagent_normal_open_tabs_v1";
  const NORMAL_ACTIVE_ID_KEY = "llmagent_normal_active_id_v1";
  let normalChats = [];
  let openNormalTabIds = [];
  let activeNormalChatId = null;

  // DOM Elements - Activity Bar & Sidebar
  const actExplorer = document.getElementById("act-explorer");
  const actSettings = document.getElementById("act-settings");
  const actToggleAgent = document.getElementById("act-toggle-agent");
  const viewExplorer = document.getElementById("view-explorer");
  const viewSettings = document.getElementById("view-settings");
  const editorPane = document.getElementById("editor-pane");
  const agentPanel = document.getElementById("agent-panel");

  const workspaceFolderNameEl = document.getElementById("workspace-folder-name");
  const btnOpenWorkspace = document.getElementById("btn-open-workspace");
  const btnWelcomeOpenFolder = document.getElementById("btn-welcome-open-folder");
  const btnWelcomeAskAgent = document.getElementById("btn-welcome-ask-agent");
  const fileTreeContainer = document.getElementById("file-tree-container");
  const btnNewFile = document.getElementById("btn-new-file");
  const btnNewFolder = document.getElementById("btn-new-folder");
  const btnDownloadUrl = document.getElementById("btn-download-url");
  const btnRefreshTree = document.getElementById("btn-refresh-tree");

  // Editor Area Elements
  const tabsBar = document.getElementById("tabs-bar");
  const breadcrumbPath = document.getElementById("breadcrumb-path");
  const btnSaveFile = document.getElementById("btn-save-file");
  const btnRunProject = document.getElementById("btn-run-project");
  const btnOpenPreview = document.getElementById("btn-open-preview");
  const btnCompileCheck = document.getElementById("btn-compile-check");
  const btnExportPdf = document.getElementById("btn-export-pdf");
  const welcomeScreen = document.getElementById("welcome-screen");

  // Agent Elements
  const modeAgentBtn = document.getElementById("mode-agent-btn");
  const modeChatBtn = document.getElementById("mode-chat-btn");
  const btnToggleWeb = document.getElementById("btn-toggle-web");
  const btnToggleAnon = document.getElementById("btn-toggle-anon");
  const chkProxyEnabled = document.getElementById("chk-proxy-enabled");
  const chkProxyAutostart = document.getElementById("chk-proxy-autostart");
  const proxyUrlInput = document.getElementById("proxy-url-input");
  const proxyStatusLine = document.getElementById("proxy-status-line");
  const btnTestProxy = document.getElementById("btn-test-proxy");
  const btnSaveProxy = document.getElementById("btn-save-proxy");
  let isAnonEnabled = false;
  const btnToggleWorkers = document.getElementById("btn-toggle-workers");
  const chkCloudEnabled = document.getElementById("chk-cloud-enabled");
  const cloudProviderSelect = document.getElementById("cloud-provider-select");
  const cloudApiKeyInput = document.getElementById("cloud-apikey-input");
  const cloudModelInput = document.getElementById("cloud-model-input");
  const cloudStatusLine = document.getElementById("cloud-status-line");
  const btnTestCloud = document.getElementById("btn-test-cloud");
  const btnSaveCloud = document.getElementById("btn-save-cloud");
  let isCloudWorkersEnabled = false;
  const agentMessages = document.getElementById("agent-messages");
  const chatMessages = document.getElementById("chat-messages");
  const normalChatBar = document.getElementById("normal-chat-bar");
  const normalChatTabs = document.getElementById("normal-chat-tabs");
  const btnNewNormalChat = document.getElementById("btn-new-normal-chat");
  const btnToggleNormalHistory = document.getElementById("btn-toggle-normal-history");
  const normalHistoryDrawer = document.getElementById("normal-history-drawer");
  const btnCloseNormalHistory = document.getElementById("btn-close-normal-history");
  const normalHistoryList = document.getElementById("normal-history-list");
  const btnOpenChatTab = document.getElementById("btn-open-chat-tab");
  const agentTextarea = document.getElementById("agent-textarea");
  const btnSendAgent = document.getElementById("btn-send-agent");
  const btnStopAgent = document.getElementById("btn-stop-agent");
  const btnClearAgentChat = document.getElementById("btn-clear-agent-chat");
  const continueBanner = document.getElementById("continue-banner");
  const btnContinueGen = document.getElementById("btn-continue-generation");
  const imageInput = document.getElementById("image-input");
  const btnUploadImage = document.getElementById("btn-upload-image");
  const imagePreviewBox = document.getElementById("image-preview-box");
  const imagePreviewImg = document.getElementById("image-preview-img");
  const btnCancelImage = document.getElementById("btn-cancel-image");

  // Hardware & Model Settings Elements
  const vramText = document.getElementById("vram-text");
  const vramPctText = document.getElementById("vram-pct-text");
  const vramFill = document.getElementById("vram-fill");
  const ramText = document.getElementById("ram-text");
  const ramFill = document.getElementById("ram-fill");
  const modelSelect = document.getElementById("model-select");
  const mmprojSelect = document.getElementById("mmproj-select");
  const enableMmproj = document.getElementById("enable-mmproj");
  const gpuLayersSlider = document.getElementById("gpu-layers-slider");
  const gpuLayersBadge = document.getElementById("gpu-layers-badge");
  const ctxSizeSelect = document.getElementById("ctx-size-select");
  const btnLoadModel = document.getElementById("btn-load-model");
  const btnUnloadModel = document.getElementById("btn-unload-model");
  const btnPurgeVram = document.getElementById("btn-purge-vram-side");
  const btnPurgeRam = document.getElementById("btn-purge-ram-side");

  // Status Bar Elements
  const statusActiveFile = document.getElementById("status-active-file");
  const statusVramText = document.getElementById("status-vram-text");
  const statusSpeedText = document.getElementById("status-speed-text");
  const statusModelName = document.getElementById("status-model-name");
  const statusModelQuant = document.getElementById("status-model-quant");

  // Modal Folder Picker Elements
  const folderPickerModal = document.getElementById("folder-picker-modal");
  const modalBtnClose = document.getElementById("modal-btn-close");
  const modalBtnCancel = document.getElementById("modal-btn-cancel");
  const modalPathInput = document.getElementById("modal-path-input");
  const modalBtnUp = document.getElementById("modal-btn-up");
  const modalBtnGo = document.getElementById("modal-btn-go");
  const modalShortcutsContainer = document.getElementById("modal-shortcuts-container");
  const modalSubfoldersContainer = document.getElementById("modal-subfolders-container");
  const modalBtnConfirm = document.getElementById("modal-btn-confirm");
  const modalBtnCreateSubfolder = document.getElementById("modal-btn-create-subfolder");
  const modalBtnNativeDialog = document.getElementById("modal-btn-native-dialog");

  let modalParentPath = null;

  // ICONOS EN LÍNEA (mismo conjunto que el marcado estático, sin dependencias externas)
  // Los iconos dinámicos reutilizan el mismo conjunto SVG en línea del marcado estático
  function svgIcon(name, size = 14, className = "") {
    const cls = className ? `ico ${className}` : "ico";
    return `<svg class="${cls}" style="width:${size}px;height:${size}px" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  }

  function getFileIconId(filename) {
    const ext = (filename.split(".").pop() || "").toLowerCase();
    if (["png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp"].includes(ext)) return "image-file";
    if (["html", "htm", "css", "js", "mjs", "ts", "jsx", "tsx", "py", "json", "md", "txt",
      "sql", "sh", "bat", "yml", "yaml", "toml", "ini", "cfg"].includes(ext)) return "code";
    return "file";
  }

  // 1. INICIALIZAR MONACO EDITOR (VS CODE)
  require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs' } });
  require(['vs/editor/editor.main'], function () {
    monaco.editor.defineTheme("llmagent", {
      base: "vs-dark",
      inherit: true,
      rules: [
        { token: "keyword", foreground: "E0A45E" },
        { token: "string", foreground: "5BD9A5" },
        { token: "number", foreground: "63C5DA" },
        { token: "regexp", foreground: "63C5DA" },
        { token: "comment", foreground: "6C7885", fontStyle: "italic" },
        { token: "type", foreground: "63C5DA" },
        { token: "type.identifier", foreground: "63C5DA" },
      ],
      colors: {
        "editor.background": "#0A0C0E",
        "editor.foreground": "#E4E9EF",
        "editorLineNumber.foreground": "#47525D",
        "editorLineNumber.activeForeground": "#94A0AC",
        "editorCursor.foreground": "#E0A45E",
        "editor.selectionBackground": "#2A333D",
        "editor.lineHighlightBackground": "#101418",
        "editorGutter.background": "#0A0C0E",
        "editorWidget.background": "#161B21",
        "editorWidget.border": "#262E37",
        "scrollbarSlider.background": "#2A333D80",
        "scrollbarSlider.hoverBackground": "#39454F80",
      },
    });

    monacoEditor = monaco.editor.create(document.getElementById('monaco-container'), {
      value: "",
      language: "javascript",
      theme: "llmagent",
      automaticLayout: true,
      fontSize: 13,
      fontFamily: "'Cascadia Code', 'Cascadia Mono', Consolas, 'Courier New', monospace",
      minimap: { enabled: false },
      lineNumbers: "on",
      roundedSelection: true,
      scrollBeyondLastLine: false,
      padding: { top: 10 },
      tabSize: 2,
    });

    // Atajo Ctrl+S en el editor para guardar
    monacoEditor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      saveActiveFile();
    });

    // Detectar cambios no guardados
    monacoEditor.onDidChangeModelContent(() => {
      if (activeFilePath && openTabs.has(activeFilePath)) {
        const tabInfo = openTabs.get(activeFilePath);
        tabInfo.isModified = true;
        updateTabVisuals();
      }
    });

    // Cargar workspace inicial
    loadWorkspace();
  });

  // 2. NAVEGACIÓN DE LA BARRA DE ACTIVIDAD
  actExplorer.addEventListener("click", () => switchSidebarView("explorer"));
  actSettings.addEventListener("click", () => switchSidebarView("settings"));
  actToggleAgent.addEventListener("click", () => {
    agentPanel.classList.toggle("hidden");
    if (monacoEditor) monacoEditor.layout();
  });

  function switchSidebarView(view) {
    const showSettings = view === "settings";

    actExplorer.classList.toggle("active", !showSettings);
    actSettings.classList.toggle("active", showSettings);

    // Los ajustes ocupan el ancho completo del banco: relevan al explorador y al editor.
    viewExplorer.classList.toggle("hidden", showSettings);
    editorPane.classList.toggle("hidden", showSettings);
    viewSettings.classList.toggle("hidden", !showSettings);

    if (!showSettings && monacoEditor) monacoEditor.layout();
  }

  // Botón de cierre de la hoja de ajustes: vuelve al explorador
  const settingsBackBtn = viewSettings.querySelector("[data-back='explorer']");
  if (settingsBackBtn) {
    settingsBackBtn.addEventListener("click", () => switchSidebarView("explorer"));
  }

  // 3. GESTIÓN DEL WORKSPACE & ÁRBOL DE ARCHIVOS
  async function loadWorkspace() {
    try {
      const res = await fetch("/api/workspace/tree");
      const data = await res.json();
      currentWorkspacePath = data.root;
      workspaceFolderNameEl.textContent = data.name || "Proyecto";

      const explorerPathEl = document.getElementById("explorer-path");
      if (explorerPathEl) {
        explorerPathEl.textContent = data.root;
        explorerPathEl.title = data.root;
      }

      renderFileTree(data.tree, fileTreeContainer);
    } catch (e) {
      console.error("Error al cargar workspace:", e);
    }
  }

  // Abrir carpeta: mostramos el modal interactivo
  btnOpenWorkspace.addEventListener("click", () => openFolderPickerModal());
  if (btnWelcomeOpenFolder) btnWelcomeOpenFolder.addEventListener("click", () => openFolderPickerModal());

  modalBtnClose.addEventListener("click", () => folderPickerModal.classList.add("hidden"));
  modalBtnCancel.addEventListener("click", () => folderPickerModal.classList.add("hidden"));

  async function openFolderPickerModal(targetPath = "") {
    folderPickerModal.classList.remove("hidden");
    await navigateFolderModal(targetPath);
  }

  async function navigateFolderModal(path = "") {
    modalSubfoldersContainer.innerHTML = '<div class="modal-note">Cargando carpetas...</div>';
    try {
      const url = path ? `/api/workspace/browse-folders?path=${encodeURIComponent(path)}` : "/api/workspace/browse-folders";
      const res = await fetch(url);
      const data = await res.json();

      modalPathInput.value = data.current_path;
      modalParentPath = data.parent_path;

      // Renderizar accesos directos
      modalShortcutsContainer.innerHTML = "";
      data.shortcuts.forEach(sc => {
        const pill = document.createElement("button");
        pill.className = "shortcut-pill";
        pill.innerHTML = `${svgIcon("folder", 12)}<span>${sc.name}</span>`;
        pill.addEventListener("click", () => navigateFolderModal(sc.path));
        modalShortcutsContainer.appendChild(pill);
      });

      // Renderizar subcarpetas
      modalSubfoldersContainer.innerHTML = "";
      if (data.subfolders && data.subfolders.length > 0) {
        data.subfolders.forEach(sf => {
          const item = document.createElement("div");
          item.className = "subfolder-item";
          item.innerHTML = `${svgIcon("folder", 13)}<span>${sf.name}</span>`;
          item.addEventListener("click", () => {
            modalPathInput.value = sf.path;
          });
          item.addEventListener("dblclick", () => {
            navigateFolderModal(sf.path);
          });
          modalSubfoldersContainer.appendChild(item);
        });
      } else {
        modalSubfoldersContainer.innerHTML = '<div class="modal-note">No hay subcarpetas en este directorio.</div>';
      }
    } catch (e) {
      modalSubfoldersContainer.innerHTML = `<div class="modal-note error">Error: ${e.message}</div>`;
    }
  }

  modalBtnUp.addEventListener("click", () => {
    if (modalParentPath) navigateFolderModal(modalParentPath);
  });

  modalBtnGo.addEventListener("click", () => {
    const val = modalPathInput.value.trim();
    if (val) navigateFolderModal(val);
  });

  modalPathInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const val = modalPathInput.value.trim();
      if (val) navigateFolderModal(val);
    }
  });

  // Confirmar selección de carpeta
  modalBtnConfirm.addEventListener("click", async () => {
    const chosenPath = modalPathInput.value.trim();
    if (!chosenPath) return;

    try {
      const res = await fetch("/api/workspace/set-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: chosenPath })
      });
      const data = await res.json();
      if (data.success) {
        folderPickerModal.classList.add("hidden");
        // Cerrar pestañas viejas y cargar nuevo workspace
        openTabs.clear();
        activeFilePath = null;
        tabsBar.innerHTML = "";
        welcomeScreen.classList.remove("hidden");
        await loadWorkspace();
      } else {
        alert(data.detail || data.message || "No se pudo abrir esa carpeta.");
      }
    } catch (e) {
      alert("Error: " + e.message);
    }
  });

  // Crear nueva subcarpeta desde el modal y seleccionarla inmediatamente como proyecto activo
  modalBtnCreateSubfolder.addEventListener("click", async () => {
    const name = prompt("Nombre de la nueva carpeta para tu proyecto:");
    if (!name || !name.trim()) return;
    const parentPath = modalPathInput.value.trim();
    try {
      const fullPath = parentPath + (parentPath.endsWith("\\") || parentPath.endsWith("/") ? "" : "\\") + name.trim();
      const res = await fetch("/api/workspace/set-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: fullPath })
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.detail || data.message || "No se pudo crear la carpeta");
      }
      folderPickerModal.classList.add("hidden");
      openTabs.clear();
      activeFilePath = null;
      tabsBar.innerHTML = "";
      welcomeScreen.classList.remove("hidden");
      await loadWorkspace();
    } catch (e) {
      alert("Error al crear carpeta: " + e.message);
    }
  });

  // Selector nativo de Windows (con TopMost al frente)
  modalBtnNativeDialog.addEventListener("click", async () => {
    try {
      const res = await fetch("/api/workspace/select-folder", { method: "POST" });
      const data = await res.json();
      if (data.success) {
        folderPickerModal.classList.add("hidden");
        openTabs.clear();
        activeFilePath = null;
        tabsBar.innerHTML = "";
        welcomeScreen.classList.remove("hidden");
        await loadWorkspace();
      } else if (data.message) {
        alert(data.message);
      }
    } catch (e) {
      alert("Error en el selector nativo: " + e.message);
    }
  });

  btnRefreshTree.addEventListener("click", loadWorkspace);

  // Crear Nuevo Archivo
  btnNewFile.addEventListener("click", async () => {
    const filename = prompt("Nombre del archivo (ejemplo: app.py, index.html):");
    if (!filename) return;
    try {
      await fetch("/api/workspace/item/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: filename, is_directory: false, workspace_path: currentWorkspacePath })
      });
      await loadWorkspace();
      openFileInEditor(filename);
    } catch (e) {
      alert("Error creando archivo: " + e.message);
    }
  });

  // Crear Nueva Carpeta y entrar en ella como carpeta activa del proyecto
  btnNewFolder.addEventListener("click", async () => {
    const foldername = prompt("Nombre de la nueva carpeta de proyecto:");
    if (!foldername || !foldername.trim()) return;
    try {
      const res = await fetch("/api/workspace/item/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: foldername.trim(),
          is_directory: true,
          workspace_path: currentWorkspacePath,
          switch_to_folder: true
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "No se pudo crear la carpeta");
      openTabs.clear();
      activeFilePath = null;
      tabsBar.innerHTML = "";
      welcomeScreen.classList.remove("hidden");
      await loadWorkspace();
    } catch (e) {
      alert("Error creando carpeta: " + e.message);
    }
  });

  // Descargar archivo desde URL directa de internet al proyecto
  if (btnDownloadUrl) {
    btnDownloadUrl.addEventListener("click", async () => {
      const url = prompt("Introduce la URL de internet para descargar al proyecto (ej. https://...):");
      if (!url || !url.trim()) return;

      const customName = prompt("Nombre con el que guardarlo en el proyecto (opcional, deja vacío para nombre original):");

      btnDownloadUrl.innerHTML = `${svgIcon("loader", 12, "spin")}`;
      try {
        const res = await fetch("/api/workspace/download", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url: url.trim(),
            filename: customName && customName.trim() ? customName.trim() : null,
            workspace_path: currentWorkspacePath
          })
        });
        const data = await res.json();
        if (data.success) {
          await loadWorkspace();
          openFileInEditor(data.filename);
        } else {
          alert("Error descargando archivo: " + (data.detail || data.message));
        }
      } catch (err) {
        alert("Fallo al descargar archivo: " + err.message);
      } finally {
        btnDownloadUrl.innerHTML = `<svg class="ico sm" aria-hidden="true"><use href="#i-download" /></svg>`;
      }
    });
  }

  function renderFileTree(nodes, container, depth = 0) {
    container.innerHTML = "";
    if (!nodes || nodes.length === 0) {
      const emptyDiv = document.createElement("div");
      emptyDiv.className = "tree-empty";
      emptyDiv.textContent = "Carpeta vacía. Pide al agente que cree una app.";
      container.appendChild(emptyDiv);
      return;
    }

    nodes.forEach(node => {
      const item = document.createElement("div");
      item.className = "tree-item";
      item.style.paddingLeft = `${12 + depth * 14}px`;

      if (node.type === "directory") {
        item.classList.add("dir");
        item.innerHTML = `${svgIcon("folder")}<span>${node.name}</span>`;
        container.appendChild(item);

        const subContainer = document.createElement("div");
        renderFileTree(node.children, subContainer, depth + 1);
        container.appendChild(subContainer);

        const iconUse = item.querySelector("use");
        item.addEventListener("click", () => {
          const isHidden = subContainer.classList.toggle("hidden");
          if (iconUse) iconUse.setAttribute("href", isHidden ? "#i-folder" : "#i-folder-open");
        });
      } else {
        item.innerHTML = `${svgIcon(getFileIconId(node.name))}<span>${node.name}</span>`;
        item.dataset.path = node.path;
        item.addEventListener("click", () => openFileInEditor(node.path));
        container.appendChild(item);
      }
    });
  }

  function getMonacoLanguage(filename) {
    const ext = filename.split(".").pop().toLowerCase();
    switch (ext) {
      case "html": return "html";
      case "css": return "css";
      case "js": return "javascript";
      case "ts": return "typescript";
      case "py": return "python";
      case "json": return "json";
      case "md": return "markdown";
      case "bat": return "bat";
      case "sh": return "shell";
      case "sql": return "sql";
      default: return "plaintext";
    }
  }

  // 4. EDICIÓN, PESTAÑAS Y MONACO
  async function openFileInEditor(path, forceReload = false) {
    welcomeScreen.classList.add("hidden");

    if (path.toLowerCase().endsWith(".pdf")) {
      window.open(`/api/workspace/file/raw?path=${encodeURIComponent(path)}&workspace_path=${encodeURIComponent(currentWorkspacePath)}`, "_blank");
      return;
    }

    if (openTabs.has(path) && !forceReload) {
      setActiveTab(path);
      return;
    }

    try {
      const res = await fetch(`/api/workspace/file/read?path=${encodeURIComponent(path)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Error leyendo archivo");

      if (openTabs.has(path) && forceReload) {
        const tabInfo = openTabs.get(path);
        if (tabInfo && tabInfo.model) {
          tabInfo.model.setValue(data.content);
          tabInfo.isModified = false;
        }
        setActiveTab(path);
        return;
      }

      const lang = getMonacoLanguage(path);
      const model = monaco.editor.createModel(data.content, lang);

      openTabs.set(path, { model: model, isModified: false });
      createTabElement(path);
      setActiveTab(path);
    } catch (e) {
      console.error("Error al abrir archivo:", e);
    }
  }

  function createTabElement(path) {
    const filename = path.split("/").pop();
    const tab = document.createElement("div");
    tab.className = "tab-item";
    tab.dataset.path = path;
    tab.setAttribute("role", "tab");
    tab.title = path;
    tab.innerHTML = `
      ${svgIcon(getFileIconId(filename), 12)}
      <span class="tab-title">${filename}</span>
      <span class="tab-close" title="Cerrar">&times;</span>
    `;

    tab.addEventListener("click", (e) => {
      if (e.target.classList.contains("tab-close")) {
        closeTab(path);
      } else {
        setActiveTab(path);
      }
    });

    tabsBar.appendChild(tab);
  }

  const agentContextFilename = document.getElementById("agent-context-filename");

  function setActiveTab(path) {
    activeFilePath = path;
    const tabInfo = openTabs.get(path);
    if (!tabInfo) return;

    monacoEditor.setModel(tabInfo.model);
    breadcrumbPath.textContent = path;
    statusActiveFile.textContent = path;
    if (agentContextFilename) {
      agentContextFilename.textContent = path;
    }

    // Vista previa si es HTML
    if (path.endsWith(".html")) {
      btnOpenPreview.classList.remove("hidden");
    } else {
      btnOpenPreview.classList.add("hidden");
    }

    // Actualizar clases activas en tabs
    tabsBar.querySelectorAll(".tab-item").forEach(t => {
      if (t.dataset.path === path) t.classList.add("active");
      else t.classList.remove("active");
    });

    // Marcar el archivo abierto en el árbol del explorador
    fileTreeContainer.querySelectorAll(".tree-item[data-path]").forEach(el => {
      el.classList.toggle("active", el.dataset.path === path);
    });

    // Auditar dependencias del archivo en segundo plano para la pestaña de Dependencias
    if (typeof auditFileDependencies === "function") {
      auditFileDependencies(path);
    }
  }

  function closeTab(path) {
    const tabInfo = openTabs.get(path);
    if (tabInfo && tabInfo.model) tabInfo.model.dispose();
    openTabs.delete(path);

    const tabEl = tabsBar.querySelector(`.tab-item[data-path="${path}"]`);
    if (tabEl) tabEl.remove();

    if (activeFilePath === path) {
      const remainingPaths = Array.from(openTabs.keys());
      if (remainingPaths.length > 0) {
        setActiveTab(remainingPaths[remainingPaths.length - 1]);
      } else {
        activeFilePath = null;
        breadcrumbPath.textContent = "Sin archivo abierto";
        statusActiveFile.textContent = "Sin archivo activo";
        fileTreeContainer.querySelectorAll(".tree-item.active").forEach(el => el.classList.remove("active"));
        if (agentContextFilename) agentContextFilename.textContent = "Ninguno (Abre un archivo para que lo analice)";
        welcomeScreen.classList.remove("hidden");
      }
    }
  }

  function updateTabVisuals() {
    tabsBar.querySelectorAll(".tab-item").forEach(t => {
      const p = t.dataset.path;
      const tabInfo = openTabs.get(p);
      const titleSpan = t.querySelector(".tab-title");
      if (tabInfo && tabInfo.isModified) {
        if (!titleSpan.textContent.endsWith(" •")) {
          titleSpan.textContent = titleSpan.textContent + " •";
        }
      } else {
        titleSpan.textContent = p.split("/").pop();
      }
    });
  }

  async function saveActiveFile() {
    if (!activeFilePath || !openTabs.has(activeFilePath)) return;
    const content = monacoEditor.getValue();

    try {
      const res = await fetch("/api/workspace/file/write", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: activeFilePath, content: content, workspace_path: currentWorkspacePath })
      });
      const data = await res.json();
      if (data.success) {
        const tabInfo = openTabs.get(activeFilePath);
        tabInfo.isModified = false;
        updateTabVisuals();
      }
    } catch (e) {
      alert("Error al guardar archivo: " + e.message);
    }
  }

  btnSaveFile.addEventListener("click", saveActiveFile);

  // =========================================================================
  // TERMINAL INTEGRADA Y GESTIÓN DE DEPENDENCIAS
  // =========================================================================
  const btnToggleTerminal = document.getElementById("btn-toggle-terminal");
  const terminalDrawer = document.getElementById("terminal-drawer");
  const termConsoleOutput = document.getElementById("term-console-output");
  const termCliInput = document.getElementById("term-cli-input");
  const btnTermSubmit = document.getElementById("btn-term-submit");
  const btnTermClear = document.getElementById("btn-term-clear");
  const btnTermKill = document.getElementById("btn-term-kill");
  const btnTermExternal = document.getElementById("btn-term-external");
  const btnTermClose = document.getElementById("btn-term-close");
  const btnTermRunActive = document.getElementById("btn-term-run-active");
  const tabBtnConsole = document.getElementById("tab-btn-console");
  const tabBtnDeps = document.getElementById("tab-btn-deps");
  const termViewConsole = document.getElementById("term-view-console");
  const termViewDeps = document.getElementById("term-view-deps");
  const termDepsList = document.getElementById("term-deps-list");
  const termDepsCount = document.getElementById("term-deps-count");

  let terminalPollInterval = null;
  let lastTermLogId = 0;

  function openTerminalDrawer() {
    if (terminalDrawer) {
      terminalDrawer.classList.remove("hidden");
      startTerminalPolling();
      if (monacoEditor) {
        setTimeout(() => monacoEditor.layout(), 250);
      }
    }
  }

  function closeTerminalDrawer() {
    if (terminalDrawer) {
      terminalDrawer.classList.add("hidden");
      stopTerminalPolling();
      if (monacoEditor) {
        setTimeout(() => monacoEditor.layout(), 250);
      }
    }
  }

  function toggleTerminalDrawer() {
    if (!terminalDrawer) return;
    if (terminalDrawer.classList.contains("hidden")) {
      openTerminalDrawer();
    } else {
      closeTerminalDrawer();
    }
  }

  function appendTerminalLine(text, stream = "stdout", level = "info") {
    if (!termConsoleOutput) return;
    const div = document.createElement("div");
    div.className = `term-line ${stream} ${level}`;
    div.textContent = text;
    termConsoleOutput.appendChild(div);
    termConsoleOutput.scrollTop = termConsoleOutput.scrollHeight;
  }

  async function pollTerminalLogs() {
    try {
      const res = await fetch(`/api/terminal/logs?since=${lastTermLogId}`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.logs && data.logs.length > 0) {
        for (const log of data.logs) {
          if (log.id > lastTermLogId) {
            lastTermLogId = log.id;
            appendTerminalLine(log.text, log.stream, log.level);
          }
        }
      }
    } catch (e) {
      // Silenciar errores de sondeo
    }
  }

  function startTerminalPolling() {
    if (!terminalPollInterval) {
      pollTerminalLogs();
      terminalPollInterval = setInterval(pollTerminalLogs, 600);
    }
  }

  function stopTerminalPolling() {
    if (terminalPollInterval) {
      clearInterval(terminalPollInterval);
      terminalPollInterval = null;
    }
  }

  async function executeTerminalCommand(cmd) {
    if (!cmd || !cmd.trim()) return;
    try {
      await fetch("/api/terminal/exec", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: cmd.trim() })
      });
      startTerminalPolling();
    } catch (e) {
      appendTerminalLine(`[Error ejecutando comando]: ${e.message}`, "stderr", "error");
    }
  }

  async function runFileWithDeps(fileName) {
    openTerminalDrawer();
    switchTermTab("console");
    appendTerminalLine(`[Orquestador] Lanzando '${fileName || "main.py"}' con auditoría de dependencias...`, "system", "info");
    try {
      const res = await fetch("/api/terminal/run_file", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file: fileName, auto_install: true })
      });
      const data = await res.json();
      if (data.success) {
        if (data.type === "html") {
          appendTerminalLine(`[Web App] Archivo HTML abierto en navegador: ${data.url}`, "system", "success");
        }
      } else {
        appendTerminalLine(`[Aviso]: ${data.message}`, "stderr", "warn");
      }
      startTerminalPolling();
    } catch (e) {
      appendTerminalLine(`[Error lanzando archivo]: ${e.message}`, "stderr", "error");
    }
  }

  async function auditFileDependencies(fileName) {
    if (!fileName || !fileName.endsWith(".py")) {
      if (termDepsList) {
        termDepsList.innerHTML = `<div class="term-deps-empty">Selecciona un archivo Python (.py) para auditar sus librerías e imports.</div>`;
      }
      if (termDepsCount) termDepsCount.classList.add("hidden");
      return;
    }
    try {
      const res = await fetch(`/api/terminal/dependencies?file=${encodeURIComponent(fileName)}`);
      if (!res.ok) return;
      const data = await res.json();
      const deps = data.dependencies || [];
      const missing = data.missing || [];
      
      if (termDepsCount) {
        if (missing.length > 0) {
          termDepsCount.textContent = missing.length;
          termDepsCount.classList.remove("hidden");
        } else {
          termDepsCount.classList.add("hidden");
        }
      }

      if (termDepsList) {
        if (deps.length === 0) {
          termDepsList.innerHTML = `<div class="term-deps-empty">No se detectaron librerías de terceros en '${fileName}' (solo módulos estándar de Python).</div>`;
          return;
        }
        let html = `<div style="margin-bottom: 8px; font-weight: 600; color: #8b949e;">Dependencias de '${fileName}':</div>`;
        for (const dep of deps) {
          const isMissing = missing.includes(dep);
          html += `
            <div class="term-dep-item">
              <div>
                <strong>${dep}</strong>
              </div>
              <div style="display: flex; align-items: center; gap: 8px;">
                <span class="term-dep-status ${isMissing ? 'missing' : 'ok'}">
                  ${isMissing ? 'Faltante en entorno' : 'Instalado ✓'}
                </span>
                ${isMissing ? `<button class="term-act-btn" onclick="installSingleDep('${dep}')">Instalar con Pip</button>` : ''}
              </div>
            </div>
          `;
        }
        if (missing.length > 0) {
          html += `
            <div style="margin-top: 12px;">
              <button class="tool-btn run" id="btn-install-all-deps" style="font-size: 11px;">
                ${svgIcon("bolt", 11)}<span>Instalar todos los paquetes faltantes (${missing.join(', ')})</span>
              </button>
            </div>
          `;
        }
        termDepsList.innerHTML = html;
        const btnInstAll = document.getElementById("btn-install-all-deps");
        if (btnInstAll) {
          btnInstAll.addEventListener("click", async () => {
            btnInstAll.disabled = true;
            btnInstAll.innerHTML = `${svgIcon("loader", 11, "spin")}<span>Instalando con pip...</span>`;
            await fetch("/api/terminal/install_deps", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ packages: missing })
            });
            auditFileDependencies(fileName);
          });
        }
      }
    } catch (e) {
      console.error("Error auditando dependencias:", e);
    }
  }

  window.installSingleDep = async function(pkg) {
    appendTerminalLine(`[Pip] Instalando '${pkg}'...`, "system", "info");
    openTerminalDrawer();
    switchTermTab("console");
    await fetch("/api/terminal/install_deps", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ packages: [pkg] })
    });
    if (activeFilePath) auditFileDependencies(activeFilePath);
  };

  function switchTermTab(tab) {
    if (tab === "console") {
      tabBtnConsole?.classList.add("active");
      tabBtnDeps?.classList.remove("active");
      termViewConsole?.classList.remove("hidden");
      termViewDeps?.classList.add("hidden");
    } else {
      tabBtnDeps?.classList.add("active");
      tabBtnConsole?.classList.remove("active");
      termViewDeps?.classList.remove("hidden");
      termViewConsole?.classList.add("hidden");
      if (activeFilePath) auditFileDependencies(activeFilePath);
    }
  }

  // Eventos de la Terminal
  if (btnToggleTerminal) btnToggleTerminal.addEventListener("click", toggleTerminalDrawer);
  if (btnTermClose) btnTermClose.addEventListener("click", closeTerminalDrawer);

  if (tabBtnConsole) tabBtnConsole.addEventListener("click", () => switchTermTab("console"));
  if (tabBtnDeps) tabBtnDeps.addEventListener("click", () => switchTermTab("deps"));

  if (btnTermClear) {
    btnTermClear.addEventListener("click", async () => {
      if (termConsoleOutput) termConsoleOutput.innerHTML = "";
      await fetch("/api/terminal/clear", { method: "POST" });
    });
  }

  if (btnTermKill) {
    btnTermKill.addEventListener("click", async () => {
      await fetch("/api/terminal/kill", { method: "POST" });
    });
  }

  if (btnTermExternal) {
    btnTermExternal.addEventListener("click", async () => {
      await fetch("/api/terminal/open_external", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file: activeFilePath || "main.py" })
      });
    });
  }

  if (btnTermRunActive) {
    btnTermRunActive.addEventListener("click", () => {
      runFileWithDeps(activeFilePath || "main.py");
    });
  }

  // Input de comando interactivo
  if (btnTermSubmit && termCliInput) {
    const handleCommandSubmit = () => {
      const cmd = termCliInput.value.trim();
      if (cmd) {
        executeTerminalCommand(cmd);
        termCliInput.value = "";
      }
    };
    btnTermSubmit.addEventListener("click", handleCommandSubmit);
    termCliInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") handleCommandSubmit();
    });
  }

  // Actualizar el botón Probar / Ejecutar de la barra superior
  if (btnRunProject) {
    btnRunProject.addEventListener("click", () => {
      runFileWithDeps(activeFilePath || "main.py");
    });
  }

  // Previsualizar app web si es HTML
  btnOpenPreview.addEventListener("click", () => {
    if (activeFilePath && activeFilePath.endsWith(".html")) {
      window.open(`file:///${currentWorkspacePath.replace(/\\/g, "/")}/${activeFilePath}`, "_blank");
    }
  });

  // Compilar y verificar sintaxis del archivo activo bajo demanda
  if (btnCompileCheck) {
    btnCompileCheck.addEventListener("click", async () => {
      if (!activeFilePath) {
        alert("Abre primero un archivo en el editor para compilarlo.");
        return;
      }
      btnCompileCheck.innerHTML = `${svgIcon("loader", 12, "spin")}<span>Compilando...</span>`;
      try {
        if (openTabs.has(activeFilePath) && openTabs.get(activeFilePath).isModified) {
          await saveActiveFile();
        }
        const res = await fetch("/api/workspace/file/compile", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: activeFilePath, workspace_path: currentWorkspacePath })
        });
        const data = await res.json();
        if (data.success) {
          alert(`✅ Compilación exitosa (${data.type.toUpperCase()}):\n${data.message}`);
        } else {
          alert(`❌ Error sintáctico en compilación:\n${data.message || data.error}`);
        }
      } catch (err) {
        alert("Error al compilar archivo: " + err.message);
      } finally {
        btnCompileCheck.innerHTML = `<svg class="ico sm" aria-hidden="true"><use href="#i-check" /></svg><span>Compilar</span>`;
      }
    });
  }

  // Exportar a PDF formateado con ReportLab
  if (btnExportPdf) {
    btnExportPdf.addEventListener("click", async () => {
      if (!activeFilePath && !editorInstance) {
        alert("Abre un archivo para exportarlo a PDF.");
        return;
      }
      const title = activeFilePath ? activeFilePath.split("/").pop() : "Reporte LLMAgent";
      const content = editorInstance ? editorInstance.getValue() : "";
      const baseName = activeFilePath ? activeFilePath.replace(/\.[^.]+$/, "") : "reporte";
      const targetPdfName = `${baseName}.pdf`;

      btnExportPdf.innerHTML = `${svgIcon("loader", 12, "spin")}<span>Generando PDF...</span>`;
      try {
        const res = await fetch("/api/workspace/export-pdf", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: `Documento / Archivo: ${title}`,
            content: content,
            filename: targetPdfName,
            workspace_path: currentWorkspacePath
          })
        });
        const d = await res.json();
        if (d.success) {
          await loadWorkspace();
          window.open(`/api/workspace/file/raw?path=${encodeURIComponent(targetPdfName)}&workspace_path=${encodeURIComponent(currentWorkspacePath)}`, "_blank");
        } else {
          alert("Error generando PDF: " + (d.detail || d.message));
        }
      } catch (err) {
        alert("Error generando PDF: " + err.message);
      } finally {
        btnExportPdf.innerHTML = `<svg class="ico sm" aria-hidden="true"><use href="#i-file" /></svg><span>Exportar PDF</span>`;
      }
    });
  }

  // 5. AGENTE IA ANTIGRAVITY (CREADOR DE APLICACIONES) Y CHAT NORMAL SEPARADOS
  function makeNormalChatId() {
    return `chat_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
  }

  function extractTextFromContent(content) {
    if (typeof content === "string") return content;
    if (Array.isArray(content)) {
      const txt = content.find(p => p && p.type === "text");
      return txt ? (txt.text || "") : "Imagen adjunta";
    }
    return "";
  }

  function extractImageFromContent(content) {
    if (Array.isArray(content)) {
      const img = content.find(p => p && p.type === "image_url" && p.image_url);
      return img ? img.image_url.url : null;
    }
    return null;
  }

  function deriveChatTitle(text) {
    const clean = (text || "").replace(/\s+/g, " ").trim();
    if (!clean) return "Nuevo chat";
    return clean.length > 34 ? clean.slice(0, 34) + "…" : clean;
  }

  function formatShortTime(ts) {
    try {
      const d = new Date(ts || Date.now());
      return d.toLocaleString("es-ES", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
    } catch {
      return "";
    }
  }

  function saveNormalChatStore() {
    try {
      localStorage.setItem(NORMAL_CHATS_KEY, JSON.stringify(normalChats));
      localStorage.setItem(NORMAL_OPEN_TABS_KEY, JSON.stringify(openNormalTabIds));
      if (activeNormalChatId) {
        localStorage.setItem(NORMAL_ACTIVE_ID_KEY, activeNormalChatId);
      }
    } catch (e) {
      console.warn("No se pudo guardar el historial de chats:", e);
    }
  }

  function loadNormalChatStore() {
    try {
      const rawChats = JSON.parse(localStorage.getItem(NORMAL_CHATS_KEY) || "[]");
      normalChats = Array.isArray(rawChats) ? rawChats.filter(c => c && c.id) : [];
    } catch {
      normalChats = [];
    }
    try {
      const rawTabs = JSON.parse(localStorage.getItem(NORMAL_OPEN_TABS_KEY) || "[]");
      openNormalTabIds = Array.isArray(rawTabs)
        ? rawTabs.filter(id => normalChats.some(c => c.id === id))
        : [];
    } catch {
      openNormalTabIds = [];
    }
    const savedActive = localStorage.getItem(NORMAL_ACTIVE_ID_KEY);

    if (normalChats.length === 0) {
      const first = {
        id: makeNormalChatId(),
        title: "Nuevo chat",
        createdAt: Date.now(),
        updatedAt: Date.now(),
        messages: []
      };
      normalChats.push(first);
      openNormalTabIds = [first.id];
      activeNormalChatId = first.id;
      saveNormalChatStore();
    } else {
      if (openNormalTabIds.length === 0) {
        openNormalTabIds = [normalChats[0].id];
      }
      activeNormalChatId = (savedActive && normalChats.some(c => c.id === savedActive))
        ? savedActive
        : openNormalTabIds[0];
      if (!openNormalTabIds.includes(activeNormalChatId)) {
        openNormalTabIds.push(activeNormalChatId);
      }
    }
  }

  function getActiveNormalChat() {
    let chat = normalChats.find(c => c.id === activeNormalChatId);
    if (!chat) {
      loadNormalChatStore();
      chat = normalChats.find(c => c.id === activeNormalChatId) || normalChats[0];
    }
    return chat;
  }

  function createNormalChat(switchTo = true) {
    const newChat = {
      id: makeNormalChatId(),
      title: "Nuevo chat",
      createdAt: Date.now(),
      updatedAt: Date.now(),
      messages: []
    };
    normalChats.unshift(newChat);
    if (!openNormalTabIds.includes(newChat.id)) {
      openNormalTabIds.push(newChat.id);
    }
    if (switchTo) {
      activeNormalChatId = newChat.id;
    }
    saveNormalChatStore();
    renderNormalChatTabs();
    renderNormalHistoryDrawer();
    renderNormalChatThread();
    return newChat;
  }

  function openNormalChatInTab(chatId) {
    const exists = normalChats.find(c => c.id === chatId);
    if (!exists) return;
    if (!openNormalTabIds.includes(chatId)) {
      openNormalTabIds.push(chatId);
    }
    activeNormalChatId = chatId;
    saveNormalChatStore();
    renderNormalChatTabs();
    renderNormalHistoryDrawer();
    renderNormalChatThread();
  }

  function closeNormalChatTab(chatId) {
    openNormalTabIds = openNormalTabIds.filter(id => id !== chatId);
    if (openNormalTabIds.length === 0) {
      createNormalChat(true);
      return;
    }
    if (activeNormalChatId === chatId) {
      activeNormalChatId = openNormalTabIds[openNormalTabIds.length - 1];
    }
    saveNormalChatStore();
    renderNormalChatTabs();
    renderNormalHistoryDrawer();
    renderNormalChatThread();
  }

  function deleteNormalChat(chatId) {
    normalChats = normalChats.filter(c => c.id !== chatId);
    openNormalTabIds = openNormalTabIds.filter(id => id !== chatId);
    if (normalChats.length === 0) {
      createNormalChat(true);
      return;
    }
    if (openNormalTabIds.length === 0) {
      openNormalTabIds = [normalChats[0].id];
    }
    if (activeNormalChatId === chatId) {
      activeNormalChatId = openNormalTabIds[0];
    }
    saveNormalChatStore();
    renderNormalChatTabs();
    renderNormalHistoryDrawer();
    renderNormalChatThread();
  }

  function renderNormalChatTabs() {
    if (!normalChatTabs) return;
    normalChatTabs.innerHTML = "";
    openNormalTabIds.forEach(id => {
      const chat = normalChats.find(c => c.id === id);
      if (!chat) return;
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = `normal-chat-tab${chat.id === activeNormalChatId ? " active" : ""}`;
      tab.title = chat.title;

      const titleSpan = document.createElement("span");
      titleSpan.className = "normal-chat-tab-title";
      titleSpan.textContent = chat.title || "Nuevo chat";
      tab.appendChild(titleSpan);

      const closeSpan = document.createElement("span");
      closeSpan.className = "normal-chat-tab-close";
      closeSpan.title = "Cerrar pestaña";
      closeSpan.textContent = "×";
      closeSpan.addEventListener("click", (e) => {
        e.stopPropagation();
        closeNormalChatTab(chat.id);
      });
      tab.appendChild(closeSpan);

      tab.addEventListener("click", () => {
        openNormalChatInTab(chat.id);
        agentTextarea.focus();
      });

      normalChatTabs.appendChild(tab);
    });
  }

  function renderNormalHistoryDrawer() {
    if (!normalHistoryList) return;
    normalHistoryList.innerHTML = "";
    const sorted = [...normalChats].sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
    if (sorted.length === 0) {
      normalHistoryList.innerHTML = `<div class="normal-history-empty">No hay conversaciones guardadas.</div>`;
      return;
    }
    sorted.forEach(chat => {
      const row = document.createElement("div");
      row.className = `normal-history-item${chat.id === activeNormalChatId ? " active" : ""}`;

      const meta = document.createElement("div");
      meta.className = "normal-history-meta";

      const title = document.createElement("div");
      title.className = "normal-history-title";
      title.textContent = chat.title || "Nuevo chat";

      const msgCount = (chat.messages || []).length;
      const sub = document.createElement("div");
      sub.className = "normal-history-sub";
      sub.textContent = `${msgCount} mensaje${msgCount === 1 ? "" : "s"} · ${formatShortTime(chat.updatedAt || chat.createdAt)}`;

      meta.appendChild(title);
      meta.appendChild(sub);
      row.appendChild(meta);

      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "normal-history-del";
      delBtn.title = "Eliminar conversación";
      delBtn.innerHTML = `${svgIcon("trash", 12)}`;
      delBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteNormalChat(chat.id);
      });
      row.appendChild(delBtn);

      row.addEventListener("click", () => {
        openNormalChatInTab(chat.id);
        if (normalHistoryDrawer) normalHistoryDrawer.classList.add("hidden");
        if (btnToggleNormalHistory) btnToggleNormalHistory.classList.remove("active");
        agentTextarea.focus();
      });

      normalHistoryList.appendChild(row);
    });
  }

  function renderNormalChatThread() {
    if (!chatMessages) return;
    const chat = getActiveNormalChat();
    chatMessages.innerHTML = "";

    if (!chat.messages || chat.messages.length === 0) {
      const empty = document.createElement("div");
      empty.className = "chat-empty-state";
      empty.innerHTML = `
        <h3>Chat normal</h3>
        <p>Conversación independiente sin modificar archivos del proyecto. Tu historial se guarda automáticamente.</p>
        <div class="starters">
          <button class="sample-btn normal-sample-btn" data-prompt="Explícame de forma clara cómo funciona HTTPS y TLS paso a paso.">
            <svg class="ico sm" aria-hidden="true"><use href="#i-plus" /></svg>Explicar un concepto
          </button>
          <button class="sample-btn normal-sample-btn" data-prompt="Ayúdame a redactar un correo profesional breve y directo.">
            <svg class="ico sm" aria-hidden="true"><use href="#i-plus" /></svg>Redactar un texto
          </button>
          <button class="sample-btn normal-sample-btn" data-prompt="Dame ideas para optimizar una consulta SQL lenta.">
            <svg class="ico sm" aria-hidden="true"><use href="#i-plus" /></svg>Consulta técnica
          </button>
        </div>
      `;
      empty.querySelectorAll(".normal-sample-btn").forEach(btn => {
        btn.addEventListener("click", () => {
          agentTextarea.value = btn.dataset.prompt;
          handleAgentSend();
        });
      });
      chatMessages.appendChild(empty);
      return;
    }

    chat.messages.forEach(msg => {
      const text = extractTextFromContent(msg.content);
      const imgSrc = msg.imgSrc || extractImageFromContent(msg.content);
      const info = appendAgentMsg(msg.role, msg.role === "assistant" ? "" : text, imgSrc, chatMessages);
      if (msg.role === "assistant") {
        renderAgentBubble(info.bubble, msg.thinking || "", text || "");
      }
    });
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function getActiveThreadEl() {
    return currentMode === "agent" ? agentMessages : chatMessages;
  }

  function getActiveHistory() {
    if (currentMode === "agent") return agentHistory;
    return getActiveNormalChat().messages;
  }

  function switchAgentMode(mode) {
    currentMode = mode;
    const isAgent = mode === "agent";
    modeAgentBtn.classList.toggle("active", isAgent);
    modeChatBtn.classList.toggle("active", !isAgent);

    if (agentMessages) agentMessages.classList.toggle("hidden", !isAgent);
    if (chatMessages) chatMessages.classList.toggle("hidden", isAgent);
    if (normalChatBar) normalChatBar.classList.toggle("hidden", isAgent);
    if (isAgent && normalHistoryDrawer) {
      normalHistoryDrawer.classList.add("hidden");
      if (btnToggleNormalHistory) btnToggleNormalHistory.classList.remove("active");
    }

    if (continueBanner) {
      continueBanner.classList.toggle("hidden", continueMode !== mode || !continueBanner.dataset.visible);
    }

    agentTextarea.placeholder = isAgent
      ? "Pide una app (ej. 'Crea un juego de Snake en HTML') o modificar código..."
      : "Escribe tu pregunta normal aquí... (Enter para enviar, Shift+Enter para salto)";
    agentTextarea.focus();
  }

  loadNormalChatStore();
  renderNormalChatTabs();
  renderNormalHistoryDrawer();
  renderNormalChatThread();

  modeAgentBtn.addEventListener("click", () => switchAgentMode("agent"));
  modeChatBtn.addEventListener("click", () => switchAgentMode("chat"));

  if (btnNewNormalChat) {
    btnNewNormalChat.addEventListener("click", () => {
      if (currentMode !== "chat") switchAgentMode("chat");
      createNormalChat(true);
      agentTextarea.focus();
    });
  }

  if (btnToggleNormalHistory && normalHistoryDrawer) {
    btnToggleNormalHistory.addEventListener("click", () => {
      const opening = normalHistoryDrawer.classList.contains("hidden");
      normalHistoryDrawer.classList.toggle("hidden", !opening);
      btnToggleNormalHistory.classList.toggle("active", opening);
      if (opening) renderNormalHistoryDrawer();
    });
  }

  if (btnCloseNormalHistory && normalHistoryDrawer) {
    btnCloseNormalHistory.addEventListener("click", () => {
      normalHistoryDrawer.classList.add("hidden");
      if (btnToggleNormalHistory) btnToggleNormalHistory.classList.remove("active");
    });
  }

  if (btnOpenChatTab) {
    btnOpenChatTab.addEventListener("click", () => {
      saveNormalChatStore();
      window.open("/static/chat.html", "_blank");
    });
  }

  window.addEventListener("storage", (e) => {
    if (e.key === NORMAL_CHATS_KEY || e.key === NORMAL_OPEN_TABS_KEY || e.key === NORMAL_ACTIVE_ID_KEY) {
      if (isGenerating && generatingMode === "chat") return;
      loadNormalChatStore();
      renderNormalChatTabs();
      renderNormalHistoryDrawer();
      renderNormalChatThread();
    }
  });

  // Toggle Búsqueda Web
  if (btnToggleWeb) {
    btnToggleWeb.addEventListener("click", () => {
      isWebSearchEnabled = !isWebSearchEnabled;
      btnToggleWeb.classList.toggle("off", !isWebSearchEnabled);
      btnToggleWeb.innerHTML = `${svgIcon("globe", 13)}<span>${isWebSearchEnabled ? "Web ON" : "Web OFF"}</span>`;
    });
  }

  // Toggle Modo Anónimo (Tor / Proxy SOCKS5/HTTP)
  async function loadProxyConfig() {
    try {
      const res = await fetch("/api/proxy/config");
      const cfg = await res.json();
      isAnonEnabled = !!cfg.enabled;
      updateAnonUi(cfg);
    } catch (e) {
      console.warn("Fallo cargando config de proxy:", e);
    }
  }

  function updateAnonUi(cfg) {
    if (chkProxyEnabled) chkProxyEnabled.checked = isAnonEnabled;
    if (chkProxyAutostart && cfg && cfg.auto_start !== undefined) chkProxyAutostart.checked = !!cfg.auto_start;
    if (proxyUrlInput && cfg && cfg.proxy_url) proxyUrlInput.value = cfg.proxy_url;

    if (btnToggleAnon) {
      btnToggleAnon.classList.toggle("off", !isAnonEnabled);
      btnToggleAnon.classList.toggle("anon-active", isAnonEnabled);
      btnToggleAnon.innerHTML = `${svgIcon("shield", 13)}<span>${isAnonEnabled ? "Anon ON" : "Anon OFF"}</span>`;
      btnToggleAnon.title = isAnonEnabled ? "Modo Anónimo ACTIVO (Tor / Proxy enrutado)" : "Modo Anónimo DESACTIVADO (Peticiones directas)";
    }
  }

  async function saveProxySettings(newEnabled, newAutoStart, newUrl) {
    try {
      const res = await fetch("/api/proxy/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: newEnabled,
          auto_start: newAutoStart,
          proxy_url: newUrl
        })
      });
      const data = await res.json();
      if (data.success) {
        isAnonEnabled = data.config.enabled;
        updateAnonUi(data.config);
      }
    } catch (e) {
      console.warn("Error guardando proxy config:", e);
    }
  }

  if (btnToggleAnon) {
    btnToggleAnon.addEventListener("click", () => {
      // 1. Cambio visual optimista e inmediato
      isAnonEnabled = !isAnonEnabled;
      updateAnonUi();

      const currentUrl = (proxyUrlInput && proxyUrlInput.value ? proxyUrlInput.value.trim() : "") || "socks5://127.0.0.1:9050";
      const autoStart = chkProxyAutostart ? chkProxyAutostart.checked : false;
      saveProxySettings(isAnonEnabled, autoStart, currentUrl);

      // 2. Comprobar si Tor o el proxy están realmente escuchando
      if (isAnonEnabled) {
        fetch("/api/proxy/status").then(r => r.json()).then(data => {
          if (!data.is_alive && !data.detected_local_tor) {
            alert("🛡️ Modo Anónimo activado en la app.\n\nNota: Para que el tráfico salga por Tor debes tener Tor Service o Tor Browser abierto en tu PC (puerto 9050 o 9150), o configurar tu proxy en Ajustes.");
          }
        }).catch(() => {});
      }
    });
  }

  if (chkProxyEnabled) {
    chkProxyEnabled.addEventListener("change", () => {
      isAnonEnabled = chkProxyEnabled.checked;
      updateAnonUi();
      const currentUrl = (proxyUrlInput && proxyUrlInput.value ? proxyUrlInput.value.trim() : "") || "socks5://127.0.0.1:9050";
      const autoStart = chkProxyAutostart ? chkProxyAutostart.checked : false;
      saveProxySettings(isAnonEnabled, autoStart, currentUrl);
    });
  }

  if (btnSaveProxy) {
    btnSaveProxy.addEventListener("click", async () => {
      const enabled = chkProxyEnabled ? chkProxyEnabled.checked : isAnonEnabled;
      const autoStart = chkProxyAutostart ? chkProxyAutostart.checked : false;
      const url = (proxyUrlInput ? proxyUrlInput.value.trim() : "") || "socks5://127.0.0.1:9050";
      await saveProxySettings(enabled, autoStart, url);
      alert("Configuración de Proxy guardada con éxito.");
    });
  }

  if (btnTestProxy) {
    btnTestProxy.addEventListener("click", async () => {
      if (proxyStatusLine) proxyStatusLine.textContent = "Comprobando conexión a Tor / Proxy...";
      btnTestProxy.innerHTML = `${svgIcon("loader", 12, "spin")}<span>Comprobando...</span>`;
      try {
        const res = await fetch("/api/proxy/status");
        const data = await res.json();
        if (data.is_alive) {
          proxyStatusLine.innerHTML = `<span style="color:#10B981;">✅ Conectado exitosamente. IP pública oculta: ${data.current_ip || 'Anónima'}</span>`;
        } else if (data.detected_local_tor) {
          proxyStatusLine.innerHTML = `<span style="color:#c084fc;">🧅 Tor local detectado en ${data.detected_local_tor}. Listo para activar.</span>`;
        } else {
          proxyStatusLine.innerHTML = `<span style="color:#f59e0b;">⚠️ ${data.message || 'Sin conexión a proxy'}. Inicia Tor o verifica el puerto.</span>`;
        }
      } catch (err) {
        if (proxyStatusLine) proxyStatusLine.textContent = "Error al verificar proxy: " + err.message;
      } finally {
        btnTestProxy.innerHTML = `<svg class="ico sm" aria-hidden="true"><use href="#i-check" /></svg><span>Comprobar estado</span>`;
      }
    });
  }

  // Toggle Trabajadores Nube Multi-Agente (Gemini Flash / Bedrock / OpenRouter)
  let hasCloudApiKey = false;

  async function loadCloudConfig() {
    try {
      const res = await fetch("/api/cloud/config");
      const cfg = await res.json();
      isCloudWorkersEnabled = !!cfg.enabled;
      hasCloudApiKey = !!cfg.has_api_key;
      updateCloudUi(cfg);
    } catch (e) {
      console.warn("Fallo cargando config de trabajadores nube:", e);
    }
  }

  const CLOUD_MODEL_PRESETS = {
    bedrock: [
      { id: "amazon.nova-pro-v1:0", label: "Amazon Nova Pro (Recomendado · Apps/Código)" },
      { id: "us.anthropic.claude-3-5-sonnet-20241022-v2:0", label: "Claude 3.5 Sonnet v2 (Máxima calidad)" },
      { id: "amazon.nova-lite-v1:0", label: "Amazon Nova Lite (Balanceado)" },
      { id: "amazon.nova-micro-v1:0", label: "Amazon Nova Micro (Ultra económico)" },
    ],
    gemini: [
      { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash (Recomendado · Rápido y potente)" },
      { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro (Máxima calidad de código)" },
      { id: "gemini-2.0-flash", label: "Gemini 2.0 Flash (Económico)" },
      { id: "gemini-1.5-flash", label: "Gemini 1.5 Flash (Clásico)" },
    ],
    openrouter: [
      { id: "deepseek/deepseek-r1", label: "DeepSeek R1 (Razonamiento y código)" },
      { id: "qwen/qwen-2.5-coder-32b-instruct", label: "Qwen 2.5 Coder 32B (Especialista código)" },
      { id: "meta-llama/llama-3.3-70b-instruct", label: "Llama 3.3 70B Instruct" },
      { id: "anthropic/claude-3.5-sonnet", label: "Claude 3.5 Sonnet (vía OpenRouter)" },
    ],
  };

  function populateCloudModelOptions(provider, desiredModel = "") {
    if (!cloudModelInput) return;
    const prov = CLOUD_MODEL_PRESETS[provider] ? provider : "bedrock";
    const list = CLOUD_MODEL_PRESETS[prov];
    cloudModelInput.innerHTML = "";
    list.forEach(item => {
      const opt = document.createElement("option");
      opt.value = item.id;
      opt.textContent = item.label;
      cloudModelInput.appendChild(opt);
    });
    if (desiredModel) {
      const exists = list.some(item => item.id === desiredModel);
      if (!exists) {
        const customOpt = document.createElement("option");
        customOpt.value = desiredModel;
        customOpt.textContent = `${desiredModel} (Actual)`;
        cloudModelInput.appendChild(customOpt);
      }
      cloudModelInput.value = desiredModel;
    } else {
      cloudModelInput.value = list[0].id;
    }
  }

  if (cloudProviderSelect) {
    cloudProviderSelect.addEventListener("change", () => {
      populateCloudModelOptions(cloudProviderSelect.value, "");
    });
    populateCloudModelOptions(cloudProviderSelect.value || "bedrock", "");
  }

  function updateCloudUi(cfg) {
    if (cfg && cfg.has_api_key !== undefined) hasCloudApiKey = !!cfg.has_api_key;
    if (chkCloudEnabled) chkCloudEnabled.checked = isCloudWorkersEnabled;
    if (cloudProviderSelect && cfg && cfg.provider) cloudProviderSelect.value = cfg.provider;
    if (cloudApiKeyInput && cfg && cfg.api_key_masked) cloudApiKeyInput.placeholder = cfg.api_key_masked;
    const activeProv = cloudProviderSelect ? cloudProviderSelect.value : (cfg && cfg.provider ? cfg.provider : "bedrock");
    populateCloudModelOptions(activeProv, cfg && cfg.model ? cfg.model : "");

    if (btnToggleWorkers) {
      btnToggleWorkers.classList.toggle("off", !isCloudWorkersEnabled);
      btnToggleWorkers.innerHTML = `${svgIcon("bolt", 13)}<span>${isCloudWorkersEnabled ? "Cloud ON" : "Cloud OFF"}</span>`;
      btnToggleWorkers.title = isCloudWorkersEnabled
        ? "Multi-Agente ACTIVO: Cerebro Qwen Local + Trabajador Nube"
        : "Multi-Agente DESACTIVADO: Solo Qwen Local";
    }
  }

  async function saveCloudSettings(enabled, provider, apiKey, model) {
    try {
      const res = await fetch("/api/cloud/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: enabled,
          provider: provider,
          api_key: apiKey,
          model: model,
          auto_delegate_coding: true
        })
      });
      const data = await res.json();
      if (data.success) {
        isCloudWorkersEnabled = data.config.enabled;
        hasCloudApiKey = !!data.config.has_api_key;
        updateCloudUi(data.config);
      }
    } catch (e) {
      console.warn("Error guardando cloud config:", e);
    }
  }

  if (btnToggleWorkers) {
    btnToggleWorkers.addEventListener("click", () => {
      isCloudWorkersEnabled = !isCloudWorkersEnabled;
      updateCloudUi();

      const prov = cloudProviderSelect ? cloudProviderSelect.value : "bedrock";
      const key = cloudApiKeyInput ? cloudApiKeyInput.value.trim() : "";
      const mdl = cloudModelInput && cloudModelInput.value ? cloudModelInput.value.trim() : (prov === "bedrock" ? "amazon.nova-micro-v1:0" : "gemini-1.5-flash");

      if (isCloudWorkersEnabled && !hasCloudApiKey && prov !== "bedrock") {
        const actSettings = document.getElementById("act-settings");
        if (actSettings) actSettings.click();
        if (cloudStatusLine) cloudStatusLine.innerHTML = `<span style="color:#f59e0b;">⚠️ Ingresa tu API Key para activar Cloud.</span>`;
      }

      saveCloudSettings(isCloudWorkersEnabled, prov, key, mdl);
    });
  }

  if (btnSaveCloud) {
    btnSaveCloud.addEventListener("click", () => {
      const enabled = chkCloudEnabled ? chkCloudEnabled.checked : isCloudWorkersEnabled;
      const prov = cloudProviderSelect ? cloudProviderSelect.value : "gemini";
      const key = cloudApiKeyInput ? cloudApiKeyInput.value.trim() : "";
      const mdl = cloudModelInput && cloudModelInput.value ? cloudModelInput.value.trim() : "gemini-1.5-flash";
      saveCloudSettings(enabled, prov, key, mdl);
      if (cloudStatusLine) cloudStatusLine.innerHTML = `<span style="color:#10B981;">✅ Ajustes del trabajador guardados con éxito.</span>`;
    });
  }

  if (btnTestCloud) {
    btnTestCloud.addEventListener("click", async () => {
      if (cloudStatusLine) cloudStatusLine.textContent = "Probando conexión con el trabajador...";
      btnTestCloud.innerHTML = `${svgIcon("loader", 12, "spin")}<span>Probando...</span>`;
      try {
        const res = await fetch("/api/cloud/test", { method: "POST" });
        const data = await res.json();
        if (data.success) {
          if (cloudStatusLine) cloudStatusLine.innerHTML = `<span style="color:#10B981;">✅ Conexión exitosa. Trabajador listo para generar código.</span>`;
        } else {
          if (cloudStatusLine) cloudStatusLine.innerHTML = `<span style="color:#f59e0b;">⚠️ ${data.error || 'No respondió'}. Revisa tu API Key.</span>`;
        }
      } catch (e) {
        if (cloudStatusLine) cloudStatusLine.innerHTML = `<span style="color:#ef4444;">❌ Error de conexión al probar API.</span>`;
      } finally {
        btnTestCloud.innerHTML = `<svg class="ico sm" aria-hidden="true"><use href="#i-check" /></svg><span>Probar API</span>`;
      }
    });
  }

  // Sample prompt buttons
  document.querySelectorAll(".sample-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      agentTextarea.value = btn.dataset.prompt;
      handleAgentSend();
    });
  });

  if (btnWelcomeAskAgent) {
    btnWelcomeAskAgent.addEventListener("click", () => {
      agentTextarea.focus();
    });
  }

  // Soporte de imágenes
  btnUploadImage.addEventListener("click", () => imageInput.click());
  imageInput.addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) handleImageFile(f);
  });
  btnCancelImage.addEventListener("click", () => {
    currentAttachedImageBase64 = null;
    imageInput.value = "";
    imagePreviewBox.classList.add("hidden");
  });
  agentTextarea.addEventListener("paste", (e) => {
    const items = (e.clipboardData || e.originalEvent.clipboardData).items;
    for (const item of items) {
      if (item.type.indexOf("image") === 0) {
        handleImageFile(item.getAsFile());
        break;
      }
    }
  });

  function handleImageFile(file) {
    const reader = new FileReader();
    reader.onload = (e) => {
      currentAttachedImageBase64 = e.target.result;
      imagePreviewImg.src = currentAttachedImageBase64;
      imagePreviewBox.classList.remove("hidden");
    };
    reader.readAsDataURL(file);
  }

  // Detección de scroll del usuario: si el usuario sube a leer, no forzar auto-scroll hacia abajo
  [agentMessages, chatMessages].filter(Boolean).forEach(threadEl => {
    threadEl.addEventListener("scroll", () => {
      if (threadEl !== getActiveThreadEl()) return;
      const distanceToBottom = threadEl.scrollHeight - threadEl.scrollTop - threadEl.clientHeight;
      userScrolledUp = distanceToBottom > 80;
    });
  });

  // Enviar mensaje al Agente
  btnSendAgent.addEventListener("click", handleAgentSend);
  agentTextarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleAgentSend();
    }
  });

  function handleAgentSend() {
    if (isGenerating) return;
    const text = agentTextarea.value.trim();
    if (!text && !currentAttachedImageBase64) return;

    continueBanner.classList.add("hidden");
    delete continueBanner.dataset.visible;
    continueMode = null;

    const targetThread = getActiveThreadEl();
    if (currentMode === "chat") {
      const emptyState = targetThread.querySelector(".chat-empty-state");
      if (emptyState) emptyState.remove();
    }

    let historyItem;
    if (currentAttachedImageBase64) {
      appendAgentMsg("user", text || "Analiza esta imagen adjunta.", currentAttachedImageBase64, targetThread);
      historyItem = {
        role: "user",
        content: [
          { type: "text", text: text || "Analiza esta imagen con tu proyector multimodal." },
          { type: "image_url", image_url: { url: currentAttachedImageBase64 } }
        ]
      };
      currentAttachedImageBase64 = null;
      imageInput.value = "";
      imagePreviewBox.classList.add("hidden");
    } else {
      appendAgentMsg("user", text, null, targetThread);
      historyItem = { role: "user", content: text };
    }

    userScrolledUp = false;
    if (currentMode === "agent") {
      agentHistory.push(historyItem);
    } else {
      const chat = getActiveNormalChat();
      if ((!chat.messages || chat.messages.length === 0) && (!chat.title || chat.title === "Nuevo chat")) {
        chat.title = deriveChatTitle(text || "Imagen adjunta");
      }
      chat.messages.push(historyItem);
      chat.updatedAt = Date.now();
      saveNormalChatStore();
      renderNormalChatTabs();
      renderNormalHistoryDrawer();
    }
    agentTextarea.value = "";
    agentTextarea.style.height = "auto";

    startAgentGeneration();
  }

  btnContinueGen.addEventListener("click", () => {
    continueBanner.classList.add("hidden");
    delete continueBanner.dataset.visible;
    const activeHist = getActiveHistory();
    activeHist.push({
      role: "user",
      content: "Continúa generando el código o respuesta exactamente desde donde te quedaste. No repitas lo anterior."
    });
    if (currentMode === "chat") {
      const chat = getActiveNormalChat();
      chat.updatedAt = Date.now();
      saveNormalChatStore();
    }
    startAgentGeneration(true, 0);
  });

  async function startAgentGeneration(isContinuation = false, autoContinueCount = 0) {
    const runMode = currentMode;
    const runThreadEl = runMode === "agent" ? agentMessages : chatMessages;
    const runNormalChat = runMode === "chat" ? getActiveNormalChat() : null;
    const runHistory = runMode === "agent" ? agentHistory : runNormalChat.messages;

    isGenerating = true;
    generatingMode = runMode;
    btnSendAgent.classList.add("hidden");
    btnStopAgent.classList.remove("hidden");
    statusSpeedText.textContent = isContinuation ? "Continuando respuesta..." : "Generando...";

    abortController = new AbortController();
    let willAutoContinue = false;
    let streamFinishReason = null;
    let chunkContentChars = 0;

    let bubble;
    if (isContinuation && lastAssistantMsgDiv && lastAssistantMsgDiv.parentElement === runThreadEl) {
      bubble = lastAssistantMsgDiv.querySelector(".msg-bubble-content");
    } else {
      lastAssistantMsgContent = "";
      lastAssistantThinking = "";
      lastAssistantOrchestratorSteps = [];
      const divInfo = appendAgentMsg("assistant", "", null, runThreadEl);
      lastAssistantMsgDiv = divInfo.wrapper;
      bubble = divInfo.bubble;
      // Mostrar indicador visual animado contextual
      const loaderText = runMode === "agent" ? "Analizando proyecto y procesando..." : "Pensando respuesta...";
      bubble.innerHTML = `<div class="thinking-loader">${svgIcon("loader", 14, "spin")}<span>${loaderText}</span></div>`;
    }

    // Contexto de Antigravity: solo en modo Crear Apps
    const projectFiles = [];
    if (runMode === "agent") {
      document.querySelectorAll(".tree-item span").forEach(el => {
        const name = el.textContent.trim();
        if (name && !projectFiles.includes(name)) projectFiles.push(name);
      });
    }

    const apiMessages = runHistory.map(m => ({ role: m.role, content: m.content }));

    const payload = {
      messages: apiMessages,
      mode: runMode,
      web_search: isWebSearchEnabled,
      cloud_workers: isCloudWorkersEnabled,
      active_file: null,
      project_files: projectFiles,
      workspace_path: runMode === "agent" ? currentWorkspacePath : "",
      max_tokens: 16384, // Máxima longitud de generación permitida por el motor
      temperature: runMode === "agent" ? 0.45 : 0.5,
      stream: true
    };

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: abortController.signal
      });

      if (!response.ok) {
        const errText = await response.text();
        let errMsg = "Error en el servidor";
        try {
          const errData = JSON.parse(errText);
          errMsg = errData.detail || errData.message || errMsg;
        } catch {
          errMsg = errText || errMsg;
        }
        throw new Error(errMsg);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("data: ")) continue;
          const dataStr = trimmed.slice(6);
          if (dataStr === "[DONE]") break;

          try {
            const parsed = JSON.parse(dataStr);

            if (parsed.type === "terminal_event") {
              if (parsed.action === "open") {
                openTerminalDrawer();
                startTerminalPolling();
              }
              continue;
            }

            if (parsed.type === "cloud_worker_warning") {
              lastAssistantOrchestratorSteps.push({
                id: "cloud_warn",
                status: "done",
                title: `Aviso Cloud: ${parsed.warning}`
              });
              renderAgentBubble(bubble, lastAssistantThinking, lastAssistantMsgContent);
              continue;
            }

            if (parsed.type === "cloud_worker_event") {
              const existingIdx = lastAssistantOrchestratorSteps.findIndex(s => s.id === "cloud_init");
              const stepObj = {
                id: "cloud_init",
                status: "done",
                title: `Orquestador Qwen Local conectado con trabajador ${parsed.worker || "AWS Bedrock"}`
              };
              if (existingIdx >= 0) {
                lastAssistantOrchestratorSteps[existingIdx] = stepObj;
              } else {
                lastAssistantOrchestratorSteps.push(stepObj);
              }
              renderAgentBubble(bubble, lastAssistantThinking, lastAssistantMsgContent);
              continue;
            }

            if (parsed.type === "kg_event") {
              if (parsed.entities && parsed.entities.length > 0) {
                const displayNodes = parsed.entities.slice(0, 3).join(" • ");
                const existingIdx = lastAssistantOrchestratorSteps.findIndex(s => s.id === "kg_init");
                const stepObj = {
                  id: "kg_init",
                  status: "done",
                  title: `Grafo Dinámico STIX/MITRE + Reranker: ${displayNodes}`
                };
                if (existingIdx >= 0) {
                  lastAssistantOrchestratorSteps[existingIdx] = stepObj;
                } else {
                  lastAssistantOrchestratorSteps.push(stepObj);
                }
                renderAgentBubble(bubble, lastAssistantThinking, lastAssistantMsgContent);
              }
              continue;
            }

            if (parsed.type === "mcp_tool_event") {
              if (parsed.workspace_changed || (parsed.tools && parsed.tools.some(t => ["download_url", "write_file", "apply_patch", "delete_file"].includes(t.tool)))) {
                loadWorkspace().catch(() => {});
              }
              if (parsed.tools && parsed.tools.length > 0) {
                const toolSummaries = parsed.tools.map(t => t.summary || t.tool).slice(0, 2).join(" | ");
                const existingIdx = lastAssistantOrchestratorSteps.findIndex(s => s.id === "mcp_init");
                const stepObj = {
                  id: "mcp_init",
                  status: "done",
                  title: `MCP / Codebase Vector RAG: ${toolSummaries}`
                };
                if (existingIdx >= 0) {
                  lastAssistantOrchestratorSteps[existingIdx] = stepObj;
                } else {
                  lastAssistantOrchestratorSteps.push(stepObj);
                }
                renderAgentBubble(bubble, lastAssistantThinking, lastAssistantMsgContent);
              }
              continue;
            }

            if (parsed.type === "web_search_event") {
              const existingIdx = lastAssistantOrchestratorSteps.findIndex(s => s.id === "web_init");
              const stepObj = {
                id: "web_init",
                status: "done",
                title: "Búsqueda en tiempo real de Internet aplicada"
              };
              if (existingIdx >= 0) {
                lastAssistantOrchestratorSteps[existingIdx] = stepObj;
              } else {
                lastAssistantOrchestratorSteps.push(stepObj);
              }
              renderAgentBubble(bubble, lastAssistantThinking, lastAssistantMsgContent);
              continue;
            }

            if (parsed.type === "orchestrator_step") {
              const stepId = parsed.id || `step_${lastAssistantOrchestratorSteps.length}`;
              const existingIdx = lastAssistantOrchestratorSteps.findIndex(s => s.id === stepId);
              const stepObj = {
                id: stepId,
                status: parsed.status || "running",
                title: parsed.title || "",
                detail: parsed.detail || "",
                files: parsed.files || null
              };
              if (existingIdx >= 0) {
                lastAssistantOrchestratorSteps[existingIdx] = {
                  ...lastAssistantOrchestratorSteps[existingIdx],
                  ...stepObj,
                  files: stepObj.files || lastAssistantOrchestratorSteps[existingIdx].files
                };
              } else {
                lastAssistantOrchestratorSteps.push(stepObj);
              }
              renderAgentBubble(bubble, lastAssistantThinking, lastAssistantMsgContent);
              if (!userScrolledUp) {
                agentMessages.scrollTop = agentMessages.scrollHeight;
              }
              continue;
            }

            if (parsed.type === "error") {
              bubble.innerHTML += `<div class="msg-error">${svgIcon("warn", 13)}<span><b>Error:</b> ${parsed.error}</span></div>`;
              continue;
            }

            if (parsed.type === "meta") {
              statusSpeedText.textContent = `${parsed.tokens_per_sec} t/s`;
              streamFinishReason = parsed.finish_reason;
              continue;
            }

            const delta = parsed.choices?.[0]?.delta?.content || "";
            const reasoning = parsed.choices?.[0]?.delta?.reasoning_content || "";

            if (reasoning) lastAssistantThinking += reasoning;
            if (delta) {
              lastAssistantMsgContent += delta;
              chunkContentChars += delta.length;
            }

            if (delta || reasoning) {
              renderAgentBubble(bubble, lastAssistantThinking, lastAssistantMsgContent);
              // Solo bajar al final si el usuario no ha subido el scroll para leer
              if (!userScrolledUp && runThreadEl) {
                runThreadEl.scrollTop = runThreadEl.scrollHeight;
              }
            }
          } catch (e) { }
        }
      }

      // Sincronizar historial manteniendo alternancia válida user -> assistant
      const assistantEntry = runMode === "chat"
        ? { role: "assistant", content: lastAssistantMsgContent, thinking: lastAssistantThinking }
        : { role: "assistant", content: lastAssistantMsgContent };

      if (isContinuation) {
        if (runHistory.length > 0 && runHistory[runHistory.length - 1].role === "user") {
          runHistory.pop();
        }
        if (runHistory.length > 0 && runHistory[runHistory.length - 1].role === "assistant") {
          runHistory[runHistory.length - 1].content = lastAssistantMsgContent;
          if (runMode === "chat") runHistory[runHistory.length - 1].thinking = lastAssistantThinking;
        } else {
          runHistory.push(assistantEntry);
        }
      } else {
        runHistory.push(assistantEntry);
      }

      if (runMode === "chat" && runNormalChat) {
        runNormalChat.updatedAt = Date.now();
        saveNormalChatStore();
        renderNormalHistoryDrawer();
      }

      // Modo 'agent' (Crear Apps): Autocontinuación infinita y sin límites hasta completar el 100% del archivo
      if (runMode === "agent" && streamFinishReason === "length" && !abortController.signal.aborted && chunkContentChars > 20 && autoContinueCount < 15) {
        willAutoContinue = true;
        // Guardar progreso intermedio en disco para que el editor se actualice en vivo
        extractAndSaveAgentFiles(lastAssistantMsgContent, bubble).catch(() => {});
        statusSpeedText.textContent = `Generando sin límite (fase ${autoContinueCount + 2})...`;
        runHistory.push({
          role: "user",
          content: "Continúa generando el código o script exactamente desde el último carácter donde te quedaste. No repitas el código anterior, escribe el resto de la implementación hasta finalizarla por completo."
        });
      } else {
        continueBanner.classList.add("hidden");
        delete continueBanner.dataset.visible;
        // Procesar y guardar en disco automáticamente los archivos o descargas generados
        if (runMode === "agent" || /```(?:file:download|download)[:\s]+https?:\/\//i.test(lastAssistantMsgContent)) {
          await extractAndSaveAgentFiles(lastAssistantMsgContent, bubble);
        }
        if (runMode === "agent") {
          addManualSaveButton(bubble, lastAssistantMsgContent);
        }
      }

    } catch (err) {
      const loader = bubble.querySelector(".thinking-loader");
      if (loader) loader.remove();
      
      if (err.name === "AbortError") {
        if (!lastAssistantMsgContent.trim() && !lastAssistantThinking.trim()) {
          bubble.innerHTML = `<div class="stopped-badge">${svgIcon("stop", 11)}<span>Generación detenida por el usuario</span></div>`;
        } else {
          bubble.innerHTML += ` <span class="stopped-note">*[Detenido]*</span>`;
        }
      } else {
        bubble.innerHTML += `<div class="msg-error">${svgIcon("warn", 13)}<span><b>Error:</b> ${err.message}</span></div>`;
      }
    } finally {
      if (!willAutoContinue) {
        isGenerating = false;
        generatingMode = null;
        btnSendAgent.classList.remove("hidden");
        btnStopAgent.classList.add("hidden");
        statusSpeedText.textContent = "0.0 t/s";
      }
    }

    if (willAutoContinue) {
      await startAgentGeneration(true, autoContinueCount + 1);
    }
  }

  btnStopAgent.addEventListener("click", () => {
    if (abortController) abortController.abort();
    fetch("/api/chat/abort", { method: "POST" }).catch(() => {});
  });

  // Copiado al portapapeles con fallback robusto
  function fallbackCopyText(text, cb) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    ta.style.top = "-9999px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      document.execCommand("copy");
      if (cb) cb();
    } catch (e) {
      console.error("Error fallback copy:", e);
    }
    document.body.removeChild(ta);
  }

  // Delegación de eventos para copiar comandos o código de cualquier celda (en ambos chats)
  agentPanel.addEventListener("click", (e) => {
    const btn = e.target.closest(".code-copy-btn");
    if (!btn) return;
    const pre = btn.closest("pre");
    const code = pre ? pre.querySelector("code") : null;
    const textToCopy = code ? (code.innerText || code.textContent || "").trim() : "";
    if (!textToCopy) return;

    const onSuccess = () => {
      btn.classList.add("copied");
      btn.innerHTML = `${svgIcon("check", 12)}<span>¡Copiado!</span>`;
      setTimeout(() => {
        btn.innerHTML = `${svgIcon("copy", 12)}<span>Copiar</span>`;
        btn.classList.remove("copied");
      }, 2000);
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(textToCopy).then(onSuccess).catch(() => {
        fallbackCopyText(textToCopy, onSuccess);
      });
    } else {
      fallbackCopyText(textToCopy, onSuccess);
    }
  });

  // Inserta barra de cabecera con lenguaje, botón de copiar y envuelve bloques largos/archivos en un Combo Box plegable estilo Antigravity
  function enhanceCodeBlocks(container) {
    if (!container) return;
    if (!container._openCodeAccordions) {
      container._openCodeAccordions = new Set();
    }
    const pres = container.querySelectorAll("pre");
    pres.forEach((pre, idx) => {
      if (pre.querySelector(".code-block-header")) return;

      const code = pre.querySelector("code");
      let lang = "";
      let fileNameLabel = "";
      let totalLines = 1;

      if (code) {
        const rawCodeText = (code.textContent || "").trim();
        totalLines = rawCodeText ? rawCodeText.split(/\r?\n/).length : 1;

        const cls = code.className || "";
        const fileMatch = cls.match(/language-file:([^\s]+)/i);
        const langMatch = cls.match(/language-([a-zA-Z0-9_\-\.:\/]+)/);

        if (fileMatch) {
          fileNameLabel = fileMatch[1].trim();
          lang = fileNameLabel.split(".").pop() || "código";
        } else if (langMatch) {
          const token = langMatch[1];
          if (token.toLowerCase().startsWith("file:")) {
            fileNameLabel = token.slice(5);
            lang = fileNameLabel.split(".").pop() || "código";
          } else if (token.includes(".") && /^[a-zA-Z0-9_\-\/]+\.[a-zA-Z0-9]+$/.test(token)) {
            fileNameLabel = token;
            lang = token.split(".").pop() || "código";
          } else {
            lang = token;
          }
        } else {
          const sample = rawCodeText.slice(0, 150).toLowerCase();
          if (sample.includes("apt") || sample.includes("sudo") || sample.includes("grep") ||
              sample.includes("iwconfig") || sample.includes("ifconfig") || sample.includes("cat ") ||
              sample.includes("chmod") || sample.includes("systemctl") || sample.includes("pip install") ||
              sample.includes("lsusb") || sample.includes("dmesg") || sample.includes("airmon-ng") ||
              sample.includes("lspci") || sample.includes("uname")) {
            lang = "bash";
          } else {
            lang = "código";
          }
        }
      }

      const header = document.createElement("div");
      header.className = "code-block-header";
      header.innerHTML = `
        <span class="code-block-lang">${svgIcon("code", 12)}<span>${fileNameLabel || lang || "terminal"}</span></span>
        <button class="code-copy-btn" title="Copiar al portapapeles" type="button">
          ${svgIcon("copy", 12)}
          <span>Copiar</span>
        </button>
      `;
      pre.insertBefore(header, pre.firstChild);

      // Si es un archivo de código generado o un bloque de más de 5 líneas, envolver en Combo Box desplegable (oculto por defecto)
      const shouldWrapInCombo = Boolean(fileNameLabel) || totalLines > 5 || currentMode === "agent";
      if (shouldWrapInCombo && !pre.closest("details.code-accordion")) {
        const accKey = fileNameLabel || `code_block_${idx}`;
        const details = document.createElement("details");
        details.className = "code-accordion";
        details.dataset.accKey = accKey;
        if (container._openCodeAccordions.has(accKey)) {
          details.open = true;
        }

        const summary = document.createElement("summary");
        summary.className = "code-accordion-summary";
        const displayTitle = fileNameLabel
          ? `Código generado: ${fileNameLabel}`
          : `Bloque de código (${lang || "script"})`;
        summary.innerHTML = `
          <div class="code-acc-left">
            ${svgIcon("chevron", 12, "acc-chevron")}
            ${svgIcon("code", 13, "acc-code-ico")}
            <span class="code-acc-title">${displayTitle}</span>
            <span class="code-acc-meta">${totalLines} línea${totalLines === 1 ? "" : "s"}</span>
          </div>
          <span class="code-acc-toggle-hint">Ver / Ocultar código</span>
        `;

        details.addEventListener("toggle", () => {
          if (details.open) {
            container._openCodeAccordions.add(accKey);
          } else {
            container._openCodeAccordions.delete(accKey);
          }
        });

        pre.parentNode.insertBefore(details, pre);
        details.appendChild(summary);
        details.appendChild(pre);
      }
    });
  }

  function buildOrchestratorFeedHtml(steps) {
    if (!steps || steps.length === 0) return "";
    const hasRunning = steps.some(s => s.status === "running");
    const lastStep = steps[steps.length - 1];
    const headerStatusText = hasRunning
      ? (lastStep?.title || "Orquestador trabajando...")
      : `Orquestación completada (${steps.length} pasos ejecutados)`;
    const headerIcon = hasRunning
      ? svgIcon("loader", 12, "spin")
      : svgIcon("check", 12);

    let itemsHtml = "";
    for (const s of steps) {
      const ico = s.status === "running"
        ? svgIcon("loader", 11, "spin")
        : svgIcon("check", 11);
      let filesSublist = "";
      if (Array.isArray(s.files) && s.files.length > 0) {
        const lis = s.files.map(f => {
          const badge = f.action === "actualizar" ? "actualizar" : "nuevo";
          return `<li><code>${f.path}</code> <span class="orch-file-tag">(${badge})</span> — ${f.description || ""}</li>`;
        }).join("");
        filesSublist = `<ul class="orch-files-list">${lis}</ul>`;
      }
      itemsHtml += `
        <div class="orch-step-row ${s.status}">
          <span class="orch-step-ico">${ico}</span>
          <div class="orch-step-body">
            <div class="orch-step-title">${s.title}</div>
            ${s.detail ? `<div class="orch-step-detail">${s.detail}</div>` : ""}
            ${filesSublist}
          </div>
        </div>
      `;
    }

    return `
      <details class="thought-box orch-activity-box" open>
        <summary>
          ${svgIcon("chevron", 12)}
          <span class="orch-summary-ico">${headerIcon}</span>
          <span class="orch-summary-title">${headerStatusText}</span>
        </summary>
        <div class="thought-content orch-steps-container">
          ${itemsHtml}
        </div>
      </details>
    `;
  }

  function renderAgentBubble(bubble, thinking, content) {
    // Preservar badges superiores (Cloud, KG, MCP, Web) y estado de combo boxes abiertos
    const savedBadges = Array.from(bubble.querySelectorAll(":scope > .cloud-badge, :scope > .cloud-warn-badge, :scope > .kg-badge, :scope > .web-search-badge"));

    let html = "";
    if (lastAssistantOrchestratorSteps && lastAssistantOrchestratorSteps.length > 0) {
      html += buildOrchestratorFeedHtml(lastAssistantOrchestratorSteps);
    }
    if (thinking) {
      html += `<details class="thought-box" open><summary>${svgIcon("chevron", 12)}<span>Proceso de pensamiento</span></summary><div class="thought-content">${marked.parse(thinking)}</div></details>`;
    }
    if (content) {
      html += marked.parse(content);
    }
    bubble.innerHTML = html;
    // Reinsertar badges al inicio
    for (let i = savedBadges.length - 1; i >= 0; i--) {
      bubble.insertBefore(savedBadges[i], bubble.firstChild);
    }
    enhanceCodeBlocks(bubble);
  }

  // EXTRACTOR Y GUARDADOR AUTOMÁTICO DE ARCHIVOS (ESTILO ANTIGRAVITY)
  async function extractAndSaveAgentFiles(fullText, bubbleContainer) {
    const detectedFiles = parseCodeBlocks(fullText);

    let savedFiles = [];
    for (const file of detectedFiles) {
      try {
        const res = await fetch("/api/workspace/file/write", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: file.path, content: file.content, workspace_path: currentWorkspacePath })
        });
        const data = await res.json();
        if (data.success) {
          // Compilar y validar el archivo inmediatamente con AST/py_compile en el backend
          let compInfo = null;
          try {
            const compRes = await fetch("/api/workspace/file/compile", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ path: file.path, workspace_path: currentWorkspacePath })
            });
            compInfo = await compRes.json();
          } catch (cErr) {
            console.warn("Error en compilación:", cErr);
          }

          savedFiles.push({
            path: file.path,
            patched: !!data.patched,
            patches_applied: data.patches_applied || 1,
            compile: compInfo
          });
        } else {
          // Si el parche parcial no coincidió exactamente, dar opción con un clic de forzar el guardado
          const failCard = document.createElement("div");
          failCard.className = "file-action-card warn";
          failCard.innerHTML = `
            <div class="file-action-info">
              ${svgIcon("warn", 15)}
              <div class="file-action-text">
                <span class="file-action-name">${file.path}</span>
                <span class="file-action-status">${data.message || data.detail || "No se pudo aplicar automáticamente"}</span>
              </div>
            </div>
            <button class="file-action-btn btn-force-write">${svgIcon("save", 12)}<span>Forzar guardado</span></button>
          `;
          failCard.querySelector(".btn-force-write").addEventListener("click", async (e) => {
            const btnEl = e.currentTarget;
            btnEl.innerHTML = `${svgIcon("loader", 12, "spin")}<span>Guardando...</span>`;
            try {
              const fRes = await fetch("/api/workspace/file/write", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: file.path, content: file.content, force: true, workspace_path: currentWorkspacePath })
              });
              const fData = await fRes.json();
              if (fData.success) {
                btnEl.innerHTML = `${svgIcon("check", 12)}<span>Guardado en disco</span>`;
                await loadWorkspace();
                if (openTabs.has(file.path)) openFileInEditor(file.path, true);
              } else {
                btnEl.innerHTML = `${svgIcon("x", 12)}<span>${fData.detail || fData.message || "Error"}</span>`;
              }
            } catch (err) {
              btnEl.innerHTML = `${svgIcon("x", 12)}<span>Error</span>`;
            }
          });
          bubbleContainer.appendChild(failCard);
        }
      } catch (e) {
        console.error("Error guardando archivo:", file.path, e);
      }
    }

    // Detección de bloques de descarga directa desde URL de internet ordenados por el agente
    const downloadRegex = /```(?:download|file:download)[:\s]+(https?:\/\/[^\s\r\n]+)(?:\s*(?:->|as)?\s*([a-zA-Z0-9_\-\.\/]+))?/gi;
    let dlMatch;
    while ((dlMatch = downloadRegex.exec(fullText)) !== null) {
      const dlUrl = dlMatch[1].trim();
      const dlName = dlMatch[2] ? dlMatch[2].trim() : null;
      try {
        const dlRes = await fetch("/api/workspace/download", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: dlUrl, filename: dlName, workspace_path: currentWorkspacePath })
        });
        const dlData = await dlRes.json();
        if (dlRes.ok && dlData.success) {
          savedFiles.push({
            path: dlData.filename,
            patched: false,
            patches_applied: 1,
            compile: { success: true, message: `Descargado de internet (${dlData.size} bytes)` }
          });
        } else {
          const errCard = document.createElement("div");
          errCard.className = "agent-file-card error";
          errCard.innerHTML = `<div class="afc-header"><span class="afc-name">${escapeHtml(dlUrl)}</span><span class="afc-badge err">Error descarga</span></div><div class="afc-msg">${escapeHtml(dlData.detail || dlData.error || `HTTP ${dlRes.status}`)}</div>`;
          bubbleContainer.appendChild(errCard);
        }
      } catch (err) {
        const errCard = document.createElement("div");
        errCard.className = "agent-file-card error";
        errCard.innerHTML = `<div class="afc-header"><span class="afc-name">${escapeHtml(dlUrl)}</span><span class="afc-badge err">Error red</span></div><div class="afc-msg">${escapeHtml(err.message)}</div>`;
        bubbleContainer.appendChild(errCard);
      }
    }

    if (savedFiles.length > 0) {
      // Recargar árbol de archivos en el explorador
      await loadWorkspace();

      // Renderizar tarjetas interactivas de Antigravity en el chat
      savedFiles.forEach((fileObj, idx) => {
        const fPath = fileObj.path;
        // Evitar duplicar tarjeta si ya existe
        const existingCard = bubbleContainer.querySelector(`.file-action-card[data-file="${fPath}"]`);
        if (existingCard) return;

        const card = document.createElement("div");
        card.className = "file-action-card";
        card.dataset.file = fPath;

        const statusHtml = fileObj.patched
          ? `<span class="dot"></span><span>Modificación aplicada en disco (${fileObj.patches_applied} cambio${fileObj.patches_applied === 1 ? "" : "s"})</span>`
          : `<span class="dot"></span><span>Archivo creado y guardado en disco</span>`;

        const comp = fileObj.compile;
        let compileBadgeHtml = "";
        let isError = false;
        if (comp) {
          if (comp.success) {
            card.classList.add("compile-success");
            compileBadgeHtml = `<div class="compile-badge success">${svgIcon("check", 11)}<span>Compilado con éxito (${comp.message || "Sintaxis válida"})</span></div>`;
          } else {
            card.classList.add("compile-error");
            isError = true;
            compileBadgeHtml = `<div class="compile-badge error">${svgIcon("warn", 11)}<span><b>Error sintáctico:</b> ${comp.message || comp.error}</span></div>`;
          }
        }

        card.innerHTML = `
          <div class="file-action-info">
            ${svgIcon("code", 14)}
            <div class="file-action-text">
              <span class="file-action-name">${fPath}</span>
              <span class="file-action-status">${statusHtml}</span>
              ${compileBadgeHtml}
            </div>
          </div>
          <button class="file-action-btn btn-open-card">${svgIcon("external", 12)}<span>Abrir en editor</span></button>
        `;

        if (isError && comp) {
          const autoFixBtn = document.createElement("button");
          autoFixBtn.className = "file-action-btn";
          autoFixBtn.style.color = "#EF4444";
          autoFixBtn.style.borderColor = "rgba(239, 68, 68, 0.4)";
          autoFixBtn.innerHTML = `${svgIcon("bolt", 11)}<span>Auto-reparar</span>`;
          autoFixBtn.addEventListener("click", () => {
            agentTextarea.value = `Corrige el error de sintaxis en '${fPath}': ${comp.message || comp.error}. Genera el bloque completo o parche corregido usando \`\`\`file:${fPath}.`;
            startAgentGeneration();
          });
          card.appendChild(autoFixBtn);
        }
        const openCardBtn = card.querySelector(".btn-open-card");
        if (openCardBtn) {
          openCardBtn.addEventListener("click", () => {
            openFileInEditor(fPath);
          });
        }
        bubbleContainer.appendChild(card);

        // Si ya está abierto en pestaña, recargarlo en vivo; si no, abrirlo si es el primer archivo
        if (openTabs.has(fPath)) {
          openFileInEditor(fPath, true);
        } else if (idx === 0) {
          openFileInEditor(fPath);
        }
      });
    }
  }

  function parseCodeBlocks(text) {
    const files = [];
    const knownFiles = Array.from(document.querySelectorAll("#file-tree-container .tree-item[data-path]"))
      .map(el => (el.dataset.path || "").trim())
      .filter(Boolean);

    // 1. Regex universal que captura bloques ```tag ... ``` donde el cierre ``` debe estar en su propia línea
    const blockRegex = /```([^\r\n]*)\s*[\r\n]+([\s\S]*?)(?:\r?\n```[ \t]*(?=\r?\n|$)|$)/g;
    let match;
    let unnamedCount = 1;

    while ((match = blockRegex.exec(text)) !== null) {
      const rawHeader = match[1].trim();
      const content = match[2].trim();
      const matchIndex = match.index;

      if (!content || content.length < 5) continue;

      // Ignorar siempre salidas de herramientas MCP ([Agente Codex Paso ...]) para que nunca se guarden como app.js
      const preCtx = matchIndex > 0 ? text.slice(Math.max(0, matchIndex - 160), matchIndex) : "";
      if (
        rawHeader.toLowerCase() === "tool-output" ||
        preCtx.includes("[Agente Codex Paso") ||
        (rawHeader.toLowerCase() === "json" && /"elapsed_ms"\s*:/.test(content) && /"success"\s*:/.test(content))
      ) {
        continue;
      }

      let filename = null;
      let isExplicitFile = false;
      const isPatchBlock = /<{3,10}\s*SEARCH/i.test(content) && /={3,10}/.test(content);
      const headerLang = rawHeader.toLowerCase().split(/[\s:]+/)[0];
      const validCodeExts = new Set([
        "py", "js", "ts", "jsx", "tsx", "html", "htm", "css", "scss",
        "json", "sql", "sh", "bat", "ps1", "yaml", "yml", "toml", "ini",
        "cfg", "md", "txt", "c", "cpp", "h", "hpp", "java", "go", "rs",
        "php", "rb", "xml"
      ]);
      const hasValidExt = (fname) => {
        if (!fname || !fname.includes(".")) return false;
        const ext = fname.split(".").pop().toLowerCase();
        return validCodeExts.has(ext);
      };

      // A. Coincidencias tipo: file:nombre.ext o file="nombre.ext"
      const fileHeaderMatch = rawHeader.match(/(?:file[:=\s]+)([a-zA-Z0-9_\-\.\/]+\.[a-zA-Z0-9]+)/i);
      if (fileHeaderMatch) {
        const candidate = fileHeaderMatch[1].replace(/^[.\/\\]+/, "").trim();
        if (hasValidExt(candidate)) {
          filename = candidate;
          isExplicitFile = true;
        }
      }

      // B. El encabezado mismo contiene el nombre del archivo (ej. ```main.py, ```sniffer.py o ```python:main.py)
      if (!filename && rawHeader.includes(".")) {
        const parts = rawHeader.split(/[:\s]+/);
        for (const p of parts) {
          const cleanP = p.replace(/^[.\/\\]+/, "").trim();
          if (cleanP.includes(".") && !cleanP.startsWith("-") && /^[a-zA-Z0-9_\-\/]+\.[a-zA-Z0-9]+$/.test(cleanP) && hasValidExt(cleanP)) {
            filename = cleanP;
            isExplicitFile = true;
            break;
          }
        }
      }

      // Ignorar bloques sin lenguaje (salidas de consola), bash, texto plano o comandos sueltos si NO es explícito y no estamos creando app
      const nonFileLangs = new Set([
        "", "bash", "sh", "shell", "zsh", "fish", "cmd", "bat", "batch",
        "powershell", "ps1", "console", "terminal", "text", "plaintext",
        "txt", "output", "log", "diff", "markdown", "md"
      ]);
      if (!isExplicitFile && !isPatchBlock) {
        if (nonFileLangs.has(headerLang) && currentMode !== "agent") continue;
        const trimmedLines = content.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
        const looksLikeCliOrBanner = (
          (trimmedLines.length <= 4 && trimmedLines.every(l =>
            /^(?:\$\s*)?(?:pip3?|python3?|py|uvicorn|flask|npm|npx|node|cd|ls|dir|mkdir|curl|git|pytest|sudo)\b/i.test(l)
          )) ||
          content.includes("==================================================")
        );
        if (looksLikeCliOrBanner) continue;
      }

      // C. Buscar en las primeras 5 líneas de código por comentarios (# main.py, // app.js, etc.)
      if (!filename) {
        const lines = content.split("\n").slice(0, 5);
        for (const line of lines) {
          const trimmed = line.trim();
          const m = trimmed.match(/^(?:#|\/\/|\/\*|<!--|--)\s*(?:archivo:|file:)?\s*([a-zA-Z0-9_\-\.\/]+\.[a-zA-Z0-9]+)/i);
          if (m) {
            const candidate = m[1].replace(/^[.\/\\]+/, "").trim();
            if (hasValidExt(candidate)) {
              filename = candidate;
              isExplicitFile = true;
              break;
            }
          }
        }
      }

      // D. Buscar si en el texto inmediatamente anterior (hasta 200 caracteres antes del bloque) se menciona un archivo válido
      if (!filename && matchIndex > 0) {
        const precedingText = text.slice(Math.max(0, matchIndex - 200), matchIndex);

        // Coincidencia con archivos que realmente existen en el explorador
        for (const kf of knownFiles) {
          if (kf && kf.includes(".") && precedingText.toLowerCase().includes(kf.toLowerCase())) {
            filename = kf;
            break;
          }
        }

        // Patrón tipo "en main.py:", "código para sniffer.py", "### database.py" con extensión válida
        if (!filename) {
          const preMatch = precedingText.match(/(?:archivo|file|modific(?:ar|aci[oó]n|a)|actualiz(?:ar|a)|c[oó]digo\s+(?:de|para)|#{1,4})\s*[`*"']*([a-zA-Z0-9_\-]+\.[a-zA-Z0-9]+)/i);
          if (preMatch && hasValidExt(preMatch[1].trim())) {
            filename = preMatch[1].trim();
          }
        }
      }

      // E. Si el usuario tiene un archivo abierto en Monaco Editor (activeFilePath)
      // y el bloque de código coincide con el lenguaje o es un bloque SEARCH/REPLACE
      if (!filename && activeFilePath) {
        const activeExt = activeFilePath.split(".").pop().toLowerCase();
        const cleanLang = rawHeader.toLowerCase();
        const matchesLang = (cleanLang.includes("py") && activeExt === "py") ||
          (cleanLang.includes("js") && (activeExt === "js" || activeExt === "ts")) ||
          (cleanLang.includes("html") && activeExt === "html") ||
          (cleanLang.includes("css") && activeExt === "css") ||
          (cleanLang.includes("json") && activeExt === "json") ||
          (isPatchBlock && !cleanLang);
        if (matchesLang) {
          filename = activeFilePath;
        }
      }

      // F. Fallback infalible en modo 'Crear Apps' (modo agent) o proyecto nuevo
      if (!filename) {
        const cleanHeader = rawHeader.toLowerCase();
        if (cleanHeader.includes("python") || cleanHeader.includes("py")) {
          const pyFile = knownFiles.find(f => f.endsWith(".py")) || "main.py";
          if (!files.some(f => f.path === pyFile)) filename = pyFile;
        } else if (cleanHeader.includes("html") || content.includes("<!DOCTYPE") || content.includes("<html")) {
          const htmlFile = knownFiles.find(f => f.endsWith(".html")) || "index.html";
          if (!files.some(f => f.path === htmlFile)) filename = htmlFile;
        } else if (cleanHeader.includes("css")) {
          const cssFile = knownFiles.find(f => f.endsWith(".css")) || "style.css";
          if (!files.some(f => f.path === cssFile)) filename = cssFile;
        } else if (cleanHeader.includes("javascript") || cleanHeader.includes("js") || cleanHeader.includes("ts")) {
          const jsFile = knownFiles.find(f => f.endsWith(".js") || f.endsWith(".ts")) || "app.js";
          if (!files.some(f => f.path === jsFile)) filename = jsFile;
        } else if (currentMode === "agent") {
          if (cleanHeader.includes("sh") || cleanHeader.includes("bash")) {
            filename = "run.sh";
          } else if (cleanHeader.includes("bat") || cleanHeader.includes("batch")) {
            filename = "run.bat";
          } else if (cleanHeader.includes("json")) {
            filename = "data.json";
          }
        }
      }

      if (filename && hasValidExt(filename) && !files.some(f => f.path === filename && f.content === content)) {
        // Evitar que un bloque inferido sin file: sobrescriba un parche o archivo explícito en el mismo mensaje
        const alreadyTargeted = files.find(f => f.path === filename);
        if (alreadyTargeted && !isExplicitFile && !isPatchBlock) {
          continue;
        }
        files.push({ path: filename, content: content, explicit: isExplicitFile, isPatch: isPatchBlock });
      }
    }

    return files;
  }

  function addManualSaveButton(bubbleContainer, text) {
    if (!text.includes("```")) return;
    if (bubbleContainer.querySelector(".btn-sync-all-files")) return;

    const detected = parseCodeBlocks(text);
    if (detected.length === 0) return;

    const fileNamesStr = detected.map(f => f.path).join(", ");
    const btn = document.createElement("button");
    btn.className = "btn-sync-all-files";
    btn.innerHTML = `${svgIcon("save", 13)}<span>Guardar en el proyecto (${fileNamesStr})</span>`;
    btn.addEventListener("click", async () => {
      btn.innerHTML = `${svgIcon("loader", 13, "spin")}<span>Reemplazando archivos en disco...</span>`;
      await extractAndSaveAgentFiles(text, bubbleContainer);
      btn.innerHTML = `${svgIcon("check", 13)}<span>Archivos actualizados en disco</span>`;
      setTimeout(() => {
        btn.remove();
      }, 3500);
    });
    bubbleContainer.appendChild(btn);
  }

  function appendAgentMsg(role, content, imgSrc = null, targetThread = null) {
    const threadEl = targetThread || getActiveThreadEl();
    const msgDiv = document.createElement("div");
    msgDiv.className = `chat-msg ${role}`;

    let imgHtml = "";
    if (imgSrc) {
      imgHtml = `<div class="msg-attachment"><img src="${imgSrc}" alt="Imagen adjunta al mensaje"></div>`;
    }

    const avatarLabel = role === "user" ? "TÚ" : "A";
    msgDiv.innerHTML = `
      <div class="msg-avatar">${avatarLabel}</div>
      <div class="msg-bubble">
        ${imgHtml}
        <div class="msg-bubble-content">${marked.parse(content)}</div>
      </div>
    `;

    threadEl.appendChild(msgDiv);
    enhanceCodeBlocks(msgDiv);
    if (!userScrolledUp) {
      threadEl.scrollTop = threadEl.scrollHeight;
    }

    return {
      wrapper: msgDiv,
      bubble: msgDiv.querySelector(".msg-bubble-content")
    };
  }

  btnClearAgentChat.addEventListener("click", () => {
    if (currentMode === "agent") {
      if (confirm("¿Reiniciar la conversación de Crear Apps?")) {
        agentHistory = [];
        userScrolledUp = false;
        agentMessages.innerHTML = `<p class="thread-note">Conversación reiniciada. Pide una app o modifica el proyecto.</p>`;
      }
    } else {
      const chat = getActiveNormalChat();
      if (confirm(`¿Limpiar el chat "${chat.title}"?`)) {
        chat.messages = [];
        chat.title = "Nuevo chat";
        chat.updatedAt = Date.now();
        userScrolledUp = false;
        saveNormalChatStore();
        renderNormalChatTabs();
        renderNormalHistoryDrawer();
        renderNormalChatThread();
      }
    }
  });

  // 6. TELEMETRÍA Y CONTROL DE HARDWARE (RTX 4050)
  async function updateTelemetry() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();

      if (data.gpu && data.gpu.has_gpu) {
        const totalGb = (data.gpu.total_mb / 1024).toFixed(1);
        const usedGb = (data.gpu.used_mb / 1024).toFixed(1);
        const pct = Math.round((data.gpu.used_mb / data.gpu.total_mb) * 100);

        vramText.textContent = `${usedGb} / ${totalGb} GB`;
        vramPctText.textContent = `${pct}%`;
        vramFill.style.width = `${pct}%`;
        statusVramText.textContent = data.gpu.name;
      }

      if (data.system) {
        ramText.textContent = `${data.system.ram_used_gb} / ${data.system.ram_total_gb} GB`;
        ramFill.style.width = `${data.system.ram_pct}%`;
      }

      if (data.current_model) {
        const base = data.current_model.replace(/\.gguf$/i, "");
        const parts = base.split(".");
        const quant = parts.length > 1 ? parts.pop() : "";
        if (statusModelName) statusModelName.textContent = parts.join(".");
        if (statusModelQuant) statusModelQuant.textContent = quant || "—";
      }

      if (data.model_loaded) {
        btnLoadModel.classList.add("hidden");
        btnUnloadModel.classList.remove("hidden");
      } else {
        btnLoadModel.classList.remove("hidden");
        btnUnloadModel.classList.add("hidden");
      }
    } catch (e) { }
  }

  async function loadModelsList() {
    try {
      const res = await fetch("/api/models/list");
      const data = await res.json();

      modelSelect.innerHTML = "";
      mmprojSelect.innerHTML = "";

      if (data.models && data.models.length > 0) {
        data.models.forEach(m => {
          const opt = document.createElement("option");
          opt.value = m.path;
          opt.textContent = `${m.name} (${m.size_gb} GB)`;
          opt.dataset.mmproj = m.suggested_mmproj || "";
          modelSelect.appendChild(opt);
        });
      }

      if (data.mmprojs && data.mmprojs.length > 0) {
        data.mmprojs.forEach(mm => {
          const opt = document.createElement("option");
          opt.value = mm.path;
          opt.textContent = `${mm.name} (${mm.size_gb} GB)`;
          mmprojSelect.appendChild(opt);
        });
        enableMmproj.checked = false;
      }

      updateModelPathLine();
    } catch (e) { }
  }

  function updateModelPathLine() {
    const pathEl = document.getElementById("model-path-line");
    if (!pathEl) return;
    const value = modelSelect.value || "";
    pathEl.textContent = value ? value.replace(/\//g, "\\") : "models\\";
    pathEl.title = value;
  }

  modelSelect.addEventListener("change", updateModelPathLine);

  // Selección de un .gguf externo y apertura de la carpeta de modelos
  const btnBrowseGguf = document.getElementById("btn-browse-gguf");
  if (btnBrowseGguf) {
    btnBrowseGguf.addEventListener("click", async () => {
      try {
        const res = await fetch("/api/browse-file");
        const data = await res.json();
        if (!data.path) return;
        const opt = document.createElement("option");
        opt.value = data.path;
        opt.textContent = `${data.name} (${data.size_gb} GB)`;
        modelSelect.appendChild(opt);
        modelSelect.value = data.path;
        updateModelPathLine();
      } catch (e) {
        alert("No se pudo abrir el selector de archivos: " + e.message);
      }
    });
  }

  const btnOpenModelsDir = document.getElementById("btn-open-models-dir");
  if (btnOpenModelsDir) {
    btnOpenModelsDir.addEventListener("click", () => {
      fetch("/api/open-models-dir", { method: "POST" });
    });
  }

  gpuLayersSlider.addEventListener("input", (e) => {
    gpuLayersBadge.textContent = e.target.value;
  });

  btnLoadModel.addEventListener("click", async () => {
    const selectedModel = modelSelect.value;
    if (!selectedModel) return alert("Selecciona un modelo GGUF");

    btnLoadModel.disabled = true;
    btnLoadModel.innerHTML = `${svgIcon("loader", 13, "spin")}<span>Inyectando en VRAM...</span>`;

    const payload = {
      model_path: selectedModel,
      n_gpu_layers: parseInt(gpuLayersSlider.value),
      ctx_size: parseInt(ctxSizeSelect.value),
      threads: null,
      flash_attn: true
    };

    if (enableMmproj.checked && mmprojSelect.value) {
      payload.mmproj_path = mmprojSelect.value;
    }

    try {
      const res = await fetch("/api/models/load", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail);
      await updateTelemetry();
      alert("Modelo inyectado con éxito en GPU con soporte multimodal!");
    } catch (e) {
      alert("Error cargando modelo: " + e.message);
    } finally {
      btnLoadModel.disabled = false;
      btnLoadModel.innerHTML = `${svgIcon("bolt", 13)}<span>Iniciar e inyectar en GPU</span>`;
    }
  });

  btnUnloadModel.addEventListener("click", async () => {
    await fetch("/api/models/unload", { method: "POST" });
    await updateTelemetry();
  });

  btnPurgeVram.addEventListener("click", async () => {
    const res = await fetch("/api/gpu/purge", { method: "POST" });
    const d = await res.json();
    await updateTelemetry();
    alert(d.message);
  });

  if (btnPurgeRam) {
    btnPurgeRam.addEventListener("click", async () => {
      btnPurgeRam.disabled = true;
      try {
        const res = await fetch("/api/ram/purge", { method: "POST" });
        const d = await res.json();
        await updateTelemetry();
        alert(d.message);
      } finally {
        btnPurgeRam.disabled = false;
      }
    });
  }

  // Gestor de Modelos estilo LM Studio
  const actModelsHub = document.getElementById("act-models-hub");
  const statusModelChip = document.getElementById("status-model-chip");
  const modelsHubModal = document.getElementById("models-hub-modal");
  const hubBtnClose = document.getElementById("hub-btn-close");
  const hubTabLocal = document.getElementById("hub-tab-local");
  const hubTabDiscover = document.getElementById("hub-tab-discover");
  const hubViewLocal = document.getElementById("hub-view-local");
  const hubViewDiscover = document.getElementById("hub-view-discover");
  const hubLocalList = document.getElementById("hub-local-list");
  const hubDiscoverList = document.getElementById("hub-discover-list");
  const hubSearchInput = document.getElementById("hub-search-input");
  const btnHubSearch = document.getElementById("btn-hub-search");
  const hubDirectUrl = document.getElementById("hub-direct-url");
  const btnHubDirectDl = document.getElementById("btn-hub-direct-dl");
  const hubDownloadBanner = document.getElementById("hub-download-banner");
  const hubDlName = document.getElementById("hub-dl-name");
  const hubDlStats = document.getElementById("hub-dl-stats");
  const hubDlBar = document.getElementById("hub-dl-bar");
  const hubBtnCancelDl = document.getElementById("hub-btn-cancel-dl");
  let hubDlPollTimer = null;

  async function loadModelDirectFromHub(modelPath, btnEl) {
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.textContent = "Cargando...";
    }
    try {
      const payload = {
        model_path: modelPath,
        n_gpu_layers: -1,
        ctx_size: null,
        threads: null,
        flash_attn: true
      };
      const res = await fetch("/api/models/load", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Error cargando modelo");
      if (modelSelect) {
        modelSelect.value = modelPath;
        updateModelPathLine();
      }
      await updateTelemetry();
      await renderHubLocalModels();
    } catch (e) {
      alert("No se pudo cargar el modelo: " + e.message);
    } finally {
      if (btnEl) btnEl.disabled = false;
    }
  }

  async function renderHubLocalModels() {
    if (!hubLocalList) return;
    hubLocalList.innerHTML = `<div class="normal-history-empty">Cargando modelos locales...</div>`;
    try {
      const [listRes, statusRes] = await Promise.all([
        fetch("/api/models/list").then(r => r.json()),
        fetch("/api/status").then(r => r.json())
      ]);
      const models = listRes.models || [];
      const activePath = statusRes.current_model_path || "";
      const isLoaded = !!statusRes.model_loaded;

      hubLocalList.innerHTML = "";
      if (models.length === 0) {
        hubLocalList.innerHTML = `<div class="normal-history-empty">No hay modelos .gguf en models/. Ve a "Descargar Modelos" para bajar uno.</div>`;
        return;
      }

      models.forEach(m => {
        const isCurrent = isLoaded && activePath && (activePath.toLowerCase() === m.path.toLowerCase() || (statusRes.current_model || "").toLowerCase() === m.name.toLowerCase());
        const qMatch = m.name.match(/((?:IQ|Q)\d+(?:_[A-Z0-9_]+)?)/i);
        const quant = qMatch ? qMatch[1].toUpperCase() : "GGUF";

        const card = document.createElement("div");
        card.className = `hub-card${isCurrent ? " active-model" : ""}`;
        card.innerHTML = `
          <div class="hub-card-info">
            <div class="hub-card-title-row">
              <span class="hub-card-name" title="${escapeHtml(m.name)}">${escapeHtml(m.name)}</span>
              <span class="hub-badge">${quant}</span>
              <span class="hub-badge">${m.size_gb} GB</span>
              ${isCurrent ? `<span class="hub-badge ok">Activo en GPU</span>` : ""}
            </div>
            <div class="hub-card-desc">${escapeHtml(m.path)}</div>
          </div>
          <div class="hub-card-actions"></div>
        `;

        const actions = card.querySelector(".hub-card-actions");
        if (isCurrent) {
          const unloadBtn = document.createElement("button");
          unloadBtn.type = "button";
          unloadBtn.className = "btn-vscode btn-secondary-vscode";
          unloadBtn.textContent = "Descargar GPU";
          unloadBtn.addEventListener("click", async () => {
            await fetch("/api/models/unload", { method: "POST" });
            await updateTelemetry();
            await renderHubLocalModels();
          });
          actions.appendChild(unloadBtn);
        } else {
          const loadBtn = document.createElement("button");
          loadBtn.type = "button";
          loadBtn.className = "btn-vscode btn-primary-vscode";
          loadBtn.textContent = "Cargar";
          loadBtn.addEventListener("click", () => loadModelDirectFromHub(m.path, loadBtn));
          actions.appendChild(loadBtn);

          const delBtn = document.createElement("button");
          delBtn.type = "button";
          delBtn.className = "icon-btn";
          delBtn.title = "Eliminar modelo del disco";
          delBtn.innerHTML = `${svgIcon("trash", 13)}`;
          delBtn.addEventListener("click", async () => {
            if (!confirm(`¿Eliminar "${m.name}" del disco?`)) return;
            const r = await fetch("/api/models/delete", {
              method: "DELETE",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ path: m.path })
            });
            if (!r.ok) {
              const d = await r.json().catch(() => ({}));
              return alert(d.detail || "No se pudo eliminar");
            }
            await loadModelsList();
            await renderHubLocalModels();
          });
          actions.appendChild(delBtn);
        }

        hubLocalList.appendChild(card);
      });
    } catch (e) {
      hubLocalList.innerHTML = `<div class="normal-history-empty">Error cargando modelos: ${escapeHtml(e.message)}</div>`;
    }
  }

  function renderDiscoverCards(items) {
    if (!hubDiscoverList) return;
    hubDiscoverList.innerHTML = "";
    if (!items || items.length === 0) {
      hubDiscoverList.innerHTML = `<div class="normal-history-empty">No se encontraron modelos GGUF.</div>`;
      return;
    }

    items.forEach(item => {
      const card = document.createElement("div");
      card.className = "hub-card";
      const sizeBadge = item.size_gb ? `<span class="hub-badge">${item.size_gb} GB</span>` : "";
      const hintBadge = item.vram_hint ? `<span class="hub-badge">${escapeHtml(item.vram_hint)}</span>` : "";
      card.innerHTML = `
        <div class="hub-card-info">
          <div class="hub-card-title-row">
            <span class="hub-card-name">${escapeHtml(item.name)}</span>
            <span class="hub-badge">${escapeHtml(item.quant || "GGUF")}</span>
            ${sizeBadge}
            ${hintBadge}
            ${item.installed ? `<span class="hub-badge ok">Instalado</span>` : ""}
          </div>
          <div class="hub-card-desc">${escapeHtml(item.description || item.filename)}</div>
        </div>
        <div class="hub-card-actions"></div>
      `;

      const actions = card.querySelector(".hub-card-actions");
      if (item.installed && item.local_path) {
        const loadBtn = document.createElement("button");
        loadBtn.type = "button";
        loadBtn.className = "btn-vscode btn-primary-vscode";
        loadBtn.textContent = "Cargar";
        loadBtn.addEventListener("click", () => loadModelDirectFromHub(item.local_path, loadBtn));
        actions.appendChild(loadBtn);
      } else {
        const dlBtn = document.createElement("button");
        dlBtn.type = "button";
        dlBtn.className = "btn-vscode btn-primary-vscode";
        dlBtn.textContent = "Descargar";
        dlBtn.addEventListener("click", () => startHubModelDownload(item.url, item.filename));
        actions.appendChild(dlBtn);
      }
      hubDiscoverList.appendChild(card);
    });
  }

  async function loadHubCatalog() {
    if (!hubDiscoverList) return;
    hubDiscoverList.innerHTML = `<div class="normal-history-empty">Cargando catálogo de modelos...</div>`;
    try {
      const res = await fetch("/api/models/catalog");
      const data = await res.json();
      renderDiscoverCards(data.models || []);
    } catch (e) {
      hubDiscoverList.innerHTML = `<div class="normal-history-empty">Error cargando catálogo: ${escapeHtml(e.message)}</div>`;
    }
  }

  async function searchHuggingFaceHub() {
    const q = (hubSearchInput ? hubSearchInput.value : "").trim();
    if (!q) return loadHubCatalog();
    hubDiscoverList.innerHTML = `<div class="normal-history-empty">Buscando "${escapeHtml(q)}" en Hugging Face...</div>`;
    try {
      const res = await fetch(`/api/models/hf-search?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Error en búsqueda");
      renderDiscoverCards(data.models || []);
    } catch (e) {
      hubDiscoverList.innerHTML = `<div class="normal-history-empty">Error buscando: ${escapeHtml(e.message)}</div>`;
    }
  }

  async function startHubModelDownload(url, filename = "") {
    try {
      const res = await fetch("/api/models/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, filename })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "No se pudo iniciar la descarga");
      pollHubDownloadStatus();
    } catch (e) {
      alert("Error al descargar modelo: " + e.message);
    }
  }

  async function pollHubDownloadStatus() {
    if (hubDlPollTimer) clearInterval(hubDlPollTimer);
    const tick = async () => {
      try {
        const res = await fetch("/api/models/download-status");
        const st = await res.json();
        if (st.status === "downloading") {
          hubDownloadBanner.classList.remove("hidden");
          hubDlName.textContent = `Descargando: ${st.filename}`;
          const mbDown = (st.downloaded_bytes / (1024 * 1024)).toFixed(1);
          const mbTot = st.total_bytes > 0 ? (st.total_bytes / (1024 * 1024)).toFixed(1) : "?";
          hubDlStats.textContent = `${st.percent}% (${mbDown} / ${mbTot} MB · ${st.speed_mbps} MB/s)`;
          hubDlBar.style.width = `${st.percent}%`;
        } else {
          clearInterval(hubDlPollTimer);
          hubDlPollTimer = null;
          if (st.status === "completed") {
            hubDownloadBanner.classList.add("hidden");
            await loadModelsList();
            await renderHubLocalModels();
            await loadHubCatalog();
          } else if (st.status === "error") {
            hubDownloadBanner.classList.add("hidden");
            alert("Error en descarga de modelo: " + (st.error || "Desconocido"));
          } else {
            hubDownloadBanner.classList.add("hidden");
          }
        }
      } catch {}
    };
    await tick();
    hubDlPollTimer = setInterval(tick, 750);
  }

  function openModelsHub(tab = "local") {
    if (!modelsHubModal) return;
    modelsHubModal.classList.remove("hidden");
    const showLocal = tab === "local";
    hubTabLocal.classList.toggle("active", showLocal);
    hubTabDiscover.classList.toggle("active", !showLocal);
    hubViewLocal.classList.toggle("hidden", !showLocal);
    hubViewDiscover.classList.toggle("hidden", showLocal);
    renderHubLocalModels();
    loadHubCatalog();
    fetch("/api/models/download-status").then(r => r.json()).then(st => {
      if (st.status === "downloading") pollHubDownloadStatus();
    }).catch(() => {});
  }

  if (actModelsHub) actModelsHub.addEventListener("click", () => openModelsHub("local"));
  if (statusModelChip) statusModelChip.addEventListener("click", () => openModelsHub("local"));
  if (hubBtnClose) hubBtnClose.addEventListener("click", () => modelsHubModal.classList.add("hidden"));
  if (modelsHubModal) {
    modelsHubModal.addEventListener("click", (e) => {
      if (e.target === modelsHubModal) modelsHubModal.classList.add("hidden");
    });
  }
  if (hubTabLocal && hubTabDiscover) {
    hubTabLocal.addEventListener("click", () => openModelsHub("local"));
    hubTabDiscover.addEventListener("click", () => openModelsHub("discover"));
  }
  if (btnHubSearch) btnHubSearch.addEventListener("click", searchHuggingFaceHub);
  if (hubSearchInput) {
    hubSearchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        searchHuggingFaceHub();
      }
    });
  }
  if (btnHubDirectDl && hubDirectUrl) {
    btnHubDirectDl.addEventListener("click", () => {
      const u = hubDirectUrl.value.trim();
      if (!u) return;
      startHubModelDownload(u, "");
      hubDirectUrl.value = "";
    });
  }
  if (hubBtnCancelDl) {
    hubBtnCancelDl.addEventListener("click", () => {
      fetch("/api/models/download-cancel", { method: "POST" });
    });
  }

  // Inicializar
  enhanceCodeBlocks(agentMessages);
  loadWorkspace();
  loadModelsList();
  loadProxyConfig();
  loadCloudConfig();
  updateTelemetry();
  setInterval(updateTelemetry, 2500);
});
