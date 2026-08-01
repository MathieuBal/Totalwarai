--[[----------------------------------------------------------------------------
    TotalWarAI — sonde de faisabilite
    ------------------------------------------------------------------------
    Objectif unique : prouver qu'un aller-retour complet est possible entre une
    bataille solo de Total War: WARHAMMER III et un programme Python.

        1. reperer une unite alliee controlable ;
        2. ecrire son identifiant et sa position dans un fichier ;
        3. lire une commande de deplacement ecrite par Python ;
        4. deplacer l'unite ;
        5. renvoyer un accuse ;
        6. rendre le controle au joueur.

    Ce n'est PAS l'agent : aucune tactique, aucune gestion d'armee, une seule
    unite a la fois. Tout le reste vit cote Python.

    Ce fichier est une implementation originale. Les API utilisees ont ete
    identifiees en etudiant la documentation de modding et le mod AI General 3
    (voir docs/research/ai-general-3-findings.md) ; aucune portion de ce mod
    n'est reprise ici.

    SECURITE
        - refus categorique en multijoueur ;
        - toute unite prise est relachee au bout de release_after_ms ;
        - la presence du fichier `totalwar_ai_stop` libere tout et coupe la
          lecture des commandes, meme si plus rien d'autre ne fonctionne ;
        - une commande deja traitee n'est jamais rejouee.
------------------------------------------------------------------------------]]

-- PREMIERE LIGNE EXECUTEE. Elle doit apparaitre dans le journal du jeu des que
-- le fichier est charge, quel que soit le contexte (frontend, campagne,
-- bataille) et quoi qu'il advienne ensuite. Son absence signifie que le jeu
-- n'a pas trouve le fichier — pas que la sonde a echoue.
out("[totalwar_ai] === fichier charge (sonde v0.1.0) ===")

-- Le meme fichier peut etre place a deux emplacements dans le pack, par
-- prudence. Sans cette garde, il tournerait en double.
if totalwar_ai_probe_loaded then
    out("[totalwar_ai] deja charge : second exemplaire ignore")
    return totalwar_ai_probe_loaded
end

local PROBE = {
    protocol_version = "0.1.0",
    mod_key = "totalwar_ai_probe",

    -- Chemins relatifs au repertoire de travail du jeu, comme le fait
    -- AI General 3 avec `./mod_config/`.
    dir = "./totalwar_ai/",
    state_file = "./totalwar_ai/totalwar_ai_state.jsonl",
    command_file = "./totalwar_ai/totalwar_ai_command.json",
    ack_file = "./totalwar_ai/totalwar_ai_ack.jsonl",
    stop_file = "./totalwar_ai/totalwar_ai_stop",

    poll_interval_ms = 500,
    state_interval_ms = 1000,

    sequence = 0,             -- compteur des etats emis
    last_command_sequence = 0, -- derniere commande executee : jamais rejouee
    controlled = {},          -- ui_id -> { uc = unitcontroller, release_at_ms = number }
    aborted = false,
    can_write = nil,          -- resultat du test d'ecriture, evalue une seule fois
}

--[[--------------------------------------------------------------------------
    Journalisation
    Tout passe aussi par `out()` : si l'ecriture de fichier s'avere impossible
    en contexte bataille, le log du jeu reste un canal de repli exploitable.
----------------------------------------------------------------------------]]

function PROBE:log(msg)
    out("[totalwar_ai] " .. tostring(msg))
end

--[[--------------------------------------------------------------------------
    Encodage JSON minimal
    Le sous-ensemble strictement necessaire aux deux messages sortants : pas de
    bibliotheque a embarquer, pas de dependance a la presence d'un module JSON.
----------------------------------------------------------------------------]]

local function escape_string(text)
    text = tostring(text)
    text = string.gsub(text, "\\", "\\\\")
    text = string.gsub(text, '"', '\\"')
    text = string.gsub(text, "\n", "\\n")
    text = string.gsub(text, "\r", "\\r")
    text = string.gsub(text, "\t", "\\t")
    return text
end

local function json_string(text)
    return '"' .. escape_string(text) .. '"'
end

