@echo off
cd /d I:\ia\export_github
git add -A
git -c user.name=LocalAI -c user.email=localai@local commit -m "Panneau pipeline Matrix live (etapes+ETA), fond chat dynamique, streaming lisse, moteur 3D Hunyuan optionnel, connecteurs en preparation"
git log --oneline
