"""Boucle RLVR : genère des taches de code, valide par EXECUTION REELLE,
ne garde que les reussites -> dataset de qualite pour re-entrainement."""
import json
import pathlib
import random
import sys

sys.path.insert(0, ".")
from backend.tools.executor import _llm_rapide, tool_check_code  # noqa: E402

ATELIER = pathlib.Path("data/lora_atelier")

TACHES = [
    ("fonction {f}(lst) qui renvoie la somme des elements pairs", "{f}([1,2,3,4]) == 6"),
    ("fonction {f}(n) qui renvoie n! (factorielle iterative)", "{f}(5) == 120"),
    ("fonction {f}(s) qui renvoie la chaine inversee", "{f}('abc') == 'cba'"),
    ("fonction {f}(lst) qui renvoie le maximum sans utiliser max()", "{f}([3,7,2]) == 7"),
    ("fonction {f}(n) qui renvoie True si n est premier", "{f}(13) == True"),
    ("fonction {f}(lst) qui tri une liste sans sorted() (tri insertion)", "{f}([3,1,2]) == [1,2,3]"),
    ("fonction {f}(s) qui compte les voyelles", "{f}('bonjour') == 3"),
    ("fonction {f}(lst, x) qui renvoie l'indice de x ou -1", "{f}([4,8,15], 8) == 1"),
    ("fonction {f}(n) qui renvoie la suite de Fibonacci jusqu'a n termes", "{f}(5) == [0,1,1,2,3]"),
    ("fonction {f}(s) qui verifie les parentheses equilibrees", "{f}('(a[b]c)') == True"),
    ("fonction {f}(d, cle) qui fusionne deux dicts en sommant les cles communes", "{f}({'a':1},{'a':2,'b':3}) == {'a':3,'b':3}"),
    ("fonction {f}(txt) qui renvoie le mot le plus long", "{f}('je code en python') == 'python'"),
    ("fonction {f}(lst) qui dedoublonne en conservant l'ordre", "{f}([1,2,1,3]) == [1,2,3]"),
    ("fonction {f}(n) qui convertit en binaire (chaine) sans bin()", "{f}(6) == '110'"),
    ("fonction {f}(mat) qui calcule la transposee d'une matrice liste de listes", "{f}([[1,2],[3,4]]) == [[1,3],[2,4]]"),
    ("fonction {f}(s) qui renvoie True si anagrammes de t", "{f}('chien','niche') == True"),
    ("fonction {f}(lst) qui applique un chiffre de Cesar decalage 3 sur minuscules", "{f}('abc') == 'def'"),
    ("fonction {f}(p, q) qui renvoie le PGCD recursif", "{f}(12, 18) == 6"),
    ("fonction {f}(lst) qui separe pairs et impairs dans deux listes", "{f}([1,2,3,4])[1] == [2,4]"),
    ("fonction {f}(phrase) qui inverse l'ordre des mots", "{f}('bonjour toi') == 'toi bonjour'"),
    # --- Taches T2 (200+ cibles) ---
    ("fonction {f}(n) qui renvoie True si n est un nombre parfait (somme diviseurs == n)", "{f}(28) == True"),
    ("fonction {f}(lst) qui renvoie la deuxieme plus grande valeur", "{f}([5,3,9,1,7]) == 7"),
    ("fonction {f}(s) qui compte les mots d'une chaine", "{f}('hello world foo') == 3"),
    ("fonction {f}(n) qui renvoie la somme des chiffres de n", "{f}(1234) == 10"),
    ("fonction {f}(lst) qui aplati une liste imbriquee", "{f}([[1,2],[3,[4]]]) == [1,2,3,4]"),
    ("fonction {f}(s) qui verifie si s est un palindrome", "{f}('radar') == True"),
    ("fonction {f}(n) qui renvoie le PPCM de a et b", "{f}(4,6) == 12"),
    ("fonction {f}(lst) qui rotate la liste de n positions vers la gauche", "{f}([1,2,3,4,5],2) == [3,4,5,1,2]"),
    ("fonction {f}(s) qui supprime les doublons de lettres consecutives", "{f}('aabbcc') == 'abc'"),
    ("fonction {f}(n) qui renvoie True si n est un nombre armstrong", "{f}(153) == True"),
    ("fonction {f}(lst) qui renvoie les elements communs a deux listes", "{f}([1,2,3],[2,3,4]) == [2,3]"),
    ("fonction {f}(s, k) qui decoupe s en morceaux de taille k", "{f}('abcdef',2) == ['ab','cd','ef']"),
    ("fonction {f}(n) qui genere les n premiers nombres premiers", "{f}(5) == [2,3,5,7,11]"),
    ("fonction {f}(mat) qui multiplie deux matrices", "{f}([[1,2],[3,4]],[[5,6],[7,8]]) == [[19,22],[43,50]]"),
    ("fonction {f}(s) qui encode en run-length (ex: 'aaabbc' -> '3a2b1c')", "{f}('aaabbc') == '3a2b1c'"),
    ("fonction {f}(n) qui renvoie True si n est un carre parfait", "{f}(16) == True"),
    ("fonction {f}(lst) qui melange Fisher-Yates sans import random", "{f}([1,2,3,4])"),
    ("fonction {f}(s) qui inverse la casse de chaque lettre", "{f}('Hello') == 'hELLO'"),
    ("fonction {f}(n) qui calcule la puissance rapide modulo", "{f}(2,10,1000) == 24"),
    ("fonction {f}(lst, x) qui compte les occurrences de x", "{f}([1,2,2,3,2],2) == 3"),
    ("fonction {f}(s) qui verifie si les crochets/parentheses/aces sont equilibres", "{f}('{[()]}') == True"),
    # --- Taches T2 complementaires (200+ cibles) ---
    ("fonction {f}(n) qui renvoie le nombre de diviseurs de n", "{f}(6) == 4"),
    ("fonction {f}(lst) qui renvoie la moyenne des elements", "{f}([2,4,6]) == 4.0"),
    ("fonction {f}(s) qui supprime les espaces en debut et fin", "{f}('  hello  ') == 'hello'"),
    ("fonction {f}(n) qui renvoie True si n est pair", "{f}(4) == True"),
    ("fonction {f}(lst) qui concatene deux listes sans +", "{f}([1,2],[3,4]) == [1,2,3,4]"),
    ("fonction {f}(s) qui compte les consonnes", "{f}('bonjour') == 4"),
    ("fonction {f}(n) qui renvoie le suivant dans la suite 1,2,4,8,16...", "{f}(5) == 32"),
    ("fonction {f}(lst) qui renvoie True si tous les elements sont positifs", "{f}([1,2,3]) == True"),
    ("fonction {f}(s) qui remplace les voyelles par *", "{f}('bonjour') == 'b*nj*r'"),
    ("fonction {f}(n) qui calcule n modulo 7", "{f}(15) == 1"),
    ("fonction {f}(lst) qui inverse l'ordre sans reverse()", "{f}([1,2,3]) == [3,2,1]"),
    ("fonction {f}(s) qui verifie si s commence par 'a'", "{f}('apple') == True"),
    ("fonction {f}(a, b) qui renvoie le minimum sans min()", "{f}(5,3) == 3"),
    ("fonction {f}(n) qui renvoie True si n est divisible par 3", "{f}(9) == True"),
    ("fonction {f}(lst) qui renvoie le produit de tous les elements", "{f}([2,3,4]) == 24"),
    ("fonction {f}(s) qui tronque s a 5 caractere", "{f}('hello world') == 'hello'"),
    ("fonction {f}(n) qui renvoie le carre de n", "{f}(5) == 25"),
    ("fonction {f}(lst) qui compte les elements > 5", "{f}([1,6,7,2]) == 2"),
    ("fonction {f}(s) qui verifie si s est en majuscules", "{f}('HELLO') == True"),
    ("fonction {f}(n) qui renvoie n+1", "{f}(10) == 11"),
]
NOMS = ["calcule", "traite", "transforme", "analyse", "resous", "genere", "construi", "verifie", "optimise", "gere"]


