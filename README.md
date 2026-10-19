# 智能工单信息抽取服务

面向人社服务热线来电工单场景，基于大语言模型实现多轮对话关键信息自动抽取与结构化输出，支持身份信息、地址、业务类型/分类、问题概要和核心诉求等字段，辅助减少人工录入。

## 功能特性

- **字段级信息抽取**：覆盖 14 个工单字段和 32 个业务分类，支持按业务动态配置抽取字段、正则规则和枚举校验项
- **Prompt 工程与结构化输出**：设计 Prompt 和 JSON 结构化输出约束，增加字段完整性、置信度、正则及枚举合法性校验；结果不符合要求时自动重试 1 次
- **历史工单回填**：基于来电手机号匹配历史工单，支持姓名、身份证、联系电话、居住地、事发地和参保地等 6 类信息回填，减少重复录入
- **FastAPI 服务化**：基于 FastAPI + Pydantic 开发，自动生成 OpenAPI 文档

## 技术栈

| 层级 | 技术选型 |
|---|---|
| Web 框架 | FastAPI、Pydantic |
| 大模型 | LLM API（OpenAI 兼容接口） |
| 数据校验 | Pydantic Schema、正则表达式、枚举校验 |
| 测试 | pytest |

## 快速开始

```bash
cd ai-flowable-server\(2\)
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

打开 http://localhost:8000/docs 查看自动生成的 API 文档。

## 项目结构

```
ai-flowable-server (2)/
├── api.py              # 入口文件
├── requirements.txt    # 依赖
├── Dockerfile          # 容器化部署
└── app/
    ├── main.py         # FastAPI 应用入口
    ├── config.py       # 配置
    ├── constants.py    # 常量定义
    ├── exceptions.py    # 异常定义
    ├── schemas.py      # Pydantic 数据模型
    ├── routers/
    │   └── extract.py  # 工单抽取路由
    └── services/
        ├── extract_service.py    # 抽取业务逻辑
        └── llm_client.py         # 大模型客户端
```

## API 示例

```bash
POST /api/extract
{
  "phone": "13800138000",
  "dialogue": "你好，我要咨询社保补缴的问题，我叫张三..."
}
```

返回结构化的工单字段，包括姓名、身份证号、业务类型、问题概要等。
