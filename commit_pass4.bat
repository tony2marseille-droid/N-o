@echo off
cd /d I:\ia\export_github
git add -A
git -c user.name=LocalAI -c user.email=localai@local commit -m "Analyse performance HTML (OpenJarvis vs Neo), routeur rapide Q5 par defaut, Telegram cerveau complet + progression, validation references durcie, moteurs 3D Hunyuan/Meshy"
git log --oneline
