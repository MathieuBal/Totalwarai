"""Tampon de rejeu borne.

Il n'entraine rien aujourd'hui : il rend les experiences passees accessibles en
memoire vive, avec un echantillonnage reproductible. C'est la brique attendue
par l'entraineur hors ligne de la Phase 6.
"""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from totalwar_ai.memory.models import Transition
from totalwar_ai.memory.repository import MemoryRepository


@dataclass
class ReplayBuffer:
    """File de transitions a capacite fixe (les plus anciennes sont oubliees)."""

    capacity: int = 250_000
    _items: deque[Transition] = field(default_factory=deque, repr=False)

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("La capacite du tampon doit etre strictement positive")
        self._items = deque(self._items, maxlen=self.capacity)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Transition]:
        return iter(self._items)

    @property
    def is_full(self) -> bool:
        return len(self._items) >= self.capacity

    def add(self, transition: Transition) -> None:
        self._items.append(transition)

    def extend(self, transitions: Iterable[Transition]) -> None:
        for transition in transitions:
            self.add(transition)

    def clear(self) -> None:
        self._items.clear()

    def sample(self, count: int, rng: random.Random | None = None) -> list[Transition]:
        """Echantillon sans remise. `rng` fourni = tirage reproductible."""
        if count <= 0:
            return []
        generator = rng or random.Random()
        population = list(self._items)
        if count >= len(population):
            return population
        return generator.sample(population, count)

    def latest(self, count: int) -> list[Transition]:
        if count <= 0:
            return []
        return list(self._items)[-count:]

    def fill_from(self, repository: MemoryRepository, limit: int | None = None) -> int:
        """Recharge le tampon depuis la memoire persistante.

        Appele au demarrage : c'est ce qui fait qu'une nouvelle session
        « connait » les batailles precedentes.
        """
        bound = limit if limit is not None else self.capacity
        loaded = 0
        for transition in repository.iter_transitions(limit=bound):
            self.add(transition)
            loaded += 1
        return loaded
