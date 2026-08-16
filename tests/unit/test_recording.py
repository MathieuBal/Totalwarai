"""Enregistrement des batailles pilotees dans le jeu.

Ces batailles sont la seule mesure capable de departager un jour le simulateur
et le jeu, dont les verdicts divergent (voir `docs/decisions/0005`). Leur
fidelite compte donc autant que celle du pont : une donnee inventee ici
ressortirait plus tard comme un fait.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from totalwar_ai.bridge.command_models import ProbeAttack, ProbeBattleState, ProbeUnitObservation
from totalwar_ai.bridge.live import LiveStep
from totalwar_ai.bridge.orders import Translation
from totalwar_ai.bridge.recording import LIVE_SCENARIO, RECORDING_FORMAT, BattleRecorder
from totalwar_ai.domain.actions import ActionType
from totalwar_ai.domain.battle_state import BattleOutcomeKind
from totalwar_ai.domain.geometry import Vector3


def _unite(unit_id: str, *, alive: bool = True, hp: float = 1.0) -> ProbeUnitObservation:
    return ProbeUnitObservation(
        unit_id=unit_id,
        position=Vector3(0.0, 0.0, 0.0),
        alive=alive,
        hitpoints=hp,
    )


def _etat(
    allies: int, enemies: int, *, ms: int = 0, phase: str = "Deployed", morts: int = 0
) -> ProbeBattleState:
    unites = [_unite(f"a{i}") for i in range(allies - morts)]
    unites += [_unite(f"m{i}", alive=False) for i in range(morts)]
    return ProbeBattleState(
        allies=tuple(unites),
        enemies=tuple(_unite(f"e{i}") for i in range(enemies)),
        game_time_ms=ms,
        phase=phase,
    )


def _tour(state: ProbeBattleState, **kwargs: object) -> LiveStep:
    return LiveStep(state=state, **kwargs)  # type: ignore[arg-type]


# --- ce qui est retenu --------------------------------------------------------


def test_chaque_tour_est_ecrit_au_fil_de_l_eau(tmp_path: Path) -> None:
    """Une interruption ne doit pas emporter la bataille avec elle."""
    recorder = BattleRecorder(directory=tmp_path)
    recorder.observe(_tour(_etat(5, 5, ms=1000)))
    recorder.observe(_tour(_etat(5, 4, ms=2000)))

    assert recorder.path is not None
    # Ecrit avant meme d'avoir ferme. L'inventaire des unites occupe ses propres
    # lignes : on ne compte donc que les tours.
    lignes = [json.loads(ligne) for ligne in recorder.path.read_text(encoding="utf-8").splitlines()]
    tours = [ligne for ligne in lignes if "turn" in ligne and "roster" not in ligne]
    assert len(tours) == 2
    assert tours[0]["allies"] == 5
    assert tours[1]["enemies"] == 4
    recorder.close()


def test_les_ordres_et_les_refus_sont_comptes(tmp_path: Path) -> None:
    recorder = BattleRecorder(directory=tmp_path)
    recorder.observe(
        _tour(
            _etat(5, 5),
            sent=3,
            blocked=("refus a", "refus b"),
            translation=Translation(
                moves=(("a0", Vector3(1.0, 0.0, 2.0)),),
                attacks=(ProbeAttack(unit_id="a1", target_id="e0"),),
                halts=("a2",),
                untranslated=((ActionType.REORIENT_FRONT, "sans equivalent"),),
            ),
        )
    )
    recorder.close()

    resume = recorder.summary()
    assert resume.actions_sent == 3
    assert resume.actions_blocked == 2
    assert recorder.actions_lost == 1

    entree = recorder.entries[0]
    assert entree["orders"]["moves"][0]["unit_id"] == "a0"
    assert entree["orders"]["attacks"][0]["target_id"] == "e0"
    assert entree["orders"]["halts"] == ["a2"]
    assert entree["untranslated"][0]["action"] == "REORIENT_FRONT"


def test_un_tour_sans_etat_n_est_pas_un_tour() -> None:
    """Le jeu n'a rien publie : il n'y a rien a enregistrer."""
    recorder = BattleRecorder()
    recorder.observe(LiveStep())
    assert recorder.turns == 0
    assert not recorder.entries


def test_la_duree_est_celle_du_jeu_pas_celle_de_l_horloge(tmp_path: Path) -> None:
    """Le temps de jeu peut etre accelere ou en pause : seul le sien compte."""
    recorder = BattleRecorder(directory=tmp_path)
    recorder.observe(_tour(_etat(5, 5, ms=30_000)))
    recorder.observe(_tour(_etat(5, 5, ms=210_000)))
    recorder.close()

    assert recorder.summary().duration == pytest.approx(180.0)


# --- ce que l'enregistrement refuse de pretendre ------------------------------


def test_l_issue_reste_inconnue_tant_que_le_jeu_ne_l_annonce_pas() -> None:
    """Deviner une victoire depuis les forces restantes polluerait la memoire.

    Une session interrompue par l'operateur, ou une armee en bonne posture au
    moment ou l'on cesse d'observer, ne sont pas des victoires.
    """
    recorder = BattleRecorder()
    recorder.observe(_tour(_etat(5, 1, phase="Deployed")))  # position dominante

    assert recorder.outcome is BattleOutcomeKind.UNKNOWN
    assert recorder.summary().outcome is BattleOutcomeKind.UNKNOWN


def test_l_issue_est_lue_quand_la_bataille_se_termine() -> None:
    recorder = BattleRecorder()
    recorder.observe(_tour(_etat(5, 5)))
    recorder.observe(_tour(ProbeBattleState(allies=(_unite("a0"),), phase="Complete")))

    assert recorder.outcome is BattleOutcomeKind.VICTORY


def test_une_armee_aneantie_est_une_defaite() -> None:
    recorder = BattleRecorder()
    recorder.observe(_tour(_etat(5, 5)))
    recorder.observe(_tour(ProbeBattleState(enemies=(_unite("e0"),), phase="Complete")))

    assert recorder.outcome is BattleOutcomeKind.DEFEAT


def test_l_episode_ne_fabrique_ni_transitions_ni_recompense() -> None:
    """Le jeu ne dit pas qui a tire sur qui : rien ne permet de les calculer.

    Les inventer ferait entrer du bruit dans la memoire d'apprentissage sous
    les traits d'une mesure.
    """
    recorder = BattleRecorder()
    recorder.observe(_tour(_etat(5, 5)))

    episode = recorder.episode()
    assert episode.transitions == []
    assert episode.summary.total_reward == 0.0


# --- comparabilite avec les batailles simulees --------------------------------


def test_les_batailles_reelles_sont_isolables_des_simulees() -> None:
    """Melanger les deux sources rendrait toute comparaison inutilisable."""
    recorder = BattleRecorder()
    recorder.observe(_tour(_etat(5, 5)))

    resume = recorder.summary()
    assert resume.scenario == LIVE_SCENARIO
    assert resume.agent_mode == "deterministic-live"
    assert resume.metrics["source"] == "game"


def test_les_pertes_sont_rapportees_a_l_effectif_de_depart() -> None:
    recorder = BattleRecorder()
    recorder.observe(_tour(_etat(4, 4)))
    recorder.observe(_tour(_etat(4, 4, morts=3)))

    resume = recorder.summary()
    assert resume.ally_remaining == pytest.approx(0.25)
    assert resume.enemy_remaining == pytest.approx(1.0)


def test_une_bataille_enregistree_se_relit_en_memoire(tmp_path: Path) -> None:
    """Le but de tout ceci : comparer plus tard le jeu et le simulateur."""
    from totalwar_ai.memory.repository import MemoryRepository

    recorder = BattleRecorder(directory=tmp_path)
    recorder.observe(_tour(_etat(5, 5, ms=1000), sent=2))
    recorder.close()

    repository = MemoryRepository(tmp_path / "memoire.sqlite3")
    repository.save_episode(recorder.episode())

    relues = repository.list_battles(scenario=LIVE_SCENARIO)
    assert [item.battle_id for item in relues] == [recorder.battle_id]
    assert relues[0].actions_sent == 2


def test_un_tour_sans_nouvel_etat_ne_gonfle_pas_le_compte(tmp_path: Path) -> None:
    """La boucle interroge plus souvent que le jeu ne publie.

    Compter ces tours vides fausserait la duree comme la cadence d'ordres, et
    rendrait deux enregistrements incomparables selon la vitesse de la machine.
    """
    recorder = BattleRecorder(directory=tmp_path)
    recorder.observe(_tour(_etat(5, 5, ms=1000)))
    recorder.observe(LiveStep())  # rien de nouveau cote jeu
    recorder.observe(LiveStep())
    recorder.observe(_tour(_etat(5, 5, ms=2000)))
    recorder.close()

    assert recorder.turns == 2
    assert recorder.summary().metrics["turns"] == 2


def test_une_unite_detruite_mais_encore_listee_ne_sauve_pas_l_adversaire() -> None:
    """L'issue se juge sur les unites vivantes, pas sur la longueur des listes.

    A la premiere bataille menee a son terme, le camp vaincu avait disparu des
    listes du jeu et l'issue etait juste. Rien ne garantit qu'il en aille
    toujours ainsi : une unite detruite mais encore publiee ferait basculer un
    aneantissement en match nul.
    """
    from totalwar_ai.bridge.command_models import ProbeBattleState, ProbeUnitObservation
    from totalwar_ai.bridge.live import LiveStep
    from totalwar_ai.bridge.recording import BattleRecorder
    from totalwar_ai.domain.battle_state import BattleOutcomeKind
    from totalwar_ai.domain.geometry import Vector3

    def unite(unit_id: str, *, alive: bool) -> ProbeUnitObservation:
        return ProbeUnitObservation(unit_id=unit_id, position=Vector3(0.0, 0.0, 0.0), alive=alive)

    recorder = BattleRecorder(directory=None)
    recorder.observe(
        LiveStep(
            state=ProbeBattleState(
                allies=(unite("a1", alive=True),),
                enemies=(unite("e1", alive=True),),
                phase="Deployed",
            )
        )
    )
    recorder.observe(
        LiveStep(
            state=ProbeBattleState(
                allies=(unite("a1", alive=True),),
                enemies=(unite("e1", alive=False),),
                phase="Complete",
            )
        )
    )

    assert recorder.outcome is BattleOutcomeKind.VICTORY


# --- ce qu'il faut pour apprendre en regardant jouer l'IA du moteur -------------
#
# Un enregistrement qui ne garde que des comptes — dix-huit allies, quatorze de
# force — permet de comparer deux issues et rien de plus. Pour apprendre les
# decisions de l'IA, il faut voir chaque unite, a chaque tour.


def _observation(
    unit_id: str,
    *,
    x: float = 0.0,
    z: float = 0.0,
    unit_type: str = "wh3_main_cth_inf_jade_warriors",
    in_melee: bool = False,
    alive: bool = True,
    ammo: int | None = None,
    missile_range: float | None = None,
) -> ProbeUnitObservation:
    return ProbeUnitObservation(
        unit_id=unit_id,
        position=Vector3(x, 0.0, z),
        unit_type=unit_type,
        in_melee=in_melee,
        alive=alive,
        hitpoints=0.8712,
        men_alive=90,
        bearing=134.523,
        ammo=ammo,
        missile_range=missile_range,
    )


def _lignes(chemin: Path) -> list[dict[str, object]]:
    return [json.loads(ligne) for ligne in chemin.read_text(encoding="utf-8").splitlines()]


def test_chaque_unite_est_enregistree_a_chaque_tour(tmp_path: Path) -> None:
    """Sans cela, on ne voit pas ce que l'IA du jeu a fait."""
    recorder = BattleRecorder(directory=tmp_path)
    etat = ProbeBattleState(
        allies=(_observation("a1", x=10.0),),
        enemies=(_observation("e1", x=200.0),),
        phase="Deployed",
    )
    recorder.observe(LiveStep(state=etat))
    recorder.close()

    assert recorder.path is not None
    tours = [ligne for ligne in _lignes(recorder.path) if "units" in ligne]
    assert len(tours) == 1
    identifiants = {unite["id"] for unite in tours[0]["units"]}  # type: ignore[union-attr,index]
    assert identifiants == {"a1", "e1"}


