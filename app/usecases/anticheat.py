from circleguard import Circleguard, ReplayPath
from app import settings
from app.constants.mods import Mods
from app.objects.score import Score

class CheatError(Exception): ...

circleguard = Circleguard(settings.OSU_API_KEY)

async def suspect(score: Score, replay_path: str):
    replay = ReplayPath(replay_path)
    snaps = circleguard.snaps(replay)
    frametime = circleguard.frametime(replay)
    ur = circleguard.ur(replay)
    
    if frametime < 14:
        raise CheatError(f"timewarp cheating (frametime: {frametime:.2f}) on {score.bmap.title}")
    
    if (not score.mods & Mods.RELAX) and ur < 70:
        raise CheatError(f"potential relax (ur: {ur:.2f}) on {score.bmap.title})")
    
    if len(snaps) > 20:
        raise CheatError(f"potential assist (snaps: {len(snaps):.2f}) on {score.bmap.title})")
