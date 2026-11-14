' Aurora v2 — Silent Windows Launcher (VBScript)
' Starts Ollama (if configured), Python backend in background, launches Electron UI
' without console flash, and terminates the backend cleanly when Electron exits.
' See AGENTS.md Rule #6 (clean process lifecycle management).

Option Explicit

' --- Configuration Options ---
Const AUTO_START_OLLAMA = True   ' Set to False if you manage Ollama manually

Dim fso, wshShell, auroraHome, venvPython, pyExe, cmdBackend, cmdElectron, cmdCleanup
Dim objWMI, colOllama

Set fso = CreateObject("Scripting.FileSystemObject")
Set wshShell = CreateObject("WScript.Shell")

' 1. Determine Aurora home directory
auroraHome = fso.GetParentFolderName(WScript.ScriptFullName)
wshShell.CurrentDirectory = auroraHome

' 2. Check and start Ollama in background if not already running
If AUTO_START_OLLAMA Then
    On Error Resume Next
    Set objWMI = GetObject("winmgmts:\\.\root\cimv2")
    If Err.Number = 0 Then
        Set colOllama = objWMI.ExecQuery("Select * from Win32_Process Where Name = 'ollama.exe' or Name = 'ollama app.exe'")
        If colOllama.Count = 0 Then
            ' Launch ollama serve completely hidden (0 = Hidden, False = Async)
            wshShell.Run "cmd /c ollama serve", 0, False
            WScript.Sleep 2000  ' Brief pause for Ollama daemon to initialize port 11434
        End If
        Set colOllama = Nothing
    End If
    Set objWMI = Nothing
    On Error GoTo 0
End If

' 3. Select Python executable (venv or system python)
venvPython = auroraHome & "\venv\Scripts\python.exe"
If fso.FileExists(venvPython) Then
    pyExe = """" & venvPython & """"
Else
    pyExe = "python"
End If

' 4. Start Python backend (detached, minimized/hidden)
' WindowStyle 0 = Hidden, bWaitOnReturn = False (async)
cmdBackend = "cmd /c start ""Aurora Backend"" /min " & pyExe & " -m aurora_core"
wshShell.Run cmdBackend, 0, False

' 5. Wait 3 seconds for backend server to bind port
WScript.Sleep 3000

' 6. Start Electron UI (hidden cmd prompt, blocks until Electron closes)
' WindowStyle 0 = Hidden console, bWaitOnReturn = True (sync)
cmdElectron = "cmd /c npm start --prefix """ & auroraHome & """"
wshShell.Run cmdElectron, 0, True

' 7. Cleanup: Stop Python backend when Electron window is closed
cmdCleanup = "cmd /c taskkill /FI ""WindowTitle eq Aurora Backend*"" /F >nul 2>&1"
wshShell.Run cmdCleanup, 0, True

Set wshShell = Nothing
Set fso = Nothing
