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
ERROR = "ERREUR"
UNTESTED = "NON TESTE"

#: Motif exact par lequel le Lua signale un accesseur **inexistant**.
#:
#: **C'est la seule absence veritable.** Le script journalise `ABSENT error=…`
#: dans deux situations qui n'ont rien de commun : l'accesseur n'existe pas, ou
#: il existe et a leve. L'un dit que le jeu ne l'expose pas, l'autre qu'on l'a
#: mal appele — et confondre les deux est exactement ce qui a fait declarer le
#: moral « structurellement absent » en revision 14, apres l'avoir demande sous
#: un mauvais nom.
#:
#: La distinction se fait ici plutot que dans le Lua **a dessein** : le pack en
#: circulation est deja le bon, et le modifier couterait un repack et une
#: session de jeu. Le script pourra emettre `ERREUR` de lui-meme a la prochaine
#: revision ; d'ici la, le lecteur rattrape sans rien demander au jeu.
NOT_A_FUNCTION = "pas une fonction"

_API = re.compile(
    r"API\s+(?P<name>[\w:]+)\s+"
    r"(?:OK value=(?P<value>.*?)(?:\s+sur=(?P<target>\w+))?"
    r"|ABSENT error=(?P<error>.*?)"
    r"|NON TESTE raison=(?P<reason>.*?))\s*$"
)
_ATTR = re.compile(
    r"ATTR\s+(?P<name>\w+)\s+(?:OK value=(?P<value>.*?)|ABSENT error=(?P<error>.*?))\s*$"
)
#: Methode recensee par simple presence : `script_ai_planner` et armee.
#:
#: Ces deux recensements n'appellent pas la methode — l'appeler aurait des effets
#: de bord, et un recensement doit rester sans consequence. Le verdict porte donc
#: sur l'existence seule.
_METHOD = re.compile(r"^(?P<name>\w+)\s+:\s+(?P<verdict>presente|ABSENT)\s*$")
#: Valeur relevee sur une armee : `army_handicap`, `unit_count`.
_ARMY = re.compile(
    r"^(?P<target>nous|eux)\s+alliance\s+\d+\s+armee\s+\d+\s+"
    r"(?P<name>\w+)\s+:\s+(?P<value>.+?)\s*$"
)
#: Concordance des trois sources d'altitude.
_TERRAIN = re.compile(
    r"CONCORDANCE\s+unit_y=(?P<unit>\S+)\s+v_to_ground=(?P<ground>\S+)"
    r"\s+get_terrain_height=(?P<height>\S+)"
)
#: Sonde ponctuelle d'altitude : `sol en (x, z) : valeur`.
_SOIL = re.compile(r"^sol en \((?P<x>[^,]+),\s*(?P<z>[^)]+)\)\s*:\s*(?P<value>.+?)\s*$")
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


#: Ecart d'altitude au-dela duquel deux sources ne disent plus la meme chose.
#:
#: Un metre : assez large pour absorber l'arrondi d'un journal texte, assez
#: etroit pour qu'une source qui rend le sol la ou une autre rend le sommet du
#: modele ne passe pas inapercue.
TERRAIN_TOLERANCE = 1.0


@dataclass(frozen=True, slots=True)
class Terrain:
    """Les trois sources d'altitude, et leur accord.

    **Trois chemins pretendent dire ou est le sol** : la position `y` de l'unite,
    `v_to_ground()` qui projette un point sur le terrain, et
    `bm:get_terrain_height(x, z)`. Savoir lequel croire decide de tout usage du
    relief par l'agent — et rien ne dit a priori qu'ils s'accordent.
    """

    unit_y: float | None = None
    v_to_ground: float | None = None
    terrain_height: float | None = None

    @property
    def available(self) -> list[tuple[str, float]]:
        return [
            (nom, valeur)
            for nom, valeur in (
                ("unit_y", self.unit_y),
                ("v_to_ground", self.v_to_ground),
                ("get_terrain_height", self.terrain_height),
            )
            if valeur is not None
        ]

    @property
    def spread(self) -> float | None:
        """Ecart entre la plus haute et la plus basse des sources disponibles."""
        valeurs = [valeur for _, valeur in self.available]
        return max(valeurs) - min(valeurs) if len(valeurs) >= 2 else None

    @property
    def consistent(self) -> bool | None:
        """`None` tant que deux sources au moins n'ont pas repondu."""
        ecart = self.spread
        return None if ecart is None else ecart <= TERRAIN_TOLERANCE

    def explain(self) -> str:
        if not self.available:
            return "  Aucune source d'altitude n'a repondu : le relief reste hors de portee."
        lignes = [f"    {nom:<20} {valeur:.2f}" for nom, valeur in self.available]
        accord = self.consistent
        if accord is None:
            lignes.append("")
            lignes.append(
                "  **Une seule source a repondu.** Rien a comparer : la concordance\n"
                "  reste inconnue, ce qui n'est pas la meme chose qu'un desaccord."
            )
        elif accord:
            lignes.append("")
            lignes.append(
                f"  Les sources s'accordent a {self.spread:.2f} m pres : l'altitude du sol\n"
                "  est lisible, et le relief exploitable."
            )
        else:
            lignes.append("")
            lignes.append(
                f"  **Elles divergent de {self.spread:.2f} m.** Elles ne mesurent donc pas\n"
                "  la meme chose — sol contre position de l'unite, par exemple. Choisir\n"
                "  la mauvaise fausserait tout usage du relief."
            )
        return "\n".join(lignes)


