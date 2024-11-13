from __future__ import annotations

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

streamer_commands = CommandSet("streamer", "Streamer commands.")
command_sets.append(streamer_commands)


@streamer_commands.add(Privileges.VERIFIED, aliases=["", "h"])
async def streamer_help(ctx: Context) -> str | None:
    """Show this message."""
    return help_pure(
        ctx,
        [*streamer_commands.commands, *streamer_commands.subcommands],
        app.settings.COMMAND_PREFIX + "streamer",
    )


@streamer_commands.add(Privileges.VERIFIED, aliases=["m"])
async def streamer_mode(ctx: Context) -> str | None:
    """Check status, enable or disable streamer mode."""
    kw = ctx.args[0] if len(ctx.args) >= 1 else None
    match (kw):
        case "status" | "stat" | None:
            return f"""Streamer mode is {'on.' if ctx.player.id in app.state.sessions.streaming_players and app.state.sessions.streaming_players[ctx.player.id] else 'off.'}
Use !streamer mode [on|off] to toggle."""
        case "on" | "off":
            stat = kw == "on"
            app.state.sessions.streaming_players[ctx.player.id] = stat
            return f"{'Activated' if stat else 'Disabled'} streamer mode."
        case _:
            return "Invalid syntax: !streamer mode [status|on|off]"


streamer_list_commands = streamer_commands.subcommand(
    CommandSet(
        "list",
        "manage streamer list.",
    )
)


@streamer_list_commands.add(Privileges.DEVELOPER, aliases=["", "h"])
@streamer_list_commands.add(Privileges.MODERATOR, aliases=["", "h"])
@streamer_list_commands.add(Privileges.ADMINISTRATOR, aliases=["", "h"])
async def streamer_list_help(ctx: Context) -> str | None:
    """Show this message."""
    return help_pure(
        ctx,
        [*streamer_list_commands.commands, *streamer_list_commands.subcommands],
        app.settings.COMMAND_PREFIX + "streamer list",
    )


@streamer_list_commands.add(Privileges.DEVELOPER)
@streamer_list_commands.add(Privileges.MODERATOR)
@streamer_list_commands.add(Privileges.ADMINISTRATOR)
async def streamer_list_show(ctx: Context) -> str | None:
    """Show all online streamers."""
    players = (
        app.state.sessions.players.get(id=player)
        for player in app.state.sessions.streaming_players
        if app.state.sessions.streaming_players[player]
    )
    players = [p for p in players if p]

    if len(players) == 0:
        return "No streamers online."

    max_id_digits = max(len(str(p.id)) for p in players)
    return "\n".join([f"{str(p.id).rjust(max_id_digits)}: {p.name}" for p in players])


@streamer_list_commands.add(Privileges.DEVELOPER)
@streamer_list_commands.add(Privileges.MODERATOR)
@streamer_list_commands.add(Privileges.ADMINISTRATOR)
async def streamer_list_has(ctx: Context) -> str | None:
    """Check if a player is a streamer."""
    if len(ctx.args) < 1:
        return "Invalid syntax: !streamer list has <player name or id>"

    player = await app.state.sessions.players.from_cache_or_sql(name=" ".join(ctx.args))

    if not player:
        return "User not found."

    return f"Streamer mode for <{player.name}> is {'on.' if player.id in app.state.sessions.streaming_players and app.state.sessions.streaming_players[player.id] else 'off.'}"


@streamer_list_commands.add(Privileges.DEVELOPER, aliases=["rm"])
@streamer_list_commands.add(Privileges.MODERATOR, aliases=["rm"])
@streamer_list_commands.add(Privileges.ADMINISTRATOR, aliases=["rm"])
async def streamer_list_remove(ctx: Context) -> str | None:
    """Remove a player from the streamer list."""
    if len(ctx.args) < 1:
        return "Invalid syntax: !streamer list remove <player name or id>"

    player = await app.state.sessions.players.from_cache_or_sql(name=" ".join(ctx.args))

    if not player:
        return "User not found."

    if player.id not in app.state.sessions.streaming_players:
        return "User is not a streamer."

    player_id = player.id
    app.state.sessions.streaming_players.pop(player_id, None)
    return f"Removed <{player.name}> from streamer list."


@streamer_list_commands.add(Privileges.DEVELOPER, aliases=["a"])
@streamer_list_commands.add(Privileges.MODERATOR, aliases=["a"])
@streamer_list_commands.add(Privileges.ADMINISTRATOR, aliases=["a"])
async def streamer_list_add(ctx: Context) -> str | None:
    """Add a player to the streamer list."""
    if len(ctx.args) < 1:
        return "Invalid syntax: !streamer list add <player name or id>"

    player = await app.state.sessions.players.from_cache_or_sql(name=" ".join(ctx.args))

    if not player:
        return "User not found."

    if player.id in app.state.sessions.streaming_players:
        return "User is already a streamer."

    app.state.sessions.streaming_players[player.id] = True
    return f"Added <{player.name}> to streamer list."
