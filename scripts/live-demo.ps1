<#
  StaleGuard live demo on Windows: Tavily + Bifrost + Claude, in one command.

  1. Keys:
       Tavily     https://app.tavily.com               -> $env:TAVILY_API_KEY     (free tier available)
       Anthropic  https://console.anthropic.com        -> $env:ANTHROPIC_API_KEY  (Claude models)
  2. In PowerShell, from the repo folder:
       $env:TAVILY_API_KEY    = "tvly-..."
       $env:ANTHROPIC_API_KEY = "sk-ant-..."
       powershell -ExecutionPolicy Bypass -File scripts\live-demo.ps1
  3. Open http://localhost:8000  (Bifrost dashboard: http://localhost:8080)

  Claude Haiku extracts claims and writes the corrected answer, Claude Sonnet judges each claim, and Bifrost
  falls back from one Claude model to the other if a call fails. Override with $env:EXTRACT_MODEL /
  $env:JUDGE_MODEL / $env:LLM_FALLBACKS (Bifrost "anthropic/<model>" names).
  Needs Node.js (for `npx @maximhq/bifrost`) and Python with requirements.txt installed.
#>
param([int]$Port = 8000, [int]$BifrostPort = 8080)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not $env:TAVILY_API_KEY)    { Write-Host "Set `$env:TAVILY_API_KEY first (https://app.tavily.com)." -ForegroundColor Red; exit 1 }
if (-not $env:ANTHROPIC_API_KEY) { Write-Host "Set `$env:ANTHROPIC_API_KEY first (https://console.anthropic.com)." -ForegroundColor Red; exit 1 }
if (-not (Get-Command npx -ErrorAction SilentlyContinue)) { Write-Host "Node.js is required for Bifrost: https://nodejs.org" -ForegroundColor Red; exit 1 }

if (-not $env:EXTRACT_MODEL) { $env:EXTRACT_MODEL = "anthropic/claude-haiku-5-5" }
if (-not $env:JUDGE_MODEL)   { $env:JUDGE_MODEL   = "anthropic/claude-sonnet-5-5" }
if (-not $env:LLM_FALLBACKS) { $env:LLM_FALLBACKS = "anthropic/claude-sonnet-5-5,anthropic/claude-haiku-5-5" }
$models = @($env:EXTRACT_MODEL, $env:JUDGE_MODEL) + ($env:LLM_FALLBACKS -split ",") |
          Where-Object { $_ } | ForEach-Object { ($_ -split "/", 2)[-1].Trim() } | Sort-Object -Unique

# Bifrost config: the Anthropic provider only. The key stays in the env var, never in the file.
$appDir = Join-Path (Get-Location) "bifrost\live-demo"
New-Item -ItemType Directory -Force -Path $appDir | Out-Null
$config = @{ providers = @{ anthropic = @{ keys = @(@{ name = "anthropic"; value = "env.ANTHROPIC_API_KEY"; models = @($models); weight = 1.0 }) } } }
$config | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $appDir "config.json")

$env:STALEGUARD_MODE = "live"
$env:BIFROST_URL = "http://localhost:$BifrostPort"
$env:BIFROST_VIRTUAL_KEY = ""

Write-Host "Starting Bifrost on :$BifrostPort ..." -ForegroundColor Cyan
$bifrost = Start-Process -FilePath "npx.cmd" -ArgumentList @("-y", "@maximhq/bifrost", "-app-dir", "`"$appDir`"", "-port", "$BifrostPort") -PassThru
$up = $false
foreach ($i in 1..90) {
  foreach ($path in @("/health", "/")) {
    try { Invoke-WebRequest -UseBasicParsing "http://localhost:$BifrostPort$path" -TimeoutSec 2 | Out-Null; $up = $true; break } catch {}
  }
  if ($up) { break }
  Start-Sleep 1
}
if (-not $up) { Write-Host "Bifrost did not come up on :$BifrostPort - check its window for errors." -ForegroundColor Red; exit 1 }
Write-Host "Bifrost is up. Extract: $env:EXTRACT_MODEL  Judge: $env:JUDGE_MODEL  Fallbacks: $env:LLM_FALLBACKS" -ForegroundColor Green

Write-Host "Starting StaleGuard on http://localhost:$Port ..." -ForegroundColor Cyan
try { python -m uvicorn staleguard.server:app --port $Port }
finally { if ($bifrost -and -not $bifrost.HasExited) { Stop-Process -Id $bifrost.Id -Force } }
