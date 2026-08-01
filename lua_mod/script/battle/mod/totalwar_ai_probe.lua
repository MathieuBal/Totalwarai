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

local function json_number(value)
    if value ~= value or value == math.huge or value == -math.huge then
        return "0" -- ni NaN ni infini dans un JSON valide
    end
    if value == math.floor(value) then
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

--- Verifie une fois pour toutes si l'ecriture est possible ici.
--- C'est LA question de faisabilite : AI General 3 demontre la lecture en
--- bataille, mais n'ecrit qu'en frontend.
function PROBE:check_write_access()
    if self.can_write ~= nil then
        return self.can_write
    end
    local ok, err = self:append_line(self.state_file, "")
    self.can_write = ok and true or false
    if ok then
        self:log("ecriture de fichier disponible : " .. self.state_file)
    else
        self:log("ECRITURE INDISPONIBLE (" .. tostring(err) .. ") — repli sur le log du jeu")
    end
    return self.can_write
end

function PROBE:file_exists(path)
    local content = self:read_file(path)
    return content ~= nil
end

--[[--------------------------------------------------------------------------
    Messages sortants
----------------------------------------------------------------------------]]

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
        self:append_line(self.state_file, line)
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
        self:append_line(self.ack_file, line)
    end
end

--[[--------------------------------------------------------------------------
    Unites
----------------------------------------------------------------------------]]

--- Premiere unite alliee vivante et controlable, ou nil.
function PROBE:find_controllable_unit()
    local alliance = bm:alliances():item(bm:local_alliance())
    local army = alliance:armies():item(bm:local_army())
    local units = army:units()

    for index = 1, units:count() do
        local unit = units:item(index)
        if unit and unit:is_valid_target() and unit:is_controllable() then
            return unit, army
        end
    end
    return nil, army
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

function PROBE:publish_state()
    if self.aborted then
        return
    end
    local unit = self:find_controllable_unit()
    if not unit then
        return
    end
    self:emit_state(
        self:unit_identifier(unit),
        tostring(unit:type()),
        self:unit_position(unit),
        unit:is_controllable()
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

function PROBE:start()
    if self:is_multiplayer_or_unknown() then
        self:log("multijoueur ou type de partie inconnu : la sonde reste desactivee")
        return
    end

    self:log("sonde active — protocole " .. self.protocol_version)
    self:log("repertoire d'echange attendu : " .. self.dir)

    bm:repeat_callback(function() self:publish_state() end,
        self.state_interval_ms, "totalwar_ai_state")
    bm:repeat_callback(function() self:process_command_file() end,
        self.poll_interval_ms, "totalwar_ai_poll")

    -- Filet de securite : quoi qu'il arrive, rien ne reste pris a la fin.
    bm:register_phase_change_callback("Complete", function()
        self:abort("fin de bataille")
    end)
end

-- `bm` est le battle_manager cree par le jeu ; sans lui, rien a faire ici.
if bm then
    bm:callback(function() PROBE:start() end, 1000, "totalwar_ai_start")
else
    out("[totalwar_ai] battle_manager absent : sonde non demarree")
end

return PROBE
