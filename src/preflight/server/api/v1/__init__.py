from fastapi import APIRouter

from preflight.server.api.v1 import auth, projects

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(projects.router)
