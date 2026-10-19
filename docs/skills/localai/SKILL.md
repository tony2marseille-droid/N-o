---
name: "localai"
description: "Cerveau LocalAI local (port 8320) : images 4K photorealistes fideles au sujet, documents Word/PDF/Excel, 3D/STL, meteo, stats PC, volume, apps, ecran, recherche approfondie. Un seul appel execute tout le workflow."
version: "2.1.0"
author: "localai"
tags: [localai, image, documents, 3d, systeme]
user_invocable: true
---

# Pont LocalAI

Pour TOUTE demande d'IMAGE, DESSIN, LOGO, DOCUMENT (rapport/lettre/CV/Excel), FICHIER 3D/STL, METEO, STATS PC, VOLUME, ouvrir une APP, ECRAN, ou RECHERCHE APPROFONDIE :

**Etape 1 â€” Lancer** (1 seul appel ; LocalAI fait tout : references, generation, controle qualite, routage) :
- POST `http://127.0.0.1:8320/api/outil/execute`, corps JSON :
`{"tool":"cerveau_local","args":{"message":"<redonne la demande COMPLETE de l'utilisateur, en francais, tous details>"}}`
- Reponse immediate : `{"job":"<id>","etat":"lance"}`

**Etape 2 â€” Recuperer le resultat (UN SEUL appel, il attend tout seul)** :
- GET `http://127.0.0.1:8320/api/outil/job/<id>?attendre=1`
- Cet appel reste bloque jusqu'a la fin du travail (2-8 min pour une image, quelques secondes pour meteo/web). Le timeout long est gere automatiquement â€” ne passe aucun parametre supplementaire.
- Si jamais tu obtiens `"fini":false`, refais exactement le meme GET.
- Quand `"fini":true` â†’ transmets `"resultat"` tel quel a l'utilisateur, en francais (chemins de fichiers inclus).
- NE DONNE JAMAIS ta main tant que tu n'as pas un `"fini":true` : la patience fait partie du travail.

**Ne contourn pas ce workflow** : pas de generation d'image par tes propres moyens, pas d'appel direct aux outils individuels â€” `cerveau_local` gere tout.
