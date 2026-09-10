# Capture the running GUI from a real desktop session (GitHub Actions windows-latest
# runs interactively enough for PrintWindow to return real pixels).
param([string]$Exe = "dist\signal-room-bot.exe", [int]$Wait = 40)
$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Force -Path screenshots | Out-Null
$env:SIGNALROOM_NO_UPLOAD = "1"
$env:GUIDEMO_HOLD_MS = "180000"
Start-Process -FilePath $Exe -ArgumentList "--guidemo"
Start-Sleep -Seconds $Wait

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

$handle = [IntPtr]::Zero
foreach ($i in 1..30) {
  $procs = Get-Process -Name "signal-room-bot" -ErrorAction SilentlyContinue
  foreach ($p in $procs) { if ($p.MainWindowHandle -ne 0) { $handle = $p.MainWindowHandle } }
  if ($handle -ne [IntPtr]::Zero) { break }
  Start-Sleep -Seconds 2
}
if ($handle -eq [IntPtr]::Zero) { Write-Host "no window handle"; exit 1 }
[Cap]::Shot($handle, (Join-Path (Get-Location) "screenshots\gui.png"))
Write-Host "captured screenshots\gui.png"
Get-Process -Name "signal-room-bot" -ErrorAction SilentlyContinue | Stop-Process -Force
