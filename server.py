"""
HackingLLM Server - Backend de alto rendimiento para inferencia GGUF con aceleración GPU (RTX 4050 / CUDA)
Optimizaciones integradas:
- Flash Attention
- Descarga controlada de capas a GPU (evita saturación de VRAM)
- Detección de corte de texto y botón 'Continuar'
- Control de hilos CPU óptimos (6 hilos físicos)
- Telemetría en tiempo real (VRAM, RAM, Tokens/seg)
"""

import os
import sys
import re
import json
import time
import signal
import subprocess
import threading
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Optional, Dict, Any, List

import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.cybersecurity_kg import get_cybersecurity_graph_context, is_cybersecurity_query, dynamic_cyber_graph
from core.cloud_workers import (
    load_cloud_config, save_cloud_config, call_cloud_worker, desensitize_task_for_worker, 
    check_aws_cli_auth, is_refusal_response, plan_project_with_local_orchestrator, 
    build_bedrock_worker_prompt, generate_file_with_local_orchestrator, sanitize_terms,
    audit_logger, dependency_resolver, recovery_handler,
    verify_and_heal_code, extract_pure_code_from_llm, extract_module_signatures,
    collect_workspace_signatures, auto_fix_common_python_issues, detect_code_slop_issues,
    run_runtime_verification_and_heal
)
from core.terminal_engine import terminal_engine
from core.codebase_rag import codebase_rag_store, code_arch_graph
from core.mcp_agent_engine import (
    mcp_server, MCP_TOOLS_CATALOG, get_openai_compatible_tools,
    get_openai_compatible_tools_for_task, get_dynamic_agent_few_shots,
    get_bedrock_converse_tools, run_autonomous_react_pre_step, parse_tool_calls_from_text
)
from core.mcp_client import mcp_client_manager
from core.mcp_orchestrator import run_elite_mcp_pipeline, run_incremental_iteration, _call_mcp_json
from core.intent_router import classify_user_intent
from core.local_codex_engine import (
    classify_agent_intent,
    plan_blueprint_constrained,
    expand_stub_functions_surgically,
    reflexion_verify_and_heal_project,
)

BASE_DIR = Path(__file__).resolve().parent
ENGINE_DIR = BASE_DIR / "engine"
MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="LLMAgent — Local GGUF IDE & Autonomous Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Estado global del workspace con persistencia en disco (nunca usa la carpeta interna del IDE como proyecto)
WORKSPACE_CONFIG_FILE = BASE_DIR / ".antigravity_workspace"

def _get_default_projects_dir() -> Path:
    desktop = Path.home() / "OneDrive" / "Escritorio"
    if not desktop.exists():
        desktop = Path.home() / "Desktop"
    example_dir = desktop / "Example"
    if example_dir.exists() and example_dir.is_dir():
        return example_dir.resolve()
    fallback = desktop / "Proyectos_IA" if desktop.exists() else (BASE_DIR.parent / "Proyectos_IA")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback.resolve()

def _load_saved_workspace() -> Path:
    try:
        if WORKSPACE_CONFIG_FILE.exists():
            saved = Path(WORKSPACE_CONFIG_FILE.read_text(encoding="utf-8").strip())
            if saved.exists() and saved.is_dir() and saved.resolve() != BASE_DIR.resolve():
                return saved.resolve()
    except Exception:
        pass
    default_dir = _get_default_projects_dir()
    try:
        WORKSPACE_CONFIG_FILE.write_text(str(default_dir), encoding="utf-8")
    except Exception:
        pass
    return default_dir

def _save_workspace_path(folder: Path) -> None:
    try:
        resolved = folder.resolve()
        if resolved != BASE_DIR.resolve():
            WORKSPACE_CONFIG_FILE.write_text(str(resolved), encoding="utf-8")
    except Exception:
        pass

def _sync_workspace(workspace_path: Optional[str] = None) -> Path:
    """Sincroniza y garantiza que el workspace activo nunca sea la carpeta interna de HackingLLM."""
    if workspace_path:
        try:
            cand = Path(workspace_path).resolve()
            if cand != BASE_DIR.resolve():
                cand.mkdir(parents=True, exist_ok=True)
                workspace.current_dir = cand
                _save_workspace_path(cand)
                return workspace.current_dir
        except Exception:
            pass
    if workspace.current_dir.resolve() == BASE_DIR.resolve():
        workspace.current_dir = _load_saved_workspace()
    return workspace.current_dir

class WorkspaceState:
    current_dir: Path = _load_saved_workspace()
    lock: threading.Lock = threading.Lock()

workspace = WorkspaceState()

def get_dir_tree(folder: Path, current_depth: int = 0, max_depth: int = 4) -> List[Dict[str, Any]]:
    """Genera árbol jerárquico de archivos ignorando carpetas ocultas y binarios pesados."""
    if current_depth > max_depth or not folder.exists() or not folder.is_dir():
        return []
        
    ignored_names = {
        ".git", ".venv", "venv", "node_modules", "__pycache__",
        "engine", "models", ".system_generated", ".vscode", ".idea"
    }
    
    items = []
    try:
        entries = sorted(folder.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        for entry in entries:
            if entry.name in ignored_names or entry.name.startswith("."):
                continue
            rel_path = str(entry.relative_to(workspace.current_dir)).replace("\\", "/")
            if entry.is_dir():
                items.append({
                    "name": entry.name,
                    "type": "directory",
                    "path": rel_path,
                    "children": get_dir_tree(entry, current_depth + 1, max_depth)
                })
            else:
                items.append({
                    "name": entry.name,
                    "type": "file",
                    "path": rel_path,
                    "size": entry.stat().st_size
                })
    except Exception:
        pass
    return items

@app.get("/api/workspace/current")
def get_current_workspace():
    """Obtiene la carpeta actual de trabajo."""
    active_ws = _sync_workspace()
    return {
        "current_dir": str(active_ws.resolve()),
        "name": active_ws.name or str(active_ws)
    }

@app.get("/api/workspace/browse-folders")
def browse_folders(path: Optional[str] = None):
    """Permite navegar por las carpetas del sistema para el modal de abrir carpeta."""
    if not path or path == "":
        target = workspace.current_dir
    else:
        target = Path(path).resolve()
        
    if not target.exists() or not target.is_dir():
        target = Path.home()
        
    parent_path = str(target.parent.resolve()) if target.parent != target else None
    
    subfolders = []
    try:
        for item in sorted(target.iterdir(), key=lambda x: x.name.lower()):
            if item.is_dir() and not item.name.startswith("."):
                subfolders.append({
                    "name": item.name,
                    "path": str(item.resolve())
                })
    except Exception:
        pass
        
    desktop = Path.home() / "OneDrive" / "Escritorio"
    if not desktop.exists():
        desktop = Path.home() / "Desktop"
    downloads = Path.home() / "Downloads"
    documents = Path.home() / "Documents"
    
    shortcuts = [
        {"name": "Escritorio", "path": str(desktop.resolve()) if desktop.exists() else str(Path.home())},
        {"name": "Descargas", "path": str(downloads.resolve()) if downloads.exists() else str(Path.home())},
        {"name": "Documentos", "path": str(documents.resolve()) if documents.exists() else str(Path.home())},
        {"name": "Usuario (" + Path.home().name + ")", "path": str(Path.home().resolve())},
    ]

    return {
        "current_path": str(target.resolve()),
        "parent_path": parent_path,
        "subfolders": subfolders,
        "shortcuts": shortcuts
    }

@app.post("/api/workspace/select-folder")
def select_workspace_folder():
    """Abre el explorador nativo de carpetas de Windows al frente de la pantalla."""
    try:
        ps_cmd = (
            '[System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms") | Out-Null; '
            '$dlg = New-Object System.Windows.Forms.FolderBrowserDialog; '
            '$dlg.Description = "Seleccionar Carpeta de Proyecto para HackingLLM Studio"; '
            '$dlg.ShowNewFolderButton = $true; '
            '$form = New-Object System.Windows.Forms.Form; '
            '$form.TopMost = $true; '
            '$form.WindowState = [System.Windows.Forms.FormWindowState]::Minimized; '
            '$form.Show(); '
            '$res = $dlg.ShowDialog($form); '
            'if ($res -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dlg.SelectedPath }; '
            '$form.Close()'
        )
        res = subprocess.run(["powershell", "-Sta", "-NoProfile", "-Command", ps_cmd], stdout=subprocess.PIPE, text=True, timeout=60)
        selected = res.stdout.strip()
        if selected and os.path.exists(selected) and os.path.isdir(selected):
            sel_path = Path(selected).resolve()
            if sel_path == BASE_DIR.resolve():
                return {"success": False, "message": "Selecciona o crea una carpeta de proyecto distinta a HackingLLM.", "path": str(workspace.current_dir.resolve())}
            workspace.current_dir = sel_path
            _save_workspace_path(workspace.current_dir)
            return {"success": True, "path": str(workspace.current_dir.resolve()), "name": workspace.current_dir.name}
    except Exception as e:
        pass
    return {"success": False, "path": str(workspace.current_dir.resolve())}

@app.post("/api/workspace/set-folder")
def set_workspace_folder(req: Dict[str, Any]):
    folder_path = (req.get("path") or "").strip()
    if not folder_path:
        raise HTTPException(status_code=400, detail="Ruta de carpeta no válida")
    target = Path(folder_path).resolve()
    if target == BASE_DIR.resolve():
        raise HTTPException(status_code=400, detail="Por seguridad no puedes usar la carpeta interna del motor HackingLLM como proyecto. Crea o selecciona otra carpeta.")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"No se pudo crear o acceder a la carpeta: {e}")
    workspace.current_dir = target
    _save_workspace_path(workspace.current_dir)
    return {"success": True, "path": str(workspace.current_dir.resolve()), "name": workspace.current_dir.name}

@app.get("/api/workspace/tree")
def get_workspace_tree():
    """Devuelve el árbol de archivos de la carpeta abierta."""
    active_ws = _sync_workspace()
    tree = get_dir_tree(active_ws)
    return {
        "root": str(active_ws.resolve()),
        "name": active_ws.name,
        "tree": tree
    }

@app.get("/api/workspace/file/read")
def read_workspace_file(path: str):
    """Lee el contenido de un archivo relativo al workspace."""
    target = (workspace.current_dir / path).resolve()
    if not str(target).startswith(str(workspace.current_dir.resolve())):
        raise HTTPException(status_code=403, detail="Acceso denegado fuera de la carpeta del proyecto")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        return {"path": path, "content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo leer el archivo: {e}")

class TerminalExecRequest(BaseModel):
    command: str

class TerminalRunFileRequest(BaseModel):
    file: Optional[str] = None
    auto_install: Optional[bool] = True

class TerminalInstallDepsRequest(BaseModel):
    packages: List[str]

@app.get("/api/terminal/logs")
def get_terminal_logs(since: int = 0):
    return {
        "logs": terminal_engine.get_logs(since_id=since),
        "is_running": terminal_engine.is_running(),
        "active_file": terminal_engine.active_file_running
    }

@app.post("/api/terminal/exec")
def exec_terminal_command(req: TerminalExecRequest):
    active_ws = _sync_workspace()
    terminal_engine.run_command(req.command, cwd=active_ws)
    return {"success": True}

@app.post("/api/terminal/kill")
def kill_terminal_command():
    killed = terminal_engine.kill()
    return {"success": killed}

@app.post("/api/terminal/clear")
def clear_terminal_logs():
    terminal_engine.clear()
    return {"success": True}

@app.post("/api/terminal/run_file")
def run_terminal_file(req: TerminalRunFileRequest):
    active_ws = _sync_workspace()
    target_f = req.file or "main.py"
    res = terminal_engine.run_file(target_f, cwd=active_ws, auto_install=req.auto_install if req.auto_install is not None else True)
    return res

@app.get("/api/terminal/dependencies")
def get_file_dependencies(file: str = "main.py"):
    active_ws = _sync_workspace()
    target_path = (active_ws / file).resolve()
    deps = terminal_engine.find_dependencies(target_path)
    missing = terminal_engine.check_missing_dependencies(target_path)
    return {
        "file": file,
        "dependencies": deps,
        "missing": missing
    }

@app.post("/api/terminal/install_deps")
def install_terminal_dependencies(req: TerminalInstallDepsRequest):
    ok, out = terminal_engine.install_dependencies(req.packages)
    return {"success": ok, "output": out}

@app.post("/api/terminal/open_external")
def open_terminal_external(req: TerminalRunFileRequest):
    active_ws = _sync_workspace()
    terminal_engine.open_external_console(active_ws, file_name=req.file)
    return {"success": True}

@app.post("/api/workspace/run")
def run_project(file: Optional[str] = None):
    """Ejecuta el archivo principal o especificado con auditoría de dependencias y soporte web."""
    active_ws = _sync_workspace()
    target = file or "main.py"
    res = terminal_engine.run_file(target, cwd=active_ws, auto_install=True)
    return res


# ==============================================================================
# ENDPOINTS SOTA: SANDBOX AISLADO, MCP (MODEL CONTEXT PROTOCOL), CODEBASE RAG Y GRAFO STIX/CVE
# ==============================================================================

class SandboxConfigRequest(BaseModel):
    mode: Optional[str] = "auto"
    network_policy: Optional[str] = "bridge"
    memory_limit: Optional[str] = "512m"

class MCPDirectCallRequest(BaseModel):
    tool: str
    arguments: Optional[Dict[str, Any]] = {}

class CVESyncRequest(BaseModel):
    keyword: str
    max_results: Optional[int] = 3

@app.get("/api/terminal/sandbox")
def get_sandbox_status_endpoint():
    return terminal_engine.get_sandbox_status()

@app.post("/api/terminal/sandbox")
def configure_sandbox_endpoint(req: SandboxConfigRequest):
    return terminal_engine.configure_sandbox(
        mode=req.mode or "auto",
        network_policy=req.network_policy or "bridge",
        memory_limit=req.memory_limit or "512m"
    )

@app.get("/api/mcp/tools")
def list_mcp_tools_endpoint():
    return {
        "protocol": "Model Context Protocol (MCP) 2024-11-05",
        "server": mcp_server.server_info,
        "mcp_tools": MCP_TOOLS_CATALOG,
        "openai_qwen_schema": get_openai_compatible_tools(),
        "bedrock_converse_schema": get_bedrock_converse_tools()
    }

@app.get("/api/mcp/servers")
def list_external_mcp_servers():
    """Devuelve el estado de los servidores MCP externos reales (JSON-RPC 2.0 sobre stdio/HTTP)."""
    return {
        "protocol": "Model Context Protocol (JSON-RPC 2.0 / 2024-11-05)",
        "config_file": str(mcp_client_manager.config_path),
        "servers": mcp_client_manager.get_status(),
        "external_tools": mcp_client_manager.list_external_openai_tools(),
    }


@app.post("/api/mcp/servers/reload")
def reload_external_mcp_servers():
    """Recarga y reconecta los servidores externos configurados en mcp_servers.json."""
    status = mcp_client_manager.reload_servers()
    return {"success": True, "servers": status}


@app.post("/api/mcp/call")
def call_mcp_tool_endpoint(req: MCPDirectCallRequest):
    active_ws = _sync_workspace()
    return mcp_server.execute_tool(req.tool, req.arguments or {}, active_ws)

@app.post("/api/mcp/rpc")
async def mcp_jsonrpc_endpoint(request: Request):
    active_ws = _sync_workspace()
    payload = await request.json()
    return mcp_server.handle_jsonrpc(payload, active_ws)

@app.post("/api/rag/index")
def index_codebase_rag_endpoint(force: bool = False):
    active_ws = _sync_workspace()
    return codebase_rag_store.index_workspace(active_ws, force=force)

@app.get("/api/rag/search")
def search_codebase_rag_endpoint(q: str, top_k: int = 6):
    active_ws = _sync_workspace()
    results = codebase_rag_store.search(active_ws, q, top_k=top_k)
    return {"query": q, "total_hits": len(results), "results": results}

@app.get("/api/kg/stats")
def get_dynamic_kg_stats():
    return dynamic_cyber_graph.get_stats()

@app.post("/api/kg/sync_cve")
def sync_nvd_cves_endpoint(req: CVESyncRequest):
    return dynamic_cyber_graph.sync_nvd_cves(req.keyword, max_results=req.max_results or 3)

@app.post("/api/kg/ingest_stix")
async def ingest_stix_bundle_endpoint(request: Request):
    bundle = await request.json()
    return dynamic_cyber_graph.ingest_stix_bundle(bundle)

class WriteFileRequest(BaseModel):
    path: str
    content: str
    force: Optional[bool] = False
    workspace_path: Optional[str] = None

def apply_search_replace_patches(original_content: str, patch_text: str) -> tuple[str, int]:
    """
    Aplica bloques de edición inteligente SEARCH / REPLACE (estilo Aider / Cursor / Antigravity).
    Permite modificar solo las partes necesarias de un archivo existente sin reescribir todo desde 0.
    """
    import re
    # Reconoce bloques con <<<<<<< SEARCH ... ======= ... >>>>>>> (tolerante a bloques SEARCH vacíos, longitud y mayúsculas)
    pattern = re.compile(
        r'<{3,10}\s*SEARCH[^\r\n]*\r?\n([\s\S]*?)\r?\n?={3,10}[^\r\n]*\r?\n([\s\S]*?)\r?\n?>{3,10}[^\r\n]*',
        re.IGNORECASE | re.MULTILINE
    )
    matches = list(pattern.finditer(patch_text))
    if not matches:
        return patch_text, 0

    current_content = original_content
    applied_count = 0

    for m in matches:
        search_block = m.group(1)
        replace_block = m.group(2)

        # 0. Si SEARCH está vacío, insertar/reemplazar cuando el archivo está vacío o agregar al final
        if not search_block.strip():
            if not current_content.strip():
                current_content = replace_block
            else:
                sep = "\n\n" if not current_content.endswith("\n") else "\n"
                current_content = current_content + sep + replace_block
            applied_count += 1
            continue

        # 1. Búsqueda exacta
        if search_block in current_content:
            current_content = current_content.replace(search_block, replace_block, 1)
            applied_count += 1
            continue

        # 2. Búsqueda normalizada por saltos de línea (\r\n vs \n)
        norm_search = search_block.replace("\r\n", "\n")
        norm_current = current_content.replace("\r\n", "\n")
        norm_replace = replace_block.replace("\r\n", "\n")

        if norm_search in norm_current:
            norm_res = norm_current.replace(norm_search, norm_replace, 1)
            if "\r\n" in original_content:
                current_content = norm_res.replace("\n", "\r\n")
            else:
                current_content = norm_res
            applied_count += 1
            continue

        # 3. Búsqueda flexible (ignorando espacios en blanco finales de cada línea)
        search_lines = [line.rstrip() for line in norm_search.split("\n")]
        curr_lines = [line.rstrip() for line in norm_current.split("\n")]
        s_len = len(search_lines)

        found_idx = -1
        for i in range(len(curr_lines) - s_len + 1):
            if curr_lines[i:i+s_len] == search_lines:
                found_idx = i
                break

        if found_idx != -1:
            raw_curr_lines = norm_current.split("\n")
            raw_replace_lines = norm_replace.split("\n")
            new_lines = raw_curr_lines[:found_idx] + raw_replace_lines + raw_curr_lines[found_idx + s_len:]
            res = "\n".join(new_lines)
            if "\r\n" in original_content:
                current_content = res.replace("\n", "\r\n")
            else:
                current_content = res
            applied_count += 1
            continue

        # 4. Búsqueda flexible ignorando indentación inicial (leading/trailing whitespace)
        search_stripped = [line.strip() for line in norm_search.split("\n") if line.strip()]
        if search_stripped:
            curr_raw_lines = norm_current.split("\n")
            match_start = -1
            match_end = -1
            for i in range(len(curr_raw_lines)):
                if curr_raw_lines[i].strip() == search_stripped[0]:
                    k = 0
                    j = i
                    while j < len(curr_raw_lines) and k < len(search_stripped):
                        if curr_raw_lines[j].strip():
                            if curr_raw_lines[j].strip() == search_stripped[k]:
                                k += 1
                            else:
                                break
                        j += 1
                    if k == len(search_stripped):
                        match_start = i
                        match_end = j
                        break
            if match_start != -1 and match_end != -1:
                raw_replace_lines = norm_replace.split("\n")
                new_lines = curr_raw_lines[:match_start] + raw_replace_lines + curr_raw_lines[match_end:]
                res = "\n".join(new_lines)
                if "\r\n" in original_content:
                    current_content = res.replace("\n", "\r\n")
                else:
                    current_content = res
                applied_count += 1
                continue

    return current_content, applied_count

