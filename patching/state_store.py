from typing import TypeVar
from collections import defaultdict

TState = TypeVar("TState")


class FunctionStateStore:
    def __init__(self):
        self.store = []

    def get_state(self, sequence_number: int) -> TState:
        assert self.contains(sequence_number)
        return self.store[sequence_number]

    def set_state(self, sequence_number: int, state: TState):
        assert sequence_number == len(self.store)
        self.store.append(state)

    def clear_later_states(self, sequence_number: int):
        assert 0 <= sequence_number <= len(self.store)
        self.store = self.store[:sequence_number]

    def contains(self, sequence_number: int):
        return 0 <= sequence_number < len(self.store)


class StateStore:
    store = defaultdict(FunctionStateStore)

    @classmethod
    def get(cls, func) -> FunctionStateStore:
        return cls.store[func.__qualname__]

    @classmethod
    def serialize(cls):
        return cls.store

    @classmethod
    def deserialize(cls, data):
        cls.store = data
