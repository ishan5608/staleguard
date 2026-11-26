<#
  StaleGuard live demo on Windows: Tavily + Bifrost + Claude, in one command.

  1. Keys:
       Tavily     https://app.tavily.com               -> $env:TAVILY_API_KEY     (free tier available)
       Anthropic  https://console.anthropic.com        -> $env:ANTHROPIC_API_KEY  (Claude models)
  2. Put them in a .env file in the repo folder (or set them as env vars in PowerShell):
       TAVILY_API_KEY=tvly-...
       ANTHROPIC_API_KEY=sk-ant-...
     then run:  powershell -ExecutionPolicy Bypass -File scripts\live-demo.ps1
  3. Open http://localhost:8000  (Bifrost dashboard: http://localhost:8080)

  Claude Haiku extracts claims and writes the corrected answer, Claude Sonnet judges each claim, and Bifrost
  falls back from one Claude model to the other if a call fails. Override with $env:EXTRACT_MODEL /
  $env:JUDGE_MODEL / $env:LLM_FALLBACKS (Bifrost "anthropic/<model>" names).
  Needs Node.js (for `npx @maximhq/bifrost`) and Python with requirements.txt installed.
#>
param([int]$Port = 8000, [int]$BifrostPort = 8080)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# Keys can also live in a .env file next to README.md (KEY=value per line); env vars already set win.
if (Test-Path ".env") {
  foreach ($line in Get-Content ".env") {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$' -and -not $line.TrimStart().StartsWith("#")) {
      $name = $Matches[1]; $value = $Matches[2].Trim('"').Trim("'")
      if ($value -and -not [Environment]::GetEnvironmentVariable($name)) { Set-Item -Path "env:$name" -Value $value }
    }
  }
}

if (-not $env:TAVILY_API_KEY)    { Write-Host "Set `$env:TAVILY_API_KEY first (https://app.tavily.com)." -ForegroundColor Red; exit 1 }
if (-not $env:ANTHROPIC_API_KEY) { Write-Host "Set `$env:ANTHROPIC_API_KEY first (https://console.anthropic.com)." -ForegroundColor Red; exit 1 }
if (-not (Get-Command npx -ErrorAction SilentlyContinue)) { Write-Host "Node.js is required for Bifrost: https://nodejs.org" -ForegroundColor Red; exit 1 }

# Claude models only: anything else (e.g. old openai/... lines in .env) is replaced with the defaults
if ($env:EXTRACT_MODEL -notlike "anthropic/*") { $env:EXTRACT_MODEL = "anthropic/claude-haiku-5-5" }
if ($env:JUDGE_MODEL   -notlike "anthropic/*") { $env:JUDGE_MODEL   = "anthropic/claude-sonnet-5-5" }
$fallbacks = @(($env:LLM_FALLBACKS -split ",") | ForEach-Object { $_.Trim() } | Where-Object { $_ -like "anthropic/*" })
$env:LLM_FALLBACKS = if ($fallbacks.Count) { $fallbacks -join "," } else { "anthropic/claude-sonnet-5-5,anthropic/claude-haiku-5-5" }
$models = @($env:EXTRACT_MODEL, $env:JUDGE_MODEL) + ($env:LLM_FALLBACKS -split ",") |
          Where-Object { $_ } | ForEach-Object { ($_ -split "/", 2)[-1].Trim() } | Sort-Object -Unique

# Bifrost config: the Anthropic provider only. The key stays in the env var, never in the file.
$appDir = Join-Path (Get-Location) "bifrost\live-demo"
New-Item -ItemType Directory -Force -Path $appDir | Out-Null
$config = @{ providers = @{ anthropic = @{ keys = @(@{ name = "anthropic"; value = "env.ANTHROPIC_API_KEY"; models = @($models); weight = 1.0 }) } } }
# Windows PowerShell's "-Encoding utf8" adds a byte-order mark, which Bifrost's JSON parser rejects
[System.IO.File]::WriteAllText((Join-Path $appDir "config.json"), ($config | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding $false))

$env:STALEGUARD_MODE = "live"
$env:BIFROST_URL = "http://localhost:$BifrostPort"
# this Bifrost has no governance config, so no virtual key; and stop StaleGuard re-reading .env,
# whose old lines (models, virtual key) would otherwise fill in anything left unset here
Remove-Item env:BIFROST_VIRTUAL_KEY -ErrorAction SilentlyContinue
$env:STALEGUARD_NO_DOTENV = "1"

Write-Host "Starting Bifrost on :$BifrostPort (first run downloads it, this can take a minute) ..." -ForegroundColor Cyan
$log = Join-Path $appDir "bifrost.log"; $errLog = Join-Path $appDir "bifrost.err.log"
$bifrost = Start-Process -FilePath "npx.cmd" -ArgumentList @("-y", "@maximhq/bifrost", "-app-dir", "`"$appDir`"", "-port", "$BifrostPort", "-host", "127.0.0.1") `
           -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $errLog -PassThru

function Test-Port([int]$p) {
  $c = New-Object System.Net.Sockets.TcpClient
  try { $c.ConnectAsync("127.0.0.1", $p).Wait(800) -and $c.Connected } catch { $false } finally { $c.Close() }
}
function Show-BifrostLog {
  Write-Host "---- Bifrost output ----" -ForegroundColor Yellow
  foreach ($f in @($log, $errLog)) { if (Test-Path $f) { Get-Content $f -Tail 25 } }
  Write-Host "------------------------" -ForegroundColor Yellow
}

$up = $false
foreach ($i in 1..180) {
  if (Test-Port $BifrostPort) { $up = $true; break }
  if ($bifrost.HasExited) { Write-Host "Bifrost exited (code $($bifrost.ExitCode))." -ForegroundColor Red; Show-BifrostLog; exit 1 }
  if ($i % 10 -eq 0) { Write-Host "  still waiting for Bifrost ($i s)..." }
  Start-Sleep 1
}
if (-not $up) { Write-Host "Bifrost did not come up on :$BifrostPort within 3 minutes." -ForegroundColor Red; Show-BifrostLog; exit 1 }
$env:BIFROST_URL = "http://127.0.0.1:$BifrostPort"
Write-Host "Bifrost is up. Extract: $env:EXTRACT_MODEL  Judge: $env:JUDGE_MODEL  Fallbacks: $env:LLM_FALLBACKS" -ForegroundColor Green

Write-Host "Starting StaleGuard on http://localhost:$Port ..." -ForegroundColor Cyan
try { python -m uvicorn staleguard.server:app --port $Port }
finally {
  # npx starts Bifrost as a child process: stop the whole tree
  if ($bifrost -and -not $bifrost.HasExited) { & taskkill /PID $bifrost.Id /T /F | Out-Null }
}
