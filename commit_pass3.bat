@echo off
cd /d I:\ia\export_github
git add -A
git -c user.name=LocalAI -c user.email=localai@local commit -m "Connecteurs : cles API locales (Meshy/Telegram/Discord) + test reel + moteur 3D Meshy v5"
git log --oneline
