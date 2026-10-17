from fastapi import APIRouter

from preflight.server.api.v1 import auth, failures, links, projects, reports, reviews, rules

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(projects.router)
router.include_router(failures.router)
router.include_router(rules.router)
router.include_router(reviews.router)
router.include_router(links.router)
router.include_router(reports.router)
