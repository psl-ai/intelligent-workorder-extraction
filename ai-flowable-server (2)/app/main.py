import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.exceptions import LLMServiceError
from app.routers.extract import router
from app.services.llm_client import warm_up_model

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        warm_up_model()
    except Exception:
        logger.warning("大模型预热失败，服务继续启动", exc_info=True)
    else:
        logger.info("大模型预热完成")
    yield


app = FastAPI(title="来电工单字段抽取 API", version="3.0.0", lifespan=lifespan)
app.include_router(router)


@app.exception_handler(LLMServiceError)
async def llm_service_error_handler(request: Request, exc: LLMServiceError):
    return JSONResponse(
        status_code=502,
        content={"code": 502, "message": str(exc), "data": None},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"code": 422, "message": f"请求参数错误: {exc.errors()}", "data": None},
    )


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": str(exc.detail), "data": None},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.exception("服务内部异常")
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "服务内部异常，请稍后重试", "data": None},
    )
