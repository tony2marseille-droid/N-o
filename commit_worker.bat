@echo off
cd /d I:\ia\export_github
git add -A
git -c user.name=LocalAI -c user.email=localai@local commit -m "worker Python chaud: tools/specs/agents/routage sans spawn (modele OpenJarvis)"
git log --oneline -1
