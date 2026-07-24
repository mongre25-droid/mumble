// Mumble.exe — the forefront launcher for the distributed package.
//
// Layout after extraction:
//   Mumble\
//     Mumble.exe        <- this (double-click to start; carries the Mumble icon)
//     Internal\         <- all the working files
//
// First run (no .venv yet) -> runs Internal\install.ps1, which sets up Python +
// dependencies, builds shortcuts, and launches Mumble. Every run after that ->
// launches the installed app directly. A tiny native .NET exe: no bundled runtime,
// no antivirus noise.
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

class MumbleLauncher
{
    static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string dir = Path.Combine(root, "Internal");
        // The source + venv live in Internal\app; the launchers (incl. the VBS)
        // stay at the top of Internal.
        string appdir = Path.Combine(dir, "app");
        string brandedExe = Path.Combine(appdir, ".venv", "Scripts", "Mumble.exe");
        string pyw = Path.Combine(appdir, ".venv", "Scripts", "pythonw.exe");
        string script = Path.Combine(appdir, "mumble.py");
        try
        {
            if (!Directory.Exists(dir))
            {
                MessageBox.Show(
                    "Mumble's \"Internal\" folder is missing. Re-extract the whole "
                    + "zip (keep Mumble.exe next to the Internal folder) and try again.",
                    "Mumble", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            if (File.Exists(brandedExe) || File.Exists(pyw))
            {
                // Launch the controller directly. Its single-instance channel
                // reveals an existing window without orphaning the WebView.
                string exe = File.Exists(brandedExe) ? brandedExe : pyw;
                ProcessStartInfo psi = new ProcessStartInfo(exe, "\"" + script + "\"");
                psi.WorkingDirectory = dir;
                psi.UseShellExecute = false;
                Process.Start(psi);
            }
            else
            {
                // First run -> install. install.ps1 sets up Python + deps, creates
                // shortcuts, then launches Mumble itself.
                string ps1 = Path.Combine(appdir, "install.ps1");
                var psi = new ProcessStartInfo("powershell.exe",
                    "-NoProfile -ExecutionPolicy Bypass -File \"" + ps1 + "\"");
                psi.WorkingDirectory = dir;
                psi.UseShellExecute = true;   // show install progress
                Process.Start(psi);
            }
        }
        catch (Exception ex)
        {
            MessageBox.Show("Couldn't start Mumble:\n\n" + ex.Message,
                "Mumble", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
