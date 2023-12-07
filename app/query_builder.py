import traceback
import random
import string
from typing import Union, Callable, Tuple, Dict, Optional


class SQLLiteral:
    def __init__(self, value: str):
        self.value = value


# Types
DatabaseAllowedNotNull = Union[str, int, bool, float]
Value = Union[DatabaseAllowedNotNull, None]
SQLValueWithTemplate = Tuple[Value, str]
SQLValueWithNested = Tuple[Value, "SQLPart"]
BuiltSQL = SubQuery = Tuple[str, Dict[str, Value]]
SQLType = Union[
    SQLValueWithTemplate, SQLValueWithNested, BuiltSQL, SQLLiteral, "OptionalSQL"
]
SQLPart = Union[SQLType, Tuple[SQLType, ...], Callable[[], SQLType]]


class Nullable:
    def __init__(self, value: Value):
        self.value = value


class OptionalSQL:
    def __init__(self, value: Value, sql: SQLPart):
        self.value = value
        self.sql = sql


def sql(value: str) -> SQLLiteral:
    return SQLLiteral(value)


def equals_variable(name: str, param_key: str | None = None) -> SQLLiteral:
    return SQLLiteral(
        f"`{name}` = :{param_key if param_key is not None else generate_random_string(5)}"
    )


def generate_random_string(length):
    # Define the characters to use in the string
    characters = string.ascii_letters + string.digits
    # Generate a random string of the specified length
    random_string = "".join(random.choice(characters) for i in range(length))
    return random_string


def table(value: str) -> SQLLiteral:
    return SQLLiteral(f"`{value}`")


def nullable(value: DatabaseAllowedNotNull | None) -> Nullable:
    return Nullable(value)


def optional_param(value: Value, sql: SQLPart) -> OptionalSQL:
    return OptionalSQL(value, sql)


def WHERE(*parts: SQLPart) -> SQLPart:
    return (sql("WHERE"), parts)


def UPDATE(table: SQLLiteral, update_set: BuiltSQL, cond: SQLPart) -> BuiltSQL:
    _sql, param = update_set
    if _sql is None:
        return None

    built, bparam = build(cond)
    if built is None:
        return None

    param.update(bparam)
    return ("UPDATE " + table.value + " SET " + _sql + " " + built, bparam)


def SET(*parts: SQLPart) -> BuiltSQL:
    parameters = {}
    query_parts = (_process_query_part(p, parameters) for p in parts)
    filtered = [q for q in query_parts if q is not None]

    # Check if filtered is empty or contains only None
    if not filtered or all(part is None for part in filtered):
        return None, parameters  # or some other appropriate return value
    return ", ".join(filtered), parameters


def AND(*parts: SQLPart) -> BuiltSQL:
    update = {}
    subq = _process_query_part((sql("AND"), parts), update)
    return (subq, update)


def OR(*parts: SQLPart) -> BuiltSQL:
    update = {}
    subq = _process_query_part((sql("OR ("), (parts, sql(")"))), update)
    return (subq, update)


def build(*parts: SQLPart) -> BuiltSQL:
    params = {}
    query_parts = (_process_query_part(p, params) for p in parts)
    query = " ".join(q for q in query_parts if q is not None)
    return query, params


def _is_nullable(value) -> bool:
    return isinstance(value, Nullable)


def _extract_value(value: Value | Nullable) -> Value:
    if _is_nullable(value):
        return value.value
    return value


def _process_query_part(part: SQLPart, parameters: Dict[str, Value]) -> Optional[str]:
    if callable(part):
        part = part()

    match part:
        case None:
            return None

        case (str(built), dict(params)):
            parameters.update(params)
            return built

        case SQLLiteral(value=value):
            return value
        case OptionalSQL(value=value, sql=parts):
            update = {}
            sql = _process_query_part((value, parts), update)
            if sql is None:
                return None
            parameters.update(update)
            return sql
        case (str(literal), dict(params)):
            parameters.update(params)
            return literal
        case tuple(parts):
            return _process_tuple_part(parts, parameters)
        case str(_literal):
            raise TypeError(
                "Raw strings are not allowed. Use sql() function for literal SQL strings."
            )
        case _:
            traceback.print_stack()
            raise TypeError(f"Unexpected type for query part: {type(part)}, {part}")


def _process_tuple_part(part: SQLPart, parameters: Dict[str, Value]) -> Optional[str]:
    match part:
        case (None, *_):
            return None
        case (SQLLiteral(value=value),):
            return value
        case (SQLLiteral(value=value), val):
            update = {}
            evaluated = _process_query_part(val, update)
            if evaluated is None:
                return None
            parameters.update(update)
            return value + " " + evaluated
        case (
            Nullable(value=cond) | bool(cond) | str(cond) | int(cond) | float(cond),
            val,
        ):
            evaluated = _process_query_part(val, parameters)
            revealed_cond = _extract_value(cond)
            if evaluated is None:
                return None
            if revealed_cond is None and _is_nullable(cond) is False:
                return None
            if ":" in evaluated and revealed_cond is not None:
                parameter_name = evaluated.split(":")[-1].strip().split(" ")[0].strip()
                parameters[parameter_name] = revealed_cond
            return evaluated
        case tuple(_), *_:
            parts: list[str] = []
            for elem in part:
                return_value = _process_query_part(elem, parameters)
                if return_value is None:
                    return None
                parts.append(return_value)
            return " ".join(parts)
        case _:
            raise TypeError(f"Unexpected type for tuple part: {type(part)} {part}")


# def test_query(
#     mode: int | None = None,
#     page: int | None = None,
#     player_id: int | None = None,
#     page_size: int | None = None,
# ):
#     return build(
#         sql(f"SELECT 1 FROM stats WHERE 1 = 1"),
#         AND(player_id, equals_variable("id")),
#         AND(mode, equals_variable("mode")),
#         (
#             (page_size, sql("LIMIT :page_size")),
#             lambda: (
#                 (page - 1) * page_size if page is not None else None,
#                 sql("OFFSET :offset"),
#             ),
#         ),
#     )


# def test_update(
#     id: int,
#     _from: Optional[int] = None,
#     to: Optional[int] = None,
#     action: Optional[str] = None,
#     msg: Optional[str] = None,
#     time: Optional[str] = None,
# ) -> BuiltSQL:
#     """Update a log entry in the database."""

#     query, params = build(
#         UPDATE(
#             table("logs"),
#             SET(
#                 optional_param(to, equals_variable("to")),
#                 optional_param(action, equals_variable("action")),
#                 optional_param(msg, equals_variable("msg")),
#                 optional_param(time, equals_variable("time")),
#             ),
#             (
#                 sql("WHERE"),
#                 equals_variable("id", "id"),
#             ),
#         ),
#     )
#     params.update({"id": id}) if query != "" else None
#     return query, params


# print("query:empty")
# print(test_query())
# print()
# print("query:populated")
# print(test_query(player_id=1001, mode=0, page=1, page_size=10))
# print()
# print("update:empty")
# print(test_update(id=3))
# print()
# print("update:populated")
# print(test_update(id=3, to=1001, action="test", msg="message", time="datetime"))