#: Fragments de cle d'unite qui designent un tireur **ordinaire**.
#:
#: Le jugement porte sur la cle du jeu — `wh3_main_cth_inf_jade_warrior_crossbowmen_0`
#: contre `wh3_main_cth_veh_sky_lantern_0` — comme le fait deja notre classifieur
#: de roles. Ce n'est pas un canal privilegie : l'agent lit la meme cle.
ORDINARY_MISSILE = ("_inf_",)

#: Etats possibles d'un chronometrage, par unite.
FIRED_MOVING = "tir en mouvement"
FIRED_STOPPED = "tir apres l'arret"
NEVER_FIRED = "jamais tire"
ABORTED = "chronometrage avorte"


@dataclass(frozen=True, slots=True)
class MissileWatch:
    """Le chronometre du tir, **pour une unite**.

    **Il n'y en avait qu'un pour toutes.** Cinq unites ont ete chronometrees le
    18/08 ; la lecture n'en rendait compte que d'une, et concluait « le jeu
    autorise le tir en mouvement » depuis le seul Sky Lantern — un vehicule
    volant — pendant que les quatre tireurs d'infanterie n'avaient rien tire du
    tout. Conclure sur les cas qui ont produit une valeur en ignorant ceux qui
    n'en ont pas produit est le defaut que l'ADR 0018 avait deja corrige sur la
    conversion d'assaut.
    """

    unit: str
    unit_type: str | None = None
    fire_while_moving: str | None = None
    #: Instants en millisecondes depuis l'ordre de deplacement.
    moving_at: float | None = None
    stopped_at: float | None = None
    target_at: float | None = None
    volley_at: float | None = None
    #: La salve est-elle partie alors que l'unite se deplacait encore ?
    volley_while_moving: bool | None = None
    volley_after_stop: float | None = None
    #: Raison pour laquelle le chronometrage n'a pas eu lieu.
    aborted: str | None = None
    #: Le chronometrage s'est-il termine sans qu'aucune salve ne parte ?
    ended_without_volley: bool = False

    @property
    def ordinary(self) -> bool:
        """Tireur ordinaire, ou piece hors normes qui ne parle pas pour les autres ?"""
        cle = self.unit_type or ""
        return any(fragment in cle for fragment in ORDINARY_MISSILE)

    @property
    def state(self) -> str:
        if self.aborted is not None:
            return ABORTED
        if self.volley_at is None:
            return NEVER_FIRED
        return FIRED_MOVING if self.volley_while_moving else FIRED_STOPPED

    def explain(self) -> str:
        marque = "inf" if self.ordinary else "HORS"
        entete = (
            f"  [{marque}] {self.unit} ({self.unit_type or '?'}) "
            f"fire_while_moving={self.fire_while_moving} -> **{self.state}**"
        )
        if self.aborted is not None:
            return entete + f"\n        {self.aborted}"
        etapes = "  ".join(
            f"{nom}={'jamais' if valeur is None else f'{valeur:.0f}ms'}"
            for nom, valeur in (
                ("marche", self.moving_at),
                ("arret", self.stopped_at),
                ("cible", self.target_at),
                ("salve", self.volley_at),
            )
        )
        return entete + f"\n        {etapes}"


