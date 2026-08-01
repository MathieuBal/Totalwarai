"""TotalWarAI — agent tactique experimental pour les batailles solo.

Le paquet est volontairement independant du jeu : rien ici n'importe ni ne
suppose la presence de *Total War: WARHAMMER III*. L'integration reelle passera
plus tard par un adaptateur (`totalwar_ai.bridge`) et un mod Lua.
"""

from totalwar_ai.bridge.protocol import PROTOCOL_VERSION

__all__ = ["PROTOCOL_VERSION", "__version__"]

__version__ = "0.1.0"