def test_ce_qui_ne_change_jamais_est_ecrit_une_seule_fois(tmp_path: Path) -> None:
    """Repeter type, camp et portee a chaque tour doublait le poids du corpus."""
    recorder = BattleRecorder(directory=tmp_path)
    etat = ProbeBattleState(
        allies=(_observation("a1", missile_range=90.0, ammo=120),),
        enemies=(_observation("e1", x=200.0),),
        phase="Deployed",
    )
    for _ in range(5):
        recorder.observe(LiveStep(state=etat))
    recorder.close()

    assert recorder.path is not None
    lignes = _lignes(recorder.path)
    inventaires = [ligne for ligne in lignes if "roster" in ligne]
    assert len(inventaires) == 1, "l'inventaire a ete republie sans nouvelle unite"
    assert inventaires[0]["roster"]["a1"]["missile_range"] == 90.0  # type: ignore[index]

    tours = [ligne for ligne in lignes if "units" in ligne]
    assert "type" not in tours[0]["units"][0]  # type: ignore[index]
    # Les munitions, elles, varient : elles restent dans le tour.
    assert tours[0]["units"][0]["ammo"] == 120  # type: ignore[index]


def test_une_unite_qui_arrive_en_cours_de_bataille_est_inventoriee(tmp_path: Path) -> None:
    """Les renforts d'une bataille de campagne resteraient sans type ni camp."""
    recorder = BattleRecorder(directory=tmp_path)
    premier = ProbeBattleState(allies=(_observation("a1"),), phase="Deployed")
    recorder.observe(LiveStep(state=premier))
    renfort = ProbeBattleState(
        allies=(_observation("a1"), _observation("a2", unit_type="wh3_main_cth_art_grand_cannon")),
        phase="Deployed",
    )
    recorder.observe(LiveStep(state=renfort))
    recorder.close()

    assert recorder.path is not None
    inventaires = [ligne for ligne in _lignes(recorder.path) if "roster" in ligne]
    assert len(inventaires) == 2, "le renfort n'a pas ete inventorie"
    assert "a2" in inventaires[1]["roster"]  # type: ignore[operator]


