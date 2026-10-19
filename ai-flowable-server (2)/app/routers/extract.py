from fastapi import APIRouter, HTTPException

from app.constants import FIELD_CONFIGS
from app.schemas import ApiResponse, ExtractConfig, ExtractFieldResult, IntentRequest
from app.services.extract_service import (
    extract_fields,
    format_extract_response,
    normalize_extract_result,
)

router = APIRouter(prefix="/api/v1", tags=["信息抽取"])
RequestPayload = IntentRequest | list[ExtractConfig]
KNOWN_FIELD_NAMES = {str(config["name"]) for config in FIELD_CONFIGS}


@router.post(
    "/intent/recognize",
    response_model=ApiResponse[list[ExtractFieldResult]],
    response_model_exclude_none=True,
    summary="来电工单字段抽取",
)
def intent_recognize(request: RequestPayload):
    if isinstance(request, list):
        request = IntentRequest(extract_config=request)
    conversation = [turn.model_dump() for turn in request.conversation]
    configs = [config.model_dump(exclude_none=True) for config in request.extract_config or []]
    if not conversation:
        raise HTTPException(status_code=422, detail="conversation 至少需要一条对话")
    names = [str(config.get("rec_type", "")) for config in configs]
    unknown = sorted({name for name in names if name and name not in KNOWN_FIELD_NAMES})
    if unknown:
        raise HTTPException(status_code=422, detail=f"不支持的字段: {', '.join(unknown)}")
    if len(names) != len(set(names)):
        raise HTTPException(status_code=422, detail="extract_config 不允许重复 rec_type")
    keys = [str(config.get("rec_type_en", "")) for config in configs if config.get("rec_type_en")]
    if len(keys) != len(set(keys)):
        raise HTTPException(status_code=422, detail="extract_config 不允许重复 rec_type_en")
    result = (
        extract_fields(
            conversation,
            request.previous_ticket,
            request.call_phone,
            configs,
        )
        if conversation
        else normalize_extract_result(
            {},
            request.previous_ticket,
            call_phone=request.call_phone,
            extract_config=configs,
        )
    )
    return ApiResponse.success(format_extract_response(result, configs))
