import re
from pathlib import Path

import json
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

MCP_URL = "http://127.0.0.1:8000/mcp"
pending = {"delete": False}  # delete ke liye confirmation yaad rakhta hai (single-user demo)

CATS = {
    "food": ["food", "khana", "restaurant", "foodpanda", "lunch", "dinner", "grocery"],
    "transport": ["petrol", "fuel", "uber", "careem", "bus", "rickshaw", "taxi", "transport"],
    "bills": ["bill", "electricity", "gas", "internet", "rent"],
    "shopping": ["shopping", "clothes", "kapray", "daraz", "shoes"],
    "health": ["medicine", "doctor", "dawai", "hospital", "health"],
}


def find_category(t):
    for cat, words in CATS.items():
        if any(w in t for w in words):
            return cat
    return ""


def find_period(t):
    if "last month" in t or "pichhle mahine" in t or "pichle mahine" in t:
        return "last_month"
    if "today" in t or "aaj" in t:
        return "today"
    if "week" in t or "hafte" in t or "hafta" in t:
        return "week"
    return "month"


def parse(text):
    """Returns (tool_name, args, direct_reply)."""
    t = text.lower().replace(",", "").replace("khane", "khana")
    words = {"ek": "1", "do": "2", "teen": "3", "paanch": "5", "panch": "5", "das": "10",
             "hazaar": "000", "hazar": "000"}
    nums = re.findall(r"\d+(?:\.\d+)?", t)
    amount = float(nums[0]) if nums else None

    # delete confirmation ka jawab
    if pending["delete"]:
        pending["delete"] = False
        if any(w in t for w in ["yes", "yeah", "haan", "han", "confirm", "ok", "sure"]):
            return "delete_last_expense", {"confirm": True}, None
        return None, None, "Okay, I kept your expense."

    if any(w in t for w in ["delete", "remove", "hata", "undo"]):
        pending["delete"] = True
        return "delete_last_expense", {"confirm": False}, None

    if "budget" in t:
        cat = find_category(t)
        if amount and cat:
            return "set_budget", {"category": cat, "limit": amount}, None
        return None, None, "Tell me the category and amount, for example: set food budget to 10000."

    if ("compare" in t or "than last month" in t
            or "pichhle mahine se" in t or "pichle mahine se" in t):
        return "compare_months", {}, None

    if any(w in t for w in ["insight", "tips", "analy", "advice", "suggest"]):
        return "get_insights", {}, None

    if any(w in t for w in ["most", "sabse zyada", "highest", "biggest", "top category"]):
        return "top_category", {"period": find_period(t)}, None

    is_question = any(w in t for w in ["how much", "kitna", "kitne", "total", "what", "?"])

    if amount and not is_question:
        return "add_expense", {"amount": amount, "note": t}, None

    if is_question or any(w in t for w in ["kharch", "spend", "spent", "expense"]):
        args = {"period": find_period(t)}
        cat = find_category(t)
        if cat:
            args["category"] = cat
        return "get_total", args, None

    return None, None, ("Sorry, I didn't understand. Try: 'I spent 800 on food' "
                        "or 'Where did I spend the most this month?'")


async def call_tool(name, args):
    async with streamablehttp_client(MCP_URL) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool(name, args)
            return result.content[0].text


async def chat(request):
    data = await request.json()
    text = (data.get("text") or "").strip()
    if not text:
        return JSONResponse({"reply": "I didn't catch that. Please try again.", "tool": None})

    tool, args, direct = parse(text)
    if tool is None:
        return JSONResponse({"reply": direct, "tool": None})

    try:
        reply = await call_tool(tool, args)
    except Exception:
        pending["delete"] = False
        reply = ("I can't reach the SpendWise server right now. "
                 "Please check that it is running and try again.")
        return JSONResponse({"reply": reply, "tool": None})

    if tool == "delete_last_expense" and "Should I delete" not in reply:
        pending["delete"] = False
    return JSONResponse({"reply": reply, "tool": tool})


async def home(request):
    return HTMLResponse(Path("index.html").read_text(encoding="utf-8"))

async def breakdown(request):
    try:
        text = await call_tool("category_breakdown", {"period": "month"})
        return JSONResponse(json.loads(text))
    except Exception:
        return JSONResponse({})


app = Starlette(routes=[
    Route("/", home),
    Route("/api/chat", chat, methods=["POST"]),
    Route("/api/breakdown", breakdown),
])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5000)