from typing import Generic, TypeVar
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.engine import ScalarResult
from sqlalchemy import select, delete, func

V = TypeVar("V")

async def add_model(session: AsyncSession, obj: V) -> V:
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


async def merge_model(session: AsyncSession, obj: V) -> V:
    obj = await session.merge(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


async def delete_model(session: AsyncSession, ident, model):
    target = await session.get(model, ident)
    await session.delete(target)
    await session.flush()
    await session.commit()  # Ensure deletion were operated


async def delete_models(session: AsyncSession, obj: Generic[V], condition):
    sentence = delete(obj).where(condition)
    await session.execute(sentence)


async def get_model(session: AsyncSession, ident, model: Generic[V]):
    return await session.get(model, ident)


def _build_select_sentence(obj: Generic[V], condition=None, offset=-1, limit=-1, order_by=None):
    return _enlarge_sentence(select(obj), condition, offset, limit, order_by)


def _enlarge_sentence(base, condition=None, offset=-1, limit=-1, order_by=None):
    if condition is not None:
        base = base.where(condition)
    if order_by is not None:
        base = base.order_by(order_by)
    if offset != -1:
        base = base.offset(offset)
    if limit != -1:
        base = base.limit(limit)
    return base


async def select_model(session: AsyncSession, obj: Generic[V], condition=None, offset=-1, limit=-1, order_by=None) -> V:
    sentence = _build_select_sentence(obj, condition, offset, limit, order_by)
    model = await session.scalar(sentence)
    return model


async def query_model(session: AsyncSession, sentence, condition=None, offset=-1, limit=-1, order_by=None):
    sentence = _enlarge_sentence(sentence, condition, offset, limit, order_by)
    model = await session.scalar(sentence)
    return model


async def select_models(session: AsyncSession, obj: Generic[V], condition=None, offset=-1, limit=-1, order_by=None) -> ScalarResult:
    sentence = _build_select_sentence(obj, condition, offset, limit, order_by)
    model = await session.scalars(sentence)
    return model


async def query_models(session: AsyncSession, sentence, condition=None, offset=-1, limit=-1, order_by=None):
    sentence = _enlarge_sentence(sentence, condition, offset, limit, order_by)
    model = await session.scalars(sentence)
    return model


async def select_models_count(session: AsyncSession, obj: Generic[V], condition=None, offset=-1, limit=-1, order_by=None) -> int:
    sentence = _build_select_sentence(obj, condition, offset, limit, order_by)
    sentence = sentence.with_only_columns(func.count(obj.id)).order_by(None)
    model = await session.scalar(sentence)
    return model


async def partial_update(session: AsyncSession, item: Generic[V], updates) -> V:
    update_data = updates.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(item, key, value)
    await session.commit()
    await session.refresh(item)
    return item