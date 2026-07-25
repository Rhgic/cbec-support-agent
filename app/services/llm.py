"""LLM 统一封装：所有 DeepSeek 调用都走这里（规格 0 约束 #5）。

为什么集中：token 计量、成本累计（驱动熔断）、重试、json_mode 处理、异常兜底都在
一处，业务节点禁止直接 import openai。openai SDK 延迟导入，避免无密钥环境导入即报错。
作者自写的 prompt 模板见文件末尾（规格 14），仅签名 + docstring。
"""
import json
from dataclasses import dataclass

from app.config import get_settings
from app.services.guardrails import add_cost

settings = get_settings()

# deepseek-chat 公开价（美元 / 1K tokens），仅作成本估算；生产应随模型版本核对
_PRICE = {"in": 0.00027, "out": 0.0011}


@dataclass
class LlmResult:
    data: dict
    token_in: int
    token_out: int
    cost_usd: float
    error: str | None = None


def _client():
    # 延迟导入：仅在实际调用时 import openai
    from openai import OpenAI

    return OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)


def chat_json(system_prompt: str, user_prompt: str, max_retries: int = 1) -> LlmResult:
    """调用 LLM 并解析 json_mode 响应。作者自写的 prompt 通过参数传入。

    重试 1 次（规格 6 工具/生成要求）；重试耗尽返回 error，由调用方兜底。
    """
    last_err: str | None = None
    for _ in range(max_retries + 1):
        try:
            resp = _client().chat.completions.create(
                model=settings.deepseek_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            text = resp.choices[0].message.content or "{}"
            data = json.loads(text)
            ti = resp.usage.prompt_tokens
            to = resp.usage.completion_tokens
            cost = ti / 1000 * _PRICE["in"] + to / 1000 * _PRICE["out"]
            add_cost(cost)  # 累计成本，驱动全局熔断
            return LlmResult(data=data, token_in=ti, token_out=to, cost_usd=cost)
        except Exception as e:  # noqa: BLE001 — 需捕获一切 LLM 异常以兜底
            last_err = str(e)
    return LlmResult(data={}, token_in=0, token_out=0, cost_usd=0.0, error=last_err)


# ===== Prompt 模板 =====
# 三个 prompt 均为 json_mode（response_format={"type":"json_object"}）。
# json_mode 要求 system 或 user prompt 中必须出现 "json" 字样，且输出必须
# 是单个合法 JSON 对象（非数组）——这是 DeepSeek/OpenAI json_mode 的坑。
# 少样本示例未在每个 prompt 中内嵌，因为 json_mode 会强制 JSON 格式；
# 分类/风险的任务语义足够简单，零样本即可稳定输出。


def classify_prompt(text: str) -> dict:
    """构造"语种识别 + 意图分类"的 json_mode prompt。

    要求模型一次返回 {"lang","intent","confidence"}。
    语种仅限 en/es/id；意图仅限 logistics/return/product/other。
    confidence 为 0~1 的浮点数，表示模型对该分类的置信度。

    面试要点：
    - 少样本（few-shot）是否内嵌在 prompt 中？当前为零样本，依赖 json_mode
      的格式约束和清晰的任务描述；若实际准确率不足，再补充 2~3 个示例。
    - 为什么语种和意图一次调用完成？省一次 LLM 调用 = 省一半 token 成本。
    """
    return {
        "system": (
            "You are a multilingual e-commerce customer service classifier. "
            "Your task is to detect the language and classify the intent of a customer message.\n\n"
            "Output a single JSON object with exactly these three fields:\n"
            '- "lang": one of "en", "es", "id"\n'
            '- "intent": one of "logistics", "return", "product", "other"\n'
            '- "confidence": a float between 0 and 1 indicating your certainty\n\n'
            "Rules:\n"
            '- "logistics" = tracking, shipping, delivery, where is my package\n'
            '- "return" = refund, return, exchange, money back, broken item\n'
            '- "product" = product info, size, color, quality, specifications\n'
            '- "other" = anything else (greetings, complaints without clear topic, etc.)\n'
            "- Confidence: use 0.9+ for clear intent, 0.7-0.89 for ambiguous, "
            "below 0.7 for very unclear.\n"
            "Output only the JSON object, no explanation."
        ),
        "user": f"Customer message:\n{text}",
    }


def generate_prompt(customer_text: str, retrieved: str, tool_info: str, lang: str) -> dict:
    """构造多语种回复生成 prompt，要求携带 source_url 引用、占位符保持不变。

    输入：
    - customer_text: 客户原话（**脱敏后**的 masked_text，绝不传原文给 LLM）
    - retrieved: 从知识库检索到的相关片段（中文，多个以 '\\n---\\n' 分隔）
    - tool_info: 工具调用结果文本（如订单状态、物流轨迹、退货政策评估）
    - lang: 目标输出语种（en/es/id）

    ⚠️ customer_text 曾被遗漏（只传知识库与工具结果），导致模型不知道客户问了什么，
    只会回「请描述你的问题」这类空话且不给引用——真跑通 LLM 后才暴露的 bug。

    输出要求模型返回 {"reply": "...", "citations": ["url1", ...]}。
    reply 中的 PII 占位符（[EMAIL_x] / [PHONE_x] 等）必须原样保留，
    不做替换——真实值在出站前由 restore() 统一还原。

    面试要点：
    - 为什么中文检索 → 多语种输出？BGE-m3 的跨语种对齐允许这样做，省翻译步骤。
    - 引用链路：知识库 doc → source_url；工具结果不产生引用（无 URL）。
    """
    lang_label = {"en": "English", "es": "Spanish", "id": "Indonesian"}.get(lang, "English")
    parts = []
    if retrieved and retrieved.strip():
        parts.append(f"Knowledge base (use for answering):\n{retrieved}")
    if tool_info and tool_info.strip():
        parts.append(f"Tool results (use for answering):\n{tool_info}")
    context = "\n\n".join(parts)
    if not context.strip():
        context = "(No knowledge base or tool results available. Generate a polite reply.)"

    return {
        "system": (
            "You are a multilingual e-commerce customer service agent. "
            f"Reply to the customer in **{lang_label}**.\n\n"
            "Use the provided knowledge base and tool results to answer the customer. "
            "If the knowledge base covers the question, reference it by including "
            'source URLs in the "citations" list. '
            "If no information is available, politely explain that the team will follow up.\n\n"
            'Output a single JSON object with exactly these fields:\n'
            '- "reply": the full reply text in the target language\n'
            '- "citations": list of source URLs from the knowledge base (empty if none)\n\n'
            "CRITICAL RULES:\n"
            "- Placeholder tokens like [EMAIL_1], [PHONE_2], [CARD_1], [TRACKING_1], "
            "[ORDER_1], [IP_1] MUST be kept exactly as-is in the reply. Never replace or remove them.\n"
            "- Always mention the tracking number, order status, or return policy "
            "if present in the tool results.\n"
            "- Keep replies concise and helpful. Do not fabricate details.\n"
            "Output only the JSON object, no explanation."
        ),
        "user": f"Customer message:\n{customer_text}\n\n{context}\n\n"
                "Answer the customer's message above, grounded in the knowledge base and tool results.",
    }


def risk_prompt(reply: str) -> dict:
    """构造风险二次判定 prompt，输出 low / mid 分级。

    注意：high 由规则层（evaluate_risk_rules）兜底，不交给 LLM——
    这是规格 6 的刻意设计，因为 LLM 的"是否 high"判断不可靠且不可审计。
    LLM 仅在规则层未命中时判 low/mid，用于区分自动出站与一键审核。

    面试要点：
    - 为什么 high 不交给 LLM？高风险 = 资损/店铺绩效受损，必须用可审计的
      确定性规则覆盖；LLM 只能判 low/mid，用于降低人工审核工时。
    """
    return {
        "system": (
            "You are a risk assessor for e-commerce customer service replies. "
            "Evaluate the draft reply and assign a risk level.\n\n"
            'Output a single JSON object with these fields:\n'
            '- "level": one of "low" or "mid"\n'
            '- "reasons": list of strings explaining the decision\n\n'
            "Criteria:\n"
            '- "low": Simple factual reply (tracking update, return policy, product info). '
            "No financial commitment, no apology offering compensation. Safe to auto-send.\n"
            '- "mid": Reply that could benefit from human review — ambiguous language, '
            "apology without clear action, partial information, or multi-step process involved. "
            "Should go to quick review (human one-click approve).\n\n"
            "Note: NEVER output \"high\". High-risk replies are detected by deterministic rules "
            "before reaching this assessment.\n"
            "Output only the JSON object, no explanation."
        ),
        "user": f"Draft reply to assess:\n---\n{reply}\n---",
    }