--- Formate un nombre pour du JSON, sans jamais toucher a `math`.
---
--- L'environnement Lua du jeu est restreint : `math.huge` y vaut nil, ce qui a
--- fait echouer la sonde au troisieme essai. On evite donc toute la
--- bibliotheque `math`, y compris `math.floor`, dont la presence n'est pas
--- davantage garantie.
---
--- * NaN se reconnait a ce qu'il est different de lui-meme ;
--- * l'infini se reconnait a ce que `x * 0` ne vaut pas 0 (un fini, si).
local function json_number(value)
    if type(value) ~= "number" then
        return "0"
    end
    if value ~= value or value * 0 ~= 0 then
        return "0" -- ni NaN ni infini dans un JSON valide
    end
    if value % 1 == 0 then
        return string.format("%d", value)
    end
    return string.format("%.3f", value)
end

--[[--------------------------------------------------------------------------
    Analyse JSON minimale
    On n'analyse que ce que Python peut envoyer : un objet plat, plus un objet
    `destination` a trois nombres. Tout le reste est rejete plutot que devine.
----------------------------------------------------------------------------]]

--- Extrait la valeur texte d'une cle : "cle" : "valeur"
local function read_string_field(text, key)
    return string.match(text, '"' .. key .. '"%s*:%s*"([^"]*)"')
end

--- Extrait la valeur numerique d'une cle : "cle" : 12.5
local function read_number_field(text, key)
    local raw = string.match(text, '"' .. key .. '"%s*:%s*(-?%d+%.?%d*)')
    if raw then
        return tonumber(raw)
    end
    return nil
end

--- Extrait un vecteur { x, y, z } depuis un sous-objet.
local function read_vector_field(text, key)
    local block = string.match(text, '"' .. key .. '"%s*:%s*(%b{})')
    if not block then
        return nil
    end
    local x = read_number_field(block, "x")
    local y = read_number_field(block, "y")
    local z = read_number_field(block, "z")
    if not x or not z then
        return nil -- l'altitude peut manquer, pas le plan du terrain
    end
    return { x = x, y = y or 0, z = z }
end

--[[--------------------------------------------------------------------------
    Acces aux fichiers
----------------------------------------------------------------------------]]

function PROBE:read_file(path)
    if not io or not io.open then
        return nil, "io indisponible dans ce contexte"
    end
    local handle, err = io.open(path, "r")
    if not handle then
        return nil, err
    end
    local content = handle:read("*a")
    handle:close()
    return content, nil
end

function PROBE:append_line(path, line)
    if not io or not io.open then
        return false, "io indisponible dans ce contexte"
    end
    local handle, err = io.open(path, "a")
    if not handle then
        return false, err
    end
    handle:write(line .. "\n")
    handle:close()
    return true, nil
end

--- Le droit d'ecrire, evalue une seule fois. Le detail du diagnostic est
--- produit par `diagnose_io`, appele au demarrage.
function PROBE:check_write_access()
    if self.can_write ~= nil then
        return self.can_write
    end
    local ok = self:append_line(self.state_file, "")
    self.can_write = ok and true or false
    return self.can_write
end

--- Etablit ce que les entrees-sorties permettent vraiment, et le dit.
---
--- Appele au demarrage, avant toute detection d'unite : c'est la question de
--- faisabilite centrale du prototype (la lecture de fichier en bataille est
--- attestee par un mod tiers, l'ecriture ne l'est pas), et sa reponse ne doit
--- dependre de rien d'autre — sinon un echec ailleurs la rendrait muette.
function PROBE:diagnose_io()
    self:log("--- diagnostic des entrees-sorties ---")

    if not io or not io.open then
        self:log("io.open INDISPONIBLE dans ce contexte : aucun echange par fichier possible")
        self.can_write = false
        return
    end
    self:log("io.open disponible")

    local ok_dir, err_dir = self:append_line(self.state_file, "")
    if ok_dir then
        self.can_write = true
        self:log("ECRITURE OK dans " .. self.state_file)
        self:log("le repertoire de travail du jeu contient donc bien " .. self.dir)
    else
        self.can_write = false
        self:log("ecriture refusee dans " .. self.state_file .. " (" .. tostring(err_dir) .. ")")

        -- Distinguer « pas de droit d'ecriture » de « dossier absent » : on
        -- tente la racine du repertoire de travail, qui existe forcement.
        local ok_root, err_root = self:append_line("./totalwar_ai_probe_io_test.txt", "test")
        if ok_root then
            self:log("mais ECRITURE OK a la racine : le dossier " .. self.dir .. " est absent")
            self:log("=> creer ce dossier dans le repertoire de travail du jeu")
        else
            self:log("ecriture refusee a la racine aussi (" .. tostring(err_root) .. ")")
            self:log("=> ECRITURE IMPOSSIBLE en bataille : repli sur ce journal")
        end
    end

    if self:read_file(self.command_file) then
        self:log("lecture OK : " .. self.command_file .. " existe deja")
    else
        self:log("lecture : " .. self.command_file .. " absent (normal avant la 1re commande)")
    end
    self:log("--- fin du diagnostic ---")