def _charger_existants():
    """Charge le pool RLVR existant (sans erreur si absent)."""
    ex = []
    try:
        with (ATELIER / "sft_rlvr.jsonl").open("r", encoding="utf-8") as fo:
            for ligne in fo:
                if ligne.strip():
                    try:
                        ex.append(json.loads(ligne))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        pass
    return ex


def generer(n_cibles=200, max_essais=800):
    reussites = []
    vus = set()
    essais = 0
    random.seed()
    while len(reussites) < n_cibles and essais < max_essais:
        essais += 1
        gabarit, test = random.choice(TACHES)
        f = random.choice(NOMS) + str(random.randint(10, 99))
        tache = gabarit.replace("{f}", f)
        testf = test.replace("{f}", f)
        if tache in vus:
            continue
        vus.add(tache)
        code_brut = _llm_rapide(
            f"Ecris UNIQUEMENT le code python d'une fonction nommee exactement {f}. "
            f"Tache : {tache}.\nPas de texte autour, pas de markdown.",
            120,
        )
        code = code_brut.strip().removeprefix("```python").removeprefix("```")
        code = code.removesuffix("```").strip()
        verdict = tool_check_code(code, "python", executer=False)
        if "OK" not in verdict:
            continue
        verdict = tool_check_code(code + f"\nassert {testf}\nprint('VALIDE')", "python", executer=True)
        if "exit 0" in verdict:
            reussites.append({
                "messages": [
                    {"role": "user", "content": f"Ecris une fonction python : {tache}"},
                    {"role": "assistant", "content": code},
                ],
                "theme": "programmation",
                "verifie": True,
            })
            print(f"[{len(reussites)}/{essais}] {tache[:50]}")
        verdict = tool_check_code(code, "python", executer=False)
        if "OK" not in verdict:
            continue
    dest = ATELIER / "sft_rlvr.jsonl"
    existants = _charger_existants()
    vus = {json.dumps(e.get("messages", []), ensure_ascii=False) for e in existants}
    nouveaux = [e for e in reussites if json.dumps(e.get("messages", []), ensure_ascii=False) not in vus]
    tous = existants + nouveaux
    with dest.open("w", encoding="utf-8") as fo:
        for ex in tous:
            fo.write(json.dumps(ex, ensure_ascii=False) + "\n")
    print(f"RLVR TERMINE: {len(reussites)} exemples valides / {essais} essais "
          f"(+{len(nouveaux)} nouveaux, pool total {len(tous)}) -> {dest}")


if __name__ == "__main__":
    generer()
