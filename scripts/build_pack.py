"""Prepare l'arborescence a empaqueter dans le `.pack`, au bon chemin.

**Le chemin source est celui qui echoue.** `docs/feasibility.md` le dit sans
ambiguite : un exemplaire depose sous `script/battle/mod/` produit
`Failed to load mod`, tandis que `script/_lib/mod/` charge. Or la source vit
justement sous `lua_mod/script/battle/mod/`.

Un developpeur qui empaquette « l'arborescence telle quelle » fabrique donc un
`.pack` muet, sans aucun message d'erreur cote Python : la sonde ne se lance
jamais, aucun etat n'est publie, et le diagnostic coute une session de jeu.

Ce script existe pour rendre cette faute impossible. Il ne fabrique pas le
`.pack` — cela reste le travail de RPFM — mais il produit le contenu a y glisser,
aux emplacements que la bataille a valides.

Usage :

    python scripts/build_pack.py [--out build/pack]

Puis, dans RPFM, ajouter le contenu de `build/pack/` a la racine du `.pack`.
Aucun dossier ne doit preceder `script` dans l'arborescence.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

#: Source canonique du script de sonde.
SOURCE = Path("lua_mod/script/battle/mod/totalwar_ai_probe.lua")

#: Emplacement **prouve charge** en bataille reelle.
PROVEN = Path("script/_lib/mod/totalwar_ai_probe.lua")

#: Emplacement laisse en second exemplaire, pour lever l'ambiguite.
#:
#: Le script se protege du double chargement — le second exemplaire rencontre
#: s'annonce puis s'arrete — donc le deposer ne coute rien et documente le doute.
SECONDARY = Path("script/battle/mod/totalwar_ai_probe.lua")


def build(root: Path, out: Path) -> list[Path]:
    """Copie la sonde aux emplacements runtime. Rend les chemins ecrits."""
    source = root / SOURCE
    if not source.is_file():
        raise SystemExit(f"source introuvable : {source}")

    ecrits: list[Path] = []
    for cible in (PROVEN, SECONDARY):
        destination = out / cible
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        ecrits.append(cible)
    return ecrits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default="build/pack", help="repertoire de sortie")
    parser.add_argument("--root", default=".", help="racine du depot")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)

    ecrits = build(root, out)
    revision = _revision(root / SOURCE)
    print(f"Sonde revision {revision} preparee dans {out}/ :")
    for chemin in ecrits:
        marque = "  <- emplacement prouve charge" if chemin == PROVEN else "  <- second exemplaire"
        print(f"  {chemin}{marque}")
    print("\nDans RPFM : ajouter le contenu de ce repertoire a la racine du .pack.")
    print("Aucun dossier ne doit preceder `script`.")
    return 0


def _revision(source: Path) -> str:
    """Revision annoncee par le script, pour qu'elle apparaisse a l'empaquetage."""
    for ligne in source.read_text(encoding="utf-8").splitlines():
        if "TOTALWAR_AI_PROBE_REVISION" in ligne and "=" in ligne:
            return ligne.split("=", 1)[1].strip()
    return "inconnue"


if __name__ == "__main__":
    sys.exit(main())
