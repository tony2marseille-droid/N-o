@echo off
cd /d I:\ia\export_github
del docs\README.md 2>nul
git add -A
git -c user.name=LocalAI -c user.email=localai@local commit -m "Neo v0.4.0: onboarding langue + adaptation materielle, reglages fiables, renommage cerveau_local"
git log --oneline
