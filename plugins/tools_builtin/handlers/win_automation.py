import asyncio
import ctypes
import ctypes.wintypes
import logging
import os
import platform
import shutil
import subprocess

import winreg

logger = logging.getLogger("aurora.tools.win_automation")

# Tracked processes to ensure no unmanaged background services (AGENTS.md Rule 6)
_TRACKED_APPS = []

# Common application aliases
_APP_ALIASES = {
    "notepad": "notepad.exe",
    "calc": "calc.exe",
    "calculator": "calc.exe",
    "explorer": "explorer.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "edge": "msedge.exe",
    "vscode": "code.exe",
    "code": "code.exe",
}

user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)


def _resolve_app_path(app_name: str) -> str:
    """Resolve an app name or alias to its full executable path on Windows."""
    clean = app_name.strip().lower()
    target = _APP_ALIASES.get(clean, app_name)
    target_with_ext = target if target.lower().endswith(".exe") else f"{target}.exe"

    # 1. Direct path exists or in system PATH
    if os.path.isabs(target) and os.path.isfile(target):
        return target
    which = shutil.which(target) or shutil.which(target_with_ext)
    if which:
        return which

    # 2. Windows Registry App Paths (HKLM and HKCU)
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for candidate in (target_with_ext, target):
            try:
                sub_key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{candidate}"
                with winreg.OpenKey(root, sub_key) as key:
                    path, _ = winreg.QueryValueEx(key, "")
                    if path and os.path.isfile(path):
                        return path
            except Exception:
                pass

    # 3. Known standard locations for common applications
    common_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    ]
    for cp in common_paths:
        if target_with_ext.lower() in cp.lower() and os.path.isfile(cp):
            return cp

    return target


def _get_window_text(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _get_window_list():
    windows = []
    def callback(hwnd, lParam):
        if user32.IsWindowVisible(hwnd):
            text = _get_window_text(hwnd)
            if text:
                windows.append({"hwnd": hwnd, "title": text})
        return True
    
    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return windows


async def handle(args: dict) -> dict:
    action = args.get('action')
    if not action:
        logger.warning("No action provided in win_automation")
        return {"ok": False, "data": None, "error": "Missing 'action' in args"}

    try:
        if action == 'open_app':
            app_name = args.get('app_name')
            if not app_name:
                logger.warning("open_app called without app_name")
                return {"ok": False, "data": None, "error": "Missing 'app_name'"}
            
            resolved_exe = _resolve_app_path(app_name)
            logger.info(f"Opening application: {resolved_exe}")
            
            # FIX: Use resolved executable path and track the handle to avoid untracked background services
            proc = subprocess.Popen(
                f'"{resolved_exe}"' if " " in resolved_exe else resolved_exe,
                shell=True
            )
            _TRACKED_APPS.append({"app": resolved_exe, "pid": proc.pid})
            
            return {"ok": True, "data": {"app": resolved_exe, "pid": proc.pid}, "error": None}

        elif action == 'get_window_list':
            logger.info("Getting window list")
            windows = _get_window_list()
            return {"ok": True, "data": {"windows": windows}, "error": None}

        elif action == 'focus_window':
            title_contains = args.get('title_contains')
            if not title_contains:
                logger.warning("focus_window called without title_contains")
                return {"ok": False, "data": None, "error": "Missing 'title_contains'"}
            
            logger.info(f"Focusing window containing: {title_contains}")
            windows = _get_window_list()
            matched_hwnd = None
            matched_title = None
            
            for w in windows:
                if title_contains.lower() in w['title'].lower():
                    matched_hwnd = w['hwnd']
                    matched_title = w['title']
                    break
            
            if matched_hwnd:
                # SW_RESTORE = 9
                user32.ShowWindow(matched_hwnd, 9)
                user32.SetForegroundWindow(matched_hwnd)
                return {"ok": True, "data": {"focused_title": matched_title, "hwnd": matched_hwnd}, "error": None}
            else:
                logger.info(f"No window found matching '{title_contains}'")
                return {"ok": False, "data": None, "error": f"No window found matching '{title_contains}'"}
            
        elif action == 'get_system_info':
            logger.info("Getting system info")
            cpu_info = platform.processor()
            cpu_cores = os.cpu_count()
            os_info = f"{platform.system()} {platform.release()} {platform.version()}"
            
            disk = shutil.disk_usage("C:\\")
            disk_info = {
                "total_gb": round(disk.total / (1024**3), 2),
                "used_gb": round(disk.used / (1024**3), 2),
                "free_gb": round(disk.free / (1024**3), 2)
            }
            
            ram_info = {}
            try:
                # Psutil-free approach using WMI via subprocess
                wmic_out = subprocess.check_output(
                    "wmic OS get FreePhysicalMemory,TotalVisibleMemorySize /Value", 
                    shell=True, text=True
                )
                for line in wmic_out.splitlines():
                    if '=' in line:
                        k, v = line.strip().split('=', 1)
                        if v.isdigit():
                            # WMI returns KB for these values, convert to GB
                            ram_info[k] = round(int(v) / (1024**2), 2)
            except Exception as e:
                logger.warning(f"Failed to get RAM info via wmic: {e}")
                ram_info = {"error": str(e)}

            sys_data = {
                "os": os_info,
                "cpu": cpu_info,
                "cpu_cores": cpu_cores,
                "disk_c": disk_info,
                "ram_gb": ram_info
            }
            return {"ok": True, "data": sys_data, "error": None}

        else:
            logger.warning(f"Unknown action: {action}")
            return {"ok": False, "data": None, "error": f"Unknown action: {action}"}

    except Exception as e:
        logger.warning(f"Exception in win_automation handle: {str(e)}")
        return {"ok": False, "data": None, "error": str(e)}