@dataclass
class MissileStudy:
    """Ce que le chronometrage dit du tir en mouvement — ou ne dit pas.

    **La question porte sur les tireurs ordinaires.** Un vehicule volant ne parle
    pas pour un arbalétrier, et le journal du 18/08 le montre : le Sky Lantern a
    tire en marchant alors que son `fire_while_moving` valait `false`, tandis que
    quatre unites d'infanterie n'ont jamais tire.
    """

    watches: list[MissileWatch] = field(default_factory=list)

    @property
    def ordinary(self) -> list[MissileWatch]:
        return [item for item in self.watches if item.ordinary]

    @property
    def conclusive(self) -> bool:
        """A-t-on vu un tireur **ordinaire** tirer, d'une facon ou d'une autre ?"""
        return any(item.volley_at is not None for item in self.ordinary)

    def explain(self) -> str:
        if not self.watches:
            return "  Aucun chronometrage : aucune unite de tir n'a ete suivie."
        lignes = [item.explain() for item in self.watches]
        lignes.append("")

        ordinaires = self.ordinary
        if not ordinaires:
            lignes.append(
                "  **Aucun tireur ordinaire chronometre.** Les unites suivies sont\n"
                "  toutes hors normes, et ne repondent pas de l'infanterie de tir."
            )
        elif not self.conclusive:
            muettes = sum(1 for item in ordinaires if item.state == NEVER_FIRED)
            lignes.append(
                f"  **Non tranche.** {muettes} tireur(s) ordinaire(s) sur "
                f"{len(ordinaires)} n'ont tire\n"
                "  aucune salve pendant le chronometrage : l'experience n'a pas produit\n"
                "  l'evenement qu'elle mesure. Ni le correctif du simulateur ni son\n"
                "  contraire ne sont justifies."
            )
        elif any(item.state == FIRED_MOVING for item in ordinaires):
            lignes.append(
                "  **Le jeu autorise le tir en mouvement pour l'infanterie de tir** :\n"
                "  une salve au moins est partie alors que l'unite se deplacait encore."
            )
        else:
            lignes.append(
                "  **L'arret est requis** : aucun tireur ordinaire n'a tire en marchant,\n"
                "  et les salves ne sont parties qu'apres l'arret. Notre simulateur, lui,\n"
                "  laisse tirer en marche — l'ecart est reel."
            )

        hors = [item for item in self.watches if not item.ordinary]
        if hors:
            lignes += [
                "",
                "  Rapporte a part, et exclu du verdict : "
                + ", ".join(f"{item.unit} ({item.state})" for item in hors)
                + ".",
            ]
        return "\n".join(lignes)


@dataclass
class Census:
    """L'inventaire des yeux et des oreilles de l'agent."""

    findings: list[Finding] = field(default_factory=list)
    missile: MissileStudy = field(default_factory=MissileStudy)
    terrain: Terrain = field(default_factory=Terrain)
    #: Altitudes sondees ponctuellement : `sol en (x, z) : valeur`.
    soil: tuple[str, ...] = ()
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
    def failed(self) -> list[Finding]:
        """Accesseurs presents qui ont leve : un defaut d'appel, pas une absence."""
        return [item for item in self.findings if item.verdict == ERROR]

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
            f"{len(self.absent)} absents, {len(self.failed)} en erreur, "
            f"{len(self.untested)} non testes"
            # Le compte de muets appartient a l'en-tete : une liste de quatre-vingts
            # noms plus bas ne dit pas la meme chose qu'une liste de deux, et il
            # faut le voir avant de derouler.
            + (f", {len(self.silent)} muets" if self.silent else "")
            + (f", revision {self.revision}" if self.revision is not None else ""),
            "",
        ]
        lignes += [item.explain() for item in self.findings]

        if self.failed:
            lignes += [
                "",
                "**En erreur : l'accesseur existe et l'appel a leve.** Ce n'est pas une",
                "  absence — le jeu l'expose, et c'est notre facon de le demander qui",
                "  est en cause. C'est la confusion qui a fait declarer le moral",
                "  « structurellement absent » en revision 14.",
            ]
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
        lignes += ["", "--- altitude et relief ---", self.terrain.explain()]
        if self.soil:
            lignes += ["", *(f"    sonde de sol : {item}" for item in self.soil)]
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
    missile: dict[str, dict[str, object]] = {}
    relief: dict[str, float] = {}
    sondes: list[str] = []
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

        if (found := _TERRAIN.search(corps)) is not None:
            for champ, groupe in (
                ("unit_y", "unit"),
                ("v_to_ground", "ground"),
                ("terrain_height", "height"),
            ):
                valeur = _float(found.group(groupe))
                if valeur is not None:
                    relief[champ] = valeur
            vus.update({"v_to_ground", "get_terrain_height"})
            continue

        if (found := _SOIL.search(corps)) is not None:
            sondes.append(
                f"({found.group('x').strip()}, {found.group('z').strip()}) "
                f"-> {found.group('value').strip()}"
            )
            continue

        trouvaille = _read_finding(corps)
        if trouvaille is not None:
            trouvailles.append(trouvaille)
            vus.add(trouvaille.name)

    muets = tuple(nom for nom in expected if nom not in vus)
    return Census(
        findings=trouvailles,
        missile=MissileStudy(
            watches=[MissileWatch(unit=unite, **champs) for unite, champs in missile.items()]  # type: ignore[arg-type]
        ),
        terrain=Terrain(**relief),
        soil=tuple(sondes),
        revision=revision,
        silent=muets,
    )


