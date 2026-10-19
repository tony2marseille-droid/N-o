$ErrorActionPreference = 'SilentlyContinue'
# Watchdog sante du serveur de production (tache LocalAI_server_watch, toutes les 5 min)
# Si le serveur 8320 est DOWN ou repond mal au health, relance via run_server_boot.ps1 (idempotent).
# NOTE: la route health est /api/health (JSON) ; /health seul renvoie la page SPA.
$log = 'E:\ia\logs\server_watch.log'
$up = $false
$c = Get-NetTCPConnection -LocalPort 8320 -State Listen -ErrorAction SilentlyContinue
if ($c) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8320/api/health' -TimeoutSec 8
        if ($r.StatusCode -eq 200 -and $r.Content -match 'ok|LocalAI') { $up = $true }
    } catch {}
}
if ($up) {
    $lastUp = ((Get-Content $log -Tail 5 -ErrorAction SilentlyContinue) -match 'WATCH: serveur OK' | Select-Object -Last 1)
    if (-not $lastUp) {
        "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') WATCH: serveur OK (port 8320 + /api/health)" | Out-File $log -Append -Encoding utf8
    }
} else {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') WATCH: serveur DOWN ou health KO -> relance" | Out-File $log -Append -Encoding utf8
    & 'E:\ia\run_server_boot.ps1'
    Start-Sleep -Seconds 5
    $c2 = Get-NetTCPConnection -LocalPort 8320 -State Listen -ErrorAction SilentlyContinue
    if ($c2) {
        "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') WATCH: relance OK (PID $($c2.OwningProcess))" | Out-File $log -Append -Encoding utf8
    } else {
        "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') WATCH: relance ECHEC - port toujours down" | Out-File $log -Append -Encoding utf8
    }
}