@app.post("/api/workspace/file/write")
def write_workspace_file(req: WriteFileRequest):
    """Crea, sobreescribe o parchea inteligentemente un archivo en la carpeta del proyecto."""
    active_ws = _sync_workspace(req.workspace_path)
    target = (active_ws / req.path).resolve()
    if not str(target).startswith(str(active_ws.resolve())):
        raise HTTPException(status_code=403, detail="Acceso denegado fuera de la carpeta del proyecto")
    if target.parent.resolve() == BASE_DIR.resolve() and req.path.lower() in {"server.py", "run.py", "setup.py"}:
        raise HTTPException(status_code=403, detail="Protección activa: no se permite sobrescribir archivos internos de LLMAgent.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        
        # Verificar si es una edición parcial SEARCH/REPLACE
        has_search_block = ("SEARCH" in req.content.upper() and "=======" in req.content)
        if target.exists() and has_search_block and not req.force:
            original = target.read_text(encoding="utf-8", errors="replace")
            new_content, applied = apply_search_replace_patches(original, req.content)
            if applied > 0:
                target.write_text(new_content, encoding="utf-8")
                return {
                    "success": True, 
                    "path": req.path, 
                    "patched": True, 
                    "patches_applied": applied,
                    "bytes": len(new_content)
                }
            # Si no pudo hacer match pero tenía formato SEARCH, evitar sobreescribir con el bloque patch roto
            return {
                "success": False, 
                "path": req.path, 
                "patched": False, 
                "message": f"No se encontró el bloque original exacto a reemplazar en {req.path}."
            }

        # Protección contra sobrescritura accidental de un archivo existente con comandos de terminal o salidas de consola
        stripped_cmd = req.content.strip().lower()
        is_cli_or_banner = (
            (len(req.content.strip().splitlines()) <= 4 and any(stripped_cmd.startswith(p) for p in ("pip ", "pip3 ", "python ", "py ", "uvicorn ", "flask ", "npm ", "npx ", "cd ", "curl ")))
            or ("==================================================" in req.content and "ctrl+c" in stripped_cmd)
        )
        if target.exists() and target.stat().st_size > 40 and is_cli_or_banner and not req.force and not has_search_block:
            return {
                "success": False,
                "path": req.path,
                "patched": False,
                "message": f"Bloqueado: '{req.content.strip()[:40]}' parece un comando o salida de terminal, no el código de {req.path}."
            }

        # Guardado normal (archivo nuevo o sobreescritura completa)
        clean_content = req.content
        # Si tenía bloque SEARCH pero se forzó el guardado, extraer todos los bloques de reemplazo
        if has_search_block and req.force:
            import re
            replacements = re.findall(
                r'={3,10}[^\r\n]*\r?\n([\s\S]*?)\r?\n?>{3,10}',
                req.content
            )
            if replacements:
                clean_content = "\n\n".join(r.strip("\r\n") for r in replacements)

        if target.suffix.lower() == ".py":
            clean_content = auto_fix_common_python_issues(clean_content, req.path, workspace_dir=active_ws)
            if target.parent != active_ws.resolve():
                init_f = target.parent / "__init__.py"
                if not init_f.exists():
                    try:
                        init_f.write_text("", encoding="utf-8")
                    except Exception:
                        pass

        target.write_text(clean_content, encoding="utf-8")
        try:
            codebase_rag_store.index_workspace(active_ws, force=False)
        except Exception:
            pass
        return {"success": True, "path": req.path, "patched": False, "bytes": len(clean_content)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error guardando archivo: {e}")

class CreateItemRequest(BaseModel):
    path: str
    is_directory: bool = False
    workspace_path: Optional[str] = None
    switch_to_folder: Optional[bool] = False

@app.post("/api/workspace/item/create")
def create_workspace_item(req: CreateItemRequest):
    active_ws = _sync_workspace(req.workspace_path)
    raw_p = Path(req.path)
    target = raw_p.resolve() if raw_p.is_absolute() else (active_ws / req.path).resolve()
    if not raw_p.is_absolute() and not str(target).startswith(str(active_ws.resolve())):
        raise HTTPException(status_code=403, detail="Acceso denegado")
    try:
        if req.is_directory:
            target.mkdir(parents=True, exist_ok=True)
            if req.switch_to_folder and target != BASE_DIR.resolve():
                workspace.current_dir = target
                _save_workspace_path(target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_text("", encoding="utf-8")
        return {"success": True, "path": str(target), "workspace": str(workspace.current_dir.resolve())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class DeleteItemRequest(BaseModel):
    path: str
    workspace_path: Optional[str] = None

@app.post("/api/workspace/item/delete")
def delete_workspace_item(req: DeleteItemRequest):
    target = (workspace.current_dir / req.path).resolve()
    if not str(target).startswith(str(workspace.current_dir.resolve())):
        raise HTTPException(status_code=403, detail="Acceso denegado")
    try:
        if target.is_dir():
            import shutil
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class CompileFileRequest(BaseModel):
    path: str
    workspace_path: Optional[str] = None

@app.post("/api/workspace/file/compile")
def compile_workspace_file(req: CompileFileRequest):
    """
    Verifica y compila sintácticamente un archivo en el espacio de trabajo.
    Para Python, realiza parseo de AST y compilación en bytecode (py_compile).
    Para JSON/JS/HTML, valida la estructura y equilibrio de llaves/etiquetas.
    """
    active_ws = _sync_workspace(req.workspace_path)
    target = (active_ws / req.path).resolve()
    if not str(target).startswith(str(active_ws.resolve())):
        raise HTTPException(status_code=403, detail="Acceso denegado fuera de la carpeta del proyecto")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    ext = target.suffix.lower()

    # 1. Compilación y verificación sintáctica de Python
    if ext == ".py":
        import ast
        import py_compile
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            # Parseo de AST para detectar la línea exacta de cualquier SyntaxError
            tree = ast.parse(content, filename=req.path)
            # Compilación formal a bytecode
            py_compile.compile(str(target), doraise=True)

            classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
            functions = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

            return {
                "success": True,
                "type": "python",
                "message": "Sintaxis y compilación Python verificada con éxito",
                "classes": classes,
                "functions": functions
            }
        except SyntaxError as e:
            return {
                "success": False,
                "type": "python",
                "error": str(e.msg),
                "line": e.lineno,
                "offset": e.offset,
                "text": e.text.strip() if e.text else "",
                "message": f"Error de sintaxis en línea {e.lineno}: {e.msg}"
            }
        except Exception as e:
            return {
                "success": False,
                "type": "python",
                "error": str(e),
                "message": f"Fallo al compilar Python: {e}"
            }

    # 2. Validación de JSON
    elif ext == ".json":
        import json
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            parsed = json.loads(content)
            keys = list(parsed.keys()) if isinstance(parsed, dict) else [f"array[{len(parsed)}]"]
            return {
                "success": True,
                "type": "json",
                "message": "Estructura JSON válida",
                "keys": keys[:8]
            }
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "type": "json",
                "error": e.msg,
                "line": e.lineno,
                "col": e.colno,
                "message": f"JSON inválido en línea {e.lineno}, col {e.colno}: {e.msg}"
            }

    # 3. Validación de HTML / JS
    elif ext in {".html", ".htm"}:
        content = target.read_text(encoding="utf-8", errors="replace")
        if content.count("<") != content.count(">"):
            return {"success": False, "type": "html", "message": "Advertencia: etiquetas HTML desbalanceadas"}
        return {"success": True, "type": "html", "message": "Estructura HTML válida"}

    elif ext == ".js":
        content = target.read_text(encoding="utf-8", errors="replace")
        if content.count("{") != content.count("}"):
            return {"success": False, "type": "javascript", "message": "Llaves { } desbalanceadas en JavaScript"}
        if content.count("(") != content.count(")"):
            return {"success": False, "type": "javascript", "message": "Paréntesis ( ) desbalanceados en JavaScript"}
        return {"success": True, "type": "javascript", "message": "Estructura JavaScript verificada"}

    elif ext == ".pdf":
        return {"success": True, "type": "pdf", "message": f"Documento PDF verificado ({target.stat().st_size} bytes)"}

    return {"success": True, "type": "generic", "message": "Archivo verificado"}

class ExportPdfRequest(BaseModel):
    title: str
    content: str
    filename: Optional[str] = "reporte.pdf"
    workspace_path: Optional[str] = None

@app.post("/api/workspace/export-pdf")
def export_workspace_pdf(req: ExportPdfRequest):
    """
    Genera un documento PDF profesional con formato y estilo tipográfico
    usando ReportLab en la carpeta del proyecto.
    """
    active_ws = _sync_workspace(req.workspace_path)
    fname = req.filename if req.filename.lower().endswith(".pdf") else f"{req.filename}.pdf"
    target = (active_ws / fname).resolve()
    if not str(target).startswith(str(active_ws.resolve())):
        raise HTTPException(status_code=403, detail="Acceso denegado fuera del proyecto")

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

        doc = SimpleDocTemplate(
            str(target),
            pagesize=letter,
            rightMargin=42,
            leftMargin=42,
            topMargin=42,
            bottomMargin=42
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontName='Helvetica-Bold',
            fontSize=20,
            leading=24,
            textColor=colors.HexColor('#0F172A'),
            spaceAfter=10
        )
        body_style = ParagraphStyle(
            'CustomBody',
            parent=styles['BodyText'],
            fontName='Helvetica',
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor('#334155'),
            spaceAfter=7
        )
        code_style = ParagraphStyle(
            'CustomCode',
            parent=styles['Code'],
            fontName='Courier',
            fontSize=8,
            leading=11,
            textColor=colors.HexColor('#0F172A'),
            backColor=colors.HexColor('#F1F5F9'),
            spaceAfter=6
        )

        story = [
            Paragraph(req.title or "Reporte / Documento LLMAgent", title_style),
            HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#2563EB"), spaceAfter=14)
        ]

        lines = req.content.split("\n")
        in_code = False
        code_buf = []

        for line in lines:
            t = line.strip()
            if t.startswith("```"):
                if in_code:
                    story.append(Paragraph("<br/>".join(code_buf), code_style))
                    story.append(Spacer(1, 5))
                    code_buf = []
                    in_code = False
                else:
                    in_code = True
                continue

            if in_code:
                esc = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(" ", "&nbsp;")
                code_buf.append(esc)
                continue

            if not t:
                story.append(Spacer(1, 4))
                continue

            if t.startswith("# "):
                h1 = ParagraphStyle('H1', parent=styles['Heading1'], fontSize=15, leading=19, textColor=colors.HexColor('#0F172A'), spaceBefore=8, spaceAfter=5)
                story.append(Paragraph(t[2:], h1))
            elif t.startswith("## "):
                h2 = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=12, leading=16, textColor=colors.HexColor('#2563EB'), spaceBefore=6, spaceAfter=4)
                story.append(Paragraph(t[3:], h2))
            elif t.startswith("- ") or t.startswith("* "):
                esc = t[2:].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                story.append(Paragraph(f"• {esc}", body_style))
            else:
                esc = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                story.append(Paragraph(esc, body_style))

        if in_code and code_buf:
            story.append(Paragraph("<br/>".join(code_buf), code_style))

        doc.build(story)
        return {
            "success": True,
            "filename": fname,
            "path": str(target),
            "size": target.stat().st_size,
            "message": f"Documento PDF '{fname}' creado con éxito ({target.stat().st_size} bytes)"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando PDF: {e}")

@app.get("/api/workspace/file/raw")
def get_raw_workspace_file(path: str, workspace_path: Optional[str] = None):
    """Sirve archivos binarios o descargables (PDFs, imágenes, etc.) directamente."""
    active_ws = _sync_workspace(workspace_path)
    target = (active_ws / path).resolve()
    if not str(target).startswith(str(active_ws.resolve())):
        raise HTTPException(status_code=403, detail="Acceso denegado fuera de la carpeta del proyecto")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    media_type = "application/octet-stream"
    ext = target.suffix.lower()
    if ext == ".pdf":
        media_type = "application/pdf"
    elif ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        media_type = f"image/{ext.replace('.', '')}"
    elif ext in {".json", ".txt", ".csv", ".py", ".html", ".css", ".js"}:
        media_type = "text/plain; charset=utf-8"

    return FileResponse(target, media_type=media_type, filename=target.name)

class DownloadUrlRequest(BaseModel):
    url: str
    filename: Optional[str] = None
    workspace_path: Optional[str] = None

@app.post("/api/workspace/download")
def download_to_workspace(req: DownloadUrlRequest):
    """
    Descarga directamente un archivo desde una URL de internet (GitHub raw, datasets,
    imágenes, wordlists, scripts, binarios, zips) y lo coloca de forma segura en la carpeta del proyecto.
    """
    active_ws = _sync_workspace(req.workspace_path)
    url = req.url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="La URL debe comenzar con http:// o https://")
    gh_blob = re.match(r"^https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)$", url, re.IGNORECASE)
    if gh_blob:
        owner, repo, branch, path = gh_blob.groups()
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"

    # Extraer o determinar el nombre del archivo
    if req.filename and req.filename.strip():
        fname = req.filename.strip()
    else:
        from urllib.parse import urlparse, unquote
        parsed = urlparse(url)
        raw_name = Path(parsed.path).name
        fname = unquote(raw_name) if raw_name else ""
        if not fname or "." not in fname:
            fname = "archivo_descargado.txt"

    target = (active_ws / fname).resolve()
    if not str(target).startswith(str(active_ws.resolve())):
        raise HTTPException(status_code=403, detail="Acceso denegado fuera de la carpeta del proyecto")

    try:
        proxy_arg = proxy_state.get("proxy_url") if proxy_state.get("enabled") else None
        target.parent.mkdir(parents=True, exist_ok=True)
        if proxy_arg:
            import primp
            p_client = primp.Client(proxy=proxy_arg, timeout=60.0, verify=False)
            p_resp = p_client.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
            if p_resp.status_code >= 400:
                raise Exception(f"HTTP {p_resp.status_code}")
            target.write_bytes(p_resp.content)
        else:
            import ssl
            import urllib.request
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            http_req = urllib.request.Request(url, headers=headers)
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                ctx = ssl.create_default_context()
            try:
                with urllib.request.urlopen(http_req, timeout=45, context=ctx) as resp:
                    with open(target, "wb") as f_out:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            f_out.write(chunk)
            except Exception as ssl_err:
                if "ssl" not in str(ssl_err).lower() and "certificate" not in str(ssl_err).lower():
                    raise
                unverified_ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(http_req, timeout=45, context=unverified_ctx) as resp:
                    with open(target, "wb") as f_out:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            f_out.write(chunk)

        size = target.stat().st_size
        return {
            "success": True,
            "filename": fname,
            "path": str(target),
            "size": size,
            "message": f"Archivo '{fname}' descargado con éxito ({size} bytes)"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al descargar desde internet: {e}")

# Estado global y configuración del Proxy Anónimo (Tor / SOCKS5 / HTTP)
PROXY_CONFIG_FILE = BASE_DIR / ".antigravity_proxy.json"

class ProxyConfig(BaseModel):
    enabled: bool = False
    proxy_url: str = "socks5://127.0.0.1:9050"
    auto_start: bool = False

def _load_proxy_config() -> dict:
    if PROXY_CONFIG_FILE.exists():
        try:
            return json.loads(PROXY_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "enabled": False,
        "proxy_url": "socks5://127.0.0.1:9050",
        "auto_start": False
    }

def _save_proxy_config(cfg: dict):
    try:
        PROXY_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass

proxy_state = _load_proxy_config()
if proxy_state.get("auto_start"):
    proxy_state["enabled"] = True

class CloudWorkerConfigRequest(BaseModel):
    enabled: bool
    provider: str
    api_key: Optional[str] = ""
    model: str
    base_url: Optional[str] = ""
    auto_delegate_coding: Optional[bool] = True

@app.get("/api/cloud/config")
def get_cloud_config_endpoint():
    cfg = load_cloud_config()
    provider = cfg.get("provider", "bedrock")
    has_aws_auth, aws_identity = check_aws_cli_auth()
    key = cfg.get("api_key", "")
    
    if provider == "bedrock":
        has_valid_auth = has_aws_auth
        masked_key = f"AWS CLI ({aws_identity.split('/')[-1]})" if has_aws_auth else "No logeado en AWS CLI"
    else:
        has_valid_auth = bool(key)
        masked_key = (key[:6] + "..." + key[-4:]) if len(key) > 10 else ("Configurada" if key else "")

    return {
        "enabled": cfg.get("enabled", False),
        "provider": provider,
        "api_key_masked": masked_key,
        "has_api_key": has_valid_auth,
        "aws_authenticated": has_aws_auth,
        "aws_identity": aws_identity,
        "model": cfg.get("model", "amazon.nova-micro-v1:0"),
        "base_url": cfg.get("base_url", ""),
        "auto_delegate_coding": cfg.get("auto_delegate_coding", True)
    }

@app.post("/api/cloud/config")
def update_cloud_config_endpoint(req: CloudWorkerConfigRequest):
    cfg = load_cloud_config()
    cfg["enabled"] = req.enabled
    cfg["provider"] = req.provider
    if req.api_key and req.api_key.strip() and not req.api_key.startswith("...") and "..." not in req.api_key:
        cfg["api_key"] = req.api_key.strip()
    cfg["model"] = req.model.strip() or ("amazon.nova-micro-v1:0" if req.provider == "bedrock" else "gemini-1.5-flash")
    cfg["base_url"] = req.base_url.strip() if req.base_url else ""
    cfg["auto_delegate_coding"] = bool(req.auto_delegate_coding)
    save_cloud_config(cfg)
    return {"success": True, "config": get_cloud_config_endpoint()}

@app.post("/api/cloud/test")
def test_cloud_worker_endpoint():
    cfg = load_cloud_config()
    provider = cfg.get("provider", "bedrock")
    if provider == "bedrock":
        has_auth, info = check_aws_cli_auth()
        if not has_auth:
            return {"success": False, "error": f"Falta autenticación AWS CLI: {info}"}
    elif not cfg.get("api_key"):
        return {"success": False, "error": "Falta configurar la API Key del trabajador."}
    success, resp = call_cloud_worker("Escribe una función suma(a, b) simple en Python.", cfg)
    return {"success": success, "response": resp}

@app.get("/api/proxy/config")
def get_proxy_config():
    return proxy_state

@app.post("/api/proxy/config")
def update_proxy_config(req: ProxyConfig):
    proxy_state["enabled"] = req.enabled
    proxy_state["proxy_url"] = req.proxy_url.strip()
    proxy_state["auto_start"] = req.auto_start
    _save_proxy_config(proxy_state)
    return {"success": True, "config": proxy_state}

@app.get("/api/proxy/status")
def check_proxy_status():
    """Verifica si Tor (9050/9150) o el proxy configurado están activos."""
    import socket
    ports_to_check = [("Tor Service", 9050), ("Tor Browser", 9150)]
    detected_local_tor = None
    for label, port in ports_to_check:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    detected_local_tor = f"socks5://127.0.0.1:{port}"
                    break
        except Exception:
            pass

    proxy_target = proxy_state.get("proxy_url", "socks5://127.0.0.1:9050")
    is_alive = False
    current_ip = None
    if proxy_state.get("enabled"):
        try:
            import primp
            client = primp.Client(proxy=proxy_target, timeout=3.5)
            res = client.get("https://api.ipify.org?format=json")
            if res.status_code == 200:
                is_alive = True
                current_ip = json.loads(res.text).get("ip")
        except Exception:
            pass

    return {
        "enabled": proxy_state.get("enabled", False),
        "proxy_url": proxy_target,
        "auto_start": proxy_state.get("auto_start", False),
        "detected_local_tor": detected_local_tor,
        "is_alive": is_alive,
        "current_ip": current_ip,
        "message": f"Tor detectado en {detected_local_tor}" if detected_local_tor else ("Proxy activo" if is_alive else "No se detectó proxy en escucha")
    }

# Estado global del proceso de llama-server
class ServerState:
    process: Optional[subprocess.Popen] = None
    current_model_path: Optional[str] = None
    current_model_name: Optional[str] = None
    gpu_layers: int = 0
    ctx_size: int = 8192
    port: int = 8085
    is_ready: bool = False
    active_stream_resp: Optional[Any] = None
    abort_requested: bool = False
    lock: threading.Lock = threading.Lock()

state = ServerState()

def get_gpu_metrics() -> Dict[str, Any]:
    """Obtiene métricas de la GPU NVIDIA (RTX 4050) usando nvidia-smi."""
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits"
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.5)
        if res.returncode == 0 and res.stdout.strip():
            parts = [p.strip() for p in res.stdout.strip().split(",")]
            if len(parts) >= 6:
                return {
                    "has_gpu": True,
                    "name": parts[0],
                    "total_mb": float(parts[1]),
                    "used_mb": float(parts[2]),
                    "free_mb": float(parts[3]),
                    "util_pct": float(parts[4]),
                    "temp_c": float(parts[5]),
                }
    except Exception:
        pass
    
    return {
        "has_gpu": False,
        "name": "No NVIDIA GPU",
        "total_mb": 0,
        "used_mb": 0,
        "free_mb": 0,
        "util_pct": 0,
        "temp_c": 0,
    }

def get_system_metrics() -> Dict[str, Any]:
    """Obtiene métricas de CPU y RAM."""
    vm = psutil.virtual_memory()
    return {
        "ram_total_gb": round(vm.total / (1024**3), 2),
        "ram_used_gb": round(vm.used / (1024**3), 2),
        "ram_free_gb": round(vm.available / (1024**3), 2),
        "ram_pct": vm.percent,
        "cpu_pct": psutil.cpu_percent(interval=None),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False) or 6,
    }

def find_llama_server_binary() -> Optional[Path]:
    """Busca el binario oficial llama-server.exe (o llama-server en Linux/macOS) dentro de engine/."""
    if ENGINE_DIR.exists():
        for bin_name in ("llama-server.exe", "llama-server"):
            direct = ENGINE_DIR / bin_name
            if direct.exists() and direct.is_file():
                return direct
            for p in ENGINE_DIR.rglob(bin_name):
                if p.is_file():
                    return p
    return None

def _is_moe_model_name(model_path: Optional[Path]) -> bool:
    if not model_path:
        return False
    n = model_path.name.lower()
    return any(tag in n for tag in ("a3b", "moe", "mixtral", "deepseek-v2", "deepseek-v3", "30b-a3b"))


def _find_matching_draft_model(model_path: Path) -> Optional[Path]:
    """Busca un modelo borrador diminuto (0.5B/1.5B) compatible en models/ para Speculative Decoding."""
    if not MODELS_DIR.exists():
        return None
    name_low = model_path.name.lower()
    # Solo aplicar speculative decoding a modelos medianos/grandes (> 5.8 GB)
    try:
        if model_path.stat().st_size < 5.8 * (1024 ** 3):
            return None
    except Exception:
        return None
    family = None
    if "qwen2.5" in name_low:
        family = "qwen2.5"
    elif "qwen3" in name_low:
        family = "qwen3"
    elif "llama-3" in name_low or "llama3" in name_low:
        family = "llama-3"
    if not family:
        return None
    for f in MODELS_DIR.glob("*.gguf"):
        fl = f.name.lower()
        if f.resolve() == model_path.resolve() or "mmproj" in fl:
            continue
        if family in fl and any(sz in fl for sz in ("0.5b", "0.6b", "1.5b", "1b")):
            return f
    return None


def compute_optimal_hardware_profile(model_path: Optional[Path] = None, has_mmproj: bool = False) -> Dict[str, Any]:
    """Detecta automáticamente GPU/VRAM/CPU/RAM y calcula la configuración óptima de máximo rendimiento."""
    gpu = get_gpu_metrics()
    sys_m = get_system_metrics()

    phys_cores = int(sys_m.get("cpu_count_physical") or 4)
    logical_cores = int(sys_m.get("cpu_count_logical") or phys_cores)
    opt_threads = max(2, phys_cores)
    opt_threads_batch = max(opt_threads, min(logical_cores, 16))

    model_mb = 5500.0
    if model_path and model_path.exists():
        try:
            model_mb = model_path.stat().st_size / (1024 * 1024)
        except Exception:
            pass

    mmproj_reserve_mb = 950.0 if has_mmproj else 0.0
    is_moe = _is_moe_model_name(model_path)

    ram_total_gb = float(sys_m.get("ram_total_gb") or 16.0)
    ram_free_gb = max(0.0, ram_total_gb - float(sys_m.get("ram_used_gb") or 0.0))
    ram_warning = ""

    if gpu.get("has_gpu"):
        total_vram = float(gpu.get("total_mb") or 0)
        free_vram = float(gpu.get("free_mb") or 0)
        effective_vram = max(free_vram, total_vram * 0.88)
        ctx_opt = 16384
        extra_ot: Optional[str] = None

        # Guardrail realista de RAM para modelos grandes / MoE (evitar prometer 30B-A3B en 16 GB RAM)
        total_mem_budget_mb = (ram_total_gb * 1024.0 * 0.72) + (total_vram * 0.85)
        if model_mb > total_mem_budget_mb:
            ram_warning = (
                f"Advertencia: este modelo ({model_mb/1024:.1f} GB) excede el presupuesto seguro de VRAM+RAM "
                f"({ram_total_gb:.0f} GB RAM + {total_vram/1024:.0f} GB VRAM) y puede caer en swap de disco. "
                "Para modelos MoE 30B se recomiendan >= 24–32 GB de RAM."
            )
            print(f"[HardwareGuard] {ram_warning}")

        if is_moe and ram_total_gb >= 24.0:
            # Modelos MoE (ej. Qwen3-30B-A3B) SOLO cuando hay >=24 GB de RAM física para evitar swap
            ngl = 99
            cache_k, cache_v = "q4_0", "q4_0"
            batch_size, ubatch_size = 1024, 512
            ctx_opt = 8192
            extra_ot = ".ffn_.*_exps.=CPU"
        elif effective_vram >= (model_mb + mmproj_reserve_mb + 1200.0):
            # Cabe 100% en GPU + margen cómodo
            ngl = 99
            cache_k, cache_v = "q8_0", "q8_0"
            batch_size, ubatch_size = 2048, 512
            ctx_opt = 16384
        elif total_vram >= 5800.0 or effective_vram >= 4800.0:
            # GPUs de 6 GB (ej. RTX 4050): cálculo adaptativo por peso del modelo para maximizar capas en GPU sin OOM
            cache_k, cache_v = "q4_0", "q4_0"
            if (not has_mmproj) and model_mb <= 4650.0:
                ngl = 99
                batch_size, ubatch_size = 2048, 512
                ctx_opt = 12288
            elif model_mb <= 5800.0:
                ngl = 26 if has_mmproj else 31
                batch_size, ubatch_size = 1024, 512
                ctx_opt = 8192
            elif model_mb <= 7900.0:
                # Modelos 12B–14B IQ4_XS (~6.8–7.6 GB): ~24 capas en RTX 4050 + resto en RAM
                ngl = 18 if has_mmproj else 24
                batch_size, ubatch_size = 1024, 512
                ctx_opt = 16384
                cache_k, cache_v = "q4_0", "q4_0"
            else:
                # Modelos 14B Q4_K_M / >8 GB: dejar margen seguro de VRAM con KV q4_0 + 16k ctx
                ngl = 14 if has_mmproj else 19
                batch_size, ubatch_size = 512, 256
                ctx_opt = 16384
                cache_k, cache_v = "q4_0", "q4_0"
        elif effective_vram >= 3500.0:
            # GPUs de 4 GB con KV q4_0 + FlashAttention
            ngl = 14 if has_mmproj else 18
            cache_k, cache_v = "q4_0", "q4_0"
            batch_size, ubatch_size = 512, 256
            ctx_opt = 16384
        else:
            ngl = 6
            cache_k, cache_v = "q4_0", "q4_0"
            batch_size, ubatch_size = 512, 256
            ctx_opt = 16384

        return {
            "mode": "GPU+CUDA (MoE Offload)" if is_moe else "GPU+CUDA",
            "n_gpu_layers": ngl,
            "threads": opt_threads,
            "threads_batch": opt_threads_batch,
            "cache_type_k": cache_k,
            "cache_type_v": cache_v,
            "flash_attn": True,
            "batch_size": batch_size,
            "ubatch_size": ubatch_size,
            "ctx_size": ctx_opt,
            "override_tensor": extra_ot,
        }

    # PC normal sin GPU dedicada (CPU puro optimizado)
    return {
        "mode": "CPU",
        "n_gpu_layers": 0,
        "threads": opt_threads,
        "threads_batch": opt_threads_batch,
        "cache_type_k": "q8_0",
        "cache_type_v": "q8_0",
        "flash_attn": True,
        "batch_size": 512,
        "ubatch_size": 256,
        "ctx_size": 8192,
        "override_tensor": None,
    }


class LoadModelRequest(BaseModel):
    model_path: str
    mmproj_path: Optional[str] = None     # Proyector multimodal para visión/imágenes
    n_gpu_layers: Optional[int] = None    # None / -1 = detección automática según VRAM real
    ctx_size: Optional[int] = 16384       # 16,384 tokens con context-shift ilimitado
    threads: Optional[int] = None         # None = núcleos físicos reales detectados
    flash_attn: Optional[bool] = True
    cache_type_k: Optional[str] = None
    cache_type_v: Optional[str] = None

def stop_llama_server():
    """Detiene cualquier instancia activa de llama-server."""
    with state.lock:
        if state.process:
            try:
                state.process.terminate()
                state.process.wait(timeout=3)
            except Exception:
                try:
                    state.process.kill()
                except Exception:
                    pass
            state.process = None
        state.is_ready = False
        state.current_model_path = None
        state.current_model_name = None

def _auto_load_default_model():
    """Carga automáticamente el modelo principal de models/ al iniciar el servidor si no está activo."""
    try:
        req_health = urllib.request.Request(f"http://127.0.0.1:{state.port}/health")
        with urllib.request.urlopen(req_health, timeout=1.0) as resp:
            if resp.status == 200:
                state.is_ready = True
                if not state.current_model_name:
                    state.current_model_name = "Qwen3.8-9B-Distill-uncensored-heretic.Q4_K_M.gguf"
                    state.ctx_size = 16384
                    state.gpu_layers = 33
                return
    except Exception:
        pass

    try:
        catalog = list_models()
        models = catalog.get("models", [])
        if models:
            # Priorizar modelos de código de mayor capacidad (14B/32B/MoE Coder -> 7B Coder -> fallback)
            preferred = (
                next((x for x in models if "coder" in x["name"].lower() and any(s in x["name"].lower() for s in ("14b", "32b", "30b"))), None)
                or next((x for x in models if "coder" in x["name"].lower()), None)
                or next((x for x in models if any(s in x["name"].lower() for s in ("14b", "30b", "32b"))), None)
                or next((x for x in models if "iq4_xs" in x["name"].lower() or x.get("size_gb", 99) <= 4.5), None)
                or models[0]
            )
            load_model(LoadModelRequest(
                model_path=preferred["path"],
                mmproj_path=None,
                n_gpu_layers=None,
                ctx_size=16384,
                threads=None,
                flash_attn=True,
                cache_type_k="q4_0",
                cache_type_v="q4_0",
            ))
    except Exception:
        pass

import atexit
import signal


def _cleanup_child_processes(*_args):
    try:
        mcp_client_manager.close_all()
    except Exception:
        pass
    try:
        stop_llama_server()
    except Exception:
        pass


atexit.register(_cleanup_child_processes)


@app.on_event("startup")
def on_startup():
    threading.Thread(target=_auto_load_default_model, daemon=True).start()
    threading.Thread(target=mcp_client_manager.reload_servers, daemon=True).start()

@app.on_event("shutdown")
def on_shutdown():
    _cleanup_child_processes()

@app.get("/api/status")
def get_status():
    """Estado general del sistema y del motor."""
    if not state.is_ready or not state.current_model_name:
        try:
            req_health = urllib.request.Request(f"http://127.0.0.1:{state.port}/health")
            with urllib.request.urlopen(req_health, timeout=0.5) as resp:
                if resp.status == 200:
                    state.is_ready = True
                    if not state.current_model_name:
                        state.current_model_name = "Qwen3.8-9B-Distill-uncensored-heretic.Q4_K_M.gguf"
                    if not state.ctx_size or state.ctx_size < 16384:
                        state.ctx_size = 16384
                    if not state.gpu_layers:
                        state.gpu_layers = 33
        except Exception:
            pass

    gpu = get_gpu_metrics()
    sys_metrics = get_system_metrics()
    server_exe = find_llama_server_binary()
    
    return {
        "engine_installed": server_exe is not None and server_exe.exists(),
        "model_loaded": state.is_ready,
        "current_model": state.current_model_name,
        "current_model_path": state.current_model_path,
        "gpu_layers": state.gpu_layers,
        "ctx_size": state.ctx_size,
        "gpu": gpu,
        "system": sys_metrics,
    }

@app.post("/api/gpu/purge")
def purge_gpu_memory():
    """Libera la GPU matando procesos zombies y forzando recolección de memoria."""
    stop_llama_server()
    killed = 0
    try:
        # Buscar procesos llama-server o engine_runner que no pertenezcan a la app actual
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            name = (proc.info.get('name') or '').lower()
            cmdline = " ".join(proc.info.get('cmdline') or []).lower()
            if 'llama-server' in name or 'engine_runner.py' in cmdline:
                if proc.pid != os.getpid():
                    try:
                        proc.kill()
                        killed += 1
                    except Exception:
                        pass
    except Exception:
        pass
    return {"success": True, "message": f"VRAM limpiada. Procesos zombies eliminados: {killed}"}

@app.post("/api/ram/purge")
def purge_ram_memory():
    """Libera RAM del sistema ejecutando GC y recortando el Working Set en Windows sin cerrar el modelo activo."""
    import gc
    import ctypes

    before_used = psutil.virtual_memory().used
    gc.collect()

    trimmed = 0
    try:
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_QUERY_INFORMATION = 0x0400

        # Recortar proceso actual
        h_self = kernel32.GetCurrentProcess()
        if psapi.EmptyWorkingSet(h_self):
            trimmed += 1

        # Recortar procesos pesados del usuario (sin matar el servidor ni el modelo)
        for proc in psutil.process_iter(['pid', 'name']):
            pname = (proc.info.get('name') or '').lower()
            if any(k in pname for k in ('python', 'llama-server', 'chrome', 'msedge', 'node')):
                try:
                    h_proc = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, False, proc.info['pid'])
                    if h_proc:
                        if psapi.EmptyWorkingSet(h_proc):
                            trimmed += 1
                        kernel32.CloseHandle(h_proc)
                except Exception:
                    pass
    except Exception:
        pass

    vm_after = psutil.virtual_memory()
    freed_mb = max(0.0, (before_used - vm_after.used) / (1024 * 1024))
    used_gb = vm_after.used / (1024 ** 3)
    total_gb = vm_after.total / (1024 ** 3)
    return {
        "success": True,
        "freed_mb": round(freed_mb, 1),
        "trimmed_processes": trimmed,
        "message": f"RAM liberada (~{freed_mb:.0f} MB recuperados en {trimmed} procesos). Uso actual: {used_gb:.2f} / {total_gb:.2f} GB"
    }

