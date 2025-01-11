from __future__ import annotations

from app.constants.gamemodes import GameMode
from app.objects.player import Player
import app.logging
import app.packets
import app.settings
import app.state
import app.usecases.performance
import app.utils
from app.commands import CommandSet
from app.commands import Context
from app.commands import command_sets
from app.commands import help_pure
from app.constants.privileges import Privileges

sb_commands = CommandSet("sb", "sb featured commands.")
command_sets.append(sb_commands)

NORMAL_PRIV = Privileges.UNRESTRICTED | Privileges.VERIFIED


@sb_commands.add(NORMAL_PRIV, aliases=["", "h"])
async def sb_help(ctx: Context) -> str:
    """Show this message."""
    return help_pure(
        ctx,
        [*sb_commands.commands, *sb_commands.subcommands],
        app.settings.COMMAND_PREFIX + "sb",
    )


sb_mp_commands = sb_commands.subcommand(CommandSet("mp", "sb mp commands."))


@sb_mp_commands.add(NORMAL_PRIV, aliases=["", "h"])
async def sb_mp_help(ctx: Context) -> str:
    """Show this message."""
    return help_pure(
        ctx,
        [*sb_mp_commands.commands, *sb_mp_commands.subcommands],
        app.settings.COMMAND_PREFIX + "sb mp",
    )


@sb_mp_commands.add(NORMAL_PRIV)
async def sb_mp_fix(ctx: Context) -> str:
    """auto remove stalled matches."""

    res: list[str] = [
        f"Checking {len([m for m in app.state.sessions.matches if m is not None])} matches..."
    ]
    for match in app.state.sessions.matches:
        if match is None:
            continue

        host = app.state.sessions.players.get(id=match.host_id)
        if host is None:
            res.append(
                f"Found invalid state: Match <{match.id}> {match.name} has no host."
            )
            next_player = None
            for slot in match.slots:
                if slot.player is None:
                    continue
                if app.state.sessions.players.get(id=slot.player.id) is None:
                    continue

                next_player = slot.player
                break

            if next_player is None:
                res.append(f"Could not find next player, terminating match.")
                app.state.sessions.matches.remove(match)
                match.slots.clear()
            else:
                res.append(f"Setting {next_player} as host.")
                match.host_id = next_player.id

        for slot in match.slots:
            if slot.player is None:
                continue

            if app.state.sessions.players.get(id=slot.player.id) is None:
                res.append(
                    f"Found invalid state: Match <{match.id}> {match.name} has ghost player {slot.player.name}."
                )
                slot.player = None

    res.append("Done.")

    return "\n".join(res)


sb_mp_room_commands = sb_mp_commands.subcommand(
    CommandSet("room", "sb mp room commands.")
)


@sb_mp_room_commands.add(NORMAL_PRIV, aliases=["", "h"])
async def sb_mp_room_help(ctx: Context) -> str:
    """Show this message."""
    return help_pure(
        ctx,
        [*sb_mp_room_commands.commands, *sb_mp_room_commands.subcommands],
        app.settings.COMMAND_PREFIX + "sb mp room",
    )


@sb_mp_room_commands.add(Privileges.DEVELOPER)
@sb_mp_room_commands.add(Privileges.ADMINISTRATOR)
@sb_mp_room_commands.add(Privileges.MODERATOR)
async def sb_mp_room_remove(ctx: Context) -> str:
    if len(ctx.args) < 1:
        return "Invalid syntax: !streamer manage remove <room id>"

    try:
        room_id = int(ctx.args[0])

        room = app.state.sessions.matches[room_id]

        if room is None:
            return "Room not found."

        app.state.sessions.matches.remove(room)

        lobby = app.state.sessions.channels.get_by_name("#lobby")
        if lobby:
            lobby.enqueue(app.packets.dispose_match(room_id))

        for slot in room.slots:
            slot.reset()

        return "Room removed."

    except ValueError:
        return "Unable to parse room id: !streamer manage remove <room id>"


sb_user_commands = sb_commands.subcommand(CommandSet("user", "sb user commands."))


@sb_user_commands.add(NORMAL_PRIV, aliases=[""])
async def sb_user_help(ctx: Context) -> str:
    """Show this message."""
    return help_pure(
        ctx,
        [*sb_user_commands.commands, *sb_user_commands.subcommands],
        app.settings.COMMAND_PREFIX + "sb user",
    )


@sb_user_commands.add(Privileges.DEVELOPER)
@sb_user_commands.add(Privileges.ADMINISTRATOR)
@sb_user_commands.add(Privileges.MODERATOR)
async def sb_user_sync(ctx: Context) -> str:
    """sync user data from database."""

    if len(ctx.args) < 1:
        return "Invalid syntax: !streamer manage remove <player name or id>"

    sql_player = await app.state.sessions.players.get_sql(name=" ".join(ctx.args))

    if not sql_player:
        return "User not found."

    maybe_cached_player = app.state.sessions.players.get(id=sql_player.id)

    if maybe_cached_player is not None:
        await maybe_cached_player.relationships_from_sql()
        await maybe_cached_player.stats_from_sql_full()
        maybe_cached_player.priv = sql_player.priv
        (await maybe_cached_player.update_rank(mode) for mode in GameMode)

        return f"synced {maybe_cached_player.name}."
    else:
        (await sql_player.update_rank(mode) for mode in GameMode)

        return f"synced {sql_player.name}."


sb_me_commands = sb_commands.subcommand(CommandSet("me", "sb me commands."))


@sb_me_commands.add(NORMAL_PRIV, aliases=["", "h"])
async def sb_me_help(ctx: Context) -> str:
    """Show this message."""
    return help_pure(
        ctx,
        [*sb_me_commands.commands, *sb_me_commands.subcommands],
        app.settings.COMMAND_PREFIX + "sb me",
    )


@sb_me_commands.add(NORMAL_PRIV)
async def sb_me_sync(ctx: Context) -> str:
    """sync your info with the database."""

    sql_player = await app.state.sessions.players.get_sql(id=ctx.player.id)

    if not sql_player:
        return "User not found."

    await ctx.player.relationships_from_sql()
    await ctx.player.stats_from_sql_full()
    ctx.player.priv = sql_player.priv
    (await ctx.player.update_rank(mode) for mode in GameMode)

    return f"synced {ctx.player.name}."
