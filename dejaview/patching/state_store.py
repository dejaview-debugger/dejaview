from collections import defaultdict
from typing import Any, DefaultDict


class FunctionStateStore:
    def __init__(self) -> None:
        self.store: list[Any] = []

    def get_state(self, sequence_number: int) -> Any:
        assert self.contains(sequence_number)
        return self.store[sequence_number]

    def set_state(self, sequence_number: int, state: Any) -> None:
        assert sequence_number == len(self.store)
        self.store.append(state)

    def clear_later_states(self, sequence_number: int) -> None:
        assert 0 <= sequence_number <= len(self.store)
        self.store = self.store[:sequence_number]

    def contains(self, sequence_number: int) -> bool:
        return 0 <= sequence_number < len(self.store)


class StateStore:
    store: DefaultDict[str, FunctionStateStore] = defaultdict(FunctionStateStore)

    @classmethod
    def get(cls, func) -> FunctionStateStore:
        return cls.store[func.__qualname__]

    @classmethod
    def serialize(cls) -> DefaultDict[str, FunctionStateStore]:
        return cls.store

    @classmethod
    def deserialize(cls, data: DefaultDict[str, FunctionStateStore]) -> None:
        cls.store = data
