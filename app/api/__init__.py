# type: ignore
# isort: dont-add-imports

from fastapi import APIRouter

from .v1 import apiv1_router
from .v2 import apiv2_router
from ..sb.sb_api import router as sb_router

api_router = APIRouter()

api_router.include_router(apiv1_router)
api_router.include_router(apiv2_router)
api_router.include_router(sb_router)

from . import domains
from . import init_api
from . import middlewares
