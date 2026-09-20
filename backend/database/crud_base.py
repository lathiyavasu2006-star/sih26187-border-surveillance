from typing import Any, Dict, Generic, List, Optional, Sequence, Type, TypeVar, Union

from pydantic import BaseModel
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.database import Base

ModelType = TypeVar("ModelType", bound=Base)
InputData = Union[Dict[str, Any], BaseModel]


def _to_dict(obj_in: InputData, exclude_unset: bool = False) -> Dict[str, Any]:
    if isinstance(obj_in, BaseModel):
        return obj_in.model_dump(exclude_unset=exclude_unset)
    return dict(obj_in)


class CRUDBase(Generic[ModelType]):
    """Generic async CRUD. Works with any single-column primary key name (camera_id, user_id, ...)."""

    def __init__(self, model: Type[ModelType]):
        self.model = model
        primary_keys = inspect(model).primary_key
        if len(primary_keys) != 1:
            raise TypeError(f"{model.__name__} must have exactly one primary key column")
        self.pk_column = primary_keys[0]
        self.pk_name = self.pk_column.key
        self._column_keys = {attr.key for attr in inspect(model).column_attrs}

    def _apply_filters(self, query, filters: Optional[Dict[str, Any]]):
        if not filters:
            return query
        for field, value in filters.items():
            if value is None:
                continue
            if field not in self._column_keys:
                raise ValueError(f"Unknown filter field '{field}' for {self.model.__name__}")
            column = getattr(self.model, field)
            if isinstance(value, (list, tuple, set)):
                query = query.where(column.in_(list(value)))
            else:
                query = query.where(column == value)
        return query

    async def get(self, db: AsyncSession, id: Any) -> Optional[ModelType]:
        result = await db.execute(select(self.model).where(self.pk_column == id))
        return result.scalar_one_or_none()

    async def get_by(self, db: AsyncSession, **filters: Any) -> Optional[ModelType]:
        query = self._apply_filters(select(self.model), filters)
        result = await db.execute(query.limit(2))
        rows = result.scalars().all()
        if len(rows) > 1:
            raise ValueError(f"get_by returned multiple {self.model.__name__} rows for {filters}")
        return rows[0] if rows else None

    async def get_multi(
        self,
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[Sequence[Any]] = None,
    ) -> List[ModelType]:
        skip = max(0, int(skip))
        limit = max(1, min(int(limit), 1000))
        query = self._apply_filters(select(self.model), filters)
        query = query.order_by(*order_by) if order_by else query.order_by(self.pk_column)
        result = await db.execute(query.offset(skip).limit(limit))
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, obj_in: InputData) -> ModelType:
        data = {k: v for k, v in _to_dict(obj_in).items() if k in self._column_keys}
        db_obj = self.model(**data)
        db.add(db_obj)
        await db.flush()
        await db.refresh(db_obj)
        return db_obj

    async def update(
        self,
        db: AsyncSession,
        db_obj: ModelType,
        obj_in: InputData,
        exclude_none: bool = True,
    ) -> ModelType:
        data = _to_dict(obj_in, exclude_unset=True)
        for field, value in data.items():
            if field == self.pk_name or field not in self._column_keys:
                continue
            if value is None and exclude_none:
                continue
            setattr(db_obj, field, value)
        db.add(db_obj)
        await db.flush()
        await db.refresh(db_obj)
        return db_obj

    async def delete(self, db: AsyncSession, id: Any) -> Optional[ModelType]:
        obj = await self.get(db, id)
        if obj:
            await db.delete(obj)
            await db.flush()
        return obj

    async def count(self, db: AsyncSession, filters: Optional[Dict[str, Any]] = None) -> int:
        query = self._apply_filters(select(func.count()).select_from(self.model), filters)
        result = await db.execute(query)
        return int(result.scalar() or 0)

    async def exists(self, db: AsyncSession, id: Any) -> bool:
        result = await db.execute(select(self.pk_column).where(self.pk_column == id).limit(1))
        return result.scalar_one_or_none() is not None
