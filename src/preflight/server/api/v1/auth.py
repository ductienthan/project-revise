from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", status_code=501)
def login() -> dict:
    return {"detail": "not implemented"}


@router.post("/device/code", status_code=501)
def device_code() -> dict:
    return {"detail": "not implemented"}


@router.post("/device/token", status_code=501)
def device_token() -> dict:
    return {"detail": "not implemented"}
