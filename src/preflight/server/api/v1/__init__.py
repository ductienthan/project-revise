from fastapi import APIRouter

from preflight.server.api.v1 import auth, failures, projects, reviews, rules

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(projects.router)
router.include_router(failures.router)
router.include_router(rules.router)
router.include_router(reviews.router)
