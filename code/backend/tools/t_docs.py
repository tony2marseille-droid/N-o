# -*- coding: utf-8 -*-
"""Outils Bureau : creation de documents (DOCX, PDF, XLSX) depuis Markdown.

Meme philosophie que les autres outils : bibliotheques legeres du venv
(python-docx, openpyxl, reportlab), aucune dependance systeme.
"""
import re
from pathlib import Path

from backend.core.security import extension_autorisee_ecriture as _extension_bloquee
from backend.core.security import verifier_acces as _verifier_acces


def _md_to_blocks(contenu: str) -> list[tuple[str, str]]:
    """Decoupe du Markdown simple en blocs (type, texte).

    Types : h1, h2, h3, bullet, num, para, table (non gere ici).
    """
    blocs: list[tuple[str, str]] = []
    lignes = [l.rstrip() for l in contenu.splitlines()]
    i = 0
    while i < len(lignes):
        l = lignes[i].strip()
        if not l:
            i += 1
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", l)
        if m:
            blocs.append((f"h{len(m.group(1))}", m.group(2)))
            i += 1
            continue
        if l.startswith("- ") or l.startswith("* "):
            items = []
            while i < len(lignes) and lignes[i].strip().startswith(("- ", "* ")):
                items.append(lignes[i].strip()[2:])
                i += 1
            blocs.append(("bullet", "\n".join(items)))
            continue
        if re.match(r"^\d+\.\s", l):
            items = []
            while i < len(lignes) and re.match(r"^\d+\.\s", lignes[i].strip()):
                items.append(re.sub(r"^\d+\.\s", "", lignes[i].strip()))
                i += 1
            blocs.append(("num", "\n".join(items)))
            continue
        para = [l]
        i += 1
        while i < len(lignes):
            nl = lignes[i].strip()
            if not nl or nl.startswith(("#", "- ", "* ")) or re.match(r"^\d+\.\s", nl):
                break
            para.append(nl)
            i += 1
        blocs.append(("para", " ".join(para)))
    return blocs


def _texte_riche(run, texte: str):
    """Gere **gras** simple dans un paragraphe python-docx."""
    for part in re.split(r"(\*\*.*?\*\*)", texte):
        if part.startswith("**") and part.endswith("**"):
            run.bold = True
            run.text = part[2:-2]
        else:
            run.bold = False
            run.text = part


def _vers_docx(titre: str, contenu: str, sortie: Path) -> str:
    from docx import Document

    doc = Document()
    doc.add_heading(titre, 0)
    for typ, txt in _md_to_blocks(contenu):
        if typ == "h1":
            doc.add_heading(txt, 1)
        elif typ == "h2":
            doc.add_heading(txt, 2)
        elif typ == "h3":
            doc.add_heading(txt, 3)
        elif typ in ("bullet", "num"):
            style = "List Bullet" if typ == "bullet" else "List Number"
            for item in txt.split("\n"):
                p = doc.add_paragraph(style=style)
                _texte_riche(p.add_run(), item)
        else:
            p = doc.add_paragraph()
            _texte_riche(p.add_run(), txt)
    doc.save(str(sortie))
    return str(sortie)


def _vers_pdf(titre: str, contenu: str, sortie: Path) -> str:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (ListFlowable, ListItem, Paragraph,
                                    SimpleDocTemplate, Spacer)

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(sortie), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
    )
    story = [Paragraph(titre.replace("&", "&amp;"), styles["Title"]), Spacer(1, 0.5 * cm)]
    for typ, txt in _md_to_blocks(contenu):
        txt = txt.replace("&", "&amp;")
        if typ == "h1":
            story.append(Paragraph(txt, styles["Heading1"]))
        elif typ == "h2":
            story.append(Paragraph(txt, styles["Heading2"]))
        elif typ == "h3":
            story.append(Paragraph(txt, styles["Heading3"]))
        elif typ in ("bullet", "num"):
            story.append(ListFlowable(
                [ListItem(Paragraph(t, styles["BodyText"])) for t in txt.split("\n")],
                bulletType="bullet" if typ == "bullet" else "1",
            ))
        else:
            story.append(Paragraph(txt, styles["BodyText"]))
        story.append(Spacer(1, 0.2 * cm))
    doc.build(story)
    return str(sortie)


def _vers_xlsx(titre: str, contenu: str, sortie: Path) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = (titre or "Feuille1")[:31]
    ws["A1"] = titre
    ws["A1"].font = Font(bold=True, size=14)
    ligne = 3
    # Table markdown (| a | b |) -> vraies cellules.
    lignes_md = contenu.splitlines()
    i = 0
    while i < len(lignes_md):
        l = lignes_md[i].strip()
        if l.startswith("|") and l.count("|") >= 2:
            cellules = [c.strip() for c in l.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", c) for c in cellules):
                i += 1
                continue
            for col, val in enumerate(cellules, 1):
                ws.cell(row=ligne, column=col, value=val)
            ligne += 1
            i += 1
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", l)
        if m:
            ws.cell(row=ligne, column=1, value=m.group(2)).font = Font(bold=True)
            ligne += 1
            i += 1
            continue
        if l.startswith("- "):
            ws.cell(row=ligne, column=1, value=l[2:])
            ligne += 1
            i += 1
            continue
        if l:
            ws.cell(row=ligne, column=1, value=l)
            ligne += 1
        i += 1
    wb.save(str(sortie))
    return str(sortie)


def tool_document_create(titre: str, contenu_md: str, type_doc: str = "docx",
                         save_path: str = "") -> str:
    """Cree un document professionnel (docx/pdf/xlsx) a partir de Markdown.

    type_doc : "docx" (Word), "pdf" ou "xlsx" (tableur ; le Markdown table
    | a | b | devient un vrai tableau).
    """
    type_doc = (type_doc or "docx").lower().lstrip(".")
    if type_doc not in ("docx", "pdf", "xlsx"):
        return "Erreur: type_doc doit etre docx, pdf ou xlsx"
    if not save_path:
        import time as _t

        slug = re.sub(r"[^a-z0-9]+", "_", (titre or "document").lower())[:40]
        save_path = str(Path("data") / f"doc_{slug}_{int(_t.time())}.{type_doc}")
    if not save_path.lower().endswith("." + type_doc):
        save_path += "." + type_doc
    refus_ext = _extension_bloquee(save_path)
    if refus_ext:
        return refus_ext
    refus = _verifier_acces(save_path, ecriture=True)
    if refus:
        return refus
    sortie = Path(save_path).expanduser().resolve()
    sortie.parent.mkdir(parents=True, exist_ok=True)
    try:
        if type_doc == "docx":
            _vers_docx(titre, contenu_md, sortie)
        elif type_doc == "pdf":
            _vers_pdf(titre, contenu_md, sortie)
        else:
            _vers_xlsx(titre, contenu_md, sortie)
    except Exception as exc:
        return f"Erreur creation document {type_doc}: {exc}"
    ko = sortie.stat().st_size // 1024
    return f"Document cree: {sortie} ({ko} Ko, format {type_doc.upper()})"
