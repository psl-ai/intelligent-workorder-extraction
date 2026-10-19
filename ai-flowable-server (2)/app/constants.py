"""Fixed fields shown on the incoming-call work order."""

FIELD_CONFIGS: list[dict[str, object]] = [
    {"name": "姓名", "key": "caller_name", "guidance": "仅提取来电用户本人姓名，不得使用坐席姓名。"},
    {
        "name": "身份证号码",
        "key": "id_card_number",
        "regex": r"\d{17}[0-9Xx]",
        "guidance": "逐位转换零、幺、一至九等中文口述数字，叉转为末位X；必须为完整18位，不得补写、替换或猜改。",
    },
    {
        "name": "联系电话",
        "key": "contact_phone",
        "regex": r"1[3-9]\d{9}",
        "guidance": "将中文口述数字逐位转换为完整11位手机号；以用户最后一次有效更正为准。",
    },
    {"name": "居住地", "key": "residence_address", "guidance": "仅返回用户居住地；合并相邻轮次中的同一地址片段，不得猜改路名、门牌号或楼层。"},
    {"name": "事发地", "key": "incident_location", "guidance": "返回各独立诉求对应的全部事发地，合并同一地址片段、去重并用顿号分隔；不得用居住地或参保地替代。"},
    {"name": "参保地", "key": "insured_location", "guidance": "仅返回明确的参保地区，不得用居住地、单位地址或事发地替代。"},
    {
        "name": "备注",
        "key": "notes",
        "guidance": "previous_ticket 存在且其联系电话与本次联系电话完全一致时，按‘根据YYYY-MM-DD历史工单备注：历史内容；本次补充：本次全部诉求’合并；否则仅写本次补充。",
    },
    {
        "name": "业务类型",
        "key": "business_type",
        "multiple": True,
        "guidance": "按全部独立诉求多选；明确要求协助或派单时必须选求助类；举报骗取社保补贴或套取基金时必须选举报类；有明确证据时不得遗漏。",
    },
    {
        "name": "业务分类",
        "key": "business_category",
        "multiple": True,
        "guidance": "按全部独立诉求多选：欠薪或工资发放问题选工资；单位欠薪并投诉时同时选监察维权，不得用劳动关系替代；申请劳动仲裁选调解仲裁；申请工伤认定或咨询工伤流程选工伤保险；举报骗取社保补贴或套取基金选资金基金监督，不选其他；延迟退休政策咨询选延迟退休；职业技能培训报名、条件或流程咨询选就业培训，不选想培训或继续教育；社保卡办理、挂失、补办、使用或备案选社会保障卡；有明确证据时不得返回空值。",
    },
    {"name": "紧急程度", "key": "urgency", "guidance": "用户未明确强调紧急时选普通。"},
    {"name": "问题概要", "key": "problem_summary", "guidance": "完整概括全部独立问题和建议，不得混入处理结论。"},
    {"name": "核心诉求", "key": "core_request", "guidance": "完整提炼用户希望得到的全部帮助或结果；明确要求派单时必须包含‘要求派单处理’。"},
    {"name": "是否派单", "key": "dispatch_required", "guidance": "由业务系统处理，不交给 AI 判断。"},
    {"name": "工单状态", "key": "ticket_status", "guidance": "由业务系统默认填写已处理。"},
]

ENUM_OPTIONS: dict[str, dict[str, str]] = {
    "业务类型": {
        "consultation": "咨询类",
        "assistance": "求助类",
        "suggestion": "建议类",
        "complaint": "投诉类",
        "report": "举报类",
    },
    "业务分类": {
        "government_institution_pension": "机关事业养老保险",
        "urban_employee_pension": "城镇职工养老",
        "urban_rural_resident_pension": "城乡居民（新型农保）",
        "pension_insurance": "养老保险",
        "unemployment_insurance": "失业保险",
        "work_injury_insurance": "工伤保险",
        "maternity_insurance": "生育保险",
        "vocational_skill_assessment": "技能鉴定",
        "continuing_education": "继续教育",
        "employment_training": "就业培训",
        "employment_policy": "就业政策",
        "labor_relations": "劳动关系",
        "work_capacity_assessment": "劳动能力鉴定",
        "labor_inspection_rights": "监察维权",
        "labor_dispute_mediation_arbitration": "调解仲裁",
        "talent_services": "人才服务",
        "professional_technical_personnel": "专业技术人员",
        "examination_consultation": "考试咨询",
        "social_insurance_enrollment_info": "参保信息",
        "wages": "工资",
        "public_information": "公共信息",
        "social_security_card": "社会保障卡",
        "job_search": "找工作",
        "rights_protection": "要维权",
        "training_request": "想培训",
        "migrant_worker_policy": "问务工政策",
        "external_labor_cooperation_station_info": "驻外劳务协作站（点）信息查询",
        "other_migrant_worker_services": "外出务工其他",
        "delayed_retirement": "延迟退休",
        "occupational_injury_protection": "职业伤害保障",
        "fund_supervision": "资金基金监督",
        "other": "其他",
    },
    "紧急程度": {"normal": "普通", "urgent": "紧急"},
    "是否派单": {"yes": "是", "no": "否"},
    "工单状态": {"resolved": "已处理", "unresolved": "未处理"},
}

# Public API codes. English codes remain accepted from model responses for compatibility.
ENUM_NUMERIC_CODES: dict[str, dict[str, int]] = {
    "业务类型": {
        "consultation": 1,
        "assistance": 2,
        "suggestion": 3,
        "complaint": 4,
        "report": 5,
    },
    "业务分类": {
        code: number
        for number, code in enumerate(ENUM_OPTIONS["业务分类"], start=1)
    },
    "紧急程度": {"normal": 1, "urgent": 2},
    "是否派单": {"yes": 1, "no": 2},
    "工单状态": {"resolved": 1, "unresolved": 2},
}