def test_un_faux_ne_prend_pas_de_place(tmp_path: Path) -> None:
    """Quarante unites sur deux cent quarante tours : chaque champ inutile compte."""
    recorder = BattleRecorder(directory=tmp_path)
    recorder.observe(
        LiveStep(state=ProbeBattleState(allies=(_observation("a1"),), phase="Deployed"))
    )
    recorder.close()

    assert recorder.path is not None
    tour = next(ligne for ligne in _lignes(recorder.path) if "units" in ligne)
    unite = tour["units"][0]  # type: ignore[index]
    assert "in_melee" not in unite, "un booleen faux a ete ecrit"
    assert "dead" not in unite
    assert "routing" not in unite


def test_l_enregistrement_par_unite_peut_etre_coupe(tmp_path: Path) -> None:
    """Un retour en arriere doit rester possible sans toucher au code."""
    recorder = BattleRecorder(directory=tmp_path, record_units=False)
    recorder.observe(
        LiveStep(state=ProbeBattleState(allies=(_observation("a1"),), phase="Deployed"))
    )
    recorder.close()

    assert recorder.path is not None
    assert all("units" not in ligne for ligne in _lignes(recorder.path))


def test_aucun_etat_publie_n_est_jete(tmp_path: Path) -> None:
    """Le jeu publie plus souvent que la boucle ne decide.

    `latest_battle_state()` vidait le flux et ne rendait que le dernier : les
    autres disparaissaient definitivement. A 2 Hz de publication pour 1 Hz de
    decision, c'etait la moitie du corpus d'apprentissage perdue en silence, et
    rien ne permettait de s'en apercevoir.
    """
    recorder = BattleRecorder(directory=tmp_path)
    etats = tuple(
        ProbeBattleState(
            allies=(_observation("a1", x=float(index)),),
            sequence=index,
            game_time_ms=index * 500,
            phase="Deployed",
        )
        for index in range(1, 4)
    )
    recorder.observe(LiveStep(state=etats[-1], observed=etats))
    recorder.close()

    assert recorder.turns == 1, "un seul tour de decision"
    assert recorder.observations == 3, "des etats publies ont ete jetes"

    assert recorder.path is not None
    tours = [ligne for ligne in _lignes(recorder.path) if "units" in ligne]
    assert [ligne["sequence"] for ligne in tours] == [1, 2, 3]
    # Seul l'etat de decision porte les ordres.
    assert [bool(ligne.get("decision")) for ligne in tours] == [False, False, True]


