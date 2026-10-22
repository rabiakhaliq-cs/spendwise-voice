import re
from pathlib import Path

import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route
import os
import json
import asyncio
import urllib.request
from dotenv import load_dotenv
import time
import datetime

load_dotenv()
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
AI_FIRST = os.getenv("AI_FIRST", "1") == "1"

MCP_URL = "http://127.0.0.1:8000/mcp"
pending = {"actions": None}

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

ALLOWED = {
    "add_expense": {"amount", "category", "note", "on_date"},
    "get_total": {"period", "category", "start_date", "end_date"},
    "top_category": {"period", "start_date", "end_date"},
    "compare_months": set(),
    "set_budget": {"category", "limit"},
    "get_budget_status": {"category"},
    "list_recent_expenses": {"count"},
    "edit_expense": {"find_amount", "position", "new_amount", "new_category"},
    "get_insights": set(),
    "delete_expense": {"amount", "position"},
    "add_recurring": {"amount", "category", "note", "day_of_month"},
    "list_recurring": set(),
    "remove_recurring": {"name"},
    "weekly_summary": set(),
    "confirm_recurring": {"name"},
    "skip_recurring": {"name"},
}

AI_PROMPT = """You are the language brain of a voice expense tracker. The user may speak
English, Urdu, Hindi, Roman Urdu, or a mix. Turn the sentence into actions.
Reply ONLY with JSON: {"actions": [{"tool": "...", "args": {...}}], "say": "..."}

Tools:
- add_expense: amount (number), category, note (short), on_date (optional: "yesterday" or YYYY-MM-DD;
  leave out for today). Infer the category: food, transport, bills, shopping, health, education,
  entertainment, other. Use "other" only if truly unclear.
- get_total: period (today, yesterday, week, last_week, month, last_month), category (optional).
  For any other range use start_date and end_date (YYYY-MM-DD) instead of period.
- top_category: period or start_date/end_date, same as get_total
- compare_months: no args
- set_budget: category, limit (number)
- get_budget_status: category (optional; empty = all budgets). For "how much budget is left".
- list_recent_expenses: count (default 5, max 10)
- edit_expense: find_amount (current amount) OR position (1 = last added, 2 = the one before),
  plus new_amount and/or new_category. "Wo 500 wala 600 kar do" = find_amount 500, new_amount 600.
  If the user says "that one" or "the last one", use position 1.
- get_insights: no args
- delete_expense: amount (latest expense with that amount) OR position (1 = last, 2 = the one before)
- add_recurring: amount, note (short name such as "rent" or "internet"), category, day_of_month (1-31).
  For fixed payments that repeat every month ("har mahine", "every month", "monthly").
- list_recurring: no args
- remove_recurring: name (the note or category, for example "rent")
- weekly_summary: no args. For "weekly summary", "haftay ka summary", "how was my week".
- confirm_recurring: name (optional). The user agrees to add the due recurring bills ("yes", "haan", "add them", "kar do").
- skip_recurring: name (optional). The user does not want them added this month ("skip", "is mahine nahi").

Rules:
- One sentence can produce several actions, in order.
- Convert number words to digits ("paanch sau" = 500, "do hazaar" = 2000).
- "kal" = yesterday. "pichhle hafte" = last_week. "is hafte" = week.
- If something important is missing or unclear, return no actions and put ONE short
  clarifying question in "say", in the user's language.
- If the sentence is not about expenses, return no actions and a short polite "say".
- Never invent amounts.
- A one-time payment is add_expense. A payment that repeats every month is add_recurring.
- If you need to ask a question, return an EMPTY actions list. Never add an expense and ask a question in the same reply.
- If the user does not give a category, still add the expense and infer the best category from the note; do not ask about the category.
- If a RECENT QUESTION is given below and the user answers it with yes/haan/ok, call confirm_recurring. If they decline or say skip, call skip_recurring. If they ask to remove a bill, call remove_recurring.
- The "say" field is ONLY for a clarifying question or a polite refusal. Never say in "say" that something was added or done.
- Never add an expense and ask a question in the same reply."""