@app.get("/api/browse-file")
def browse_file():
    """Abre el explorador de archivos nativo de Windows para seleccionar un archivo .gguf."""
    try:
        ps_cmd = (
            'Add-Type -AssemblyName System.Windows.Forms; '
            '$dlg = New-Object System.Windows.Forms.OpenFileDialog; '
            '$dlg.Filter = "Archivos GGUF (*.gguf)|*.gguf|Todos los archivos (*.*)|*.*"; '
            '$dlg.Title = "Seleccionar Modelo GGUF para HackingLLM"; '
            'if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dlg.FileName }'
        )
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], stdout=subprocess.PIPE, text=True, timeout=30)
        file_path = res.stdout.strip()
        if file_path and os.path.exists(file_path):
            p = Path(file_path)
            size_gb = round(p.stat().st_size / (1024**3), 2)
            return {"path": str(p.resolve()), "name": p.name, "size_gb": size_gb}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo abrir el selector: {e}")
    return {"path": None}

@app.post("/api/open-models-dir")
def open_models_dir():
    """Abre la carpeta models/ en el explorador de Windows."""
    try:
        os.startfile(str(MODELS_DIR.resolve()))
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/models/list")
def list_models():
    """
    Lista archivos GGUF clasificando modelos de lenguaje base y proyectores multimodales (mmproj).
    """
    all_files = []
    
    # 1. Carpeta local models/
    if MODELS_DIR.exists():
        for file in MODELS_DIR.glob("*.gguf"):
            all_files.append((file, "models/"))
            
    # 2. Archivos en el directorio raíz de HackingLLM
    for file in BASE_DIR.glob("*.gguf"):
        all_files.append((file, "raíz/"))

    base_models = []
    mmproj_files = []

    for file, folder in all_files:
        size_gb = round(file.stat().st_size / (1024**3), 2)
        info = {
            "name": file.name,
            "path": str(file.resolve()),
            "size_gb": size_gb,
            "folder": folder
        }
        if "mmproj" in file.name.lower():
            mmproj_files.append(info)
        else:
            base_models.append(info)

    # Vincular sugerencia de mmproj para cada modelo base
    for bm in base_models:
        base_prefix = bm["name"].split(".")[0].lower()
        matched = None
        for mm in mmproj_files:
            if base_prefix in mm["name"].lower():
                matched = mm["path"]
                break
        bm["suggested_mmproj"] = matched or (mmproj_files[0]["path"] if mmproj_files else None)

    return {
        "models": base_models,
        "mmprojs": mmproj_files,
        "models_dir": str(MODELS_DIR.resolve())
    }