end

function PROBE:file_exists(path)
    local content = self:read_file(path)
    return content ~= nil
end

--[[--------------------------------------------------------------------------
    Messages sortants
----------------------------------------------------------------------------]]

--- Ecrit une ligne, et se plaint si l'ecriture echoue.
---
--- Le droit d'ecriture a ete constate une seule fois, au demarrage, en phase de
--- deploiement. Rien ne garantit qu'il subsiste toute la bataille. Un echec
--- ulterieur doit donc se voir dans le journal plutot que de passer inapercu.
function PROBE:write_or_complain(path, line, label)
    local ok, err = self:append_line(path, line)
    if not ok then
        self:log_occasionally(
            "write_failure_" .. label,
            "ECRITURE REFUSEE dans " .. path .. " : " .. tostring(err)
        )
    end
    return ok
end

function PROBE:emit_state(unit_id, unit_type, position, controllable)
    self.sequence = self.sequence + 1
    local line = "{"
        .. '"protocol_version":' .. json_string(self.protocol_version) .. ","
        .. '"type":"unit_state",'
        .. '"sequence":' .. json_number(self.sequence) .. ","
        .. '"game_time_ms":' .. json_number(bm:time_elapsed_ms()) .. ","
        .. '"unit":{'
        .. '"id":' .. json_string(unit_id) .. ","
        .. '"type":' .. json_string(unit_type) .. ","
        .. '"position":{'
        .. '"x":' .. json_number(position.x) .. ","
        .. '"y":' .. json_number(position.y) .. ","
        .. '"z":' .. json_number(position.z)
        .. "},"
        .. '"controllable":' .. tostring(controllable and true or false)
        .. "}}"

    -- Le log recoit systematiquement le message : c'est le canal de repli.
    self:log("STATE " .. line)
    if self:check_write_access() then
        self:write_or_complain(self.state_file, line, "state")
    end
    return self.sequence
end

function PROBE:emit_ack(sequence, status, error_message, detail)
    local line = "{"
        .. '"protocol_version":' .. json_string(self.protocol_version) .. ","
        .. '"type":"action_result",'
        .. '"sequence":' .. json_number(sequence) .. ","
        .. '"status":' .. json_string(status) .. ","
        .. '"error":' .. (error_message and json_string(error_message) or "null")

    if detail then
        line = line .. ',"detail":{"note":' .. json_string(detail) .. "}"
    end
    line = line .. "}"

    self:log("ACK " .. line)
    if self:check_write_access() then
        self:write_or_complain(self.ack_file, line, "ack")
    end
end

--[[--------------------------------------------------------------------------
    Unites
----------------------------------------------------------------------------]]

--- Premiere unite alliee vivante et controlable.
--- Renvoie aussi le nombre d'unites vues et le nombre de controlables, pour que
--- l'appelant puisse expliquer un echec au lieu de se taire.
function PROBE:find_controllable_unit()
    local alliance = bm:alliances():item(bm:local_alliance())
    local army = alliance:armies():item(bm:local_army())
    local units = army:units()
    local seen = units:count()
    local controllable = 0
    local first = nil

    for index = 1, seen do
        local unit = units:item(index)
        if unit and unit:is_controllable() then
            controllable = controllable + 1
            if not first and unit:is_valid_target() then
                first = unit
            end
        end
    end
    return first, army, seen, controllable
end

--[[--------------------------------------------------------------------------
    Recensement des capacites

    Le mod tiers etudie ne lit ni les effectifs, ni le moral, ni la fatigue :
    rien ne dit si le jeu les expose. Plutot que de supposer, on demande. Chaque
    accesseur candidat est appele une fois sous `pcall` sur une unite reelle, et
    le resultat est journalise. Une seule bataille suffit alors a trancher ce
    que l'agent pourra observer.

    Ce recensement ne tourne qu'au demarrage, une fois : ce n'est pas un cout
    par tick.
----------------------------------------------------------------------------]]

