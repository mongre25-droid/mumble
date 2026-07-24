' Launch Mumble with no console window (used by existing shortcuts).
'
' The controller owns the single-instance check. A second launch asks the healthy
' instance to reveal its window instead of force-killing it and orphaning WebView.
Option Explicit
Dim sh, fso, d, exe
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
' Source (and the venv) live in the app\ subfolder; launchers stay at the top.
d = fso.GetParentFolderName(WScript.ScriptFullName) & "\app"
sh.CurrentDirectory = d

' Launch the BRANDED Mumble.exe (so Task Manager / the window say "Mumble", per
' the round-A branding) when present; fall back to the venv pythonw otherwise.
exe = d & "\.venv\Scripts\Mumble.exe"
If Not fso.FileExists(exe) Then exe = d & "\.venv\Scripts\pythonw.exe"
sh.Run """" & exe & """ """ & d & "\mumble.py""", 0, False
