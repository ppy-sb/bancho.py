from __future__ import annotations

import textwrap
from typing import Any
from typing import Optional

import app.state.services

# +--------------+------------------------+------+-----+---------+-------+
# | Field        | Type                   | Null | Key | Default | Extra |
# +--------------+------------------------+------+-----+---------+-------+
# | id           | int                    | NO   | PRI | NULL    |       |
# | userid       | int                    | NO   |     | NULL    |       |
# | ip           | varchar(45)            | NO   |     | NULL    |       |
# | osu_ver      | date                   | NO   |     | NULL    |       |
# | osu_stream   | varchar(11)            | NO   |     | NULL    |       |
# | datetime     | datetime               | NO   |     | NULL    |       |
# +--------------+------------------------+------+-----+---------+-------+

READ_PARAMS = textwrap.dedent(
    """\
        id, userid, ip, osu_ver, osu_stream, datetime
    """,
)


async def create(userid: int, ip: str, osu_ver: str, osu_stream: str) -> dict[str, Any]:
    """Create a new login entry in the database."""
    query = f"""\
        INSERT INTO ingame_logins (userid, ip, osu_ver, osu_stream, datetime)
             VALUES (:userid, :ip, :osu_ver, :osu_stream, NOW())
    """
    params = {
        "userid": userid,
        "ip": ip,
        "osu_ver": osu_ver,
        "osu_stream": osu_stream,
    }
    rec_id = await app.state.services.database.execute(query, params)

    query = f"""\
        SELECT {READ_PARAMS}
          FROM ingame_logins
         WHERE id = :id
    """
    params = {
        "id": rec_id,
    }
    rec = await app.state.services.database.fetch_one(query, params)
    assert rec is not None
    return dict(rec)


async def fetch_one(
    id: Optional[int] = None,
    userid: Optional[int] = None,
    ip: Optional[str] = None,
    osu_ver: Optional[str] = None,
    osu_stream: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Fetch a login entry from the database."""
    if (
        id is None
        and userid is None
        and ip is None
        and osu_ver is None
        and osu_stream is None
    ):
        raise ValueError("Must provide at least one parameter.")

    query = f"""\
        SELECT {READ_PARAMS}
          FROM ingame_logins
         WHERE id = COALESCE(:id, id)
           AND userid = COALESCE(:userid, userid)
           AND ip = COALESCE(:ip, ip)
           AND osu_ver = COALESCE(:osu_ver, osu_ver)
           AND osu_stream = COALESCE(:osu_stream, osu_stream)
    """
    params = {
        "id": id,
        "userid": userid,
        "ip": ip,
        "osu_ver": osu_ver,
        "osu_stream": osu_stream,
    }
    rec = await app.state.services.database.fetch_one(query, params)
    return dict(rec) if rec is not None else None


async def fetch_count(
    userid: Optional[int] = None,
    ip: Optional[str] = None,
    osu_ver: Optional[str] = None,
    osu_stream: Optional[str] = None,
) -> int:
    """Fetch the number of logins in the database."""
    query = """\
        SELECT COUNT(*) AS count
          FROM ingame_logins
        WHERE userid = COALESCE(:userid, userid)
          AND ip = COALESCE(:ip, ip)
          AND osu_ver = COALESCE(:osu_ver, osu_ver)
          AND osu_stream = COALESCE(:osu_stream, osu_stream)

    """
    params = {
        "userid": userid,
        "ip": ip,
        "osu_ver": osu_ver,
        "osu_stream": osu_stream,
    }
    rec = await app.state.services.database.fetch_one(query, params)
    assert rec is not None
    return rec["count"]


async def fetch_many(
    userid: Optional[int] = None,
    ip: Optional[str] = None,
    osu_ver: Optional[str] = None,
    osu_stream: Optional[str] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Fetch a list of logins from the database."""
    query = f"""\
        SELECT {READ_PARAMS}
          FROM ingame_logins
         WHERE userid = COALESCE(:userid, userid)
           AND ip = COALESCE(:ip, ip)
           AND osu_ver = COALESCE(:osu_ver, osu_ver)
           AND osu_stream = COALESCE(:osu_stream, osu_stream)
    """
    params = {
        "userid": userid,
        "ip": ip,
        "osu_ver": osu_ver,
        "osu_stream": osu_stream,
    }

    if page is not None and page_size is not None:
        query += """\
            LIMIT :limit
           OFFSET :offset
        """
        params["limit"] = page_size
        params["offset"] = (page - 1) * page_size

    recs = await app.state.services.database.fetch_all(query, params)
    return [dict(rec) for rec in recs]


async def update(
    id: int,
    userid: Optional[int] = None,
    ip: Optional[str] = None,
    osu_ver: Optional[str] = None,
    osu_stream: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Update a login entry in the database."""
    query = """\
        UPDATE ingame_logins
           SET userid = COALESCE(:userid, userid),
               ip = COALESCE(:ip, ip),
               osu_ver = COALESCE(:osu_ver, osu_ver),
               osu_stream = COALESCE(:osu_stream, osu_stream)
         WHERE id = :id
    """
    params = {
        "id": id,
        "userid": userid,
        "ip": ip,
        "osu_ver": osu_ver,
        "osu_stream": osu_stream,
    }
    await app.state.services.database.execute(query, params)

    query = f"""\
        SELECT {READ_PARAMS}
          FROM ingame_logins
        WHERE id = :id
    """
    params = {
        "id": id,
    }
    rec = await app.state.services.database.fetch_one(query, params)
    return dict(rec) if rec is not None else None


async def delete(id: int) -> Optional[dict[str, Any]]:
    """Delete a login entry from the database."""
    query = f"""\
        SELECT {READ_PARAMS}
          FROM ingame_logins
        WHERE id = :id
    """
    params = {
        "id": id,
    }
    rec = await app.state.services.database.fetch_one(query, params)
    if rec is None:
        return None

    query = """\
        DELETE FROM ingame_logins
              WHERE id = :id
    """
    params = {
        "id": id,
    }
    await app.state.services.database.execute(query, params)
    return dict(rec)