def _float(brut: str) -> float | None:
    """Une valeur d'altitude, ou `None` quand le Lua a ecrit « indisponible »."""
    try:
        return float(brut)
    except ValueError:
        return None


def _verdict_of(erreur: str) -> str:
    """Absence veritable, ou accesseur present qui a leve ? Voir `NOT_A_FUNCTION`."""
    return ABSENT if erreur.strip() == NOT_A_FUNCTION else ERROR


def _read_finding(corps: str) -> Finding | None:
    if (found := _API.search(corps)) is not None:
        nom = found.group("name")
        kind = "bm" if nom.startswith("bm:") else "API"
        if found.group("value") is not None:
            return Finding(
                nom, kind, OK, value=found.group("value").strip(), target=found.group("target")
            )
        if found.group("error") is not None:
            erreur = found.group("error").strip()
            return Finding(nom, kind, _verdict_of(erreur), detail=erreur)
        return Finding(nom, kind, UNTESTED, detail=(found.group("reason") or "").strip())
    if (found := _ATTR.search(corps)) is not None:
        if found.group("value") is not None:
            return Finding(found.group("name"), "ATTR", OK, value=found.group("value").strip())
        erreur = (found.group("error") or "").strip()
        return Finding(found.group("name"), "ATTR", _verdict_of(erreur), detail=erreur)
    if (found := _ARMY.search(corps)) is not None:
        valeur = found.group("value").strip()
        return Finding(
            found.group("name"),
            "armee",
            ERROR if valeur == "ERREUR" else OK,
            value=None if valeur == "ERREUR" else valeur,
            detail="l'appel a leve" if valeur == "ERREUR" else None,
            target=found.group("target"),
        )
    if (found := _METHOD.search(corps)) is not None:
        presente = found.group("verdict") == "presente"
        return Finding(
            found.group("name"),
            "meth",
            OK if presente else ABSENT,
            # **Presence seule, sans valeur.** Ces methodes ne sont pas appelees :
            # elles ont des effets de bord, et un recensement doit rester sans
            # consequence sur la bataille.
            value="presente" if presente else None,
            detail=None if presente else "absente de la table de classe",
        )
    return None


def _read_missile(corps: str, into: dict[str, dict[str, object]]) -> None:
    """Reconstitue le chronometre, **une entree par unite**.

    Les lignes ne se suivent pas par unite : cinq chronometres tournent en
    parallele et leurs `t1`..`t4` s'entrelacent. Les agreger dans un seul
    enregistrement revenait a garder la derniere valeur vue de chaque champ, tous
    tireurs confondus — c'est ainsi que le journal du 18/08 s'est lu comme une
    seule unite ayant marche, s'etre arretee, puis avoir tire.
    """
    if (found := re.search(r"MISSILE t0 ordre envoye unite=(?P<unit>\S+)", corps)) is not None:
        entree = into.setdefault(found.group("unit"), {})
        for cle, motif in (
            ("unit_type", r"type=(\S+)"),
            ("fire_while_moving", r"fire_while_moving=(\S+)"),
        ):
            if (trouve := re.search(motif, corps)) is not None:
                entree[cle] = trouve.group(1)
        return

    for marque, champ in (("t1", "moving_at"), ("t2", "stopped_at"), ("t3", "target_at")):
        motif = rf"MISSILE {marque} (?P<unit>\S+) .*? a (?P<ms>[\d.]+) ms"
        if (found := re.search(motif, corps)) is not None:
            into.setdefault(found.group("unit"), {})[champ] = float(found.group("ms"))
            return

    if (found := re.search(r"MISSILE t4 (?P<unit>\S+) .*? a (?P<ms>[\d.]+) ms", corps)) is not None:
        entree = into.setdefault(found.group("unit"), {})
        entree["volley_at"] = float(found.group("ms"))
        if (trouve := re.search(r"en_marche=(\w+)", corps)) is not None:
            entree["volley_while_moving"] = trouve.group(1) == "true"
        if (trouve := re.search(r"apres_arret=([\d.]+)", corps)) is not None:
            entree["volley_after_stop"] = float(trouve.group(1))
        return

    if (
        found := re.search(r"MISSILE (?P<unit>\S+) fin du chronometrage sans salve", corps)
    ) is not None:
        into.setdefault(found.group("unit"), {})["ended_without_volley"] = True
        return

    # **Les avortements portent leur motif, et parfois pas d'unite.** « aucune
    # unite de tir » ne designe personne : l'attribuer a un identifiant invente
    # ferait apparaitre un tireur qui n'existe pas.
    if "chronometrage impossible" in corps or "aucun deplacement" in corps:
        found = re.search(r"MISSILE (?P<unit>\d+) ", corps)
        unite = found.group("unit") if found is not None else "?"
        into.setdefault(unite, {})["aborted"] = corps.removeprefix("MISSILE ").strip()
