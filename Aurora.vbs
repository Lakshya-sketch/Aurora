' Aurora v2 — Silent Windows Launcher (VBScript)
' Starts Python backend in background, launches Electron UI without console flash,
' and terminates the backend cleanly when Electron exits.
' See AGENTS.md Rule #6 (clean process lifecycle management).

Option Explicit

Dim fso, wshShell, auroraHome, venvPython, pyExe, cmdBackend, cmdElectron, cmdCleanup

Set fso = CreateObject("Scripting.FileSystemObject")
Set wshShell = CreateObject("WScript.Shell")

' 1. Determine Aurora home directory
auroraHome = fso.GetParentFolderName(WScript.ScriptFullName)
wshShell.CurrentDirectory = auroraHome

' 2. Select Python executable (venv or system python)
venvPython = auroraHome & "\venv\Scripts\python.exe"
If fso.FileExists(venvPython) Then
    pyExe = """" & venvPython & """"
Else
    pyExe = "python"
End If

' 3. Start Python backend (detached, minimized/hidden)
' WindowStyle 0 = Hidden, bWaitOnReturn = False (async)
cmdBackend = "cmd /c start ""Aurora Backend"" /min " & pyExe & " -m aurora_core"
wshShell.Run cmdBackend, 0, False

' 4. Wait 3 seconds for backend server to bind port
WScript.Sleep 3000

' 5. Start Electron UI (hidden cmd prompt, blocks until Electron closes)
' WindowStyle 0 = Hidden console, bWaitOnReturn = True (sync)
cmdElectron = "cmd /c npm start --prefix """ & auroraHome & """"
wshShell.Run cmdElectron, 0, True

' 6. Cleanup: Stop Python backend when Electron window is closed
cmdCleanup = "cmd /c taskkill /FI ""WindowTitle eq Aurora Backend*"" /F >nul 2>&1"
wshShell.Run cmdCleanup, 0, True

Set wshShell = Nothing
Set fso = Nothing