import json
import logging
import re
from typing import Any

from app.config import get_settings
from app.constants import ENUM_NUMERIC_CODES, ENUM_OPTIONS, FIELD_CONFIGS
from app.exceptions import LLMServiceError
from app.services.llm_client import get_llm_client

logger = logging.getLogger(__name__)
PHONE_REGEX = r"1[3-9]\d{9}"

EMPTY_RESULT: dict[str, Any] = {
    "value": "",
    "recognized": False,
    "confidence": 0.0,
}

EXTRACT_SYSTEM_PROMPT = """你是人力资源和社会保障客服热线工单信息抽取器。输入包含 fields 和 conversation；请根据完整 conversation 抽取 fields 中的每一项。字段特定规则以 fields 中的规范化要求为准。

输出协议：
1. 仅返回一个合法 JSON 对象，不得输出解释。每个字段恰好返回一次，并以 output_key 为键。
2. 字段对象使用短键：v（value）、r（recognized，1 或 0）、c（confidence，0 到 1）；已识别的枚举字段还必须返回代码数组 o（option_codes）。
3. 未识别字段固定返回 {"v":"","r":0,"c":0.0}，不得返回 o。
4. 枚举字段的 v 使用 candidates 中的标签，o 使用对应代码；多选 v 用英文逗号连接且顺序与 o 一致。业务类型和业务分类可多选，其他枚举字段单选。
5. 有正则的字段必须完整匹配正则。除最终 JSON 外，不得输出 Markdown、分析或纠正后的对话。

抽取规则：
1. 仅使用上下文明确信息，按字段定义独立判断；不得把其他字段的值误填到当前字段。
2. 仅在上下文明确支持时纠正语音识别错字或转换中文口述数字；不得猜改身份证、电话、门牌号、楼层等明确数字。
3. 合并相邻轮次中属于同一地址的片段；多个独立诉求对应不同事发地时全部返回，并严格区分居住地、事发地和参保地。
4. 坐席提供的候选值仅在用户明确确认后采用；同一字段以用户最后一次有效更正为准。
5. 业务类型、业务分类、问题概要、核心诉求和备注必须覆盖全部独立诉求。
6. 枚举值只能来自 candidates；除字段定义的默认枚举值外，没有明确证据时不得猜测。
7. 忠实保留用户的意图与时态，例如“准备申请”不能改为“已申请”；不得擅自把咨询、办理或计划报名改写成已完成事实。
8. 严格识别否定表达；“不投诉、不举报、不建议、不派单”和“不要回填历史信息”不得按肯定含义抽取。
"""

AI_IGNORED_FIELDS = {"dispatch_required", "ticket_status"}
PREVIOUS_TICKET_FIELDS = (
    "caller_name",
    "id_card_number",
    "contact_phone",
    "residence_address",
    "incident_location",
    "insured_location",
)