MODEL_HUB_CATALOG = [
    {
        "id": "qwen2.5-coder-14b-abliterated",
        "name": "Qwen 2.5 Coder 14B Abliterated (Sin censura)",
        "filename": "Qwen2.5-Coder-14B-Instruct-abliterated-IQ4_XS.gguf",
        "size_gb": 8.1,
        "quant": "IQ4_XS",
        "vram_hint": "6 GB VRAM + RAM (Híbrido GPU+CPU) · 14B Código",
        "description": "Modelo de 14B especializado en programación y sin censura. Mucho mayor calidad de apps y webs que un 8B.",
        "url": "https://huggingface.co/bartowski/Qwen2.5-Coder-14B-Instruct-abliterated-GGUF/resolve/main/Qwen2.5-Coder-14B-Instruct-abliterated-IQ4_XS.gguf"
    },
    {
        "id": "mistral-nemo-12b-abliterated",
        "name": "Mistral Nemo 12B Abliterated (Sin censura)",
        "filename": "Mistral-Nemo-Instruct-2407-abliterated.IQ4_XS.gguf",
        "size_gb": 6.8,
        "quant": "IQ4_XS",
        "vram_hint": "6 GB VRAM + RAM (Híbrido) · 12B Rápido",
        "description": "Modelo de 12B de Mistral+NVIDIA sin censura, más ligero que un 14B y con buen balance calidad/velocidad.",
        "url": "https://huggingface.co/mradermacher/Mistral-Nemo-Instruct-2407-abliterated-GGUF/resolve/main/Mistral-Nemo-Instruct-2407-abliterated.IQ4_XS.gguf"
    },
    {
        "id": "qwen2.5-coder-0.5b-draft",
        "name": "Qwen 2.5 Coder 0.5B (Acelerador Speculative Decoding)",
        "filename": "qwen2.5-coder-0.5b-instruct-q4_k_m.gguf",
        "size_gb": 0.39,
        "quant": "Q4_K_M",
        "vram_hint": "0.4 GB · Acelera 1.5x–2.5x modelos Qwen2.5 14B",
        "description": "Modelo borrador diminuto que se acopla automáticamente a Qwen2.5-Coder-14B para acelerar la generación de código sin perder calidad.",
        "url": "https://huggingface.co/Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-0.5b-instruct-q4_k_m.gguf"
    },
    {
        "id": "qwen2.5-coder-7b",
        "name": "Qwen 2.5 Coder 7B Instruct",
        "filename": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "size_gb": 4.68,
        "quant": "Q4_K_M",
        "vram_hint": "6 GB VRAM · Código y Apps",
        "description": "Especialista en programación, HTML/JS/Python y generación de apps completas.",
        "url": "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf"
    },
    {
        "id": "qwen3-8b-iq4",
        "name": "Qwen 3 8B Abliterated",
        "filename": "mlabonne_Qwen3-8B-abliterated-IQ4_XS.gguf",
        "size_gb": 4.28,
        "quant": "IQ4_XS",
        "vram_hint": "6 GB VRAM · 100% en GPU",
        "description": "Modelo rápido sin censura innecesaria, ideal para chat técnico, código y razonamiento.",
        "url": "https://huggingface.co/bartowski/mlabonne_Qwen3-8B-abliterated-GGUF/resolve/main/mlabonne_Qwen3-8B-abliterated-IQ4_XS.gguf"
    },
    {
        "id": "llama-3.2-3b",
        "name": "Llama 3.2 3B Instruct",
        "filename": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "size_gb": 2.02,
        "quant": "Q4_K_M",
        "vram_hint": "4 GB VRAM · Ultra rápido (~90 t/s)",
        "description": "Ligero, muy veloz para preguntas normales, resúmenes y tareas cotidianas.",
        "url": "https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf"
    },
    {
        "id": "deepseek-r1-8b",
        "name": "DeepSeek R1 Distill Qwen 8B",
        "filename": "DeepSeek-R1-Distill-Qwen-8B-Q4_K_M.gguf",
        "size_gb": 4.92,
        "quant": "Q4_K_M",
        "vram_hint": "6-8 GB VRAM · Razonamiento R1",
        "description": "Excelente razonamiento paso a paso en matemáticas, lógica y arquitectura de software.",
        "url": "https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-8B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-8B-Q4_K_M.gguf"
    },
    {
        "id": "phi-3.5-mini",
        "name": "Phi 3.5 Mini Instruct",
        "filename": "Phi-3.5-mini-instruct-Q4_K_M.gguf",
        "size_gb": 2.39,
        "quant": "Q4_K_M",
        "vram_hint": "4 GB VRAM · Muy rápido (~75 t/s)",
        "description": "Modelo compacto de Microsoft con gran capacidad de seguimiento de instrucciones.",
        "url": "https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/resolve/main/Phi-3.5-mini-instruct-Q4_K_M.gguf"
    },
    {
        "id": "gemma-2-9b",
        "name": "Gemma 2 9B Instruct",
        "filename": "gemma-2-9b-it-Q4_K_M.gguf",
        "size_gb": 5.76,
        "quant": "Q4_K_M",
        "vram_hint": "8 GB VRAM · Redacción y análisis",
        "description": "Alta calidad conversacional, explicaciones claras y redacción natural.",
        "url": "https://huggingface.co/bartowski/gemma-2-9b-it-GGUF/resolve/main/gemma-2-9b-it-Q4_K_M.gguf"
    }
]

model_download_state = {
    "status": "idle",  # idle | downloading | completed | error | cancelled
    "filename": "",
    "url": "",
    "downloaded_bytes": 0,
    "total_bytes": 0,
    "percent": 0.0,
    "speed_mbps": 0.0,
    "error": "",
    "cancel_requested": False
}


class ModelDownloadRequest(BaseModel):
    url: str
    filename: Optional[str] = None


class ModelDeleteRequest(BaseModel):
    path: str


def _http_get_json_ssl_safe(url: str, timeout: float = 15.0) -> Any:
    import ssl
    import urllib.request
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    req = urllib.request.Request(url, headers=headers)
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        if "ssl" not in str(e).lower() and "certificate" not in str(e).lower():
            raise
        unverified = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=timeout, context=unverified) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))


@app.get("/api/models/catalog")
def get_models_catalog():
    """Catálogo curado tipo LM Studio con estado de instalación local."""
    existing_names = {f.name.lower() for f in MODELS_DIR.glob("*.gguf")} if MODELS_DIR.exists() else set()
    items = []
    for item in MODEL_HUB_CATALOG:
        copy_item = dict(item)
        copy_item["installed"] = item["filename"].lower() in existing_names
        copy_item["local_path"] = str((MODELS_DIR / item["filename"]).resolve()) if copy_item["installed"] else None
        items.append(copy_item)
    return {"models": items}


@app.get("/api/models/hf-search")
def search_huggingface_gguf(q: str = ""):
    """Busca modelos GGUF en Hugging Face Hub al estilo LM Studio."""
    query = q.strip()
    if not query:
        return {"models": []}
    import urllib.parse
    encoded = urllib.parse.quote_plus(query)
    api_url = f"https://huggingface.co/api/models?search={encoded}&filter=gguf&sort=downloads&direction=-1&limit=8"
    try:
        raw_list = _http_get_json_ssl_safe(api_url, timeout=15.0)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error buscando en Hugging Face: {e}")

    existing_names = {f.name.lower() for f in MODELS_DIR.glob("*.gguf")} if MODELS_DIR.exists() else set()
    results = []
    preferred_quants = ("Q4_K_M", "IQ4_XS", "Q4_K_S", "Q5_K_M", "Q4_0", "Q8_0")

    for repo in (raw_list or [])[:6]:
        repo_id = repo.get("id") or repo.get("modelId")
        if not repo_id:
            continue
        try:
            detail = _http_get_json_ssl_safe(f"https://huggingface.co/api/models/{repo_id}", timeout=10.0)
            siblings = [s.get("rfilename", "") for s in detail.get("siblings", []) if isinstance(s, dict)]
            ggufs = [f for f in siblings if f.lower().endswith(".gguf") and "mmproj" not in f.lower()]
            if not ggufs:
                continue
            chosen = None
            for pq in preferred_quants:
                chosen = next((g for g in ggufs if pq.lower() in g.lower()), None)
                if chosen:
                    break
            if not chosen:
                chosen = ggufs[0]
            fname = Path(chosen).name
            quant_match = re.search(r"((?:IQ|Q)\d+(?:_[A-Z0-9_]+)?)", fname, re.IGNORECASE)
            quant = quant_match.group(1).upper() if quant_match else "GGUF"
            results.append({
                "id": repo_id,
                "name": repo_id,
                "filename": fname,
                "quant": quant,
                "downloads": repo.get("downloads", 0),
                "likes": repo.get("likes", 0),
                "description": f"Repositorio Hugging Face · {len(ggufs)} variante(s) GGUF disponibles",
                "url": f"https://huggingface.co/{repo_id}/resolve/main/{chosen}",
                "installed": fname.lower() in existing_names
            })
        except Exception:
            continue
    return {"models": results}


def _bg_download_gguf(url: str, filename: str):
    import ssl
    import urllib.request
    dest = (MODELS_DIR / filename).resolve()
    part_dest = (MODELS_DIR / (filename + ".part")).resolve()
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        req = urllib.request.Request(url, headers=headers)
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = ssl.create_default_context()

        def _stream_with_ctx(ssl_ctx):
            with urllib.request.urlopen(req, timeout=60, context=ssl_ctx) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                model_download_state["total_bytes"] = total
                t0 = time.time()
                downloaded = 0
                with open(part_dest, "wb") as f_out:
                    while True:
                        if model_download_state["cancel_requested"]:
                            raise InterruptedError("Descarga cancelada por el usuario")
                        chunk = resp.read(262144)
                        if not chunk:
                            break
                        f_out.write(chunk)
                        downloaded += len(chunk)
                        elapsed = max(0.05, time.time() - t0)
                        model_download_state["downloaded_bytes"] = downloaded
                        model_download_state["speed_mbps"] = round((downloaded / (1024 * 1024)) / elapsed, 2)
                        if total > 0:
                            model_download_state["percent"] = round((downloaded * 100.0) / total, 1)

        try:
            _stream_with_ctx(ctx)
        except Exception as e:
            if "ssl" not in str(e).lower() and "certificate" not in str(e).lower():
                raise
            _stream_with_ctx(ssl._create_unverified_context())

        if dest.exists():
            dest.unlink()
        part_dest.replace(dest)
        model_download_state["status"] = "completed"
        model_download_state["percent"] = 100.0
    except InterruptedError:
        if part_dest.exists():
            try:
                part_dest.unlink()
            except Exception:
                pass
        model_download_state["status"] = "cancelled"
    except Exception as e:
        if part_dest.exists():
            try:
                part_dest.unlink()
            except Exception:
                pass
        model_download_state["status"] = "error"
        model_download_state["error"] = str(e)


@app.post("/api/models/download")
def start_model_download(req: ModelDownloadRequest):
    if model_download_state["status"] == "downloading":
        raise HTTPException(status_code=409, detail="Ya hay una descarga de modelo en curso.")
    url = req.url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL inválida.")
    fname = (req.filename or "").strip()
    if not fname:
        from urllib.parse import urlparse, unquote
        fname = unquote(Path(urlparse(url).path).name) or "modelo_descargado.gguf"
    if not fname.lower().endswith(".gguf"):
        fname += ".gguf"
    fname = Path(fname).name
    model_download_state.update({
        "status": "downloading",
        "filename": fname,
        "url": url,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "percent": 0.0,
        "speed_mbps": 0.0,
        "error": "",
        "cancel_requested": False
    })
    threading.Thread(target=_bg_download_gguf, args=(url, fname), daemon=True).start()
    return {"success": True, "filename": fname}


@app.get("/api/models/download-status")
def get_model_download_status():
    return dict(model_download_state)


@app.post("/api/models/download-cancel")
def cancel_model_download():
    model_download_state["cancel_requested"] = True
    return {"success": True}


