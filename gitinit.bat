@echo off
cd /d I:\ia\export_github
git init -b main > git_init.log 2>&1
git add . >> git_init.log 2>&1
git -c user.name=LocalAI -c user.email=localai@local commit -m "LocalAI : assistant IA local complet (Rust + Python + fusion OpenJarvis)" >> git_init.log 2>&1
git log --oneline >> git_init.log 2>&1
type git_init.log