def test_un_trou_dans_le_flux_devient_visible(tmp_path: Path) -> None:
    """Sans la sequence de la sonde, rien ne distingue une bataille trouee."""
    recorder = BattleRecorder(directory=tmp_path)
    for sequence in (1, 2, 7):  # quatre etats manquants
        recorder.observe(
            LiveStep(
                state=ProbeBattleState(
                    allies=(_observation("a1"),), sequence=sequence, phase="Deployed"
                )
            )
        )
    recorder.close()

    assert recorder.path is not None
    sequences = [ligne["sequence"] for ligne in _lignes(recorder.path) if "units" in ligne]
    assert sequences == [1, 2, 7]
    assert max(sequences) - min(sequences) + 1 != len(sequences), "le trou est indetectable"


def test_l_altitude_est_conservee(tmp_path: Path) -> None:
    """C'est la seule donnee de terrain que le jeu nous donne, et on la jetait.

    `position():get_y()` repond en bataille — entre 21 et 33 releves. Elle dit
    qui tient la hauteur, ce que reclame toute doctrine d'artillerie.
    """
    recorder = BattleRecorder(directory=tmp_path)
    haut = ProbeUnitObservation(unit_id="a1", position=Vector3(10.0, 33.4, -20.0))
    bas = ProbeUnitObservation(unit_id="e1", position=Vector3(10.0, 21.1, 200.0))
    recorder.observe(LiveStep(state=ProbeBattleState(allies=(haut,), enemies=(bas,))))
    recorder.close()

    assert recorder.path is not None
    tour = next(ligne for ligne in _lignes(recorder.path) if "units" in ligne)
    altitudes = {unite["id"]: unite["y"] for unite in tour["units"]}  # type: ignore[index,union-attr]
    assert altitudes == {"a1": 33.4, "e1": 21.1}