def _gemini_call(model, body):
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def ai_plan(text, context=""):
    """Ask Gemini for a list of actions. Retries and falls back to other models.
    context = a recent question we asked the user (for example the recurring reminder)."""
    system_text = (
        AI_PROMPT
        + "\n\nToday is " + datetime.date.today().strftime("%A, %Y-%m-%d") + "."
        + (f"\n\nRECENT QUESTION asked to the user: {context}" if context else "")
    )
    body = {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    models = [GEMINI_MODEL] + [m.strip() for m in
                               os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]
    data = None
    for model in models:
        for attempt in range(2):
            try:
                data = _gemini_call(model, body)
                break
            except urllib.error.HTTPError as e:
                print(f"Gemini HTTP {e.code} on {model} (try {attempt + 1})")
                if e.code not in (429, 500, 503, 504):
                    break
                time.sleep(1.5)
            except Exception as e:
                print("Gemini error:", repr(e))
                time.sleep(1)
        if data:
            print("Gemini used model:", model)
            break
    if not data:
        return None

    try:
        parts = data["candidates"][0]["content"]["parts"]
        raw = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        raw = raw.strip().removeprefix("```json").removesuffix("```").strip()
        print("Gemini raw:", raw[:400])
        out = json.loads(raw)
        if isinstance(out, list):
            out = {"actions": out, "say": ""}

        actions = []
        for a in (out.get("actions") or [])[:5]:
            tool = a.get("tool")
            if tool not in ALLOWED:
                print("Dropped (unknown tool):", tool)
                continue
            args = {k: v for k, v in (a.get("args") or {}).items()
                    if k in ALLOWED[tool] and v is not None and v != ""}
            try:
                if tool == "add_expense":
                    args["amount"] = float(args["amount"])
                    args.setdefault("note", text)
                elif tool == "set_budget":
                    args["limit"] = float(args["limit"])
                elif tool == "delete_expense":
                    if "amount" in args:
                        args["amount"] = float(args["amount"])
                    if "position" in args:
                        args["position"] = int(args["position"])
                elif tool == "edit_expense":
                    for k in ("find_amount", "new_amount"):
                        if k in args:
                            args[k] = float(args[k])
                    if "position" in args:
                        args["position"] = int(args["position"])
                elif tool == "list_recent_expenses":
                    if "count" in args:
                        args["count"] = int(args["count"])
                elif tool == "add_recurring":
                    args["amount"] = float(args["amount"])
                    if "day_of_month" in args:
                        args["day_of_month"] = int(args["day_of_month"])
            except Exception as e:
                print("Dropped action:", tool, args, repr(e))
                continue
            actions.append({"tool": tool, "args": args})
        return {"actions": actions, "say": out.get("say") or ""}
    except Exception as e:
        print("Gemini parse error:", repr(e))
        return None


async def call_tool(name, args):
    async with streamablehttp_client(MCP_URL) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool(name, args)
            return result.content[0].text


YES_WORDS = {"yes", "yeah", "yep", "haan", "han", "ji", "jee", "confirm", "ok", "okay", "sure", "theek"}
NO_WORDS = {"no", "nahi", "nahin", "cancel", "nope"}


async def run_action(a, confirm=False):
    args = dict(a["args"])
    if a["tool"] == "delete_expense":
        args["confirm"] = confirm
    return await call_tool(a["tool"], args)

state = {"reminder": ""}
RECURRING_TOOLS = {"add_recurring", "confirm_recurring", "skip_recurring", "remove_recurring"}


async def get_reminder():
    try:
        text = await call_tool("recurring_reminder", {})
    except Exception:
        return ""
    return "" if text.strip() == "NONE" else text


async def reminder(request):
    state["reminder"] = await get_reminder()
    return JSONResponse({"reply": state["reminder"]})

async def chat(request):
    data = await request.json()
    text = (data.get("text") or "").strip()
    if not text:
        return JSONResponse({"reply": "I didn't catch that. Please try again.", "tool": None})

    try:
        # 1) Delete ka confirmation pending ho to pehle wahi dekho
        if pending["actions"]:
            actions = pending["actions"]
            pending["actions"] = None
            words = set(re.findall(r"[a-z]+", text.lower()))
            if words & YES_WORDS and len(words) <= 4:
                replies = [await run_action(a, confirm=True) for a in actions]
                tools = ", ".join(a["tool"] for a in actions)
                return JSONResponse({"reply": " ".join(replies),
                                     "tool": tools + " (understood by Gemini)"})
            if words & NO_WORDS and len(words) <= 3:
                return JSONResponse({"reply": "Okay, I kept your expenses.", "tool": None})
            # warna ye naya jumla hai, neeche AI ko do

        # 2) Gemini se samjho
        plan = await asyncio.to_thread(ai_plan, text, state["reminder"]) if GEMINI_KEY else None
        if plan is None:
            return JSONResponse({"reply": "I'm having trouble understanding right now. "
                                          "Please try again in a moment.", "tool": None})
        actions, say = plan["actions"], plan["say"]
        if not actions:
            return JSONResponse({"reply": say or "Sorry, I didn't get that. Could you say it again?",
                                 "tool": None})

        # 3) Delete ho to pehle confirmation
        if any(a["tool"] == "delete_expense" for a in actions):
            previews = [await run_action(a) for a in actions if a["tool"] == "delete_expense"]
            missing = [p for p in previews if p.startswith("I couldn't")]
            if missing:
                return JSONResponse({"reply": " ".join(missing), "tool": None})
            pending["actions"] = actions
            reply = "Should I delete " + " and ".join(previews) + "?"
            if any(a["tool"] != "delete_expense" for a in actions):
                reply += " I will also apply your other changes."
            return JSONResponse({"reply": reply, "tool": "delete_expense (understood by Gemini)"})

        # 4) Baaqi actions seedha chalao
        replies = [await run_action(a) for a in actions]
        tools = ", ".join(a["tool"] for a in actions)
        reply = " ".join(replies)
        if any(a["tool"] in RECURRING_TOOLS for a in actions):
            state["reminder"] = await get_reminder()
            if state["reminder"]:
                reply += " " + state["reminder"]
        return JSONResponse({"reply": reply, "tool": tools + " (understood by Gemini)"})

    except Exception as e:
        print("Chat error:", e)
        pending["actions"] = None
        return JSONResponse({"reply": "I can't reach the SpendWise server right now. "
                                      "Please check that it is running and try again.",
                             "tool": None})


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
    Route("/api/reminder", reminder),
])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5000)