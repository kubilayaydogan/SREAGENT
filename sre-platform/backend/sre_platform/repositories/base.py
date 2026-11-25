"""Ortak repository yardımcıları: sayfalama, beyaz listeli sıralama, tam metin araması, hata tipleri."""
from __future__ import annotations

from airlab_agent.errors import UserError

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from sqlalchemy import ColumnElement, Select, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")
MAX_PAGE_SIZE = 200


class NotFound(LookupError):
    pass


class Conflict(RuntimeError):
    pass


class InvalidTransition(ValueError):
    pass


@dataclass
class Page(Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int

    @property
    def pages(self) -> int:
        return (self.total + self.size - 1) // self.size if self.size else 0


def order_by(stmt: Select, sort: str | None, allowed: dict[str, Any], default: str) -> Select:
    """`sort` = "alan" veya "-alan" (azalan). Yalnız `allowed` içindeki alanlar; aksi halde ValueError."""
    key = (sort or default).strip()
    desc = key.startswith("-")
    name = key.lstrip("-+")
    if name not in allowed:
        raise UserError(f"geçersiz sıralama alanı: {name} (izinli: {', '.join(sorted(allowed))})")
    col = allowed[name]
    return stmt.order_by(col.desc().nulls_last() if desc else col.asc().nulls_last())


async def paginate(s: AsyncSession, stmt: Select, page: int, size: int, *, scalars: bool = True) -> Page:
    page, size = max(1, page), max(1, min(size, MAX_PAGE_SIZE))
    total = (await s.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    res = await s.execute(stmt.limit(size).offset((page - 1) * size))
    return Page(list(res.scalars().all() if scalars else res.all()), total, page, size)


def ts_query(q: str) -> ColumnElement:
    """Kullanıcı metnini güvenli tsquery'ye çevirir (websearch sözdizimi; SQL enjeksiyonu yok — bağlı parametre)."""
    return func.websearch_to_tsquery(literal_column("'turkish'::regconfig"), q).op("||")(
        func.websearch_to_tsquery(literal_column("'simple'::regconfig"), q))


def ts_match(tsv_col, q: str) -> ColumnElement:
    return tsv_col.op("@@")(ts_query(q))


def ts_rank(tsv_col, q: str) -> ColumnElement:
    return func.ts_rank_cd(tsv_col, ts_query(q))