# --- une session qui n'a pas commence ne laisse rien -----------------------------


def test_aucun_fichier_tant_que_rien_n_est_observe(tmp_path: Path) -> None:
    """Delegation refusee, bataille pas encore lancee : rien a enregistrer.

    Neuf fichiers `aucun tour enregistre` pour trois vraies batailles, au
    premier soir de corpus : le fichier etait ouvert avant de savoir s'il y
    aurait quoi que ce soit dedans.
    """
    recorder = BattleRecorder(directory=tmp_path)
    recorder.close()

    assert list(tmp_path.glob("*.jsonl")) == []


def test_le_premier_etat_cree_le_fichier(tmp_path: Path) -> None:
    recorder = BattleRecorder(directory=tmp_path)
    recorder.observe(_tour(_etat(3, 3, ms=1000)))
    recorder.close()

    ecrits = list(tmp_path.glob("*.jsonl"))
    assert len(ecrits) == 1
    assert recorder.path is not None and ecrits[0] == recorder.path
    premiere = ecrits[0].read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(premiere)["format"] == RECORDING_FORMAT


def test_une_armee_entierement_en_deroute_ne_vaut_pas_cent_pour_cent() -> None:
    """**Le chiffre par lequel le projet se juge annoncait une armee intacte.**

    Bataille `a1274d62` : douze unites alliees sur douze ont rompu, la derniere
    a t=417 s, et le bilan a rendu « Forces restantes : 100 % ». Une unite en
    deroute a des hommes debout, donc elle etait comptee vivante — alors qu'il
    ne restait personne au combat.
    """
    debout = ProbeBattleState(
        allies=tuple(_unite(f"a{i}") for i in range(4)),
        enemies=tuple(_unite(f"e{i}") for i in range(4)),
        phase="Deployed",
    )
    en_fuite = ProbeBattleState(
        allies=tuple(
            ProbeUnitObservation(
                unit_id=f"a{i}",
                position=Vector3(0.0, 0.0, 0.0),
                alive=True,
                hitpoints=0.5,
                routing=True,
            )
            for i in range(4)
        ),
        enemies=tuple(_unite(f"e{i}") for i in range(4)),
        phase="Deployed",
    )
    recorder = BattleRecorder()
    recorder.observe(_tour(debout))
    recorder.observe(_tour(en_fuite))

    resume = recorder.summary()
    assert resume.ally_remaining == pytest.approx(0.0)
    assert resume.enemy_remaining == pytest.approx(1.0)