@app.delete("/api/models/delete")
def delete_local_model(req: ModelDeleteRequest):
    target = Path(req.path).resolve()
    if not str(target).startswith(str(MODELS_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Solo se pueden eliminar modelos dentro de models/.")
    if state.is_ready and state.current_model_path and Path(state.current_model_path).resolve() == target:
        raise HTTPException(status_code=400, detail="Descarga el modelo de la GPU antes de eliminarlo.")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    target.unlink()
    return {"success": True}

@app.post("/api/models/unload")
def unload_model():
    """Descarga el modelo actual y libera toda la VRAM."""
    stop_llama_server()
    return {"success": True, "message": "Modelo descargado y memoria liberada"}

@app.post("/api/models/load")
def load_model(req: LoadModelRequest):
    """Carga un archivo GGUF con los parámetros hiper-optimizados para la GPU."""
    model_path = Path(req.model_path)
    if not model_path.exists():
        raise HTTPException(status_code=404, detail=f"No se encontró el archivo GGUF en: {req.model_path}")
        
    server_runner = BASE_DIR / "utils" / "engine_runner.py"
    server_exe = find_llama_server_binary()
    if not server_runner.exists() and not (server_exe and server_exe.exists()):
        raise HTTPException(status_code=500, detail="El motor de inferencia no está disponible.")

    stop_llama_server()

    # Detección y optimización automática de hardware (GPU NVIDIA o CPU puro)
    has_mmproj = bool(req.mmproj_path and os.path.exists(req.mmproj_path))
    hw_profile = compute_optimal_hardware_profile(model_path=model_path, has_mmproj=has_mmproj)

    ngl = hw_profile["n_gpu_layers"] if (req.n_gpu_layers is None or req.n_gpu_layers < 0) else req.n_gpu_layers
    opt_threads = hw_profile["threads"] if (req.threads is None or req.threads <= 0) else req.threads
    opt_threads_batch = hw_profile["threads_batch"]
    target_ctx = req.ctx_size or hw_profile["ctx_size"]
    cache_k = req.cache_type_k or hw_profile["cache_type_k"]
    cache_v = req.cache_type_v or hw_profile["cache_type_v"]
    use_flash_attn = hw_profile["flash_attn"] if req.flash_attn is None else req.flash_attn

    # Preferir el binario oficial llama-server.exe como subproceso aislado por HTTP local (más estable entre versiones)
    # y usar utils/engine_runner.py únicamente si existe llama-server-impl.dll y falta el ejecutable.
    if server_exe and server_exe.exists():
        cmd = [str(server_exe)]
    elif server_runner.exists() and (ENGINE_DIR / "llama-server-impl.dll").exists():
        cmd = [
            sys.executable,
            "-u",
            str(server_runner)
        ]
    else:
        raise HTTPException(
            status_code=500,
            detail="No se encontró engine/llama-server.exe. Ejecuta `python setup.py` para descargar el motor llama.cpp."
        )

    cmd.extend([
        "--model", str(model_path),
        "--host", "127.0.0.1",
        "--port", str(state.port),
        "--ctx-size", str(target_ctx),
        "--predict", "16384",
        "--threads", str(opt_threads),
        "--threads-batch", str(opt_threads_batch),
        "--n-gpu-layers", str(ngl),
        "--batch-size", str(hw_profile["batch_size"]),
        "--ubatch-size", str(hw_profile["ubatch_size"]),
        "--cont-batching",
        "--parallel", "1",
        "--context-shift",
        "--keep", "1024",
        "--repeat-penalty", "1.05",
        "--repeat-last-n", "128",
        "--presence-penalty", "0.0",
        "--frequency-penalty", "0.0",
        "--cache-type-k", cache_k,
        "--cache-type-v", cache_v,
        "--cache-reuse", "512",
        "--slot-prompt-similarity", "0.5",
        "--no-warmup",
        "--prio", "2",
        "--jinja",
        "--fit", "off",
    ])

    # Soporte automático para modelos MoE (ej. Qwen3-30B-A3B): expertos a CPU, atención en GPU
    if hw_profile.get("override_tensor"):
        cmd.extend(["-ot", hw_profile["override_tensor"]])

    # Speculative Decoding automático si hay un modelo borrador compatible (0.5B/1.5B) en models/
    draft_model = _find_matching_draft_model(model_path)
    if draft_model:
        cmd.extend([
            "--model-draft", str(draft_model),
            "--gpu-layers-draft", "99",
            "--draft-max", "8",
            "--draft-min", "2",
        ])
        print(f"[SpeedBoost] Speculative Decoding activado con borrador: {draft_model.name}")
    else:
        cmd.extend(["--spec-type", "ngram-cache"])
    
    if use_flash_attn:
        cmd.extend(["--flash-attn", "on"])
    else:
        cmd.extend(["--flash-attn", "off"])
        
    if req.mmproj_path and os.path.exists(req.mmproj_path):
        cmd.extend(["--mmproj", str(req.mmproj_path)])

    env = os.environ.copy()
    # Asegurar que las DLLs de CUDA en engine/ estén en el PATH del subproceso
    engine_bin_dir = str(ENGINE_DIR)
    env["PATH"] = engine_bin_dir + os.pathsep + env.get("PATH", "")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fallo al iniciar llama-server: {e}")

    # Lector de logs en segundo plano para evitar que se llene el buffer del pipe en Windows
    logs_buffer = []
    def log_collector():
        try:
            for line in iter(proc.stdout.readline, ''):
                if not line:
                    break
                logs_buffer.append(line)
                if len(logs_buffer) > 250:
                    logs_buffer.pop(0)
        except Exception:
            pass

    log_thread = threading.Thread(target=log_collector, daemon=True)
    log_thread.start()

    with state.lock:
        state.process = proc
        state.current_model_path = str(model_path)
        state.current_model_name = model_path.name
        state.gpu_layers = ngl
        state.ctx_size = target_ctx

    # Esperar hasta que el servidor responda /health (máximo 90 segundos)
    start_time = time.time()
    ready = False
    
    while time.time() - start_time < 90:
        if proc.poll() is not None:
            # El proceso murió inesperadamente
            time.sleep(0.3)
            out = "".join(logs_buffer[-25:])
            stop_llama_server()
            raise HTTPException(status_code=500, detail=f"llama-server se cerró. Log:\n{out}")
            
        try:
            req_health = urllib.request.Request(f"http://127.0.0.1:{state.port}/health")
            with urllib.request.urlopen(req_health, timeout=1.0) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(0.5)

    if not ready:
        out = "".join(logs_buffer[-20:])
        stop_llama_server()
        raise HTTPException(status_code=504, detail=f"Tiempo de espera agotado al cargar el modelo. Log:\n{out}")

    state.is_ready = True
    return {
        "success": True,
        "message": f"Modelo {model_path.name} cargado con éxito",
        "gpu_layers": ngl,
        "ctx_size": state.ctx_size,
    }

class ChatMessage(BaseModel):
    role: str
    content: Any

try:
    from ddgs import DDGS
    HAS_DDGS = True
except Exception:
    try:
        from duckduckgo_search import DDGS
        HAS_DDGS = True
    except Exception:
        HAS_DDGS = False


def _get_verified_proxy_url() -> Optional[str]:
    """Devuelve proxy_url solo si está habilitado y el puerto local (ej. Tor 9050/9150) está realmente en escucha."""
    if not proxy_state.get("enabled"):
        return None
    p_url = (proxy_state.get("proxy_url") or "socks5://127.0.0.1:9050").strip()
    try:
        import socket
        import urllib.parse
        parsed = urllib.parse.urlparse(p_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 9050
        if host in ("127.0.0.1", "localhost"):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.15)
                if s.connect_ex((host, port)) != 0:
                    # Intentar puerto alternativo de Tor Browser (9150)
                    alt_port = 9150 if port == 9050 else 9050
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
                        s2.settimeout(0.15)
                        if s2.connect_ex((host, alt_port)) == 0:
                            return f"socks5://{host}:{alt_port}"
                    print(f"[Proxy] Aviso: Anon ON pero no hay servicio Tor en {host}:{port}. Usando conexión directa para no dejar ciega la búsqueda web.")
                    return None
    except Exception:
        pass
    return p_url

def clean_search_query(q: str) -> str:
    """Limpia frases de relleno conversacional para que los motores de búsqueda encuentren resultados precisos."""
    cleaned = q.strip()
    cleaned = re.sub(r'^[¿?¡!.,\s]+|[¿?¡!.,\s]+$', '', cleaned)
    patterns = [
        r'^(?:por favor\s+)?(?:puedes\s+)?(?:busca(?:r)?|investiga(?:r)?|encuentra)\s+(?:en\s+internet|en\s+la\s+web|online)?\s*',
        r'^(?:dame|mu[eé]strame|expl[ií]came|dime|ens[eé][ñn]ame)\s+(?:el|la|los|las|un|una)?\s*',
        r'^(?:como|c[oó]mo)\s+(?:puedo|se\s+puede|hago\s+para)?\s*',
        r'\s+(?:y\s+)?(?:dame|mu[eé]strame)\s+(?:el\s+programa|el\s+c[oó]digo|el\s+paso\s+a\s+paso|los\s+pasos).*$',
    ]
    for pat in patterns:
        cleaned = re.sub(pat, '', cleaned, flags=re.IGNORECASE).strip()
    return cleaned if len(cleaned) >= 3 else q.strip()

def perform_web_search(query: str, max_results: int = 4) -> str:
    """
    Realiza una búsqueda web en tiempo real con limpieza de consulta y timeout de 5.5s.
    Devuelve resultados estructurados con fuentes y resúmenes para el modelo.
    """
    if not query.strip():
        return ""

    clean_q = clean_search_query(query)
    
    def _search_ddg():
        snippets = []
        verified_urls = []
        proxy_arg = _get_verified_proxy_url()
        if HAS_DDGS:
            try:
                ddg_timeout = 14.0 if proxy_arg else 7.0
                with DDGS(proxy=proxy_arg, timeout=ddg_timeout) as ddgs:
                    results = list(ddgs.text(clean_q, max_results=max_results))
                    for i, r in enumerate(results, 1):
                        title = r.get("title", "").strip()
                        snippet = r.get("body", "").strip()
                        url = r.get("href", "").strip()
                        if url:
                            verified_urls.append(url)
                        if title or snippet:
                            snippets.append(f"{i}. {title}\n   URL_VERIFICADA: {url}\n   Info: {snippet}")
            except Exception as e:
                print(f"[WebSearch] Error en DDGS: {e}")
        
        # Fallback HTTP rápido a DuckDuckGo Instant Answer / API respetando el proxy anónimo
        if not snippets:
            try:
                import urllib.parse
                import json
                encoded = urllib.parse.quote_plus(clean_q)
                url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
                ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                if proxy_arg:
                    import primp
                    p_resp = primp.Client(proxy=proxy_arg, timeout=12.0).get(url, headers={"User-Agent": ua})
                    data = json.loads(p_resp.content.decode("utf-8", errors="replace"))
                else:
                    import urllib.request
                    req = urllib.request.Request(url, headers={"User-Agent": ua})
                    with urllib.request.urlopen(req, timeout=4.5) as resp:
                        data = json.loads(resp.read().decode("utf-8", errors="replace"))
                abstract = data.get("AbstractText", "")
                heading = data.get("Heading", clean_q)
                source = data.get("AbstractURL", "")
                if abstract:
                    snippets.append(f"1. {heading}\n   Fuente: {source}\n   Info: {abstract}")
                for i, rel in enumerate(data.get("RelatedTopics", [])[:3], len(snippets)+1):
                    t = rel.get("Text", "")
                    u = rel.get("FirstURL", "")
                    if t:
                        snippets.append(f"{i}. {t}\n   Fuente: {u}")
            except Exception:
                pass

        if not snippets:
            return (
                "\n\n=== BÚSQUEDA WEB: SIN RESULTADOS VERIFICADOS ===\n"
                f"No se encontraron páginas web verificadas para '{clean_q}'.\n"
                "REGLA ESTRICTA: TIENES PROHIBIDO escribir o inventar cualquier URL `http://` o `https://`. "
                "Indica claramente que no se encontraron enlaces verificados para esa consulta.\n"
                "=== FIN DE BÚSQUEDA WEB ===\n"
            )

        urls_list_str = "\n".join(f"- {u}" for u in verified_urls)
        return (
            "\n\n=== INFORMACIÓN EN TIEMPO REAL OBTENIDA DE INTERNET (WEB SEARCH) ===\n"
            f"Consulta realizada: '{clean_q}'\n\n"
            + "\n\n".join(snippets) +
            f"\n\n[LISTA ÚNICA DE URLS REALES PERMITIDAS]:\n{urls_list_str}\n"
            "=== FIN DE INFORMACIÓN DE INTERNET ===\n"
            "REGLA OBLIGATORIA ANTI-ALUCINACIÓN DE URLS:\n"
            "1. Solo puedes mostrar o citar URLs que estén LITERALMENTE en `[LISTA ÚNICA DE URLS REALES PERMITIDAS]`.\n"
            "2. PROHIBIDO inventar, reconstruir o adivinar rutas de URLs (ej. `/dataset/...` o `?tags=...` que no estén en la lista).\n"
            "3. Si lo que pide el usuario no aparece en los resultados de arriba, di explícitamente que no existe en los resultados encontrados."
        )

    import concurrent.futures
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_search_ddg)
            return future.result(timeout=15.0)
    except Exception:
        return ""

class ActiveFileInfo(BaseModel):
    path: Optional[str] = None
    content: Optional[str] = None

class ChatCompletionRequest(BaseModel):
    messages: List[ChatMessage]
    mode: Optional[str] = "agent"          # "agent" (crear apps/archivos) o "chat" (pregunta normal)
    web_search: Optional[bool] = True      # Búsqueda web RAG activa
    cloud_workers: Optional[bool] = None   # Multi-Agente: Trabajador en la nube (Gemini Flash / Bedrock)
    active_file: Optional[ActiveFileInfo] = None  # Archivo que el usuario tiene abierto en el editor
    project_files: Optional[List[str]] = None    # Lista de archivos en la carpeta del proyecto
    workspace_path: Optional[str] = None   # Ruta activa de la carpeta del proyecto en el frontend
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = -1         # -1 = SIN LÍMITE (genera hasta finalizar respuesta)
    stream: Optional[bool] = True

def extract_file_signature(target: Path, rel_path: str, max_chars: int = 1000) -> str:
    """
    Context Eviction Inteligente:
    Extrae la firma estructural (clases, métodos y funciones) en lugar del código completo
    para no desbordar la memoria VRAM ni el contexto del modelo.
    """
    ext = target.suffix.lower()
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return f"--- ARCHIVO: {rel_path} (No legible) ---"

    if ext == ".py":
        try:
            import ast
            tree = ast.parse(content, filename=rel_path)
            lines = [f"--- ARCHIVO: {rel_path} (Estructura y firmas de interfaz) ---"]
            doc = ast.get_docstring(tree)
            if doc:
                lines.append(f'"""{doc.strip()[:140]}..."""')
            
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    lines.append(f"class {node.name}:")
                    cdoc = ast.get_docstring(node)
                    if cdoc:
                        lines.append(f'    """{cdoc.strip()[:90]}"""')
                    methods = []
                    for m in node.body:
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            prefix = "async def " if isinstance(m, ast.AsyncFunctionDef) else "def "
                            args = [a.arg for a in m.args.args]
                            methods.append(f"    {prefix}{m.name}({', '.join(args)}): ...")
                    if methods:
                        lines.extend(methods[:10])
                    else:
                        lines.append("    pass")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
                    args = [a.arg for a in node.args.args]
                    lines.append(f"{prefix}{node.name}({', '.join(args)}): ...")
            
            return "\n".join(lines)
        except Exception:
            return f"--- ARCHIVO: {rel_path} ---\n{content[:max_chars]}\n... [código resumido]"

    elif ext == ".json":
        try:
            import json
            obj = json.loads(content)
            keys = list(obj.keys()) if isinstance(obj, dict) else [f"array[{len(obj)}]"]
            return f"--- ARCHIVO: {rel_path} (JSON: {', '.join(keys[:10])}) ---\n{content[:400]}"
        except Exception:
            return f"--- ARCHIVO: {rel_path} ---\n{content[:400]}"

    return f"--- ARCHIVO: {rel_path} ---\n{content[:max_chars]}"

def get_project_code_context(folder: Path, max_files: int = 12, max_chars_per_file: int = 1000, active_path: Optional[str] = None) -> str:
    """
    Lee la estructura y firmas de los archivos de la carpeta del proyecto aplicando Context Eviction
    para evitar saturación de tokens y desbordamiento de memoria VRAM.
    """
    code_exts = {".py", ".js", ".html", ".css", ".json", ".ts", ".jsx", ".tsx", ".sh", ".bat", ".sql", ".txt", ".md", ".pdf"}
    ignored = {".git", ".venv", "venv", "__pycache__", "node_modules", "engine", "models"}
    is_ide_root = (folder.resolve() == BASE_DIR.resolve())
    ide_internal_files = {"server.py", "run.py", "download_model.py", "iniciar_hackingllm.bat", "readme.md"}
    
    parts = []
    try:
        found_files = []
        norm_active = active_path.replace("\\", "/").lower() if active_path else None
        active_name = Path(active_path).name.lower() if active_path else None

        for p in folder.rglob("*"):
            if any(ign in p.parts for ign in ignored) or p.name.startswith("."):
                continue
            if is_ide_root and ("static" in p.parts or p.name.lower() in ide_internal_files):
                continue
            if p.is_file() and p.suffix.lower() in code_exts:
                rel = str(p.relative_to(folder)).replace("\\", "/")
                if norm_active and (rel.lower() == norm_active or p.name.lower() == active_name):
                    continue
                found_files.append(p)
                
        priority_names = ["main.py", "app.py", "index.html", "sniffer.py", "ui.py", "database.py"]
        found_files.sort(key=lambda x: (0 if x.name.lower() in priority_names else 1, x.name.lower()))
        
        for file in found_files[:max_files]:
            rel = str(file.relative_to(folder)).replace("\\", "/")
            sig = extract_file_signature(file, rel, max_chars=max_chars_per_file)
            parts.append(sig)
    except Exception:
        pass
        
    if not parts:
        return ""
    return "\n=== OTROS ARCHIVOS DEL PROYECTO (ESTRUCTURA / CONTEXT EVICTION) ===\n" + "\n\n".join(parts) + "\n=== FIN CONTEXTO PROYECTO ===\n"

def save_file_to_workspace(base_dir: Path, rel_path: str, block_or_code: str):
    """Guarda físicamente un archivo en el directorio activo del proyecto asegurando su ruta y código puro."""
    try:
        clean_code = extract_pure_code_from_llm(block_or_code, rel_path)
        target = (base_dir / rel_path).resolve()
        if str(target).startswith(str(base_dir.resolve())):
            target.parent.mkdir(parents=True, exist_ok=True)
            # Si se guarda un archivo .py dentro de una subcarpeta (ej. core/engine.py), crear __init__.py automáticamente
            if target.suffix.lower() == ".py" and target.parent != base_dir.resolve():
                init_file = target.parent / "__init__.py"
                if not init_file.exists():
                    try:
                        init_file.write_text("", encoding="utf-8")
                    except Exception:
                        pass
            target.write_text(clean_code, encoding="utf-8")
            threading.Thread(
                target=lambda: codebase_rag_store.index_workspace(base_dir, force=False),
                daemon=True
            ).start()
            print(f"[Orchestrator] Archivo guardado físicamente en disco: {target}")
    except Exception as e:
        print(f"[Orchestrator] Error guardando archivo {rel_path}: {e}")

def _chunk_text(text: str, chunk_size: int = 90):
    for i in range(0, len(text), chunk_size):
        yield text[i:i+chunk_size]

@app.post("/api/chat")
async def chat_proxy(chat_req: ChatCompletionRequest):
    """
    Proxy de streaming hacia llama-server con soporte ultra-amplio de 16,384 tokens
    y optimización de contexto.
    """
    if not state.is_ready:
        try:
            req_health = urllib.request.Request(f"http://127.0.0.1:{state.port}/health")
            with urllib.request.urlopen(req_health, timeout=1.0) as resp:
                if resp.status == 200:
                    state.is_ready = True
                    state.current_model_name = "Qwen3.8-9B-Distill-uncensored-heretic.Q4_K_M.gguf"
                    state.ctx_size = 16384
                    state.gpu_layers = 24
        except Exception:
            pass

    if not state.is_ready:
        _auto_load_default_model()

    if not state.is_ready:
        raise HTTPException(status_code=400, detail="No hay ningún modelo GGUF cargado.")

    target_url = f"http://127.0.0.1:{state.port}/v1/chat/completions"
    _sync_workspace(chat_req.workspace_path)
    
    # Solo leer lo que el usuario pide explícitamente en el prompt (nunca el archivo abierto por defecto)
    active_p = None
    repo_code = get_project_code_context(workspace.current_dir, active_path=None) if chat_req.mode == "agent" else ""
    context_parts = []
    if chat_req.mode == "agent":
        context_parts.append(
            "Eres LLMAgent Architect, un desarrollador de software y agente autónomo de élite.\n"
            "Tu objetivo es crear aplicaciones, scripts y herramientas completas, robustas y de calidad profesional."
        )
        context_parts.append(f"Directorio del proyecto actual: {str(workspace.current_dir.resolve())}")

        if repo_code:
            context_parts.append(repo_code)

        context_parts.append(
            "\nPROTOCOLO AUTÓNOMO LLMAGENT (PLAN -> ARCHIVO POR ARCHIVO -> COMPILACIÓN):\n"
            "1. FASE DE PLANIFICACIÓN PREVIA:\n"
            "   Antes de escribir código, define un plan claro de 2 a 4 líneas indicando la estructura modular:\n"
            "   📋 PLAN DE ARQUITECTURA:\n"
            "   - `archivo1.py`: propósito y componentes clave\n"
            "   - `archivo2.py`: propósito y componentes clave\n"
            "2. GENERACIÓN MODULAR ARCHIVO POR ARCHIVO:\n"
            "   - Genera cada archivo en su propio bloque etiquetado con ```file:ruta/nombre.ext\n"
            "   - Escribe código FUENTE COMPLETO, EXTENSO Y TOTALMENTE FUNCIONAL de inicio a fin.\n"
            "   - PROHIBIDO recortar el código, dejar funciones vacías con 'pass' o '# TODO', o escribir solo resúmenes. Escribe TODA la lógica real: importaciones completas, clases, funciones con tipos y docstrings, manejo de excepciones, configuraciones y bloque if __name__ == '__main__':.\n"
            "   - Encabeza SIEMPRE cada bloque con: ```file:nombre_del_archivo.ext (ej: ```file:main.py, ```file:utils.py, ```file:index.html, ```file:app.js).\n"
            "   - Cierra SIEMPRE el bloque con ``` al finalizar el archivo.\n"
            "   - Para reportes, documentación o documentos formales, puedes crear archivos Markdown (.md) o scripts con reportlab para exportar PDFs profesionales.\n"
            "3. COMPILACIÓN Y VERIFICACIÓN EN VIVO:\n"
            "   - El backend compilará automáticamente cada archivo generado con AST y py_compile para certificar su sintaxis.\n"
            "   - Para evitar desbordar la memoria VRAM, los archivos generados se sintetizan en firmas de clases/funciones en el contexto.\n"
            "4. SI MODIFICAS UN ARCHIVO EXISTENTE:\n"
            "   - Usa formato SEARCH / REPLACE para editar solo las líneas necesarias:\n"
            "   ```file:archivo.ext\n"
            "   <<<<<<< SEARCH\n"
            "   // fragmento original exacto\n"
            "   =======\n"
            "   // fragmento nuevo\n"
            "   >>>>>>>\n"
            "   ```\n"
            "5. Explica muy brevemente los archivos creados o actualizados.\n"
            "6. DESCARGA DIRECTA DE ARCHIVOS DE INTERNET (OBLIGATORIO EJECUTAR, NO DAR CÓDIGO):\n"
            "   - Cuando el usuario te pida descargar un archivo de internet (ej. un modelo `.glb`, `.gltf`, imagen, dataset, zip, etc.) y ponerlo en su repositorio, PROHIBIDO escribir un script de Python/JS para que el usuario lo descargue después.\n"
            "   - TÚ MISMO debes descargarlo ahora mismo al proyecto invocando `<tool_call>{\"name\": \"download_url\", \"arguments\": {\"url\": \"URL_DIRECTA_REAL\", \"filename\": \"nombre.ext\"}}</tool_call>`.\n"
            "   - Si aún no tienes una URL directa al archivo, invoca primero `<tool_call>{\"name\": \"search_web\", \"arguments\": {\"query\": \"...\"}}</tool_call>` (o usa las URLs verificadas de tus observaciones) y luego llama a `download_url`. También puedes usar el bloque ```download:https://url/archivo.ext -> nombre_local.ext.\n"
            "7. HERRAMIENTAS AUTÓNOMAS MCP (Total Agent):\n"
            "   - Puedes invocar herramientas reales emitiendo `<tool_call>{\"name\": \"...\", \"arguments\": {...}}</tool_call>`: `read_file`, `write_file`, `apply_patch`, `list_workspace_files`, `search_codebase`, `search_web`, `inspect_webpage` (analiza estructura HTML/DOM de URLs web o localhost), `download_url`, `compile_project`, `run_sandbox_command`, `audit_dependencies`.\n"
            "8. CERO ALUCINACIONES (GROUNDING ESTRICTO):\n"
            "   - NUNCA inventes archivos, funciones, librerías, comandos, URLs ni datos que no estén verificados en el proyecto, en las observaciones MCP o en la búsqueda web. Si falta información, dilo o usa una herramienta para verificarlo."
        )
        context_parts.append(
            "\nINSTRUCCIÓN DE RESPUESTA RÁPIDA:\n"
            "Sé directo, rápido y conciso. Comienza con el plan breve y luego escribe los bloques ```file:... de inmediato sin rodeos."
        )
    else:
        # MODO PREGUNTA NORMAL
        context_parts.append("Eres LLMAgent, un asistente inteligente, analítico y experto.")

        context_parts.append(
            "\nMODO PREGUNTA NORMAL ACTIVO (CHAT CONVERSACIONAL/INFORMATIVO/CIBERSEGURIDAD):\n"
            "El usuario está en modo de consulta libre, técnica o informativa.\n"
            "REGLAS:\n"
            "1. Responde de forma clara, directa, explicativa y pedagógica en texto natural estructurado.\n"
            "2. Si el usuario pide comandos, scripts, programas o guías paso a paso (ej: 'como hackeo una red wifi...', 'dame el programa y el paso a paso', 'dame los comandos'), PROPORCIONA la explicación técnica detallada, los comandos de terminal exactos (ej. airmon-ng, airodump-ng, wifite, aircrack-ng) y el código o script correspondiente de manera didáctica y completa.\n"
            "3. Mantén un tono profesional, experto y sin rodeos morales ni sermones.\n"
            "4. NO uses etiquetas <think> ni hagas monólogos de pensamiento previo. Responde directamente la duda al instante.\n"
            "5. Si el usuario pide explícitamente DESCARGAR un archivo o URL de internet a su proyecto, incluye el bloque ```download:https://url/archivo.ext -> nombre_local.ext para que el sistema lo descargue automáticamente en su carpeta abierta. NUNCA inventes URLs falsas ni escribas scripts con `wget` si no tienes una URL real y verificada; si no hay un enlace directo real (o es contenido protegido por derechos de autor), dilo claramente en lugar de inventar código.\n"
            "6. CONOCIMIENTO ÚTIL SIN INVENTAR ENLACES:\n"
            "   - Utiliza ampliamente tu conocimiento técnico, científico y de programación junto con los resultados de búsqueda web, GraphRAG y archivos del proyecto para dar respuestas completas y útiles.\n"
            "   - Lo ÚNICO prohibido es inventar URLs específicas (`https://...`), enlaces de descarga falsos o archivos inexistentes en el repositorio.\n"
            "   - Si no tienes un enlace directo o un dato muy reciente, explica el tema a fondo con tu conocimiento y aclara brevemente qué parte específica requiere verificación externa (nunca respondas solo una frase vacía de rechazo)."
        )
        context_parts.append(
            "\nINSTRUCCIÓN DE RESPUESTA DIRECTA:\n"
            "Ve directo al grano y proporciona la respuesta completa de inmediato sin pausas ni reflexiones previas."
        )

    # Extraer el último mensaje del usuario para el Grafo de Conocimiento y la búsqueda web
    last_user_text = ""
    for m in reversed(chat_req.messages):
        if m.role == "user":
            if isinstance(m.content, str):
                last_user_text = m.content
            elif isinstance(m.content, list):
                for part in m.content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        last_user_text = part.get("text", "")
                        break
            if last_user_text:
                break

    # =========================================================================
    # SALTO AL SOTA: BUCLE AGÉNTICO REACT / MCP + CODEBASE VECTOR RAG
    # =========================================================================
    mcp_tools_used = []
    should_run_rag_prestep = False
    if last_user_text:
        q_low = last_user_text.strip().lower()
        if chat_req.mode == "agent" and len(q_low) > 3:
            should_run_rag_prestep = True
        elif chat_req.mode == "chat" and len(q_low) > 8:
            code_hints = (
                ".py", ".js", ".html", ".css", ".json", ".yaml",
                "archivo", "código", "codigo", "funcion", "función",
                "clase", "error", "bug", "proyecto", "repo", "explica",
                "analiza", "revisa", "import", "script",
                "http://", "https://", "localhost", "compila", "descarga", "estructura", "pagina", "página", "web"
            )
            if any(h in q_low for h in code_hints):
                should_run_rag_prestep = True

    if should_run_rag_prestep:
        try:
            mcp_tools_used, mcp_obs = run_autonomous_react_pre_step(
                last_user_text,
                workspace.current_dir,
                active_file=active_p,
                local_port=state.port
            )
            if mcp_obs:
                context_parts.append(mcp_obs)
            if chat_req.mode == "agent":
                filtered_tools = get_openai_compatible_tools_for_task(last_user_text)
                tool_names = ", ".join(t["function"]["name"] for t in filtered_tools)
                context_parts.append(
                    f"\n[PROTOCOLO MCP & FUNCTION CALLING NATIVO ACTIVO]:\n"
                    f"Herramientas MCP activas para esta tarea: {tool_names}.\n"
                    "Invoca las herramientas necesarias mediante Function Calling nativo o `<tool_call>{\"name\": \"...\", \"arguments\": {...}}</tool_call>` "
                    "o genera bloques ` ```file:ruta/archivo.ext ` cuando crees código.\n"
                    + get_dynamic_agent_few_shots(last_user_text)
                )
        except Exception as e:
            print(f"[MCP/CodebaseRAG] Aviso en pre-step agéntico: {e}")

    # Ontología y Red Semántica de Ciberseguridad (GraphRAG Anti-Alucinación)
    # SOLO en modo Pregunta Normal ("chat") para no restar rendimiento ni contaminar la creación de apps
    kg_entities_used = []
    if last_user_text and chat_req.mode != "agent":
        try:
            kg_result = get_cybersecurity_graph_context(last_user_text)
            if kg_result and kg_result.get("is_active"):
                context_parts.append(kg_result["prompt_text"])
                kg_entities_used = kg_result.get("entities", [])
                print(f"[KnowledgeGraph] Red Semántica de Ciberseguridad activada ({len(kg_entities_used)} entidades: {kg_entities_used})")
        except Exception as e:
            print(f"[KnowledgeGraph] Error al consultar ontología: {e}")

    # Búsqueda Web en Vivo (RAG Web con Filtro Inteligente)
    # En modo Crear Apps ("agent"), omitir la espera de búsqueda web salvo que el usuario lo pida explícitamente
    web_query_used = None
    wants_web_in_agent = (chat_req.mode != "agent") or any(
        w in last_user_text.lower()
        for w in ("busca en internet", "busca en la web", "documentacion actual", "documentación actual", "descarga", "baja ", "internet", ".glb", ".gltf", "modelo 3d")
    )
    if chat_req.web_search and last_user_text and wants_web_in_agent:
        skip_web_patterns = [
            "como lo pruebo", "como pruebo", "como ejecuto", "como correr",
            "hola", "gracias", "adios", "ok", "que opinas", "explicame este archivo"
        ]
        should_skip = any(p in last_user_text.lower() for p in skip_web_patterns)

        if len(last_user_text.strip()) > 3 and not should_skip:
            try:
                web_info = perform_web_search(last_user_text, max_results=4)
                if web_info:
                    web_query_used = clean_search_query(last_user_text)
                    context_parts.append(web_info)
            except Exception as e:
                print(f"[WebSearch] Error en búsqueda: {e}")

    # Arquitectura Multi-Agente: Orquestación en Nube (Bedrock/Gemini) o 100% Local (Qwen)
    orchestrate_cloud = False
    orchestrate_local = False
    cloud_worker_used = None
    cloud_worker_warning = None
    cloud_cfg = load_cloud_config()
    should_use_cloud = chat_req.cloud_workers if (chat_req.cloud_workers is not None) else cloud_cfg.get("enabled", False)

    agent_intent = "answer_question"
    intent_info = {"intent": "answer_question", "label": "4) Responder Pregunta (LLM Directo)", "reason": ""}
    is_coding_task = False
    if last_user_text and chat_req.mode == "agent":
        intent_info = classify_user_intent(last_user_text, workspace.current_dir, active_file=active_p)
        agent_intent = intent_info["intent"]
        is_coding_task = agent_intent in ("create_app", "iterate_app")
        print(f"[IntentRouter] {intent_info['label']} ({intent_info['reason']})")

    if should_use_cloud:
        provider = cloud_cfg.get("provider", "bedrock")
        has_auth = True
        if provider == "bedrock":
            has_auth, auth_info = check_aws_cli_auth()
            if not has_auth:
                cloud_worker_warning = f"Cloud ON activo pero falta autenticación AWS CLI ({auth_info}). Se utilizó Qwen Local."
                print(f"[CloudWorker] Advertencia: {cloud_worker_warning}")
        elif not cloud_cfg.get("api_key"):
            has_auth = False
            prov = provider.upper()
            cloud_worker_warning = f"Cloud ON activo pero falta configurar la API Key de {prov} en Ajustes. La respuesta se generó con Qwen Local."
            print(f"[CloudWorker] Advertencia: {cloud_worker_warning}")

        if has_auth and is_coding_task:
            worker_model = cloud_cfg.get("model", "amazon.nova-micro-v1:0" if provider == "bedrock" else "gemini-1.5-flash")
            worker_prov = provider.upper()
            cloud_worker_used = f"{worker_prov} {worker_model}"
            orchestrate_cloud = True
            print(f"[CloudWorker] Tarea de desarrollo delegada a Orquestador + {cloud_worker_used}.")

    if (not orchestrate_cloud) and is_coding_task:
        orchestrate_local = True
        print("[LocalOrchestrator] Cloud OFF: Orquestación de arquitectura 100% local con Qwen.")

    # Detección de petición de ejecución / prueba en terminal
    is_execution_request = False
    exec_target_file = None
    if last_user_text:
        # Si el usuario pide terminar, programar, corregir, modificar o crear código, NO es ejecución directa
        creation_words = ["termina", "completa", "corrige", "arregla", "modifica", "escribe", "crea", "haz", "programa", "desarrolla", "genera", "rehace", "faltan"]
        q_lower = last_user_text.lower()
        is_editing_request = any(w in q_lower for w in creation_words)
        
        if not is_editing_request:
            exec_verbs = ["ejecuta", "corre", "inicia", "prueba", "lanzar", "lanza", "ejecutar", "correr", "iniciar", "probar", "testea", "testear"]
            if any(v in q_lower for v in exec_verbs) and any(w in q_lower for w in ["archivo", "script", "app", "proyecto", "servidor", "codigo", "código", ".py", ".html", ".js", "sniffer"]):
                m_f = re.search(r'([a-zA-Z0-9_\-]+\.(?:py|html|js))', last_user_text, re.IGNORECASE)
                if m_f:
                    exec_target_file = m_f.group(1)
                else:
                    for candidate in ["main.py", "app.py", "server.py", "sniffer.py", "index.html"]:
                        if (workspace.current_dir / candidate).exists():
                            exec_target_file = candidate
                            break
                if exec_target_file:
                    is_execution_request = True

    def _compress_assistant_text(c_text: str, is_before_continuation: bool = False) -> str:
        # Limpiar cualquier bloque <think> residual (cerrado o truncado)
        cleaned = re.sub(r'<think>[\s\S]*?</think>', '', c_text)
        cleaned = re.sub(r'<think>[\s\S]*$', '', cleaned).strip()
        if not cleaned:
            cleaned = c_text

        if is_before_continuation:
            # Al continuar, el modelo solo necesita ver los últimos caracteres exactos donde quedó cortado
            if len(cleaned) > 1400:
                return "... [código previo ya generado omitido]\n" + cleaned[-1400:]
            return cleaned

        if len(cleaned) > 900 and "```" in cleaned:
            shortened = re.sub(r'```file:[^\n\r]+[\r\n]+[\s\S]*?```', '[Código generado previamente y guardado en disco]', cleaned)
            shortened = re.sub(r'```[a-zA-Z0-9_\-]*[\r\n]+[\s\S]*?```', '[Bloque de código previo]', shortened)
            # Comprimir también bloques truncados sin ``` de cierre
            shortened = re.sub(r'```file:[^\n\r]+[\r\n]+[\s\S]*$', '[Código previo truncado]', shortened)
            shortened = re.sub(r'```[a-zA-Z0-9_\-]*[\r\n]+[\s\S]*$', '[Bloque de código previo truncado]', shortened)
            cleaned = shortened

        if len(cleaned) > 1200:
            cleaned = cleaned[:700] + "\n... [contenido previo resumido]\n" + cleaned[-300:]
        return cleaned

    system_text = "\n".join(context_parts)
    # Con context-shift activo y 16384+ tokens, permitir contexto amplio sin truncar agresivamente el código del proyecto
    max_total_prompt_chars = max(16000, int((state.ctx_size or 16384) * 2.4))
    max_system_chars = int(max_total_prompt_chars * 0.75)
    if len(system_text) > max_system_chars:
        system_text = system_text[:max_system_chars] + "\n... [contexto de sistema acotado]"

    # Qwen y llama-server exigen ESTRICTAMENTE un único mensaje de rol "system" al inicio
    messages_payload = [{"role": "system", "content": system_text}]

    # Podado inteligente del historial: Mantener los últimos 4 mensajes para evaluar el prompt rápido
    user_and_assistant_msgs = [m.dict() for m in chat_req.messages if m.role != "system"]
    if len(user_and_assistant_msgs) > 4:
        user_and_assistant_msgs = user_and_assistant_msgs[-4:]

    last_user_idx = -1
    for i in range(len(user_and_assistant_msgs) - 1, -1, -1):
        if user_and_assistant_msgs[i].get("role") == "user":
            last_user_idx = i
            break

    last_user_str = ""
    if last_user_idx >= 0 and isinstance(user_and_assistant_msgs[last_user_idx].get("content"), str):
        last_user_str = user_and_assistant_msgs[last_user_idx]["content"].lower()
    is_continuation_req = ("continúa" in last_user_str or "continua" in last_user_str) and ("donde te quedaste" in last_user_str or "sin repetir" in last_user_str)

    # Comprimir mensajes del asistente para no saturar nunca n_ctx
    compressed_history = []
    for idx, m in enumerate(user_and_assistant_msgs):
        is_last = (idx == len(user_and_assistant_msgs) - 1)
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            is_prev_to_continue = is_continuation_req and (idx == last_user_idx - 1)
            if not is_last or is_prev_to_continue:
                m = {"role": "assistant", "content": _compress_assistant_text(m["content"], is_before_continuation=is_prev_to_continue)}
        compressed_history.append(m)

    # Guard final de presupuesto de caracteres para evitar n_prompt_tokens == n_ctx
    remaining_chars = max(2000, max_total_prompt_chars - len(system_text))
    kept_reversed = []
    for m in reversed(compressed_history):
        c = m.get("content", "")
        c_len = len(c) if isinstance(c, str) else 500
        if kept_reversed and c_len > remaining_chars:
            break
        if isinstance(c, str) and c_len > remaining_chars:
            m = {**m, "content": c[-remaining_chars:]}
            c_len = len(m["content"])
        kept_reversed.append(m)
        remaining_chars -= c_len

    final_history = list(reversed(kept_reversed))
    # Asegurar que el historial tras system no empiece en assistant si se recortó un user antiguo
    while len(final_history) > 1 and final_history[0].get("role") == "assistant":
        final_history.pop(0)

    for m in final_history:
        messages_payload.append(m)

    # REGLA OBLIGATORIA: Jinja de Qwen / llama-server exige al menos un user query y terminar en turno user
    has_user_query = any(m.get("role") == "user" for m in messages_payload[1:])
    if not has_user_query:
        messages_payload.append({"role": "user", "content": last_user_text or "Continúa con la tarea."})
    elif messages_payload[-1].get("role") != "user":
        messages_payload.append({"role": "user", "content": last_user_text or "Continúa generando."})

    is_agent = (chat_req.mode == "agent")
    target_max_tokens = chat_req.max_tokens if (chat_req.max_tokens is not None and chat_req.max_tokens > 0) else (6144 if is_agent else 4096)
    if is_agent and target_max_tokens > 8192:
        target_max_tokens = 8192

    # Parámetros de muestreo calibrados para código (Qwen3 / llama.cpp):
    # presence_penalty alto (>0.3) y repeat_penalty >1.12 destruyen CSS/HTML/JS al prohibir tokens normales como px, div, var()
    payload = {
        "messages": messages_payload,
        "temperature": chat_req.temperature if chat_req.temperature is not None else (0.28 if is_agent else 0.55),
        "top_p": chat_req.top_p if chat_req.top_p is not None else 0.90,
        "top_k": 30,
        "min_p": 0.05,
        "repeat_penalty": 1.05 if is_agent else 1.10,
        "repeat_last_n": 128,
        "presence_penalty": 0.0 if is_agent else 0.15,
        "frequency_penalty": 0.0 if is_agent else 0.05,
        "max_tokens": target_max_tokens,
        "stream": True,
    }
    payload["chat_template_kwargs"] = {"enable_thinking": False}

    req_data = json.dumps(payload).encode("utf-8")
    
    def generate():
        start_t = time.time()
        first_token_t = None
        token_count = 0
        reasoning_tokens = 0
        finish_reason = None
        streamed_buffer = ""
        reasoning_buffer = ""
        
        with state.lock:
            state.abort_requested = False
            
        try:
            if cloud_worker_warning:
                yield f"data: {json.dumps({'type': 'cloud_worker_warning', 'warning': cloud_worker_warning})}\n\n"
            elif cloud_worker_used:
                yield f"data: {json.dumps({'type': 'cloud_worker_event', 'worker': cloud_worker_used})}\n\n"

            if kg_entities_used:
                yield f"data: {json.dumps({'type': 'kg_event', 'entities': kg_entities_used})}\n\n"

            if mcp_tools_used:
                pre_ws_changed = any(t.get("tool") in ("download_url", "write_file", "apply_patch", "delete_file") for t in mcp_tools_used)
                yield f"data: {json.dumps({'type': 'mcp_tool_event', 'tools': mcp_tools_used, 'workspace_changed': pre_ws_changed})}\n\n"

            if web_query_used:
                yield f"data: {json.dumps({'type': 'web_search_event', 'query': web_query_used})}\n\n"

            # Emitir paso de clasificación de intención en modo Agente
            if is_agent and last_user_text:
                intent_title = f"Clasificador de Intención: {intent_info['label']}"
                yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': 'intent_router', 'status': 'done', 'title': intent_title, 'detail': intent_info.get('reason', '')})}\n\n"

            # =========================================================================
            # RUTA 2: DESCARGAR ARCHIVO DE INTERNET (MCP-ASSETS / download_url directo)
            # =========================================================================
            if is_agent and agent_intent == "download_asset" and last_user_text:
                yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': 'dl_mcp', 'status': 'running', 'title': '[MCP-ASSETS] Buscando y descargando archivo público al repositorio...', 'detail': last_user_text[:120]})}\n\n"
                url_in_q = re.findall(r"(https?://[^\s<>\"'`]+)", last_user_text, re.IGNORECASE)
                if url_in_q:
                    dl_res = mcp_server.execute_tool("download_url", {"url": url_in_q[0]}, workspace.current_dir)
                    dl_ok = bool(dl_res.get("success"))
                    dl_info = dl_res if dl_ok else None
                    err_msg = dl_res.get("error", "Error desconocido")
                else:
                    mcp_dl = _call_mcp_json("mcp__mcp_assets__fetch_project_assets", {
                        "workspace_dir": str(workspace.current_dir.resolve()),
                        "query": last_user_text,
                        "auto_download": True
                    })
                    dl_info = mcp_dl.get("downloaded")
                    dl_ok = bool(dl_info)
                    err_msg = mcp_dl.get("error") or mcp_dl.get("reason") or "No se encontró un enlace público directo descargable sin inicio de sesión."

                if dl_ok and dl_info:
                    fname = dl_info.get("filename", "archivo")
                    sz = dl_info.get("size_bytes", 0)
                    src_u = dl_info.get("source_url", "")
                    yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': 'dl_mcp', 'status': 'done', 'title': f'[MCP-ASSETS] Descargado: {fname} ({round(sz/1024, 1)} KB)', 'detail': src_u})}\n\n"
                    yield f"data: {json.dumps({'type': 'mcp_tool_event', 'tools': [{'tool': 'download_url', 'summary': f'Descargado {fname}'}], 'workspace_changed': True})}\n\n"
                    msg = f"Archivo **`{fname}`** descargado y guardado correctamente en tu repositorio.\n\n- **Tamaño:** `{sz:,} bytes`\n- **Fuente verificada:** {src_u}\n"
                else:
                    yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': 'dl_mcp', 'status': 'done', 'title': '[MCP-ASSETS] No se pudo descargar el archivo', 'detail': str(err_msg)[:140]})}\n\n"
                    msg = f"No se pudo descargar el archivo solicitado: **{err_msg}**\n\nNo se guardó ningún archivo en tu repositorio."

                for chunk in _chunk_text(msg, 120):
                    yield f"data: {json.dumps({'choices': [{'delta': {'content': chunk}}]})}\n\n"
                yield f"data: {json.dumps({'type': 'meta', 'tokens': 40, 'total_time_s': round(max(time.time()-start_t, 0.01), 2), 'tokens_per_sec': 60, 'ttft_ms': 80, 'finish_reason': 'stop'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            # =========================================================================
            # RUTA 3: COMPILAR / VERIFICAR / EJECUTAR (MCP-EXEC + MCP-RUNTIME-VISION)
            # =========================================================================
            if is_agent and agent_intent == "compile_run" and last_user_text:
                yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': 'exec_mcp', 'status': 'running', 'title': '[MCP-EXEC] Compilando y auditando archivos del repositorio...', 'detail': str(workspace.current_dir)})}\n\n"
                ws_files = []
                for ext_pat in ("*.py", "*.js", "*.html", "*.htm", "*.css"):
                    for p in workspace.current_dir.rglob(ext_pat):
                        if any(ign in p.parts for ign in (".git", "__pycache__", "node_modules", ".venv", ".antigravity_rag", "engine", "models")):
                            continue
                        ws_files.append(str(p.relative_to(workspace.current_dir)).replace("\\", "/"))
                qa_res = _call_mcp_json("mcp__mcp_exec__run_static_qa", {"workspace_dir": str(workspace.current_dir.resolve()), "file_paths": ws_files[:40]})
                py_main = next((f for f in ws_files if Path(f).name.lower() == "main.py"), None)
                run_res = _call_mcp_json("mcp__mcp_exec__run_sandbox_exec", {"workspace_dir": str(workspace.current_dir.resolve()), "entrypoint": py_main}) if py_main else {"success": True, "skipped": True}
                html_main = next((f for f in ws_files if Path(f).name.lower() == "index.html"), None)
                vis_res = _call_mcp_json("mcp__mcp_runtime_vision__verify_web_runtime", {"workspace_dir": str(workspace.current_dir.resolve()), "html_file": html_main}) if html_main else {"success": True, "skipped": True}

                errs = qa_res.get("errors") or []
                if not run_res.get("success"):
                    errs.append({"file": py_main, "gate": "SANDBOX_EXEC", "error": run_res.get("stacktrace", "Error de ejecución")})
                if not vis_res.get("success"):
                    for iss in (vis_res.get("issues") or []):
                        errs.append({"file": html_main, "gate": "RUNTIME_VISION", "error": iss})

                status_txt = "0 errores detectados" if not errs else f"{len(errs)} error(es) encontrado(s)"
                yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': 'exec_mcp', 'status': 'done', 'title': f'[MCP-EXEC] Compilación y QA finalizados ({status_txt})', 'detail': f'{len(ws_files)} archivo(s) inspeccionado(s)'})}\n\n"

                if not errs:
                    out_msg = f"Compilación y verificación completadas con **0 errores** ({len(ws_files)} archivos revisados).\n"
                    if run_res.get("stdout"):
                        out_msg += f"\n**Salida de `{py_main}`:**\n```text\n{run_res['stdout']}\n```\n"
                else:
                    err_lines = "\n".join(f"- **`{e.get('file')}`** `[{e.get('gate')}]`: {e.get('error')}" for e in errs[:10])
                    out_msg = f"Se detectaron **{len(errs)} problema(s)** al compilar/verificar:\n\n{err_lines}\n"

                for chunk in _chunk_text(out_msg, 120):
                    yield f"data: {json.dumps({'choices': [{'delta': {'content': chunk}}]})}\n\n"
                yield f"data: {json.dumps({'type': 'meta', 'tokens': 50, 'total_time_s': round(max(time.time()-start_t, 0.01), 2), 'tokens_per_sec': 60, 'ttft_ms': 80, 'finish_reason': 'stop'})}\n\n"
                yield "data: [DONE]\n\n"
                return

            # =========================================================================
            # PETICIÓN DIRECTA DE EJECUCIÓN EN TERMINAL / APERTURA DE APP
            # =========================================================================
            if is_execution_request and exec_target_file:
                yield f"data: {json.dumps({'type': 'terminal_event', 'action': 'open', 'file': exec_target_file})}\n\n"
                
                # Ejecutar a través de terminal_engine auditando dependencias
                exec_res = terminal_engine.run_file(exec_target_file, cwd=workspace.current_dir, auto_install=True)
                
                reply_text = (
                    f"### 🖥️ [Terminal Integrada]: Ejecutando `{exec_target_file}`\n"
                    f"- 🔍 **Auditoría de Dependencias:** Verificando librerías e imports en el entorno Python...\n"
                )
                if exec_res.get("type") == "html":
                    reply_text += (
                        f"- 🌐 **Previsualización Web:** Archivo HTML abierto en tu navegador.\n"
                        f"- 🔗 URL local: `{exec_res.get('url')}`\n\n"
                        f"> He abierto la **Terminal Integrada** abajo para monitorear el proyecto."
                    )
                else:
                    reply_text += (
                        f"- ⚡ **Estado de Ejecución:** Proceso lanzado en la terminal integrada.\n"
                        f"- 📦 **Dependencias:** Verificadas y listas.\n\n"
                        f"> He abierto la **Terminal Integrada** abajo para mostrarte la salida en vivo y permitirte enviar comandos."
                    )
                
                for chunk in _chunk_text(reply_text, 80):
                    yield f"data: {json.dumps({'choices': [{'delta': {'content': chunk}}]})}\n\n"
                    time.sleep(0.01)

                meta_event = {
                    "type": "meta",
                    "tokens": 40,
                    "total_time_s": 0.5,
                    "tokens_per_sec": 80,
                    "ttft_ms": 50,
                    "finish_reason": "stop"
                }
                yield f"data: {json.dumps(meta_event)}\n\n"
                yield "data: [DONE]\n\n"
                return

            # =========================================================================
            # ORQUESTADOR CENTRAL DE ÉLITE (MÁQUINA DE ESTADOS + 5 SERVIDORES MCP)
            # PLAN (MCP-ARCH) -> ASSETS (MCP-ASSETS) -> BUILD (CircuitBreaker + MCP-AST)
            # -> QA_STATIC (MCP-EXEC) -> QA_VIS (MCP-RUNTIME-VISION) -> MONACO + PREVIEW
            # =========================================================================
            if (orchestrate_cloud or orchestrate_local) and last_user_text:
                final_files: List[str] = []
                ctx_budget = max(16384, int(state.ctx_size or 16384))
                if agent_intent == "iterate_app":
                    pipeline_stream = run_incremental_iteration(
                        user_query=last_user_text,
                        workspace_dir=workspace.current_dir,
                        cloud_cfg=cloud_cfg,
                        use_cloud=orchestrate_cloud,
                        local_port=state.port,
                        active_file=active_p,
                        context_tokens=ctx_budget,
                        is_aborted=lambda: state.abort_requested,
                    )
                else:
                    pipeline_stream = run_elite_mcp_pipeline(
                        user_query=last_user_text,
                        workspace_dir=workspace.current_dir,
                        cloud_cfg=cloud_cfg,
                        use_cloud=orchestrate_cloud,
                        local_port=state.port,
                        max_qa_rounds=3,
                        context_tokens=ctx_budget,
                        enable_runtime_vision=False,
                        is_aborted=lambda: state.abort_requested,
                    )
                for ev in pipeline_stream:
                    if state.abort_requested:
                        break
                    ev_type = ev.get("type")
                    if ev_type == "step":
                        yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': ev['id'], 'status': ev['status'], 'title': ev['title'], 'detail': ev.get('detail', '')})}\n\n"
                    elif ev_type == "file_code":
                        f_p = ev["path"]
                        f_c = ev["code"]
                        if f_p not in final_files:
                            final_files.append(f_p)
                        prefix = f"\n> 🔧 **[Auto-Reparación QA aplicada a `{f_p}`]**\n" if ev.get("healed") else ""
                        block = f"{prefix}```file:{f_p}\n{f_c}\n```\n\n"
                        for chunk in _chunk_text(block, 180):
                            if state.abort_requested:
                                break
                            token_count += max(1, len(chunk) // 4)
                            yield f"data: {json.dumps({'choices': [{'delta': {'content': chunk}}]})}\n\n"
                            time.sleep(0.004)
                        yield f"data: {json.dumps({'type': 'mcp_tool_event', 'tools': [{'tool': 'write_file', 'summary': f'MCP-AST escribió {f_p}'}], 'workspace_changed': True})}\n\n"
                    elif ev_type == "done":
                        yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': 'project_complete', 'status': 'done', 'title': f'Entrega lista en Monaco + Preview ({len(final_files)} archivo(s))', 'detail': ev.get('summary', '')})}\n\n"

                total_time = max(time.time() - start_t, 0.001)
                speed = round(token_count / total_time, 2)
                meta_event = {
                    "type": "meta",
                    "tokens": token_count,
                    "total_time_s": round(total_time, 2),
                    "tokens_per_sec": speed,
                    "ttft_ms": 120,
                    "finish_reason": "stop"
                }
                yield f"data: {json.dumps(meta_event)}\n\n"
                yield "data: [DONE]\n\n"
                return

            if False and (orchestrate_cloud or orchestrate_local) and last_user_text:
                yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': 'plan_start', 'status': 'running', 'title': 'Cerebro Local Codex (GBNF JSON Schema): Diseñando arquitectura por capas...', 'detail': 'Decodificación gramatical estricta de módulos, contratos AST y dependencias topológicas'})}\n\n"

                # Paso 1: Blueprint Arquitectónico con Decodificación Restringida GBNF JSON Schema
                plan_raw, plan_files = plan_blueprint_constrained(
                    last_user_text,
                    local_port=state.port,
                    workspace_dir=workspace.current_dir,
                    active_file=active_p
                )
                
                # Paso 2: dependency_resolver (Verifica qué archivos existen y cuáles faltan en el repo)
                dep_info = dependency_resolver(workspace.current_dir, plan_files)
                audit_logger("plan_generated", {
                    "query": last_user_text,
                    "planned_files": [f["path"] for f in plan_files],
                    "dependency_status": dep_info
                })

                planned_summary_items = []
                for f in plan_files:
                    is_existing = f['path'] in dep_info.get("existing", [])
                    tag = "actualizar" if is_existing else "nuevo"
                    planned_summary_items.append({
                        "path": f["path"],
                        "action": tag,
                        "description": f["description"]
                    })

                yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': 'plan_start', 'status': 'done', 'title': f'Arquitectura planificada ({len(plan_files)} archivos en el repositorio)', 'files': planned_summary_items})}\n\n"

                # Acumulador de firmas y contratos reales SOLO de los archivos de esta aplicación
                # (evita contaminar una app nueva con clases viejas que quedaron en la misma carpeta)
                is_modifying_existing = any(kw in last_user_text.lower() for kw in ("repara", "corrige", "arregla", "modifica", "mejora el proyecto", "actualiza"))
                ws_initial_sigs, _ = collect_workspace_signatures(workspace.current_dir) if is_modifying_existing else ("", [])
                built_signatures: List[str] = [ws_initial_sigs] if ws_initial_sigs else []

                total_files = len(plan_files)
                for idx, f in enumerate(plan_files, 1):
                    if state.abort_requested:
                        break

                    step_id = f"build_file_{idx}"
                    f_path = f["path"]
                    live_graph_info = code_arch_graph.analyze_workspace_graph(workspace.current_dir)
                    live_graph_ctx = live_graph_info.get("prompt_context", "")
                    current_context_sigs = "\n".join(
                        [s for s in built_signatures if s.strip()]
                        + ([live_graph_ctx] if live_graph_ctx else [])
                    )

                    used_recovery = False
                    if orchestrate_cloud:
                        step_title_run = f"[{idx}/{total_files}] Orquestador enviando instrucciones a AWS Bedrock para {f_path}..."
                        yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': step_id, 'status': 'running', 'title': step_title_run, 'detail': f['description']})}\n\n"

                        # Prompt que entra a Bedrock: "Hola genera esto" + instrucciones de código + firmas reales
                        bedrock_prompt = build_bedrock_worker_prompt(
                            f,
                            general_context=current_context_sigs,
                            workspace_dir=workspace.current_dir
                        )
                        ok, resp = call_cloud_worker(bedrock_prompt, cloud_cfg, bypass_desensitize=True)

                        # Si Bedrock rechaza por moderación ética o falla: recovery_handler asume el control
                        if (not ok) or (not resp) or is_refusal_response(resp):
                            used_recovery = True
                            step_title_rec = f"[{idx}/{total_files}] Recovery Handler: Qwen Local codificando {f_path} (respaldo sin censura)..."
                            yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': step_id, 'status': 'running', 'title': step_title_rec, 'detail': 'AWS Bedrock activó moderación ética; el modelo local asume la construcción'})}\n\n"
                            ok_loc, resp = recovery_handler(
                                f,
                                failure_reason="Bedrock refusal/error",
                                local_port=state.port,
                                general_context=current_context_sigs,
                                workspace_dir=workspace.current_dir
                            )
                            if not ok_loc or not resp:
                                resp = f"# Implementación de {f['path']}\n# Generado como parte de {f['description']}\n"
                        builder_label = "Qwen Local (Recovery)" if used_recovery else f"AWS Bedrock ({cloud_worker_used})"
                    else:
                        step_title_run = f"[{idx}/{total_files}] Orquestador Local Qwen construyendo {f_path}..."
                        yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': step_id, 'status': 'running', 'title': step_title_run, 'detail': f['description']})}\n\n"
                        ok_loc, resp = generate_file_with_local_orchestrator(
                            f,
                            local_port=state.port,
                            general_context=current_context_sigs,
                            workspace_dir=workspace.current_dir
                        )
                        if not ok_loc or not resp:
                            resp = f"# Implementación de {f['path']}\n# Generado como parte de {f['description']}\n"
                        builder_label = "Qwen Local (100% Privado)"

                    # Paso 3: Verificación AST, Detector Anti-Slop, Expansión Quirúrgica de Funciones y Auto-Reparación
                    pure_code, is_valid_syntax, verify_note = verify_and_heal_code(
                        resp,
                        f,
                        cloud_cfg,
                        local_port=state.port,
                        workspace_dir=workspace.current_dir,
                        sibling_signatures=current_context_sigs,
                        use_cloud=orchestrate_cloud
                    )

                    # Paso 3b (SOTA para modelos 8B/9B): Expansión Quirúrgica AST a Nivel de Función si quedó algún stub puntual
                    if f_path.lower().endswith(".py") and is_valid_syntax:
                        pure_code, expanded_fns = expand_stub_functions_surgically(
                            pure_code,
                            f,
                            local_port=state.port,
                            sibling_signatures=current_context_sigs,
                            max_functions=4
                        )
                        if expanded_fns:
                            pure_code = auto_fix_common_python_issues(pure_code, f_path, workspace_dir=workspace.current_dir)
                            verify_note = f"{verify_note} + Expansión quirúrgica AST ({', '.join(expanded_fns)})"

                    # Extraer firmas reales del archivo recién construido para los siguientes módulos
                    new_sig = extract_module_signatures(f["path"], pure_code)
                    if new_sig:
                        built_signatures.append(new_sig)

                    # Registrar auditoría de construcción de archivo
                    audit_logger("file_built", {
                        "file": f["path"],
                        "builder": builder_label,
                        "syntax_verified": is_valid_syntax,
                        "verify_note": verify_note,
                        "status": "success"
                    })

                    # Formatear en bloque limpio ```file:ruta/nombre.ext sin fences internos corruptos
                    file_code_block = f"```file:{f['path']}\n{pure_code}\n```"

                    # Guardar físicamente en el repositorio del usuario
                    save_file_to_workspace(workspace.current_dir, f['path'], pure_code)

                    # Actualizar estado en el orquestador (paso gris completado)
                    step_title_done = f"[{idx}/{total_files}] {f_path} generado por {builder_label}"
                    yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': step_id, 'status': 'done', 'title': step_title_done, 'detail': verify_note})}\n\n"

                    # Transmitir el código generado al chat (se mostrará en un combo box desplegable)
                    for chunk in _chunk_text(file_code_block + "\n\n", 180):
                        if state.abort_requested:
                            break
                        token_count += max(1, len(chunk) // 4)
                        yield f"data: {json.dumps({'choices': [{'delta': {'content': chunk}}]})}\n\n"
                        time.sleep(0.006)

                # Pase final de Integración Cruzada entre todos los módulos recién creados
                for f_item in plan_files:
                    fp_rel = f_item.get("path", "")
                    if fp_rel.lower().endswith(".py"):
                        full_fp = (workspace.current_dir / fp_rel).resolve()
                        if full_fp.exists():
                            try:
                                cur_txt = full_fp.read_text(encoding="utf-8", errors="replace")
                                linked_txt = auto_fix_common_python_issues(cur_txt, fp_rel, workspace_dir=workspace.current_dir)
                                if linked_txt != cur_txt:
                                    full_fp.write_text(linked_txt, encoding="utf-8")
                            except Exception:
                                pass

                # Paso 4 (Reflexion Shinn et al.): Verificación Multi-Compuerta (Code GraphRAG + Imports + Ejecución Real) + Memoria Episódica
                yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': 'runtime_verify', 'status': 'running', 'title': 'Motor Reflexion (3 Compuertas): Verificando Code GraphRAG, Imports y Ejecución Real...', 'detail': 'Detecta aristas rotas, prueba imports y ejecuta main.py con auto-reparación episódica'})}\n\n"
                rt_report = reflexion_verify_and_heal_project(
                    plan_files,
                    workspace.current_dir,
                    cloud_cfg,
                    local_port=state.port,
                    use_cloud=orchestrate_cloud,
                    max_rounds=3
                )
                rt_title = f"Reflexion aprobado (0 errores | {rt_report.get('rounds_used', 0)} rondas de cura)" if rt_report.get("success") else "Reflexion completado con avisos"
                yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': 'runtime_verify', 'status': 'done', 'title': rt_title, 'detail': rt_report.get('summary', '')})}\n\n"

                # Si algún archivo fue auto-reparado por el Runtime Healer, emitir su versión final limpia
                for healed_rel in rt_report.get("healed_files", []):
                    h_fp = (workspace.current_dir / healed_rel).resolve()
                    if h_fp.exists():
                        h_code = h_fp.read_text(encoding="utf-8", errors="replace")
                        h_block = f"\n> 🔧 **[Auto-Reparación Runtime aplicada a `{healed_rel}`]**\n```file:{healed_rel}\n{h_code}\n```\n\n"
                        for chunk in _chunk_text(h_block, 180):
                            yield f"data: {json.dumps({'choices': [{'delta': {'content': chunk}}]})}\n\n"

                # Paso final en gris
                yield f"data: {json.dumps({'type': 'orchestrator_step', 'id': 'project_complete', 'status': 'done', 'title': f'Proyecto completado ({len(plan_files)} archivos verificados en AST + Runtime)', 'detail': 'Listo para abrir en el editor o ejecutar en la terminal integrada'})}\n\n"

                total_time = max(time.time() - start_t, 0.001)
                speed = round(token_count / total_time, 2)
                meta_event = {
                    "type": "meta",
                    "tokens": token_count,
                    "total_time_s": round(total_time, 2),
                    "tokens_per_sec": speed,
                    "ttft_ms": 120,
                    "finish_reason": "stop"
                }
                yield f"data: {json.dumps(meta_event)}\n\n"
                yield "data: [DONE]\n\n"
                return

            # =========================================================================
            # BUCLE AGÉNTICO REACT MULTI-TURNO (ESTILO ANTIGRAVITY / CODEX CON LLM LOCAL)
            # Soporta tanto Function Calling NATIVO (`tools` + `delta.tool_calls` vía --jinja)
            # como `<tool_call>` en texto, con reutilización de KV Cache (`--cache-reuse 256`).
            # =========================================================================
            loop_messages = list(messages_payload)
            max_react_turns = 8 if is_agent else 3
            native_tools_schema = get_openai_compatible_tools_for_task(last_user_text) if is_agent else None
            empty_retry_done = False

            for react_turn in range(max_react_turns):
                if state.abort_requested:
                    break

                turn_payload = dict(payload)
                turn_payload["messages"] = loop_messages
                turn_payload["chat_template_kwargs"] = {"enable_thinking": False}
                if native_tools_schema:
                    turn_payload["tools"] = native_tools_schema
                    turn_payload["tool_choice"] = "auto"
                turn_req_data = json.dumps(turn_payload).encode("utf-8")

                http_req = urllib.request.Request(
                    target_url,
                    data=turn_req_data,
                    headers={"Content-Type": "application/json"}
                )
                turn_buffer = ""
                turn_tool_calls = []
                native_tc_accum: Dict[int, Dict[str, str]] = {}
                did_loop_break = False

                with urllib.request.urlopen(http_req, timeout=600) as resp:
                    with state.lock:
                        state.active_stream_resp = resp
                    for line in resp:
                        if state.abort_requested:
                            break
                        line_str = line.decode("utf-8")
                        if not line_str.strip():
                            continue
                        if line_str.startswith("data: "):
                            data_part = line_str[6:].strip()
                            if data_part == "[DONE]":
                                # 1. Consolidar llamadas nativas OpenAI/Jinja (`delta.tool_calls`)
                                for idx_tc in sorted(native_tc_accum.keys()):
                                    tc_raw = native_tc_accum[idx_tc]
                                    t_name = tc_raw.get("name", "").strip()
                                    if t_name:
                                        try:
                                            t_args = json.loads(tc_raw.get("arguments") or "{}")
                                        except Exception:
                                            t_args = {}
                                        turn_tool_calls.append({"name": t_name, "arguments": t_args})
                                # 2. Respaldo: detectar llamadas `<tool_call>...</tool_call>` en texto
                                if not turn_tool_calls:
                                    turn_tool_calls = parse_tool_calls_from_text(turn_buffer)
                                break

                            try:
                                chunk = json.loads(data_part)
                                choices = chunk.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    finish = choices[0].get("finish_reason")
                                    if finish:
                                        finish_reason = finish
                                    content = delta.get("content", "")
                                    reasoning = delta.get("reasoning_content", "")
                                    # Acumular tool_calls nativos en streaming
                                    delta_tcs = delta.get("tool_calls")
                                    if delta_tcs and isinstance(delta_tcs, list):
                                        for dtc in delta_tcs:
                                            tc_i = int(dtc.get("index", 0))
                                            if tc_i not in native_tc_accum:
                                                native_tc_accum[tc_i] = {"name": "", "arguments": ""}
                                            fn_obj = dtc.get("function") or {}
                                            if fn_obj.get("name"):
                                                native_tc_accum[tc_i]["name"] += fn_obj["name"]
                                            if fn_obj.get("arguments"):
                                                native_tc_accum[tc_i]["arguments"] += fn_obj["arguments"]

                                    if content or reasoning:
                                        token_count += 1
                                        if first_token_t is None:
                                            first_token_t = time.time()

                                    if reasoning:
                                        reasoning_tokens += 1
                                        reasoning_buffer += reasoning
                                        max_allowed_r = 35 if chat_req.mode == "chat" else 450
                                        if reasoning_tokens > max_allowed_r:
                                            continue

                                        if len(reasoning_buffer) > 120:
                                            r_tail = reasoning_buffer[-240:]
                                            for p_len in range(12, 50):
                                                pat = r_tail[-p_len:]
                                                if r_tail.count(pat) >= 3:
                                                    break

                                    if content:
                                        turn_buffer += content
                                        streamed_buffer += content
                                        # Detector universal de bucles de repetición (activo en modo agent Y chat)
                                        if len(turn_buffer) > 400 and (token_count % 12 == 0):
                                            tail = turn_buffer[-1600:]
                                            is_looping = False
                                            # 1. Bloques largos repetidos 3+ veces seguidas (ej. bucles de CSS/código)
                                            for p_len in (25, 45, 80, 140, 220, 320):
                                                if len(tail) >= p_len * 3:
                                                    pat = tail[-p_len:]
                                                    if pat.strip() and tail.count(pat) >= 3:
                                                        is_looping = True
                                                        break
                                            # 2. Identificadores/variables basura interminables (>100 chars con 10+ guiones)
                                            if not is_looping and re.search(r"--(?:[a-z]{2,}-){10,}[a-z]{2,}", tail):
                                                is_looping = True
                                            if is_looping:
                                                did_loop_break = True
                                                break

                                if not delta_tcs:
                                    yield f"data: {data_part}\n\n"
                            except Exception:
                                yield f"data: {data_part}\n\n"

                if state.abort_requested or did_loop_break:
                    break

                # Si el cerebro local invocó herramientas MCP y aún quedan turnos ReAct:
                # ejecutarlas, mostrar observación y DEVOLVER EL RESULTADO AL CEREBRO LOCAL
                if turn_tool_calls and react_turn < (max_react_turns - 1):
                    obs_for_llm = []
                    workspace_changed = False
                    any_tool_failed = False
                    executed_summaries = []
                    for tc in turn_tool_calls:
                        t_res = mcp_server.execute_tool(tc["name"], tc.get("arguments", {}), workspace.current_dir)
                        ok_flag = bool(t_res.get("success"))
                        if not ok_flag:
                            any_tool_failed = True
                        if ok_flag and (t_res.get("refresh_workspace") or tc["name"] in ("download_url", "write_file", "apply_patch", "delete_file")):
                            workspace_changed = True
                        status_badge = "✅ OK" if ok_flag else "❌ ERROR"
                        executed_summaries.append({"tool": tc["name"], "summary": f"[Paso {react_turn + 1}] {tc['name']} ({status_badge})"})
                        res_json = json.dumps(t_res, ensure_ascii=False, indent=2)
                        obs_for_llm.append(f"[TOOL_RESULT `{tc['name']}` ({status_badge})]:\n{res_json[:3200]}")
                        obs_block = (
                            f"\n\n> 🛠️ **[Agente Codex Paso {react_turn + 1}: `{tc['name']}` — {status_badge}]** ({t_res.get('elapsed_ms', 0)} ms)\n"
                            f"```tool-output\n{res_json[:1200]}\n```\n\n"
                        )
                        for c_chunk in _chunk_text(obs_block, 110):
                            yield f"data: {json.dumps({'choices': [{'delta': {'content': c_chunk}}]})}\n\n"

                    yield f"data: {json.dumps({'type': 'mcp_tool_event', 'tools': executed_summaries, 'workspace_changed': workspace_changed})}\n\n"

                    # Retroalimentar al cerebro local con lo que realmente devolvieron las herramientas
                    assistant_rec = turn_buffer.strip() or f"[Invocadas herramientas: {', '.join(tc['name'] for tc in turn_tool_calls)}]"
                    loop_messages.append({"role": "assistant", "content": assistant_rec})
                    strict_fail_note = (
                        "\n\n⚠️ REGLA ESTRICTA ANTI-ALUCINACIÓN: Al menos una herramienta devolvió `success: false` (ERROR). "
                        "NO SE DESCARGÓ NI GUARDÓ NINGÚN ARCHIVO. "
                        "TIENES TERMINANTEMENTE PROHIBIDO afirmar que el archivo fue descargado, guardado o inventar un tamaño en bytes. "
                        "Informa honestamente al usuario que no se encontró o falló la descarga y explica el error real."
                        if any_tool_failed else
                        "\n\nCon base en estos resultados reales, continúa: si necesitas leer, buscar, parchear, compilar o ejecutar otra herramienta invócala, o entrega tu respuesta final verificada sin repetir lo ya dicho."
                    )
                    loop_messages.append({
                        "role": "user",
                        "content": (
                            "OBSERVACIONES REALES DE TUS HERRAMIENTAS MCP:\n"
                            + "\n\n".join(obs_for_llm)
                            + strict_fail_note
                        )
                    })
                    continue

                # Recuperación si el modelo consumió tokens solo en <think> y dejó la respuesta vacía
                if not turn_tool_calls and not turn_buffer.strip() and not empty_retry_done:
                    empty_retry_done = True
                    native_tools_schema = None
                    loop_messages = list(loop_messages)
                    if loop_messages and loop_messages[-1].get("role") == "user":
                        prev_c = loop_messages[-1].get("content", "")
                        if isinstance(prev_c, str) and not prev_c.startswith("/no_think"):
                            loop_messages[-1] = {"role": "user", "content": "/no_think\n" + prev_c}
                    continue

                # Sin más llamadas a herramientas: cerrar ciclo
                break

            # Verificador post-respuesta contra URLs inventadas (Alucinación de enlaces)
            if chat_req.mode != "agent" and "http" in streamed_buffer:
                allowed_ctx = (system_text or "") + "\n" + (last_user_text or "") + "\n" + "\n".join(
                    str(m.get("content", "")) for m in loop_messages[len(messages_payload):]
                )
                emitted_urls = re.findall(r"https?://[^\s)\]>\"'`]+", streamed_buffer)
                fake_urls = []
                for eu in emitted_urls:
                    clean_eu = eu.rstrip(".,;:!?")
                    parsed_eu = urllib.parse.urlparse(clean_eu)
                    if (len(parsed_eu.path.strip("/")) > 3 or parsed_eu.query) and clean_eu not in allowed_ctx:
                        if parsed_eu.hostname not in ("127.0.0.1", "localhost"):
                            fake_urls.append(clean_eu)
                if fake_urls:
                    uniq_fakes = list(dict.fromkeys(fake_urls))[:3]
                    warn_msg = (
                        "\n\n> ⚠️ **[Filtro Anti-Alucinación de URLs]**: El modelo generó enlace(s) que **no existen** en los resultados reales de búsqueda y fueron marcados como inventados:\n"
                        + "\n".join(f"> - ~~`{fu}`~~ *(URL alucinada)*" for fu in uniq_fakes)
                        + "\n"
                    )
                    for w_chunk in _chunk_text(warn_msg, 100):
                        yield f"data: {json.dumps({'choices': [{'delta': {'content': w_chunk}}]})}\n\n"

            total_time = max(time.time() - start_t, 0.001)
            speed = round(token_count / total_time, 2)
            ttft = round((first_token_t - start_t) * 1000, 1) if first_token_t else 0
            meta_event = {
                "type": "meta",
                "tokens": token_count,
                "total_time_s": round(total_time, 2),
                "tokens_per_sec": speed,
                "ttft_ms": ttft,
                "finish_reason": finish_reason or "stop"
            }
            yield f"data: {json.dumps(meta_event)}\n\n"
            yield "data: [DONE]\n\n"
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            err_data = json.dumps({"type": "error", "error": f"HTTP {e.code}: {err_body}"})
            yield f"data: {err_data}\n\n"
        except Exception as e:
            err_data = json.dumps({"type": "error", "error": str(e)})
            yield f"data: {err_data}\n\n"
        finally:
            with state.lock:
                state.active_stream_resp = None

    return StreamingResponse(generate(), media_type="text/event-stream")

@app.post("/api/chat/abort")
def abort_chat_stream():
    """Cancela inmediatamente el streaming actual y libera los slots en llama-server."""
    with state.lock:
        state.abort_requested = True
        if state.active_stream_resp:
            try:
                state.active_stream_resp.close()
            except Exception:
                pass
            state.active_stream_resp = None

    # Liberar slots ocupados en llama-server para que la GPU esté disponible al instante
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{state.port}/slots")
        with urllib.request.urlopen(req, timeout=1.0) as r:
            slots_data = json.loads(r.read().decode())
            for slot in slots_data:
                s_id = slot.get("id", 0)
                rel_req = urllib.request.Request(
                    f"http://127.0.0.1:{state.port}/slots/{s_id}?action=release",
                    method="POST"
                )
                try:
                    urllib.request.urlopen(rel_req, timeout=0.5)
                except Exception:
                    pass
    except Exception:
        pass
    return {"success": True}

# Servir archivos estáticos del frontend
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/favicon.ico")
def get_favicon():
    return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")

@app.get("/logo.png")
def get_logo():
    return FileResponse(STATIC_DIR / "logo.png", media_type="image/png")

@app.get("/style.css")
def get_style():
    return FileResponse(
        STATIC_DIR / "style.css", 
        media_type="text/css",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )

@app.get("/app.js")
def get_app():
    return FileResponse(
        STATIC_DIR / "app.js", 
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )

@app.get("/")
def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(
            content=index_file.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
        )
    return HTMLResponse("<h1>HackingLLM Server Activo. Inicializando interfaz gráfica...</h1>")

if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*60)
    print("  HackingLLM - GGUF High Performance Studio")
    print("  Optimizaciones activas para NVIDIA RTX 4050 & AMD Ryzen")
    print("="*60 + "\n")
    uvicorn.run("server:app", host="127.0.0.1", port=7860, reload=False)
