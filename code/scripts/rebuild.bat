@echo off
cd /d E:\localai-rs
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8320 ^| findstr LISTENING') do taskkill /f /pid %%a >> E:\localai-rs\rebuild.log 2>&1
timeout /t 2 /nobreak >nul
cargo build --release >> E:\localai-rs\rebuild.log 2>&1
if errorlevel 1 (echo BUILD_ECHEC >> E:\localai-rs\rebuild.log & exit /b 1)
echo BUILD_OK >> E:\localai-rs\rebuild.log
schtasks /run /tn LocalAI_server_boot >> E:\localai-rs\rebuild.log 2>&1
timeout /t 12 /nobreak >nul
curl -s http://localhost:8320/api/health >> E:\localai-rs\rebuild.log 2>&1
echo. >> E:\localai-rs\rebuild.log
curl -s -m 30 -X POST http://localhost:8320/api/outil/execute -H "Content-Type: application/json" -d "{\"tool\":\"get_datetime\",\"args\":{}}" >> E:\localai-rs\rebuild.log 2>&1
echo. >> E:\localai-rs\rebuild.log
echo FINI >> E:\localai-rs\rebuild.log
