import ast
import datetime
import logging
import operator

logger = logging.getLogger(__name__)

# --- Safe calculator ---

_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
}


def safe_calc(expr: str) -> str:
    try:
        node = ast.parse(expr, mode="eval")
        result = _eval(node.body)
        return str(result)
    except Exception as e:
        return f"Ошибка вычисления: {e}"


def _eval(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("только числа")
    if isinstance(node, ast.BinOp):
        op = _ALLOWED_OPS.get(type(node.op))
        if not op:
            raise ValueError(f"оператор {type(node.op).__name__} запрещен")
        return op(_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _ALLOWED_OPS.get(type(node.op))
        if not op:
            raise ValueError("унарный оператор запрещен")
        return op(_eval(node.operand))
    raise ValueError(f"выражение {type(node).__name__} не поддерживается")


# --- Tool schemas (OpenAI function calling) ---

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Возвращает текущее время UTC и локальное (Europe/Moscow). Вызывать когда спрашивают про время/дату.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Вычислить математическое выражение. Поддерживает + - * / ** % и скобки.",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "например: (12+5)*3/2"}},
                "required": ["expression"],
            },
        },
    },
]

# For Live Search, OpenRouter/Muse may expect web_search tool separately
WEB_SEARCH_TOOL = {"type": "web_search"}


def execute_tool(name: str, arguments: dict) -> str:
    logger.info("tool execute %s args=%s", name, arguments)
    if name == "get_current_time":
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_msk = now_utc.astimezone(datetime.timezone(datetime.timedelta(hours=3)))
        return f"UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')} | MSK: {now_msk.strftime('%Y-%m-%d %H:%M:%S')}"
    if name == "calculate":
        expr = arguments.get("expression", "")
        return safe_calc(expr)
    return f"Unknown tool: {name}"


# Available tool names for quick check
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}
