<#
  StaleGuard live demo on Windows: Tavily + Bifrost + LLM, in one command.

  1. Keys (put them in a .env file in the repo folder, one KEY=value per line, or set them as env vars):
       TAVILY_API_KEY=...       https://app.tavily.com           (free tier)
       GEMINI_API_KEY=...       https://aistudio.google.com      (free tier)   <- primary model
       GROQ_API_KEY=...         https://console.groq.com/keys    (free tier)   <- Bifrost fallback (optional)
       ANTHROPIC_API_KEY=...    https://console.anthropic.com    (prepaid credit; used only with LLM_PROVIDER=anthropic)
  2. Run:  powershell -ExecutionPolicy Bypass -File scripts\live-demo.ps1
  3. Open http://localhost:8000

  Every LLM call goes through Bifrost. The primary provider is chosen automatically (Gemini, then Groq),
  or set LLM_PROVIDER=gemini|groq|anthropic. Bifrost falls back to the other providers you have keys for.
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

if (-not $env:TAVILY_API_KEY) { Write-Host "Set TAVILY_API_KEY in .env first (https://app.tavily.com)." -ForegroundColor Red; exit 1 }
if (-not (Get-Command npx -ErrorAction SilentlyContinue)) { Write-Host "Node.js is required for Bifrost: https://nodejs.org" -ForegroundColor Red; exit 1 }

# Provider catalogue: Bifrost provider name -> key variable and the models StaleGuard uses on it
$catalog = [ordered]@{
  gemini    = @{ key = "GEMINI_API_KEY";    extract = "gemini-3.5-flash-lite"; judge = "gemini-3.5-flash-lite" }
  groq      = @{ key = "GROQ_API_KEY";      extract = "openai/gpt-oss-120b";   judge = "openai/gpt-oss-120b" }
  anthropic = @{ key = "ANTHROPIC_API_KEY"; extract = "claude-haiku-5-5";      judge = "claude-sonnet-5-5" }
}
$available = @($catalog.Keys | Where-Object { [Environment]::GetEnvironmentVariable($catalog[$_].key) })
# Anthropic's API needs prepaid credit (a Claude Pro plan doesn't cover it), so it is only used when asked for
$usable = @($available | Where-Object { $_ -ne "anthropic" -or $env:LLM_PROVIDER -eq "anthropic" })
if (-not $usable.Count) {
  Write-Host "Add GEMINI_API_KEY (https://aistudio.google.com) and/or GROQ_API_KEY (https://console.groq.com) to .env - both are free." -ForegroundColor Red; exit 1
}
$primary = if ($env:LLM_PROVIDER -and $usable -contains $env:LLM_PROVIDER) { $env:LLM_PROVIDER } else { $usable[0] }
$others = @($usable | Where-Object { $_ -ne $primary })

# Models: always derived from the providers you have keys for (old model lines in .env are ignored).
# With two providers, extraction runs on the primary and judging on the second, and each falls back to
# the other through Bifrost - Gemini's free-tier Flash model is too slow for interactive use.
$env:EXTRACT_MODEL = "$primary/$($catalog[$primary].extract)"
if ($others.Count) {
  $env:JUDGE_MODEL = "$($others[0])/$($catalog[$others[0]].judge)"
} else {
  $env:JUDGE_MODEL = "$primary/$($catalog[$primary].judge)"
}
$fb = @($env:EXTRACT_MODEL, $env:JUDGE_MODEL) + @($others | ForEach-Object { "$_/$($catalog[$_].extract)" })
$env:LLM_FALLBACKS = ($fb | Select-Object -Unique) -join ","

# Bifrost config: only the providers in use. Keys stay in env vars, never in the file.
$providers = [ordered]@{}
foreach ($p in $usable) {
  $c = $catalog[$p]
  $providers[$p] = @{ keys = @(@{ name = $p; value = "env.$($c.key)"; models = @(@($c.extract, $c.judge) | Select-Object -Unique); weight = 1.0 }) }
}
$appDir = Join-Path (Get-Location) "bifrost\live-demo"
New-Item -ItemType Directory -Force -Path $appDir | Out-Null
$config = [ordered]@{
  # Newer Bifrost builds require a virtual key on every request unless this is off; this demo uses none.
  client       = @{ enforce_auth_on_inference = $false }
  # File-only mode: config.json is the source of truth on every start (no settings left over in config.db).
  config_store = @{ enabled = $false }
  providers    = $providers
}
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
