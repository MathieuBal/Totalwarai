"""Helpers de validation partages par tous les `from_dict` du domaine.

Regle du projet : aucune validation ad hoc ailleurs. Un message venant du pont
Lua est une donnee externe, potentiellement incomplete ou mal typee ; ces
helpers produisent systematiquement une :class:`SchemaError` explicite plutot
qu'un `KeyError` ou un `TypeError` opaque.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, TypeVar

_MISSING = object()

EnumT = TypeVar("EnumT", bound=Enum)


class SchemaError(ValueError):
    """Donnee entrante invalide au regard du schema attendu."""


def require_mapping(data: Any, context: str) -> Mapping[str, Any]:
    """Verifie que `data` est bien un mapping avant toute lecture de champ."""
    if not isinstance(data, Mapping):
        raise SchemaError(f"{context} : mapping attendu, recu {type(data).__name__}")
    return data


def require(data: Mapping[str, Any], key: str) -> Any:
    """Lit une cle obligatoire."""
    if key not in data:
        raise SchemaError(f"Champ obligatoire manquant : '{key}'")
    return data[key]


def as_str(data: Mapping[str, Any], key: str, *, default: Any = _MISSING) -> str:
    value = data.get(key, default)
    if value is _MISSING:
        raise SchemaError(f"Champ obligatoire manquant : '{key}'")
    if not isinstance(value, str):
        raise SchemaError(f"Le champ '{key}' doit etre une chaine, recu {type(value).__name__}")
    return value


def as_bool(data: Mapping[str, Any], key: str, *, default: Any = _MISSING) -> bool:
    value = data.get(key, default)
    if value is _MISSING:
        raise SchemaError(f"Champ obligatoire manquant : '{key}'")
    if not isinstance(value, bool):
        raise SchemaError(f"Le champ '{key}' doit etre un booleen, recu {type(value).__name__}")
    return value


def as_int(data: Mapping[str, Any], key: str, *, default: Any = _MISSING) -> int:
    value = data.get(key, default)
    if value is _MISSING:
        raise SchemaError(f"Champ obligatoire manquant : '{key}'")
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"Le champ '{key}' doit etre un entier, recu {type(value).__name__}")
    return value


def as_float(data: Mapping[str, Any], key: str, *, default: Any = _MISSING) -> float:
    value = data.get(key, default)
    if value is _MISSING:
        raise SchemaError(f"Champ obligatoire manquant : '{key}'")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SchemaError(f"Le champ '{key}' doit etre un nombre, recu {type(value).__name__}")
    return float(value)


def as_ratio(data: Mapping[str, Any], key: str, *, default: Any = _MISSING) -> float:
    """Nombre borne a l'intervalle [0, 1] — sante, fatigue, munitions, confiance."""
    value = as_float(data, key, default=default)
    if not 0.0 <= value <= 1.0:
        raise SchemaError(f"Le champ '{key}' doit etre compris entre 0 et 1, recu {value}")
    return value


def as_optional_str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SchemaError(f"Le champ '{key}' doit etre une chaine ou null")
    return value


def as_str_list(data: Mapping[str, Any], key: str, *, default: Any = _MISSING) -> list[str]:
    value = data.get(key, default)
    if value is _MISSING:
        raise SchemaError(f"Champ obligatoire manquant : '{key}'")
    if value is None:
        return []
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise SchemaError(f"Le champ '{key}' doit etre une liste de chaines")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise SchemaError(f"Le champ '{key}' ne doit contenir que des chaines")
        result.append(item)
    return result


def as_enum(
    data: Mapping[str, Any],
    key: str,
    enum_type: type[EnumT],
    *,
    default: EnumT | None = None,
) -> EnumT:
    """Convertit une valeur en membre d'enumeration.

    `default` sert de repli tolerant : une valeur inconnue envoyee par une
    version plus recente du mod ne doit pas faire tomber tout le message.
    """
    raw = data.get(key)
    if raw is None:
        if default is not None:
            return default
        raise SchemaError(f"Champ obligatoire manquant : '{key}'")
    if isinstance(raw, enum_type):
        return raw
    if not isinstance(raw, str):
        raise SchemaError(f"Le champ '{key}' doit etre une chaine")
    try:
        return enum_type(raw)
    except ValueError:
        if default is not None:
            return default
        allowed = ", ".join(sorted(member.value for member in enum_type))  # type: ignore[misc]
        raise SchemaError(f"Valeur '{raw}' invalide pour '{key}' (attendu : {allowed})") from None


def as_mapping(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Sous-mapping libre (parametres d'action, metadonnees)."""
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SchemaError(f"Le champ '{key}' doit etre un mapping")
    return dict(value)


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    """Borne une valeur — utilise partout ou un ratio est calcule."""
    return max(minimum, min(maximum, value))