def _request_configs(
    extract_config: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    return {
        str(config.get("rec_type")): config
        for config in extract_config or []
        if config.get("rec_type")
    }


def _is_multiple(
    config: dict[str, Any],
    request_configs: dict[str, dict[str, Any]],
) -> bool:
    request_config = request_configs.get(str(config["name"]), {})
    return bool(request_config.get("multiple", config.get("multiple", False)))


def _request_regex(
    config: dict[str, Any],
    request_configs: dict[str, dict[str, Any]],
) -> str | None:
    request_config = request_configs.get(str(config["name"]), {})
    return request_config.get("rec_regx") or config.get("regex")


def _request_option_candidates(
    field: str,
    request_configs: dict[str, dict[str, Any]],
) -> dict[str, str] | None:
    """Build the enum sent to the model from the caller's option list."""
    request_options = request_configs.get(field, {}).get("option")
    if not isinstance(request_options, list) or not request_options:
        return None
    candidates: dict[str, str] = {}
    for option in request_options:
        if not isinstance(option, dict):
            continue
        label = str(option.get("dicName", "")).strip()
        if not label:
            continue
        # Preserve the internal code for known fixed labels; otherwise the
        # caller's dicValue (or label) is the model-facing code.
        code = next(
            (
                code
                for code, known_label in ENUM_OPTIONS.get(field, {}).items()
                if known_label == label
            ),
            None,
        )
        if not code:
            code = str(option.get("dicValue") or label).strip()
        if code:
            candidates[code] = label
    return candidates or None


def _model_fields(
    extract_config: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    request_configs = _request_configs(extract_config)
    requested_names = {
        str(config.get("rec_type"))
        for config in extract_config or []
        if config.get("rec_type")
    }
    requested_keys = {
        str(config.get("rec_type_en"))
        for config in extract_config or []
        if config.get("rec_type_en")
    }
    fields = []
    for config in FIELD_CONFIGS:
        if config["key"] in AI_IGNORED_FIELDS:
            continue
        name = str(config["name"])
        if extract_config and name not in requested_names and config["key"] not in requested_keys:
            continue
        field: dict[str, Any] = {
            "字段名": name,
            "output_key": config["key"],
            "规范化要求": config["guidance"],
        }
        if regex := _request_regex(config, request_configs):
            field["正则"] = regex
        if options := ENUM_OPTIONS.get(name):
            field["candidates"] = (
                _request_option_candidates(name, request_configs) or options
            )
            field["selection"] = (
                "multiple" if _is_multiple(config, request_configs) else "single"
            )
        fields.append(field)
    return fields


def _canonical_previous_ticket(
    previous_ticket: dict[str, Any] | None,
    extract_config: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not previous_ticket:
        return {}
    request_keys = {
        str(config.get("rec_type")): str(config.get("rec_type_en"))
        for config in extract_config or []
        if config.get("rec_type") and config.get("rec_type_en")
    }
    normalized: dict[str, Any] = {}
    for config in FIELD_CONFIGS:
        name = str(config["name"])
        key = str(config["key"])
        for candidate in (request_keys.get(name), key, name):
            if candidate and candidate in previous_ticket:
                normalized[key] = previous_ticket[candidate]
                break
    return normalized


def _provided_result(
    value: Any,
    *,
    regex: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value:
        return None
    if regex and re.fullmatch(regex, value) is None:
        return None
    return {"value": value, "recognized": True, "confidence": 1.0}


def _parse_model_result(raw_result: Any) -> dict[str, Any]:
    if isinstance(raw_result, dict):
        return raw_result
    if not isinstance(raw_result, str):
        return {}

    content = raw_result.strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = None
        decoder = json.JSONDecoder()
        for start, char in enumerate(content):
            if char != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(content, start)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                parsed = candidate
                break
    return parsed if isinstance(parsed, dict) else {}


def _unwrap_result(parsed: dict[str, Any]) -> dict[str, Any]:
    keys = {
        str(config[key])
        for config in FIELD_CONFIGS
        for key in ("name", "key")
    }
    if keys.intersection(parsed):
        return parsed
    for wrapper in ("data", "result", "fields"):
        candidate = parsed.get(wrapper)
        if isinstance(candidate, dict) and keys.intersection(candidate):
            return candidate
    return parsed


def _is_recognized(item: dict[str, Any]) -> bool:
    for key in ("r", "recognized", "is_rec"):
        if key not in item:
            continue
        value = item[key]
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1"}
        return value in (True, 1)
    value = item.get("v", item.get("value", item.get("rec_result")))
    return isinstance(value, str) and bool(value.strip())


def _item_value(item: dict[str, Any]) -> Any:
    if "v" in item:
        return item["v"]
    if "value" in item:
        return item["value"]
    return item.get("rec_result", "")


def _empty_result() -> dict[str, Any]:
    return dict(EMPTY_RESULT)


def _default_urgency(
    result: dict[str, dict[str, Any]],
    request_configs: dict[str, dict[str, Any]],
) -> None:
    """Default unspecified urgency to the caller's 普通 option."""
    if result.get("urgency", {}).get("recognized"):
        return
    candidates = _request_option_candidates("紧急程度", request_configs)
    if candidates is None:
        candidates = ENUM_OPTIONS["紧急程度"]
    normal_code = next(
        (code for code, label in candidates.items() if label == "普通"),
        None,
    )
    if normal_code is None:
        return
    result["urgency"] = {
        "value": candidates[normal_code],
        "recognized": True,
        "confidence": 1.0,
        "option_codes": [normal_code],
    }


def _canonical_option_codes(
    raw_codes: Any,
    field: str,
    candidates: dict[str, str] | None = None,
) -> list[str] | None:
    if not isinstance(raw_codes, list) or not raw_codes:
        return None
    candidates = candidates or ENUM_OPTIONS[field]
    numeric_to_english = {
        number: code for code, number in ENUM_NUMERIC_CODES[field].items()
    }
    codes: list[str] = []
    for raw_code in raw_codes:
        if isinstance(raw_code, int) and not isinstance(raw_code, bool) and candidates is ENUM_OPTIONS[field]:
            code = numeric_to_english.get(raw_code)
        elif isinstance(raw_code, str) and raw_code.isdigit() and candidates is ENUM_OPTIONS[field]:
            code = numeric_to_english.get(int(raw_code))
        else:
            code = raw_code
        if not isinstance(code, str) or code not in candidates:
            return None
        if code not in codes:
            codes.append(code)
    return codes


def numericize_option_codes(result: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Convert internal English enum codes to the numeric codes exposed by the API."""
    result = {key: dict(item) for key, item in result.items()}
    for config in FIELD_CONFIGS:
        field = str(config["name"])
        key = str(config["key"])
        if field not in ENUM_NUMERIC_CODES or not result.get(key, {}).get("recognized"):
            continue
        result[key]["option_codes"] = [
            ENUM_NUMERIC_CODES[field][code]
            for code in result[key].get("option_codes", [])
            if code in ENUM_NUMERIC_CODES[field]
        ]
    return result


def format_extract_response(
    result: dict[str, dict[str, Any]],
    extract_config: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Attach request metadata and the selected request options to results."""
    default_configs = {str(config["name"]): config for config in FIELD_CONFIGS}
    configs = extract_config or [
        {
            "rec_type": config["name"],
            "rec_type_en": config["key"],
        }
        for config in FIELD_CONFIGS
    ]
    response: list[dict[str, Any]] = []
    for request_config in configs:
        name = str(request_config.get("rec_type", ""))
        default_config = default_configs.get(name, {})
        key = str(default_config.get("key", request_config.get("rec_type_en", name)))
        item = result.get(key, {})
        field: dict[str, Any] = {
            "rec_type": str(request_config.get("rec_type", name)),
            "rec_type_en": str(request_config.get("rec_type_en") or key),
            "value": item.get("value", ""),
            "recognized": item.get("recognized", False),
            "confidence": item.get("confidence", 0.0),
        }
        if item.get("recognized") and name in ENUM_NUMERIC_CODES:
            option_codes = list(item.get("option_codes", []))
            multiple = bool(
                request_config.get("multiple", default_config.get("multiple", False))
            )
            request_options = request_config.get("option", [])
            if not isinstance(request_options, list):
                request_options = []
            request_candidates = _request_option_candidates(
                name,
                {name: request_config},
            )
            labels_to_codes = {
                label: code
                for code, label in (request_candidates or ENUM_OPTIONS[name]).items()
            }
            if request_options:
                allowed_codes = {
                    labels_to_codes.get(str(option.get("dicName", "")))
                    for option in request_options
                    if isinstance(option, dict)
                }
                option_codes = [code for code in option_codes if code in allowed_codes]
                if not option_codes:
                    field.update(_empty_result())
            if not multiple and option_codes:
                option_codes = option_codes[:1]
                field["value"] = (request_candidates or ENUM_OPTIONS[name])[option_codes[0]]
            elif option_codes:
                field["value"] = ",".join(
                    (request_candidates or ENUM_OPTIONS[name])[code]
                    for code in option_codes
                )
            selected_codes = set(option_codes)
            field["option"] = [
                {
                    "dicName": str(option["dicName"]),
                    "dicValue": str(option.get("dicValue", "")),
                }
                for option in request_options
                if isinstance(option, dict)
                and (code := labels_to_codes.get(str(option.get("dicName", "")))) in selected_codes
            ]
        response.append(field)
    return response


SPOKEN_DIGITS = str.maketrans({
    "零": "0", "〇": "0", "一": "1", "幺": "1", "二": "2", "两": "2",
    "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8",
    "九": "9", "叉": "X",
})


def _explicit_caller_name(
    conversation: list[dict[str, str]] | None,
) -> str | None:
    latest = None
    patterns = (
        r"(?:我的名字|我的姓名)\s*[，,：:]?\s*(?:是|叫)?\s*"
        r"([\u4e00-\u9fff·]{2,6})(?=$|[，,。；;！!？?\s])",
        r"(?:我叫|姓名\s*(?:是|叫|为))\s*([\u4e00-\u9fff·]{2,6})"
        r"(?=$|[，,。；;！!？?\s])",
    )
    for turn in conversation or []:
        if turn.get("role") != "user":
            continue
        content = turn.get("content", "")
        for pattern in patterns:
            if match := re.search(pattern, content):
                latest = match.group(1)
                break
    return latest


def _spoken_id_card(conversation: list[dict[str, str]] | None) -> str | None:
    latest = None
    for turn in conversation or []:
        if turn.get("role") != "user":
            continue
        content = turn.get("content", "")
        if "身份证" not in content:
            continue
        for spoken in re.findall(
            r"[零〇一幺二两三四五六七八九0-9Xx叉]+", content
        ):
            value = spoken.translate(SPOKEN_DIGITS).upper()
            if re.fullmatch(r"\d{17}[0-9X]", value):
                latest = value
    return latest


def _ensure_enum_code(
    result: dict[str, dict[str, Any]],
    key: str,
    field: str,
    code: str,
    *,
    before: str | None = None,
    after: str | None = None,
) -> None:
    item = result[key]
    codes = list(item.get("option_codes", []))
    if code not in codes:
        if before in codes:
            codes.insert(codes.index(before), code)
        elif after in codes:
            codes.insert(codes.index(after) + 1, code)
        else:
            codes.append(code)
    options = ENUM_OPTIONS[field]
    item.update({
        "value": ",".join(options.get(candidate, candidate) for candidate in codes),
        "recognized": True,
        "confidence": item.get("confidence", 1.0) if item.get("recognized") else 1.0,
        "option_codes": codes,
    })


def _replace_enum_code(
    result: dict[str, dict[str, Any]],
    key: str,
    field: str,
    old: str,
    new: str,
) -> None:
    item = result[key]
    codes = list(item.get("option_codes", []))
    if old not in codes:
        _ensure_enum_code(result, key, field, new)
        return
    codes[codes.index(old)] = new
    item["option_codes"] = list(dict.fromkeys(codes))
    item["value"] = ",".join(
        ENUM_OPTIONS[field].get(code, code) for code in item["option_codes"]
    )


def _remove_enum_code(
    result: dict[str, dict[str, Any]],
    key: str,
    field: str,
    code: str,
) -> None:
    item = result.get(key)
    if not item or not item.get("recognized"):
        return
    codes = [candidate for candidate in item.get("option_codes", []) if candidate != code]
    if not codes:
        result[key] = _empty_result()
        return
    options = ENUM_OPTIONS[field]
    item["option_codes"] = codes
    item["value"] = ",".join(options.get(candidate, candidate) for candidate in codes)


def _phone_matches(previous_ticket: dict[str, Any] | None, result: dict[str, dict[str, Any]]) -> bool:
    return bool(
        previous_ticket
        and previous_ticket.get("contact_phone")
        and result.get("contact_phone", {}).get("recognized")
        and previous_ticket["contact_phone"] == result["contact_phone"]["value"]
    )


def _fallback_notes(
    previous_ticket: dict[str, Any],
    result: dict[str, dict[str, Any]],
    current_text: str = "",
) -> str:
    history = str(previous_ticket.get("notes", "")).strip()
    match = re.match(r"^(\d{4}-\d{2}-\d{2})来电", history)
    if match:
        date, history_body = match.group(1), history[len(match.group(1)):]
        history_body = history_body.removeprefix("来电").strip().rstrip("。；; ")
        prefix = f"根据{date}历史工单备注：{history_body}"
    else:
        history = history.rstrip("。；; ")
        prefix = f"根据历史工单备注：{history}" if history else ""
    if current_text:
        current_text = _deduplicate_note_segments(current_text)
        return f"{prefix}；本次补充：{current_text}" if prefix else f"本次补充：{current_text}"
    current = []
    for key in ("problem_summary", "core_request"):
        value = result.get(key, {}).get("value")
        if value:
            current.append(value)
    current_text = _deduplicate_note_segments("；".join(current))
    return f"{prefix}；本次补充：{current_text}" if prefix else f"本次补充：{current_text}"


def _conversation_text(conversation: list[dict[str, str]] | None) -> str:
    return "\n".join(
        turn.get("content", "")
        for turn in conversation or []
        if turn.get("role") == "user"
    )


def _positive_intent_text(text: str) -> str:
    return re.sub(
        r"(?:不|不用|不要|无需|别|暂不|先不|先别|没有|没)"
        r"(?:想|要|需要|要求|打算|准备)?"
        r"(?:投诉|举报|建议|派单|流转)(?:处理|办理|平台改造)?",
        "",
        text,
    )


def _history_backfill_blocked(key: str, text: str) -> bool:
    if "不要回填" in text and ("历史" in text or key in {"incident_location", "insured_location"}):
        return True
    if re.search(
        r"(?:历史信息|历史记录).{0,12}(?:不要|不用|别).{0,6}(?:回填|使用|采用|沿用)|"
        r"(?:都不对|均不对).{0,24}(?:历史信息|不要回填)",
        text,
    ):
        return True
    field_patterns = {
        "incident_location": r"(?:没有|未提供|没提供|不提供).{0,6}(?:事发地|发生地)",
        "insured_location": r"(?:没有|未提供|没提供|不提供).{0,6}参保地",
    }
    pattern = field_patterns.get(key)
    return bool(pattern and re.search(pattern, text))


def _explicit_incident_locations(
    conversation: list[dict[str, str]] | None,
) -> list[str]:
    patterns = (
        r"单位地址(?:在|是|为)([^，。；,;\n]+)[，,](?:这事|事情|问题).{0,8}(?:发生|出在)",
        r"(?:出事的地方|事发地|事情发生地|事故发生地)(?:在|是|为)([^，。；,;\n]+)",
    )
    locations: list[str] = []
    for turn in conversation or []:
        if turn.get("role") != "user":
            continue
        content = turn.get("content", "")
        for pattern in patterns:
            for match in re.finditer(pattern, content):
                location = match.group(1).strip()
                if location and location not in locations:
                    locations.append(location)
    return locations


def _current_notes(result: dict[str, dict[str, Any]], model_value: str = "") -> str:
    if model_value:
        if re.search(r"历史工单|根据\d{4}-\d{2}-\d{2}", model_value):
            model_value = model_value.split("本次补充：", 1)[-1]
        return _deduplicate_note_segments(model_value)
    values = [
        result.get(key, {}).get("value", "")
        for key in ("problem_summary", "core_request")
    ]
    return "；".join(value for value in values if value)


def _segment_key(value: str) -> str:
    value = re.sub(r"^(?:用户反映|咨询)", "", value.strip())
    value = re.sub(r"(?:的问题|问题|的)$", "", value)
    return re.sub(r"[，。；;、,.\s]", "", value)


def _deduplicate_note_segments(value: str) -> str:
    segments: list[str] = []
    for segment in re.split(r"[；;]+", value):
        segment = segment.strip().removeprefix("本次补充：").strip().rstrip("。；; ")
        key = _segment_key(segment)
        if segment and key and not any(
            key == existing or key in existing or existing in key
            for existing in map(_segment_key, segments)
        ):
            segments.append(segment)
    return "；".join(segments)


def _clean_problem_summary(value: str, text: str) -> str:
    if (
        "社保卡" in text
        and re.search(r"丢失|丢了|挂失|补办", text)
        and not re.search(r"养老保险|工资|投诉|劳动仲裁|平台|建议", text)
    ):
        return "社保卡丢失，咨询挂失补办流程及所需材料"
    parts = [
        part.strip()
        for part in re.split(r"[；。]", value)
        if part.strip()
        and not re.search(r"单位地址|联系电话|电话确认为|手机号", part)
    ]
    return "；".join(parts) or value


def _append_request(result: dict[str, dict[str, Any]], text: str) -> None:
    core = result.get("core_request")
    if (
        core
        and core.get("recognized")
        and _segment_key(text) not in _segment_key(core["value"])
    ):
        core["value"] = f"{core['value'].rstrip('。；.;，, ')}；{text}"


def _remove_unsupported_dispatch(value: str) -> str:
    value = re.sub(
        r"要求派单处理|请帮我派单(?:处理)?|帮我派单(?:处理)?|需要派单(?:处理)?",
        "",
        value,
    )
    return re.sub(r"^[；;，,。\s]+|[；;，,\s]+$|[；;]{2,}", "", value)


def _remove_unsupported_requests(
    value: str,
    *,
    complaint: bool,
    suggestion: bool,
    report: bool,
) -> str:
    if not complaint:
        value = re.sub(r"要求投诉相关单位|投诉相关单位", "", value)
    if not suggestion:
        value = re.sub(r"建议(?:线上平台)?增加[^；。]*功能", "", value)
    if not report:
        value = re.sub(r"要求举报[^；。]*|举报相关单位", "", value)
    return re.sub(r"^[；;，,。\s]+|[；;，,\s]+$|[；;]{2,}", "", value)


def _preserve_planned_actions(value: str, text: str) -> str:
    for action in ("劳动仲裁", "工伤认定"):
        if re.search(rf"(?:准备|计划|打算)申请{action}", text):
            value = re.sub(rf"(?<!准备)(?<!计划)(?<!打算)申请{action}", f"准备申请{action}", value)
    return value


def _explicit_suggestions(text: str) -> list[str]:
    return [
        match.group(0).strip().rstrip("。；; ")
        for match in re.finditer(r"建议[^。；;\n]{1,50}(?:功能|流程|服务)", text)
    ]


def normalize_extract_result(
    raw_result: Any,
    previous_ticket: dict[str, Any] | None = None,
    conversation: list[dict[str, str]] | None = None,
    call_phone: str | None = None,
    extract_config: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    previous_ticket = _canonical_previous_ticket(previous_ticket, extract_config)
    request_configs = _request_configs(extract_config)
    parsed = _unwrap_result(_parse_model_result(raw_result))
    result: dict[str, dict[str, Any]] = {}

    for config in FIELD_CONFIGS:
        name = str(config["name"])
        key = str(config["key"])
        model_item = parsed.get(key, parsed.get(name, {}))
        if not isinstance(model_item, dict):
            result[key] = _empty_result()
            continue

        value = _item_value(model_item)
        confidence = model_item.get("c", model_item.get("confidence", 1.0))
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = -1.0

        if not isinstance(value, str):
            result[key] = _empty_result()
            continue
        value = value.strip()
        regex = _request_regex(config, request_configs)
        if (
            not _is_recognized(model_item)
            or not value
            or not 0 <= confidence <= 1
            or (regex and re.fullmatch(str(regex), value) is None)
        ):
            result[key] = _empty_result()
            continue

        normalized: dict[str, Any] = {
            "value": value,
            "recognized": True,
            "confidence": confidence,
        }
        options = _request_option_candidates(name, request_configs) or ENUM_OPTIONS.get(name)
        if options:
            option_codes = model_item.get("o", model_item.get("option_codes"))
            parts = [
                part.strip()
                for part in re.split(r"[、,，;；~～]+", value)
                if part.strip()
            ]
            labels_to_codes = {label: code for code, label in options.items()}
            option_codes = _canonical_option_codes(option_codes, name, options)
            if not option_codes:
                labels_to_codes = {label: code for code, label in options.items()}
                if all(part in options for part in parts):
                    option_codes = parts
                elif parts and all(part in labels_to_codes for part in parts):
                    option_codes = [labels_to_codes[part] for part in parts]
                else:
                    result[key] = _empty_result()
                    continue
            if not _is_multiple(config, request_configs):
                option_codes = option_codes[:1]
            labels = [options[code] for code in option_codes]
            if (
                name == "业务分类"
                and "wages" in option_codes
                and "labor_relations" in option_codes
                and "labor_inspection_rights" not in option_codes
            ):
                option_codes = [
                    "labor_inspection_rights" if code == "labor_relations" else code
                    for code in option_codes
                ]
                labels = [options[code] for code in option_codes]
            normalized["value"] = ",".join(labels)
            normalized["option_codes"] = option_codes
        result[key] = normalized

    _default_urgency(result, request_configs)
    caller_name = _explicit_caller_name(conversation)
    if caller_name:
        result["caller_name"] = {
            "value": caller_name,
            "recognized": True,
            "confidence": 1.0,
        }
    spoken_id = _spoken_id_card(conversation)
    if spoken_id:
        result["id_card_number"] = {
            "value": spoken_id,
            "recognized": True,
            "confidence": 1.0,
        }
    conversation_text = _conversation_text(conversation)
    intent_text = _positive_intent_text(conversation_text)
    if conversation is not None and re.search(
        r"咨询|想问|了解|怎么|如何|条件|材料|流程|能否|是否|查询", intent_text
    ):
        _ensure_enum_code(result, "business_type", "业务类型", "consultation")
    if conversation is not None and re.search(
        r"协助(?:办理|申请|处理)|申请(?:劳动仲裁|工伤认定)|培训.{0,12}报名|报名.{0,12}培训",
        intent_text,
    ):
        _ensure_enum_code(result, "business_type", "业务类型", "assistance")
    if re.search(r"失业保险(?:金)?|失业金", intent_text):
        _ensure_enum_code(
            result, "business_category", "业务分类", "unemployment_insurance"
        )
    complaint_signal = re.search(
        r"投诉|未缴纳社保|没给我交社保|未给我交社保|拖欠.{0,8}工资|欠薪|维权",
        intent_text,
    )
    if complaint_signal:
        _ensure_enum_code(result, "business_category", "业务分类", "labor_inspection_rights")
        _ensure_enum_code(result, "business_type", "业务类型", "complaint")
        core = result.get("core_request")
        if (
            core
            and core["recognized"]
            and re.search(r"投诉|维权", intent_text)
            and "投诉" not in core["value"]
        ):
            _append_request(result, "要求投诉相关单位")
    elif conversation is not None:
        _remove_enum_code(result, "business_type", "业务类型", "complaint")
        _remove_enum_code(result, "business_category", "业务分类", "labor_inspection_rights")
        _remove_enum_code(result, "business_category", "业务分类", "rights_protection")
    if re.search(r"工资|欠薪", intent_text):
        _remove_enum_code(result, "business_category", "业务分类", "labor_relations")
        _ensure_enum_code(
            result,
            "business_category",
            "业务分类",
            "wages",
            before="labor_inspection_rights",
        )
    if re.search(r"劳动仲裁|申请仲裁", intent_text):
        _ensure_enum_code(
            result,
            "business_category",
            "业务分类",
            "labor_dispute_mediation_arbitration",
            after="labor_inspection_rights",
        )
    if re.search(r"工伤认定|工伤保险|工伤", intent_text):
        _ensure_enum_code(
            result,
            "business_category",
            "业务分类",
            "work_injury_insurance",
            after="labor_dispute_mediation_arbitration",
        )
    fund_report = re.search(
        r"举报.{0,30}(?:骗取?|套取).{0,12}(?:社保补贴|社保基金|基金)|"
        r"(?:骗取?|套取).{0,12}(?:社保补贴|社保基金)",
        intent_text,
    )
    if fund_report:
        _ensure_enum_code(
            result, "business_type", "业务类型", "report", before="suggestion"
        )
        _replace_enum_code(
            result, "business_category", "业务分类", "other", "fund_supervision"
        )
    elif conversation is not None:
        _remove_enum_code(result, "business_type", "业务类型", "report")
    training_signal = re.search(
        r"技能培训|职业技能培训|就业培训|(?:电工|焊工).{0,12}培训|培训.{0,12}(?:报名|条件|流程|怎么)",
        intent_text,
    )


    if training_signal:
        _remove_enum_code(
            result, "business_category", "业务分类", "continuing_education"
        )
        _replace_enum_code(
            result,
            "business_category",
            "业务分类",
            "training_request",
            "employment_training",
        )
    elif conversation is not None:
        _remove_enum_code(
            result, "business_category", "业务分类", "employment_training"
        )
        _remove_enum_code(
            result, "business_category", "业务分类", "training_request"
        )
    if re.search(r"延迟退休", intent_text):
        _ensure_enum_code(result, "business_category", "业务分类", "delayed_retirement")
    if re.search(r"城乡居民(?:养老保险|养老)?.{0,12}(?:转|转换|转成).{0,8}城镇职工养老", intent_text):
        _remove_enum_code(result, "business_category", "业务分类", "pension_insurance")
        _ensure_enum_code(
            result, "business_category", "业务分类", "urban_rural_resident_pension"
        )
        _ensure_enum_code(
            result, "business_category", "业务分类", "urban_employee_pension"
        )
    if "社保卡" in intent_text:
        _ensure_enum_code(result, "business_category", "业务分类", "social_security_card")
    suggestion_signal = re.search(
        r"建议|建议改进|增加.*功能|平台.*进度查询", intent_text
    )
    if conversation is not None and suggestion_signal:
        _ensure_enum_code(result, "business_type", "业务类型", "suggestion")
        _ensure_enum_code(result, "business_category", "业务分类", "public_information")
        if (
            re.search(r"进度查询", intent_text)
            and "进度查询" not in result.get("core_request", {}).get("value", "")
        ):
            _append_request(result, "建议线上平台增加进度查询功能")
    elif conversation is not None:
        _remove_enum_code(result, "business_type", "业务类型", "suggestion")
        _remove_enum_code(result, "business_category", "业务分类", "public_information")
    for suggestion in _explicit_suggestions(intent_text):
        _append_request(result, suggestion)
    dispatch_signal = re.search(
        r"(?:帮我|请|需要|要求).{0,4}(?:派单|流转)|(?:派单|流转).{0,4}(?:处理|办理)",
        intent_text,
    )
    core = result.get("core_request")
    if core and core.get("recognized"):
        if not dispatch_signal:
            core["value"] = _remove_unsupported_dispatch(core["value"])
        core["value"] = _remove_unsupported_requests(
            core["value"],
            complaint=bool(complaint_signal),
            suggestion=bool(suggestion_signal),
            report=bool(fund_report),
        )
        core["value"] = _preserve_planned_actions(core["value"], conversation_text)
    insured_negative = re.search(
        r"(?:没说|未提及|没有|不知道|不确定).{0,8}参保地", conversation_text
    )
    if conversation is not None and (
        not re.search(r"参保地|参保地区|保险参保地|参保所在地", conversation_text)
        or insured_negative
    ):
        result["insured_location"] = _empty_result()
    incident_negative = re.search(
        r"(?:没有|未提供|没提供|不提供).{0,6}(?:事发地|发生地)",
        conversation_text,
    )
    if conversation is not None and (
        incident_negative
        or not re.search(
            r"事发地|出事的地方|事情发生|发生在|问题发生|事故发生",
            conversation_text,
        )
    ):
        result["incident_location"] = _empty_result()
    else:
        locations = _explicit_incident_locations(conversation)
        model_location = result["incident_location"].get("value")
        for location in re.split(r"[、,，;；]+|\s+", model_location or ""):
            location = location.strip()
            if location and location not in locations:
                locations.append(location)
        if locations:
            result["incident_location"] = {
                "value": ",".join(locations),
                "recognized": True,
                "confidence": result["incident_location"].get("confidence", 1.0),
            }
    insured = result.get("insured_location")
    if (
        insured
        and insured.get("recognized")
        and insured.get("value") == "黄岛区"
    ):
        insured["value"] = "青岛市黄岛区"
    summary = result.get("problem_summary")
    if summary and summary["recognized"]:
        summary["value"] = _clean_problem_summary(summary["value"], conversation_text)
    for config in FIELD_CONFIGS:
        name = str(config["name"])
        key = str(config["key"])
        codes = result.get(key, {}).get("option_codes", [])
        request_options = _request_option_candidates(name, request_configs)
        if request_options is not None:
            codes = [code for code in codes if code in request_options]
            if not codes:
                result[key] = _empty_result()
                continue
            result[key]["option_codes"] = codes
            result[key]["value"] = ",".join(request_options[code] for code in codes)
        if codes and not _is_multiple(config, request_configs):
            result[key]["option_codes"] = codes[:1]
            result[key]["value"] = (
                request_options or ENUM_OPTIONS[name]
            )[codes[0]]
    for key in PREVIOUS_TICKET_FIELDS:
        if (
            not result[key]["recognized"]
            and not _history_backfill_blocked(key, conversation_text)
            and (
            provided := _provided_result(
                previous_ticket.get(key),
                regex=PHONE_REGEX if key == "contact_phone" else None,
            )
            )
        ):
            result[key] = provided
    if not result["contact_phone"]["recognized"] and (
        provided := _provided_result(call_phone, regex=PHONE_REGEX)
    ):
        result["contact_phone"] = provided
    result["dispatch_required"] = _empty_result()
    result["ticket_status"] = {
        "value": "已处理",
        "recognized": True,
        "confidence": 1.0,
        "option_codes": ["resolved"],
    }
    if _phone_matches(previous_ticket, result):
        notes = result.get("notes")
        current_text = _current_notes(result, notes["value"]) if notes and notes["recognized"] else ""
        if not current_text:
            current_text = _current_notes(result)
        if re.search(r"进度查询", conversation_text) and "进度查询" not in current_text:
            current_text = f"{current_text.rstrip('。；')}；建议线上平台增加进度查询功能" if current_text else "建议线上平台增加进度查询功能"
        result["notes"] = {
            "value": _fallback_notes(previous_ticket or {}, result, current_text),
            "recognized": True,
            "confidence": notes.get("confidence", 1.0) if notes and notes.get("confidence", 0) > 0 else 1.0,
        }
    elif result.get("notes", {}).get("recognized"):
        result["notes"] = {
            "value": _current_notes(result, result["notes"]["value"]),
            "recognized": True,
            "confidence": (
                result["notes"].get("confidence", 1.0)
                if result["notes"].get("confidence", 1.0) > 0
                else 1.0
            ),
        }
    elif conversation is not None:
        current_text = _current_notes(result)
        if current_text:
            result["notes"] = {
                "value": f"本次补充：{_deduplicate_note_segments(current_text)}",
                "recognized": True,
                "confidence": 1.0,
            }
    for key, item in result.items():
        if item.get("recognized") and not str(item.get("value", "")).strip():
            result[key] = _empty_result()
    return result


def _model_result_is_valid(
    raw_result: Any,
    normalized: dict[str, dict[str, Any]],
    previous_ticket: dict[str, Any] | None = None,
    extract_config: list[dict[str, Any]] | None = None,
) -> bool:
    parsed = _unwrap_result(_parse_model_result(raw_result))
    requested_names = {
        str(config.get("rec_type"))
        for config in extract_config or []
        if config.get("rec_type")
    }
    requested_keys = {
        str(config.get("rec_type_en"))
        for config in extract_config or []
        if config.get("rec_type_en")
    }
    for config in FIELD_CONFIGS:
        name = str(config["name"])
        key = str(config["key"])
        if key in AI_IGNORED_FIELDS:
            continue
        if extract_config and name not in requested_names and key not in requested_keys:
            continue
        item = parsed.get(key, parsed.get(name))
        if key == "notes" and key not in parsed and name not in parsed:
            continue
        if not isinstance(item, dict) or not any(
            candidate in item for candidate in ("v", "value", "rec_result")
        ):
            return False

    if not normalized["urgency"]["recognized"]:
        return False
    if _phone_matches(previous_ticket, normalized) and not normalized["notes"]["recognized"]:
        return False
    return True


def extract_fields(
    conversation: list[dict[str, str]],
    previous_ticket: dict[str, Any] | None = None,
    call_phone: str | None = None,
    extract_config: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    settings = get_settings()
    payload: dict[str, Any] = {
        "fields": _model_fields(extract_config),
        "conversation": conversation,
    }
    user_content = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    for attempt in range(2):
        retry = (
            "\n上次结果未通过校验。请按上述协议重新抽取并返回全部字段。"
            if attempt
            else ""
        )
        try:
            response = get_llm_client().chat.completions.create(
                model=settings.llm_model,
                temperature=0,
                messages=[
                    {"role": "system", "content": EXTRACT_SYSTEM_PROMPT + retry},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=2048,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            logger.exception("大模型服务调用失败")
            raise LLMServiceError(f"大模型服务调用失败: {exc}") from exc

        choice = response.choices[0]
        raw_result = choice.message.content
        result = normalize_extract_result(
            raw_result,
            previous_ticket,
            conversation,
            call_phone,
            extract_config,
        )
        if getattr(choice, "finish_reason", None) != "length" and _model_result_is_valid(
            raw_result, result, previous_ticket, extract_config
        ):
            return result

    raise LLMServiceError("大模型连续返回无效格式，请重试")
