"""Connector interface. The profiler only calls query() so warehouses can plug in later."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = True


def quote_ident(name: str) -> str:
    """Quote an SQL identifier (double quotes, embedded quotes doubled)."""
    return '"' + name.replace('"', '""') + '"'


class Connector(ABC):
    @abstractmethod
    def list_tables(self) -> list[str]: ...

    @abstractmethod
    def get_schema(self, table: str) -> list[Column]: ...

    @abstractmethod
    def sample(self, table: str, n: int) -> pd.DataFrame: ...

    @abstractmethod
    def query(self, sql: str) -> pd.DataFrame: ...
