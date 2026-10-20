# Synchronise les sources de production (E:) vers l'export GitHub (I:\ia\export_github)
# Usage : powershell -NoProfile -ExecutionPolicy Bypass -File export_github.ps1
$ErrorActionPreference = 'Continue'
$EXPORT = 'I:\ia\export_github'

function Sync([string]$src, [string]$dst, [string[]]$exclude) {
    if (-not (Test-Path $src)) { Write-Output "ABSENT: $src"; return }
    $opts = @($src, $dst, '/E', '/NFL', '/NDL', '/NJH', '/R:1', '/W:1', '/XD')
    foreach ($x in $exclude) { $opts += $x }
    robocopy @opts | Out-Null
    Write-Output "OK: $src -> $dst"
}

New-Item -ItemType Directory -Force -Path "$EXPORT\code", "$EXPORT\docs\skills\localai", "$EXPORT\assets" | Out-Null

# --- Rust (sources + manifests, sans target/) ---
Sync 'E:\localai-rs\localai-core'   "$EXPORT\code\localai-rs\localai-core"   @('target', '__pycache__')
Sync 'E:\localai-rs\localai-server' "$EXPORT\code\localai-rs\localai-server" @('target', '__pycache__')
Sync 'E:\localai-rs\localai-watch'  "$EXPORT\code\localai-rs\localai-watch"  @('target', '__pycache__')
if (Test-Path 'E:\localai-rs\localai-pipeline\src') {
    Sync 'E:\localai-rs\localai-pipeline' "$EXPORT\code\localai-rs\localai-pipeline" @('target', '__pycache__')
}
Copy-Item 'E:\localai-rs\Cargo.toml' "$EXPORT\code\localai-rs\" -Force -ErrorAction SilentlyContinue
if (Test-Path 'E:\localai-rs\Cargo.lock') { Copy-Item 'E:\localai-rs\Cargo.lock' "$EXPORT\code\localai-rs\" -Force }
foreach ($f in Get-ChildItem 'E:\localai-rs' -Filter 'rust-toolchain*') { Copy-Item $f.FullName "$EXPORT\code\localai-rs\" -Force }

# --- Backend Python (sans caches) ---
Sync 'E:\ia\backend' "$EXPORT\code\backend" @('__pycache__', '*.pyc')

# --- Catalogue de modeles (config) ---
if (Test-Path 'E:\ia\config') {
    Sync 'E:\ia\config' "$EXPORT\code\config" @('__pycache__')
}

# --- Frontend (src + config, sans node_modules/dist) ---
Sync 'E:\ia\frontend\src' "$EXPORT\code\frontend\src" @('__pycache__')
foreach ($f in @('package.json', 'vite.config.ts', 'tsconfig.json', 'index.html')) {
    if (Test-Path "E:\ia\frontend\$f") { Copy-Item "E:\ia\frontend\$f" "$EXPORT\code\frontend\" -Force }
}

# --- Scripts d'exploitation ---
New-Item -ItemType Directory -Force -Path "$EXPORT\code\scripts" | Out-Null
foreach ($f in @('run_ollama.ps1', 'run_server_boot.ps1', 'run_server_watch.ps1')) {
    if (Test-Path "E:\ia\$f") { Copy-Item "E:\ia\$f" "$EXPORT\code\scripts\" -Force }
}
if (Test-Path 'E:\localai-rs\rebuild.bat') { Copy-Item 'E:\localai-rs\rebuild.bat' "$EXPORT\code\scripts\" -Force }

# --- Skill pont (copie deployee) ---
Copy-Item 'C:\Users\Tony\.openjarvis\skills\localai\SKILL.md' "$EXPORT\docs\skills\localai\SKILL.md" -Force -ErrorAction SilentlyContinue

# --- Nettoyage .bak dans l'export ---
Get-ChildItem $EXPORT -Recurse -Include '*.bak_*', '*.bak' | Remove-Item -Force -ErrorAction SilentlyContinue

Write-Output '=== SYNC TERMINE ==='
