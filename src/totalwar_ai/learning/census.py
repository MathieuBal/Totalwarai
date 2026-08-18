"""Ce que le jeu expose reellement, lu dans le journal de la sonde.

**Pourquoi ceci existe.** La sonde recense une centaine d'accesseurs a chaque
session de jeu, et les quatre sessions precedentes ont ete depouillees a l'oeil,
sur des centaines de lignes melees aux etats periodiques. C'est ainsi qu'on rate
une ligne — et une session de jeu ne se rejoue pas a volonte.

Ce module ne decide de rien et n'appelle rien : il transforme le journal en table
de verdicts. C'est le pendant, cote lecture, de ce que la revision 15 a fait cote
ecriture.

.. rubric:: Trois verdicts, et non deux

`ABSENT` et `NON TESTE` ne disent pas la meme chose, et les confondre est
precisement l'erreur que la revision 15 existe pour empecher : la revision 14
avait declare le moral « structurellement absent » apres l'avoir demande sous un
mauvais nom.

* `OK` — l'accesseur existe et a repondu ; la valeur est publiee ;
* `ABSENT` — il a ete demande et le jeu ne l'a pas rendu ;
* `NON TESTE` — **l'experience n'a pas eu lieu** : pas d'unite du bon camp,
  argument infabricable. Cela ne dit rien du jeu, seulement de la session.

.. rubric:: Le silence est un quatrieme cas, et le plus dangereux

Un accesseur attendu qui n'apparait **nulle part** dans le journal ne produit
aucune ligne, donc aucun verdict, donc aucune alerte. Il se confond avec un
accesseur jamais inscrit au recensement. `missing()` le rend visible.

La liste des accesseurs attendus se lit **dans la source Lua**, jamais recopiee
ici : deux listes de meme intention qui derivent l'une de l'autre est exactement
le defaut que `STILL_DISTANCE` a coute a debusquer (ADR 0019).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

#: Prefixe appose par `PROBE:log`. Tout le reste du journal appartient au jeu.
PREFIX = "[totalwar_ai]"

OK = "OK"
ABSENT = "ABSENT"
UNTESTED = "NON TESTE"

_API = re.compile(
    r"API\s+(?P<name>[\w:]+)\s+"
    r"(?:OK value=(?P<value>.*?)(?:\s+sur=(?P<target>\w+))?"
    r"|ABSENT error=(?P<error>.*?)"
    r"|NON TESTE raison=(?P<reason>.*?))\s*$"
)
_ATTR = re.compile(
    r"ATTR\s+(?P<name>\w+)\s+(?:OK value=(?P<value>.*?)|ABSENT error=(?P<error>.*?))\s*$"
)
_REVISION = re.compile(r"revision (\d+)\)")

#: Lignes d'etat periodique : repetitives, et sans rapport avec le recensement.
_ROUTINE = ("] STATE ", "] BATTLE ")


@dataclass(frozen=True, slots=True)
class Finding:
    """Un accesseur, et ce que la session en a appris."""

    name: str
    #: `API` (methode d'unite ou de `bm`) ou `ATTR` (cle de `has_attribute`).
    kind: str
    verdict: str
    value: str | None = None
    #: Motif d'absence, ou raison de non-test. Les deux ne se melangent pas.
    detail: str | None = None
    #: Camp de l'unite testee, pour les accesseurs a argument.
    target: str | None = None

    @property
    def usable(self) -> bool:
        return self.verdict == OK

    def explain(self) -> str:
        marque = {OK: "OK ", ABSENT: "-- ", UNTESTED: " ? "}.get(self.verdict, "   ")
        corps = f"  {marque} {self.kind:<4} {self.name:<34}"
        if self.verdict == OK:
            valeur = self.value or ""
            return corps + f" {valeur}" + (f"  [sur {self.target}]" if self.target else "")
        return corps + f" {self.detail or ''}"


@dataclass(frozen=True, slots=True)
class MissileTiming:
    """Le chronometre du tir : mouvement, arret, cible, premiere salve.

    **C'est cette mesure qui juge un correctif du simulateur.** Notre simulateur
    laisse aujourd'hui toute unite de tir non engagee tirer en se deplacant, sans
    preuve que le jeu l'autorise. Si le jeu l'interdit, le repli tirant repose
    sur une permissivite qui n'existe pas — et il porte `balanced_clash`.
    """

    unit: str | None = None
    unit_type: str | None = None
    fire_while_moving: str | None = None
    #: Instants en millisecondes depuis l'ordre de deplacement.
    moving_at: float | None = None
    stopped_at: float | None = None
    target_at: float | None = None
    volley_at: float | None = None
    #: La salve est-elle partie alors que l'unite se deplacait encore ?
    volley_while_moving: bool | None = None
    #: Delai entre l'arret et la salve, quand l'unite s'est arretee.
    volley_after_stop: float | None = None
    #: Raison pour laquelle le chronometrage n'a pas eu lieu.
    aborted: str | None = None

    @property
    def conclusive(self) -> bool:
        """Une salve a-t-elle ete observee ?

        Sans salve, il n'y a **rien a conclure** — ni dans un sens ni dans
        l'autre. Le dire est le seul comportement acceptable : un instrument qui
        tranche sur une case vide est celui que l'ADR 0018 a du corriger.
        """
        return self.volley_at is not None

    def explain(self) -> str:
        if self.aborted is not None:
            return f"  Chronometrage impossible : {self.aborted}"
        if not self.conclusive:
            return (
                "  **Aucune salve observee.** Le tir en mouvement reste non tranche :\n"
                "  ni le correctif du simulateur ni son contraire ne sont justifies."
            )
        entete = (
            f"  unite {self.unit} ({self.unit_type}), fire_while_moving={self.fire_while_moving}"
        )
        etapes = []
        for nom, valeur in (
            ("en marche", self.moving_at),
            ("arret", self.stopped_at),
            ("cible acquise", self.target_at),
            ("premiere salve", self.volley_at),
        ):
            etapes.append(f"    {nom:<16} {'jamais' if valeur is None else f'{valeur:.0f} ms'}")
        verdict = (
            "  **Le jeu autorise le tir en mouvement** pour cette unite : la salve\n"
            "  est partie alors qu'elle se deplacait encore."
            if self.volley_while_moving
            else (
                "  **La salve n'est partie qu'a l'arret**"
                + (
                    f", {self.volley_after_stop:.0f} ms apres."
                    if self.volley_after_stop is not None
                    else "."
                )
                + "\n  Notre simulateur, lui, laisse tirer en marche : l'ecart est reel."
            )
        )
        return "\n".join([entete, *etapes, "", verdict])


@dataclass
class Census:
    """L'inventaire des yeux et des oreilles de l'agent."""

    findings: list[Finding] = field(default_factory=list)
    missile: MissileTiming = field(default_factory=MissileTiming)
    revision: int | None = None
    #: Accesseurs attendus par la sonde et absents du journal. Voir l'en-tete.
    silent: tuple[str, ...] = ()

    @property
    def usable(self) -> list[Finding]:
        return [item for item in self.findings if item.verdict == OK]

    @property
    def absent(self) -> list[Finding]:
        return [item for item in self.findings if item.verdict == ABSENT]

    @property
    def untested(self) -> list[Finding]:
        return [item for item in self.findings if item.verdict == UNTESTED]

    def render(self) -> str:
        if not self.findings:
            return (
                "Aucune ligne de recensement dans ce journal.\n"
                "La sonde s'est-elle chargee ? Le recensement se declenche au deploiement."
            )
        lignes = [
            f"Recensement : {len(self.usable)} utilisables, "
            f"{len(self.absent)} absents, {len(self.untested)} non testes"
            # Le compte de muets appartient a l'en-tete : une liste de quatre-vingts
            # noms plus bas ne dit pas la meme chose qu'une liste de deux, et il
            # faut le voir avant de derouler.
            + (f", {len(self.silent)} muets" if self.silent else "")
            + (f", revision {self.revision}" if self.revision is not None else ""),
            "",
        ]
        lignes += [item.explain() for item in self.findings]

        if self.untested:
            lignes += [
                "",
                "**Non testes : l'experience n'a pas eu lieu.** Ces accesseurs ne sont",
                "  pas declares absents — la session n'a simplement pas fourni de quoi",
                "  les eprouver. Il faut la refaire avec la composition demandee.",
            ]
        if self.silent:
            lignes += [
                "",
                "**Attendus et jamais apparus** — ni OK, ni absents, ni non testes :",
                *(f"    {nom}" for nom in self.silent),
                "  Une ligne muette ne se distingue pas d'un accesseur oublie. Verifier",
                "  que le pack embarque bien la revision du depot.",
            ]
        lignes += ["", "--- chronometre du tir ---", self.missile.explain()]
        return "\n".join(lignes)


def expected_accessors(lua_source: str) -> tuple[str, ...]:
    """Accesseurs que la sonde declare recenser, lus dans sa propre source.

    **Recopier ces listes en Python les ferait deriver.** Le journal ne porte que
    ce qui a repondu ; savoir ce qui *aurait du* repondre demande la liste de
    reference, et il ne doit y en avoir qu'une.
    """
    noms: list[str] = []
    for bloc, motif in (
        ("UNIT_ACCESSORS", r'^\s*"([\w:]+)",'),
        ("UNIT_ATTRIBUTES", r'^\s*"([\w:]+)",'),
        ("UNIT_ACCESSORS_WITH_ARGS", r'^\s*name\s*=\s*"([\w:]+)"'),
    ):
        debut = lua_source.find(f"local {bloc} = {{")
        if debut < 0:
            continue
        fin = lua_source.find("\n}", debut)
        corps = lua_source[debut : fin if fin > 0 else len(lua_source)]
        noms += re.findall(motif, corps, flags=re.MULTILINE)
    # `has_attribute` porte les ATTR et se journalise a part.
    return tuple(dict.fromkeys(noms))


def read(lines: Iterable[str], *, expected: Sequence[str] = ()) -> Census:
    """Lit un journal de sonde et rend l'inventaire.

    Accepte le journal complet du jeu : tout ce qui ne porte pas le prefixe de la
    sonde est ignore, et les etats periodiques avec.
    """
    trouvailles: list[Finding] = []
    revision: int | None = None
    missile: dict[str, object] = {}
    vus: set[str] = set()

    for brute in lines:
        ligne = brute.rstrip()
        if PREFIX not in ligne:
            continue
        if any(marque in ligne for marque in _ROUTINE):
            continue
        corps = ligne.split(PREFIX, 1)[1].strip()

        if (found := _REVISION.search(corps)) is not None:
            revision = int(found.group(1))

        if corps.startswith("MISSILE"):
            _read_missile(corps, missile)
            continue

        trouvaille = _read_finding(corps)
        if trouvaille is not None:
            trouvailles.append(trouvaille)
            vus.add(trouvaille.name)

    muets = tuple(nom for nom in expected if nom not in vus)
    return Census(
        findings=trouvailles,
        missile=MissileTiming(**missile),  # type: ignore[arg-type]
        revision=revision,
        silent=muets,
    )


def _read_finding(corps: str) -> Finding | None:
    if (found := _API.search(corps)) is not None:
        nom = found.group("name")
        kind = "bm" if nom.startswith("bm:") else "API"
        if found.group("value") is not None:
            return Finding(
                nom, kind, OK, value=found.group("value").strip(), target=found.group("target")
            )
        if found.group("error") is not None:
            return Finding(nom, kind, ABSENT, detail=found.group("error").strip())
        return Finding(nom, kind, UNTESTED, detail=(found.group("reason") or "").strip())
    if (found := _ATTR.search(corps)) is not None:
        if found.group("value") is not None:
            return Finding(found.group("name"), "ATTR", OK, value=found.group("value").strip())
        return Finding(
            found.group("name"), "ATTR", ABSENT, detail=(found.group("error") or "").strip()
        )
    return None


def _read_missile(corps: str, into: dict[str, object]) -> None:
    """Reconstitue le chronometre depuis les lignes `MISSILE t0..t4`."""
    if "chronometrage impossible" in corps or "aucun deplacement" in corps:
        into["aborted"] = corps.removeprefix("MISSILE ").strip()
        return
    if "fin du chronometrage sans salve" in corps:
        return
    if corps.startswith("MISSILE t0"):
        if (found := re.search(r"unite=(\S+)", corps)) is not None:
            into["unit"] = found.group(1)
        if (found := re.search(r"type=(\S+)", corps)) is not None:
            into["unit_type"] = found.group(1)
        if (found := re.search(r"fire_while_moving=(\S+)", corps)) is not None:
            into["fire_while_moving"] = found.group(1)
        return
    for marque, champ in (
        ("t1", "moving_at"),
        ("t2", "stopped_at"),
        ("t3", "target_at"),
        ("t4", "volley_at"),
    ):
        if (
            corps.startswith(f"MISSILE {marque}")
            and (found := re.search(r"a ([\d.]+) ms", corps)) is not None
        ):
            into[champ] = float(found.group(1))
    if corps.startswith("MISSILE t4"):
        if (found := re.search(r"en_marche=(\w+)", corps)) is not None:
            into["volley_while_moving"] = found.group(1) == "true"
        if (found := re.search(r"apres_arret=([\d.]+)", corps)) is not None:
            into["volley_after_stop"] = float(found.group(1))
