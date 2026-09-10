# Launch the built program and capture its own window from the real desktop session.
# PrintWindow (not CopyFromScreen): it asks the window to paint itself, so the capture is
# correct even if the desktop framebuffer is stale or the window is occluded.
# It also measures cold start: from Start-Process until the window handle exists. That is
# the number that matters for this deliverable (a onefile build unpacked ~1300 files into
# %TEMP% on every launch and took 3m44s on a real customer PC, which is why we ship onedir).
param([string]$Exe = "dist\signal-room-bot\signal-room-bot.exe",
      [int]$Wait = 8,
      [string]$Out = "screenshots/gui.png",
      [int]$Tab = 0,
      [string]$TimingOut = "")
$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Force -Path screenshots | Out-Null
$env:SIGNALROOM_NO_UPLOAD = "1"
$env:GUIDEMO_HOLD_MS = "180000"
$env:SIGNALROOM_DEMO_TAB = "$Tab"

Add-Type @"
using System;
using System.Drawing;
using System.Runtime.InteropServices;
public class Cap {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  public struct RECT { public int L, T, R, B; }
  public static void Shot(IntPtr h, string path) {
    RECT r; GetWindowRect(h, out r);
    int w = r.R - r.L, ht = r.B - r.T;
    using (Bitmap bmp = new Bitmap(w, ht))
    using (Graphics g = Graphics.FromImage(bmp)) {
      IntPtr dc = g.GetHdc();
      PrintWindow(h, dc, 2);
      g.ReleaseHdc(dc);
      bmp.Save(path, System.Drawing.Imaging.ImageFormat.Png);
    }
  }
}
"@ -ReferencedAssemblies System.Drawing, System.Windows.Forms

$name = [System.IO.Path]::GetFileNameWithoutExtension($Exe)
$clock = [System.Diagnostics.Stopwatch]::StartNew()
Start-Process -FilePath $Exe -ArgumentList "--guidemo"

# Poll by process NAME, not by the handle of the process we started: a onefile
# bootloader parent never owns the window, its child does.
$handle = [IntPtr]::Zero
foreach ($i in 1..240) {
  foreach ($p in (Get-Process -Name $name -ErrorAction SilentlyContinue)) {
    if ($p.MainWindowHandle -ne 0) { $handle = $p.MainWindowHandle }
  }
  if ($handle -ne [IntPtr]::Zero) { break }
  Start-Sleep -Milliseconds 500
}
$clock.Stop()
if ($handle -eq [IntPtr]::Zero) { Write-Host "no window handle"; exit 1 }
$cold = [math]::Round($clock.Elapsed.TotalSeconds, 2)
Write-Host "cold start to window: $cold s ($Exe)"
if ($TimingOut) { "cold start to first window: $cold s ; exe: $Exe" | Out-File -Encoding utf8 $TimingOut }

Start-Sleep -Seconds $Wait
[Cap]::Shot($handle, (Join-Path (Get-Location) $Out))
Write-Host "captured $Out"
Get-Process -Name $name -ErrorAction SilentlyContinue | Stop-Process -Force