--- Accesseurs sans argument a tester sur une unite alliee.
local UNIT_ACCESSORS = {
    "unique_ui_id",
    "type",
    "name",
    "is_controllable",
    "is_valid_target",
    "number_of_men",
    "number_of_men_alive",
    "unary_hitpoints",
    "unary_morale",
    "is_routing",
    "is_shattered",
    "is_hidden",
    "is_in_melee",
    "can_fly",
    "bearing",
    "ammo_left",
    "missile_range",
    "fatigue",
    "unary_fatigue",
    "fatigue_level",
    "speed",
    "width",
}

--- Decrit brievement une valeur, sans jamais lever d'erreur.
local function describe(value)
    local kind = type(value)
    if kind == "number" then
        return "number " .. json_number(value)
    end
    if kind == "string" then
        return "string " .. string.sub(value, 1, 48)
    end
    if kind == "boolean" then
        return "boolean " .. tostring(value)
    end
    if kind == "nil" then
        return "nil"
    end
    return kind
end

--- Journalise, pour une unite, les accesseurs disponibles et leur valeur.
function PROBE:census_unit_accessors(unit)
    self:log("--- recensement des accesseurs d'unite ---")
    local disponibles = {}
    for index = 1, #UNIT_ACCESSORS do
        local name = UNIT_ACCESSORS[index]
        local method = unit[name]
        if type(method) ~= "function" then
            self:log("  " .. name .. " : ABSENT")
        else
            local ok, value = pcall(method, unit)
            if ok then
                self:log("  " .. name .. " : " .. describe(value))
                disponibles[#disponibles + 1] = name
            else
                self:log("  " .. name .. " : ERREUR " .. tostring(value))
            end
        end
    end
    self:log("accesseurs utilisables : " .. table.concat(disponibles, ", "))
    self:log("--- fin du recensement ---")
    return disponibles
end

--- Journalise ce que l'on peut savoir des alliances en presence.
function PROBE:census_alliances()
    self:log("--- recensement des alliances ---")
    local ok, err = pcall(function()
        local alliances = bm:alliances()
        local total = alliances:count()
        local locale = bm:local_alliance()
        self:log("  alliances : " .. tostring(total) .. ", locale = " .. tostring(locale))
        for index = 1, total do
            local alliance = alliances:item(index)
            local armies = alliance:armies()
            local unites = 0
            for army_index = 1, armies:count() do
                unites = unites + armies:item(army_index):units():count()
            end
            self:log(
                "  alliance "
                    .. tostring(index)
                    .. " : "
                    .. tostring(armies:count())
                    .. " armee(s), "
                    .. tostring(unites)
                    .. " unite(s)"
                    .. (index == locale and " <- la notre" or "")
            )
        end
    end)
    if not ok then
        self:log("  ERREUR pendant le recensement des alliances : " .. tostring(err))
    end
    self:log("--- fin du recensement ---")
end

function PROBE:unit_position(unit)
    local position = unit:position()
    return {
        x = position:get_x(),
        y = position:get_y(),
        z = position:get_z(),
    }
end

function PROBE:unit_identifier(unit)
    return tostring(unit:unique_ui_id())
end

--- Retrouve une unite alliee par son identifiant d'interface.
function PROBE:find_unit_by_id(wanted_id)
    local alliance = bm:alliances():item(bm:local_alliance())
    local army = alliance:armies():item(bm:local_army())
    local units = army:units()

    for index = 1, units:count() do
        local unit = units:item(index)
        if unit and tostring(unit:unique_ui_id()) == tostring(wanted_id) then
            return unit, army
        end
    end
    return nil, army
end

--[[--------------------------------------------------------------------------
    Prise et restitution du controle
----------------------------------------------------------------------------]]

function PROBE:release_unit(ui_id)
    local entry = self.controlled[ui_id]
    if not entry then
        return false
    end
    if entry.uc then
        entry.uc:release_control()
    end
    self.controlled[ui_id] = nil
    self:log("controle rendu au joueur pour l'unite " .. tostring(ui_id))
    return true
end

function PROBE:release_all(reason)
    local count = 0
    for ui_id, _ in pairs(self.controlled) do
        if self:release_unit(ui_id) then
            count = count + 1
        end
    end
    if count > 0 then
        self:log("toutes les unites relachees (" .. tostring(reason or "sans motif") .. ")")
    end
    return count
end

--[[--------------------------------------------------------------------------
    Execution d'une commande
----------------------------------------------------------------------------]]

function PROBE:execute_move(sequence, unit_id, destination, release_after_ms)
    local unit, army = self:find_unit_by_id(unit_id)
    if not unit then
        self:emit_ack(sequence, "rejected", "unite introuvable : " .. tostring(unit_id))
        return
    end
    if not unit:is_controllable() then
        self:emit_ack(sequence, "rejected", "unite non controlable : " .. tostring(unit_id))
        return
    end

    local uc = army:create_unit_controller()
    if not uc then
        self:emit_ack(sequence, "rejected", "creation du unitcontroller impossible")
        return
    end

    local added = pcall(function() uc:add_units(unit) end)
    if not added then
        self:emit_ack(sequence, "rejected", "uc:add_units a echoue (groupe verrouille ?)")
        return
    end

    local moved = pcall(function()
        uc:goto_location(v_to_ground(v(destination.x, destination.y, destination.z)), true)
    end)
    if not moved then
        uc:release_control()
        self:emit_ack(sequence, "rejected", "uc:goto_location a echoue")
        return
    end

    self.controlled[unit_id] = { uc = uc, release_at_ms = bm:time_elapsed_ms() + release_after_ms }
    self:emit_ack(sequence, "accepted", nil, "deplacement lance")
    self:log("unite " .. tostring(unit_id) .. " envoyee vers "
        .. json_number(destination.x) .. ", " .. json_number(destination.z))

    -- Restitution garantie : meme si l'unite marche encore, on rend la main.
    bm:callback(function()
        if self.controlled[unit_id] then
            self:release_unit(unit_id)
            self:emit_ack(sequence, "released", nil, "controle rendu apres delai")
        end
    end, release_after_ms, "totalwar_ai_release_" .. tostring(sequence))
end

--[[--------------------------------------------------------------------------
    Boucle de lecture des commandes
----------------------------------------------------------------------------]]

function PROBE:process_command_file()
    if self.aborted then
        return
    end

    -- L'arret d'urgence par fichier prime sur tout le reste : il fonctionne
    -- meme si l'analyse des commandes echoue.
    if self:file_exists(self.stop_file) then
        self:abort("fichier d'arret present")
        return
    end

    local content = self:read_file(self.command_file)
    if not content or content == "" then
        return
    end

    local version = read_string_field(content, "protocol_version")
    if version ~= self.protocol_version then
        return -- version absente ou incompatible : on ignore, sans bruit
    end

    local sequence = read_number_field(content, "sequence")
    if not sequence then
        return -- commande incomplete : Python ecrivait peut-etre encore
    end

    if sequence <= self.last_command_sequence then
        return -- deja traitee : on ne rejoue jamais une commande
    end

    local command_type = read_string_field(content, "type")
    if not command_type then
        self.last_command_sequence = sequence
        self:emit_ack(sequence, "rejected", "champ 'type' absent")
        return
    end

    -- La sequence est consommee avant execution : une commande qui echoue ne
    -- doit pas etre retentee en boucle a chaque tick.
    self.last_command_sequence = sequence

    if command_type == "abort" then
        self:abort("commande abort recue")
        self:emit_ack(sequence, "released", nil, "arret demande")
        return
    end

    if command_type ~= "move_unit" then
        self:emit_ack(sequence, "rejected", "type de commande inconnu : " .. command_type)
        return
    end

    local unit_id = read_string_field(content, "unit_id")
    local destination = read_vector_field(content, "destination")
    if not unit_id or not destination then
        self:emit_ack(sequence, "rejected", "commande incomplete (unit_id ou destination)")
        return
    end

    local release_after_ms = read_number_field(content, "release_after_ms") or 5000

    local ok, err = pcall(function()
        self:execute_move(sequence, unit_id, destination, release_after_ms)
    end)
    if not ok then
        self:emit_ack(sequence, "rejected", "erreur d'execution : " .. tostring(err))
    end
end

--- Journalise une explication, mais pas a chaque tick : les premieres fois,
--- puis de loin en loin. Un journal noye est un journal inutile.
function PROBE:log_occasionally(counter_name, msg)
    local count = (self[counter_name] or 0) + 1
    self[counter_name] = count
    if count <= 3 or count % 20 == 0 then
        self:log(msg .. " (occurrence " .. tostring(count) .. ")")
    end
end

function PROBE:publish_state()
    if self.aborted then
        return
    end

    local unit, army, seen, controllable = self:find_controllable_unit()
    if not unit then
        -- Ne jamais echouer en silence : dire ce qu'on a vu.
        self:log_occasionally(
            "no_unit_count",
            "aucune unite controlable : " .. tostring(seen) .. " unites vues, "
                .. tostring(controllable) .. " controlables"
        )
        return
    end

    self:emit_state(
        self:unit_identifier(unit),
        tostring(unit:type()),
        self:unit_position(unit),
        true
    )
end

--[[--------------------------------------------------------------------------
    Arret et demarrage
----------------------------------------------------------------------------]]

function PROBE:abort(reason)
    if self.aborted then
        return
    end
    self.aborted = true
    self:release_all(reason)
    bm:remove_process("totalwar_ai_poll")
    bm:remove_process("totalwar_ai_state")
    self:log("SONDE ARRETEE : " .. tostring(reason))
end

--- Vrai si la bataille est multijoueur, ou si on n'a pas pu le determiner.
--- Le doute profite a la prudence : ce prototype ne doit jamais tourner dans
--- une partie a plusieurs.
function PROBE:is_multiplayer_or_unknown()
    local ok, result = pcall(function() return bm:is_multiplayer() end)
    if not ok then
        self:log("impossible de determiner le type de partie : sonde desactivee")
        return true
    end
    return result and true or false
end

--- Execute une methode en rattrapant toute erreur.
--- Une erreur dans un callback periodique le tue silencieusement : sans cette
--- garde, la sonde s'arreterait sans que rien ne l'indique.
function PROBE:guarded(method_name)
    local ok, err = pcall(function() self[method_name](self) end)
    if not ok then
        self:log_occasionally(
            "error_" .. method_name,
            "ERREUR dans " .. method_name .. " : " .. tostring(err)
        )
    end
end

function PROBE:start()
    if self:is_multiplayer_or_unknown() then
        self:log("multijoueur ou type de partie inconnu : la sonde reste desactivee")
        return
    end

    self:log("sonde active - protocole " .. self.protocol_version)
    self:log("repertoire d'echange attendu : " .. self.dir)

    -- Le diagnostic des entrees-sorties passe en premier : c'est la question
    -- de faisabilite centrale, et sa reponse ne doit dependre de rien d'autre.
    self:diagnose_io()

    local unit, _, seen, controllable = self:find_controllable_unit()
    self:log(
        "armee du joueur : " .. tostring(seen) .. " unites, "
            .. tostring(controllable) .. " controlables, "
            .. (unit and ("premiere = " .. self:unit_identifier(unit)) or "aucune utilisable")
    )

    -- Recensement unique : il dit ce que le jeu expose vraiment, et remplace
    -- les lignes « inconnue » de la fiche de faisabilite par des faits.
    self:census_alliances()
    if unit then
        local ok, err = pcall(function() self:census_unit_accessors(unit) end)
        if not ok then
            self:log("ERREUR pendant le recensement des accesseurs : " .. tostring(err))
        end
    end

    bm:repeat_callback(function() self:guarded("publish_state") end,
        self.state_interval_ms, "totalwar_ai_state")
    bm:repeat_callback(function() self:guarded("process_command_file") end,
        self.poll_interval_ms, "totalwar_ai_poll")

    -- Filet de securite : quoi qu'il arrive, rien ne reste pris a la fin.
    bm:register_phase_change_callback("Complete", function()
        self:abort("fin de bataille")
    end)
end

totalwar_ai_probe_loaded = PROBE

-- `bm` est le battle_manager, cree par le jeu en contexte de bataille
-- uniquement. Ce fichier peut aussi etre charge depuis le menu principal ou la
-- carte de campagne : il n'y a alors rien a faire, et c'est normal.
if bm then
    out("[totalwar_ai] contexte de bataille detecte : demarrage dans 1 seconde")
    bm:callback(function() PROBE:start() end, 1000, "totalwar_ai_start")
else
    out("[totalwar_ai] pas de battle_manager : hors bataille, sonde en veille")
end

return PROBE
