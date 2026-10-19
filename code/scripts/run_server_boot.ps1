$ErrorActionPreference = 'SilentlyContinue'
# Server de production : demarrage idempotent + persistance au boot (tache LocalAI_server_boot)
# CUTOVER RUST 2026-10-14 : lance le binaire Rust (release) au lieu de Python.
# Repli : restaurer run_server_boot.ps1.bak_py et relancer (Python intact sur E:\ia).
$log = 'E:\ia\logs\server_boot.log'
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') server_boot: demarrage (rust)" | Out-File $log -Append -Encoding utf8
$c = Get-NetTCPConnection -LocalPort 8320 -State Listen -ErrorAction SilentlyContinue
if ($c) {
    "deja en ecoute sur 8320 (PID $($c.OwningProcess)) - rien a faire" | Out-File $log -Append -Encoding utf8
    exit 0
}
$bin = 'E:\localai-rs\target\release\localai-server.exe'
if (-not (Test-Path $bin)) {
    "binaire rust absent: $bin" | Out-File $log -Append -Encoding utf8
    exit 1
}
$env:LOCALAI_PORT = '8320'
$env:LOCALAI_ROOT = 'E:\ia'
$p = Start-Process -FilePath $bin -WorkingDirectory 'E:\localai-rs' -WindowStyle Hidden -RedirectStandardOutput 'E:\ia\logs\server_rs.out.log' -RedirectStandardError 'E:\ia\logs\server_rs.err.log' -PassThru
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') server_boot: rust lance PID $($p.Id)" | Out-File $log -Append -Encoding utf8