def test_la_force_restante_pese_les_hommes_et_leur_sante() -> None:
    """La simulation somme des points de vie ; compter des unites ici rendait
    les deux chiffres etrangers l'un a l'autre alors que tout les compare."""
    intacte = ProbeBattleState(
        allies=(
            ProbeUnitObservation(
                unit_id="a1", position=Vector3(0.0, 0.0, 0.0), hitpoints=1.0, men_alive=100
            ),
        ),
        phase="Deployed",
    )
    entamee = ProbeBattleState(
        allies=(
            ProbeUnitObservation(
                unit_id="a1", position=Vector3(0.0, 0.0, 0.0), hitpoints=0.5, men_alive=50
            ),
        ),
        phase="Deployed",
    )
    recorder = BattleRecorder()
    recorder.observe(_tour(intacte))
    recorder.observe(_tour(entamee))

    # Moitie des hommes, a moitie de leur sante : un quart de la force.
    assert recorder.summary().ally_remaining == pytest.approx(0.25)


def test_deux_armees_differentes_n_ont_pas_la_meme_empreinte() -> None:
    """**L'empreinte ne comptait que les unites.**

    Elle rendait `allies:12|enemies:10`, si bien que douze lanciers et douze
    chevaliers portaient la meme. `MemoryRepository.find_similar` les donnait
    donc pour comparables, et l'adaptation de doctrine — qui demarre des la
    deuxieme bataille — tirait une lecon de la moyenne de deux affrontements
    sans rapport.
    """

    def _bataille(cle_alliee: str) -> ProbeBattleState:
        return ProbeBattleState(
            allies=(
                ProbeUnitObservation(
                    unit_id="a1",
                    position=Vector3(0.0, 0.0, 0.0),
                    unit_type=cle_alliee,
                    controllable=True,
                    men_alive=80,
                ),
            ),
            enemies=(
                ProbeUnitObservation(
                    unit_id="e1",
                    position=Vector3(0.0, 0.0, 50.0),
                    unit_type="wh_main_emp_inf_swordsmen",
                    men_alive=80,
                ),
            ),
            phase="Deployed",
        )

    def _empreinte(etat: ProbeBattleState) -> str:
        recorder = BattleRecorder()
        recorder.observe(_tour(etat))
        return recorder.summary().army_fingerprint

    cavalerie = _empreinte(_bataille("wh_main_emp_cav_reiksguard"))
    infanterie = _empreinte(_bataille("wh_main_emp_inf_spearmen"))

    assert cavalerie != infanterie, "deux armees differentes partagent une empreinte"
    # Et l'empreinte decrit bien des roles, comme celle du simulateur.
    assert "x1" in cavalerie and "ally:" in cavalerie
