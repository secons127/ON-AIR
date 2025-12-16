# app.py
# -*- coding: utf-8 -*-
from flask import Flask, render_template, abort, request, redirect, url_for, jsonify, session
import math, time, uuid, base64, os, re, json, logging
from gtts import gTTS
import requests
from copy import deepcopy
import firebase_admin
from firebase_admin import credentials, auth as firebase_auth

# ───────────────────────── Flask 초기화 ─────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static")
app.url_map.strict_slashes = False
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-onair-key")

# ───────────────────────── 로깅 ─────────────────────────
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
log = logging.getLogger("onair")

# ───────────────────────── 전역 상태 ─────────────────────────
STATE = {
    "keys": {
        # OpenAI 제거, Gemini만 사용
        "gemini": os.environ.get("GEMINI_API_KEY", "AIzaSyBQ1Vb76X_Jx2tO4LrFaDxCLmgxToayjDk").strip(),
    },
    "provider": None,
    "model": None,
    "sessions": {},
    "logs": [],
}

DEFAULT_MAX_ROUNDS = 8
MAX_CONTEXT_TURNS = 8

# ───────────────────────── LLM 설정 (Gemini만) ─────────────────────────
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
GEMINI_TIMEOUT = 30

# 부팅 시 Gemini 키가 있으면 provider 고정
if STATE["keys"].get("gemini"):
    STATE["provider"] = "gemini"
    STATE["model"] = GEMINI_MODEL

# ───────────────────────── 네비게이션 탭 ─────────────────────────
PER_PAGE = 10
POSTS = [
    {"id": 5, "pinned": True, "category": "공지", "title": "ON:AIR에 오신 것을 환영합니다 !", "author": "ON:AIR", "created_at": "25.06.11", "views": 1101},
    {"id": 4, "pinned": False, "category": "기타", "title": "으아아아아아아아아아", "author": "관리자", "created_at": "25.06.11", "views": 1101},
    {"id": 3, "pinned": False, "category": "공지", "title": "ON:AIR 커뮤니티를 시작해요 !", "author": "ON:AIR", "created_at": "25.06.11", "views": 1101},
    {"id": 2, "pinned": False, "category": "점검", "title": "서버 점검 안내 ( 2025. 07. 17 )", "author": "관리자", "created_at": "25.06.11", "views": 1101},
    {"id": 1, "pinned": False, "category": "공지", "title": "ON:AIR를 시작하는 법, 함께 대화해볼래요?", "author": "ON:AIR", "created_at": "25.06.11", "views": 1101},
]

@app.context_processor
def inject_nav():
    nav_tabs = [
        {"label": "강의실", "endpoint": "roadmap"},
        {"label": "수강 안내", "endpoint": "guide"},
        {"label": "수강 후기", "endpoint": "reviews"},
        {"label": "멤버십 안내", "endpoint": "membership"},
        {"label": "전화 시뮬레이션", "endpoint": "call_page"},
        {"label": "채팅 연습", "endpoint": "chat_page"},
    ]
    def is_active(endpoint_name: str) -> bool:
        try: return request.path.rstrip("/") == url_for(endpoint_name).rstrip("/")
        except: return False
    return dict(nav_tabs=nav_tabs, is_active=is_active)

# ───────────────────────── 기본 페이지 라우트 ─────────────────────────
@app.route("/")
def main(): return render_template("main.html")

@app.route("/login")
def login(): return render_template("login.html")

@app.route("/roadmap")
def roadmap(): return render_template("roadmap.html")

@app.route("/guide")
def guide(): return render_template("guide.html")

@app.route("/reviews")
def reviews(): return render_template("reviews.html")

@app.route("/subscribe")
def subscribe(): return "구독 기능 준비 중"

@app.route("/membership")
def membership(): return render_template("membership.html")

@app.route("/community")
def community():
    return render_template("community.html")

@app.route("/notice")
def notice():
    page = request.args.get("page", 1, type=int)
    posts_all = sorted(POSTS, key=lambda p: (not p.get("pinned", False), -p["id"]))
    total_pages = max(1, math.ceil(len(posts_all)/PER_PAGE))
    if page > total_pages: page = total_pages
    start, end = (page-1)*PER_PAGE, (page-1)*PER_PAGE+PER_PAGE
    return render_template("notice.html", posts=posts_all[start:end], page=page, total_pages=total_pages)

# ───────────────────────── 유틸 ─────────────────────────
def clean_text(text:str)->str:
    return re.sub(r"\s+"," ",(text or "")).strip()

def trim_messages(messages:list, keep_opening=True)->list:
    msgs = deepcopy(messages or [])
    if keep_opening and msgs and msgs[0].get("role")=="ai":
        return [msgs[0]] + msgs[1:][- (MAX_CONTEXT_TURNS*2):]
    return msgs[- (MAX_CONTEXT_TURNS*2):]

# ───────────────────────── 키 검증/상태 ─────────────────────────
def _test_gemini_key(key:str):
    try:
        payload={"contents":[{"role":"user","parts":[{"text":"pong?"}]}],
                 "system_instruction":{"parts":[{"text":"healthcheck"}]},
                 "generationConfig":{"maxOutputTokens":250}}
        r=requests.post(GEMINI_URL,params={"key":key},
                        headers={"Content-Type":"application/json"},
                        data=json.dumps(payload),timeout=GEMINI_TIMEOUT)
        if r.status_code!=200:
            msg=r.json().get("error",{}).get("message",f"HTTP {r.status_code}")
            return False,msg
        return True,GEMINI_MODEL
    except Exception as e: return False,f"EXC {e}"

# ───────────────────────── 키 등록 API (Gemini only) ─────────────────────────
@app.route("/api/key", methods=["POST"])
def api_set_key():
    data = request.json or {}
    raw = (data.get("api_key") or "").strip()
    if not raw:
        return jsonify({"ok": False, "reason": "empty key"}), 400

    ok, info = _test_gemini_key(raw)
    if ok:
        STATE["keys"]["gemini"] = raw
        STATE["provider"] = "gemini"
        STATE["model"] = GEMINI_MODEL
        return jsonify({"ok": True, "provider": "gemini", "model": STATE["model"]})

    return jsonify({"ok": False, "reason": info}), 400

# 상태 패널용
@app.route("/api/status")
def api_status():
    ok = bool(STATE["keys"].get("gemini"))
    return jsonify({
        "ok": ok,
        "provider": "gemini" if ok else None,
        "model": STATE.get("model"),
        "gemini_set": ok
    })

# ───────────────────────── LLM 호출 (Gemini only) ─────────────────────────
def call_gemini(messages, scenario=None, mode="chat"):
    key = STATE["keys"].get("gemini")
    if not key: return None
    try:
        sys_lines = [
            "너는 ON:AIR 콜포비아 극복 훈련에서 상담원 역할을 맡는다.",
            "반복하지 말고 간결히 1~3문장으로 답하며, 필요한 경우 질문 1개를 덧붙여라.",            
        ]
        # 고객센터형은 신원확인 1회 유도
        SERVICE_SCENARIOS = {"inquiry", "exchange", "order", "shipping", "reschedule", "shipping_delay", "consult"}
        if scenario: sys_lines.append(f"[상황: {scenario}]")
        if scenario in SERVICE_SCENARIOS:
            sys_lines.append("상황에 알맞게 고객의 정보를 한 번은 확인해야 한다. (예: 주문번호, 예약자 이름 등)")
        if scenario and scenario in SCENARIO_HINTS:
            sys_lines.append(f"[시나리오 가이드] {SCENARIO_HINTS[scenario]}")
        sys_lines.append("전화 상황: 음성 대화체로 간결히." if mode == "call" else "채팅 상황: 예의 바르고 간결히.")
        sys_text = "\n".join(sys_lines)

        trimmed = trim_messages(messages, keep_opening=True)
        contents = [{"role": "model" if m["role"] == "ai" else "user", "parts": [{"text": m["text"]}]} for m in trimmed]

        payload = {
            "contents": contents,
            "system_instruction": {"parts": [{"text": sys_text}]},
            "generationConfig": {"temperature": 0.7, "topP": 0.9, "candidateCount": 1, "maxOutputTokens": 4096}
        }
        r = requests.post(GEMINI_URL, params={"key": key}, headers={"Content-Type": "application/json"},
                          data=json.dumps(payload, ensure_ascii=False), timeout=GEMINI_TIMEOUT)
        if r.status_code != 200:
            log.warning(f"[Gemini] HTTP {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        cands = data.get("candidates") or []
        parts = (cands[0].get("content") or {}).get("parts") if cands else None
        text = (parts[0].get("text") if parts else "") or ""
        STATE["provider"] = "gemini"; STATE["model"] = GEMINI_MODEL
        return clean_text(text)
    except Exception:
        log.exception("[Gemini] Exception")
        return None

def call_llm(messages, scenario=None, mode="chat"):
    ans = call_gemini(messages, scenario, mode)
    if ans: return ans
    # 실패 시 규칙엔진 폴백
    last_user = next((m["text"] for m in reversed(messages) if m["role"] == "user"), "")
    return rule_based_next_for_messages(messages, last_user)

# ───────────────────────── 규칙엔진 (Slot 기반) ─────────────────────────
def extract_slots(text: str):
    if not text: return {}
    t = text.strip(); slots = {}
    m = re.search(r"(\d{6,})", t)
    if m: slots["order_number"] = m.group(1)
    if "미개봉" in t: slots["unopened"] = True
    elif "개봉" in t: slots["unopened"] = False
    if any(k in t for k in ["한 사이즈 작", "작게", "다운"]): slots["size_dir"] = "down"
    if any(k in t for k in ["한 사이즈 크", "크게", "업"]): slots["size_dir"] = "up"
    if any(k in t for k in ["오염","파손","불량","색상","사이즈","교환","반품","작다","크다"]): slots["issue"] = t[:80]
    m2 = re.search(r"[\"“]?([가-힣A-Za-z0-9\-\_\s]{2,20})[\"”]?", t)
    if m2:
        cand = m2.group(1).strip()
        if len(cand) >= 2 and not cand.isdigit(): slots["product"] = cand
    return slots

ASK_TEMPLATES = {
    "product": ["제품명(모델명)을 알려주시면 바로 확인해 드릴게요.", "사용하신 상품명을 한 줄로 알려주실 수 있을까요?"],
    "order_number": ["주문 번호를 알려주세요. 숫자만 주셔도 됩니다.", "확인을 위해 주문 번호가 필요합니다."],
    "issue": ["교환/문의 사유를 한 문장으로만 적어주세요.", "어떤 문제가 있었는지 간단히 설명해 주실 수 있을까요?"],
    "size_dir": ["사이즈를 한 단계 작게/크게 중 어느 쪽으로 원하시나요?", "한 사이즈 다운/업 중 어떤 걸 원하시나요?"],
    "unopened": ["상품은 미개봉 상태인가요?", "포장은 개봉하지 않으셨나요?"],
    "confirm": ["말씀 주신 내용으로 접수해도 괜찮을까요?", "위 내용대로 진행해도 될까요?"]
}

def _rotate(key, turn):
    arr = ASK_TEMPLATES.get(key, [])
    return arr[turn % len(arr)] if arr else ""

def rule_based_next(sim, last_user_text: str):
    scenario = sim.get("scenario", "exchange")
    slots = sim.setdefault("slots", {})
    slots.update(extract_slots(last_user_text))
    sim["slots"] = slots
    turn = sim.setdefault("turn", 0)

    needed = ["product", "order_number", "issue", "size_dir", "unopened"]
    missing = next((k for k in needed if k not in slots), None)
    if missing:
        prompt = _rotate(missing, turn)
        sim["turn"] = turn + 1
        return f"교환 요청 계속 도와드릴게요. {prompt}"

    summary = " / ".join([f"{k}: {v}" for k, v in slots.items()])
    sim["turn"] = turn + 1
    return f"확인 내용: {summary}. {_rotate('confirm', turn)}"

def rule_based_next_for_messages(messages, last_user_text):
    sim = {"scenario": "exchange", "slots": {}, "turn": 0}
    return rule_based_next(sim, last_user_text)

# ───────────────────────── 오프닝 템플릿 ─────────────────────────
OPENINGS = {
    "consult": {"staff": "안녕하세요, 동양병원 상담센터입니다. 예약 도와드릴까요?", "customer": "안녕하세요, 상담 예약하려고 전화드렸습니다."},
    "inquiry": {"staff": "안녕하세요, ON:AIR 고객센터입니다. 어떤 문의 주실까요?", "customer": "안녕하세요, 상품 관련 문의가 있어서 연락드렸습니다."},
    "exchange": {"staff": "네, 교환 관련 상담 도와드리겠습니다. 상품명과 주문번호가 어떻게 되시나요?", "customer": "안녕하세요, 교환 문의하려고 채팅 남깁니다."},
    "order": {"staff": "주문 제작 확인 도와드리겠습니다. 주문 번호 알려주시겠어요?", "customer": "안녕하세요, 주문 제작 확인 때문에 연락드렸습니다."},
    "shipping": {
        "staff":    "안녕하세요, ON:AIR 배송센터입니다. 주문번호나 송장번호 알려주실 수 있을까요?",
        "customer": "안녕하세요, 배송 관련해서 문의드리려고 전화했습니다."
    },
    "reschedule": {
        "staff":    "안녕하세요, 예약 변경 도와드리겠습니다. 예약자 성함과 기존 예약일자 확인해도 될까요?",
        "customer": "안녕하세요, 예약 일정을 변경하고 싶어서 연락드렸습니다."
    },
    "shipping_delay": {
        "staff":    "불편을 드려 죄송합니다. 주문번호/수령인 성함을 알려주시면 지연 사유와 예상 도착일 바로 확인해드릴게요.",
        "customer": "안녕하세요, 배송이 많이 지연되어 문의드립니다."
    },
    # 면접(채팅)
    "interview_step1": {
        "staff":    "안녕하세요, 면접 연습 Step 1(기본 질문)입니다. 간단히 자기소개부터 시작해볼까요?",
        "customer": "안녕하세요, 면접 기본 질문부터 연습해보고 싶습니다."
    },
    "interview_step2": {
        "staff":    "Step 2(자기소개·강점) 연습을 시작할게요. 60초 내 자기소개부터 부탁드립니다.",
        "customer": "자기소개와 강점 위주로 연습하고 싶습니다."
    },
    "interview_step3": {
        "staff":    "Step 3(상황 질문)입니다. 최근 갈등 상황을 어떻게 해결하셨는지 STAR 방식으로 말씀해주시겠어요?",
        "customer": "실전 상황 질문 위주로 연습해보고 싶어요."
    },
    # 일상(채팅)
    "greeting": {
        "staff":    "안녕하세요! 요즘 어떻게 지내세요? 오늘 하루 중 가장 좋았던 순간 하나만 공유해보실래요?",
        "customer": "안녕하세요!"
    },
    "make_appointment": {
        "staff":    "좋아요, 약속을 잡아볼게요. 가능한 날짜/시간/장소를 한 가지씩 말씀해주시겠어요?",
        "customer": "안녕하세요, 저 상담 예약 좀 할 수 있을까요?"
    },
    "hobby_chat": {
        "staff":    "취미 이야기 좋아요! 요즘 가장 즐겨 하시는 취미가 무엇인가요?",
        "customer": "취미 얘기하면서 자연스럽게 대화 연습해보고 싶어요."
    },
}
DEFAULT_OPENING = {"staff": "안녕하세요, 무엇을 도와드릴까요?", "customer": "안녕하세요. 상담 가능하실까요?"}

SCENARIO_HINTS = {
    # 콜/채팅 공용
    "shipping": (
        "배송 문의: 반드시 주문번호/송장번호 확인 → 택배사/배송상태/예상도착일 순서로 안내. "
        "필요 시 고객이 직접 조회할 수 있는 경로도 1줄로 제시."
    ),
    "reschedule": (
        "예약 변경: 예약자명/연락처/예약번호 중 2개 이상 확인. 가능한 시간 2~3개 제안 → 확정 멘트와 변경 요약을 마지막에 제공."
    ),
    "shipping_delay": (
        "배송 지연: 사과 멘트로 시작. 지연 사유/현재 위치/새 예상 도착일을 간결히. 필요한 경우 보상 또는 대안(부분환불/재발송/수령지 변경) 1가지 제안."
    ),
    # 면접
    "interview_step1": (
        "면접 Step1(기본 질문): 자기소개, 지원동기, 직무 이해도 등 기본 3문항을 순차적으로 묻고, "
        "각 답변 뒤에 1문장 피드백 제공. 답변은 2~4문장으로 유도."
    ),
    "interview_step2": (
        "면접 Step2(자기소개·강점): STAR 구조(S/T/A/R)로 유도. 강점 1~2개를 구체 사례와 함께 말하도록 질문. "
        "각 답변 후 요약 칭찬 1문장과 개선 팁 1문장 추가."
    ),
    "interview_step3": (
        "면접 Step3(상황 질문): 갈등 해결/실수 대응/리더십 중 하나를 선정해 심층 질문 2~3개 진행. "
        "끝에 핵심 포인트를 2줄로 정리."
    ),
    # 일상
    "greeting": (
        "일상-안부: 개방형 질문 1개 → 공감 1문장 → 되묻기 1개 흐름. 톤은 부담 없이 따뜻하게."
    ),
    "make_appointment": (
        "일상-약속: 날짜/시간/장소/목적 4요소 체크. 후보 2개 제안 후 확정 멘트와 캘린더 메모형 요약 1줄 제공."
    ),
    "hobby_chat": (
        "일상-취미: 취향 탐색 질문 2개 → 공감/확장 질문 1개 → 다음 액션(콘텐츠/모임 제안) 1개."
    ),
}

# ───────────────────────── 시뮬레이션 공통 ─────────────────────────
def simulate_send(sim, text):
    # 사용자의 한 턴 입력
    sim["messages"].append({"role": "user", "text": text})
    sim["rounds"] += 1

    # (1) 유저 턴 직후 종료 판정
    if sim["rounds"] >= sim.get("max_rounds", DEFAULT_MAX_ROUNDS):
        sim["ended"] = True
        STATE["logs"].insert(0, {
            "id": int(time.time() * 1000),
            "session_id": sim.get("session_id"),
            "topic": sim["topic"],
            "messages": trim_messages(sim["messages"], True),
            "rounds": sim["rounds"],
            "mode": sim.get("mode")
        })
        return None

    # (2) AI 응답 생성
    reply = call_llm(sim["messages"], sim.get("scenario"), sim.get("mode"))
    if not reply:
        reply = rule_based_next(sim, text)

    # (3) AI 응답 추가
    sim["messages"].append({"role": "ai", "text": reply})
    return reply

# ───────────────────────── 전화/채팅 API ─────────────────────────
@app.route("/api/call/start", methods=["POST"])
def call_start():
    data = request.json or {}
    topic = data.get("topic", "전화 훈련")
    scenario = data.get("scenario", "exchange")
    user_role = data.get("user_role", "customer")
    ai_role = "staff" if user_role == "customer" else "customer"
    opening = OPENINGS.get(scenario, DEFAULT_OPENING).get(ai_role, DEFAULT_OPENING["staff"])
    sid = uuid.uuid4().hex
    STATE["sessions"][sid] = {
        "session_id": sid,
        "topic": topic,
        "scenario": scenario,
        "user_role": user_role,
        "ai_role": ai_role,
        "mode": "call",
        "messages": [{"role": "ai", "text": opening}],
        "rounds": 0,
        "ended": False,
        "max_rounds": data.get("rounds", DEFAULT_MAX_ROUNDS),
        "slots": {},
        "turn": 0
    }
    return jsonify({"session_id": sid, "opening": opening, "ai_role": ai_role})

@app.route("/api/call/send", methods=["POST"])
def call_send():
    data = request.json or {}
    sid, text = data.get("session_id"), (data.get("text") or "").strip()
    sim = STATE["sessions"].get(sid)
    if not sim: return jsonify({"error": "session not found"}), 404
    if sim["ended"]: return jsonify({"error": "session ended"}), 400
    reply = simulate_send(sim, text)
    if sim["ended"] and reply is None:
        return jsonify({"rounds": sim["rounds"], "ended": True}), 200
    return jsonify({"reply": reply, "rounds": sim["rounds"], "ended": sim["ended"]})

@app.route("/api/chat/start", methods=["POST"])
def chat_start():
    data = request.json or {}
    topic = data.get("topic", "채팅 훈련")
    scenario = data.get("scenario", "exchange")
    user_role = data.get("user_role", "customer")
    ai_role = "staff" if user_role == "customer" else "customer"
    opening = OPENINGS.get(scenario, DEFAULT_OPENING).get(ai_role, DEFAULT_OPENING["staff"])
    sid = uuid.uuid4().hex
    STATE["sessions"][sid] = {
        "session_id": sid,
        "topic": topic,
        "scenario": scenario,
        "user_role": user_role,
        "ai_role": ai_role,
        "mode": "chat",
        "messages": [{"role": "ai", "text": opening}],
        "rounds": 0,
        "ended": False,
        "max_rounds": data.get("rounds", DEFAULT_MAX_ROUNDS),
        "slots": {},
        "turn": 0
    }
    return jsonify({"session_id": sid, "opening": opening, "ai_role": ai_role})

@app.route("/api/chat/send", methods=["POST"])
def chat_send():
    data = request.json or {}
    sid, text = data.get("session_id"), (data.get("text") or "").strip()
    sim = STATE["sessions"].get(sid)
    if not sim: return jsonify({"error": "session not found"}), 404
    if sim["ended"]: return jsonify({"error": "session ended"}), 400
    reply = simulate_send(sim, text)
    if sim["ended"] and reply is None:
        return jsonify({"rounds": sim["rounds"], "ended": True}), 200
    return jsonify({"reply": reply, "rounds": sim["rounds"], "ended": sim["ended"]})

# ───────────────────────── 로그 & TTS ─────────────────────────
@app.route("/api/logs", methods=["GET"])
def logs_api(): return jsonify(STATE["logs"])

@app.route("/api/tts", methods=["POST"])
def tts_api():
    text = (request.json or {}).get("text", "")
    if not text: return jsonify({"error": "text required"}), 400
    tts = gTTS(text=clean_text(text), lang="ko")
    fname = f"voice_{int(time.time()*1000)}.mp3"
    tts.save(fname)
    with open(fname, "rb") as f: b64 = base64.b64encode(f.read()).decode()
    try: os.remove(fname)
    except: pass
    return jsonify({"mp3_base64": b64})

# ───────────────────────── UI 라우트 ─────────────────────────
@app.route("/call")
def call_page(): return render_template("call.html", scenario=request.args.get("scenario"))

@app.route("/chat")
def chat_page(): return render_template("chat.html", scenario=request.args.get("scenario"))

@app.context_processor
def utility_processor():
    def is_active(endpoint_name): return request.endpoint == endpoint_name
    return dict(is_active=is_active)

# ───────────────────────── 피드백 페이지 및 API ─────────────────────────
def generate_feedback_with_gemini(messages: list, override_key: str = None) -> dict:
    key = override_key or STATE["keys"].get("gemini")
    if not key:
        return {"feedback": "Gemini API 키가 설정되지 않았습니다.", "score": 0}

    conversation_log = ""
    if messages and messages[0].get("role") == "ai":
        conversation_log += f"상담원: {messages[0]['text']}\n"
    for msg in messages[1:]:
        role = "상담원" if msg["role"] == "ai" else "사용자"
        conversation_log += f"{role}: {msg['text']}\n"

    system_prompt = """
    당신은 'ON:AIR' 콜포비아/채팅 연습 서비스의 대화 분석 AI입니다.
    주어진 '상담원'(AI)과 '사용자'(훈련자) 간의 대화 내용을 분석하여,
    사용자의 대화 수행 능력을 평가하고 조언을 제공해야 합니다.
    평가 항목:
    1.  **자연스러움**: 대화의 흐름이 자연스러웠는가?
    2.  **목표 달성**: 사용자가 원래 의도했던 목표(예: 교환 문의, 예약)를 명확하게 전달하고 달성했는가?
    3.  **개선점**: 더 나은 대화를 위해 사용자가 보완할 점은 무엇인가? (예: 정보 전달 순서, 명확성 등)
    분석 후, 다음 JSON 형식에 맞춰서만 응답해 주세요.
    피드백은 2~3문장으로 간결하고 친절하게 작성합니다.
    {
      "feedback": "대화에 대한 종합적인 피드백 (자연스러움, 목표 달성 여부, 핵심 개선점 1가지 포함)",
      "score": 1에서 5 사이의 정수 (별점 5점 만점).
    }
    """
    user_prompt = f"다음은 사용자와 상담원 AI 간의 대화록입니다. 이 대화를 분석하여 JSON 형식으로 피드백을 제공해 주세요:\n\n{conversation_log}"

    try:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {"temperature": 0.5, "topP": 0.9, "maxOutputTokens": 4096, "response_mime_type": "application/json"}
        }
        r = requests.post(GEMINI_URL, params={"key": key}, headers={"Content-Type": "application/json"},
                          data=json.dumps(payload, ensure_ascii=False), timeout=GEMINI_TIMEOUT)
        data = r.json()
        if r.status_code != 200:
            err = data.get("error", {}).get("message", f"HTTP {r.status_code}")
            return {"feedback": f"오류 발생: {err}", "score": 0}

        text = (data.get("candidates")[0].get("content")["parts"][0]["text"]) if data.get("candidates") else ""
        feedback_data = json.loads(text)
        return {"feedback": feedback_data.get("feedback", ""), "score": feedback_data.get("score", 0)}
    except Exception as e:
        log.exception("[FeedbackGen] Exception")
        return {"feedback": f"예외 발생: {e}", "score": 0}

@app.route("/feedback/<string:session_id>")
def feedback_page(session_id: str):
    sim = STATE["sessions"].get(session_id)
    if not sim:
        return redirect(url_for("main"))

    session_type = sim.get("mode", "chat")
    retry_url = url_for(f"{session_type}_page", scenario=sim.get("scenario"))

    return render_template(
        "feed.html",
        session_id=session_id,
        session_type=session_type,
        retry_url=retry_url,
        main_url=url_for("roadmap")
    )

@app.route("/api/feedback_data/<string:session_id>")
def get_feedback_data(session_id: str):
    override_key = request.headers.get('X-API-Key')
    sim = STATE["sessions"].get(session_id)
    if not sim: return jsonify({"error": "세션을 찾을 수 없습니다."}), 404
    messages = sim.get("messages", [])
    if not messages: return jsonify({"error": "대화 내용이 없습니다."}), 404
    feedback = generate_feedback_with_gemini(messages, override_key)
    return jsonify({"ok": True, "feedback": feedback["feedback"], "score": feedback["score"]})

# ───────────────────────── 상태 확인 ─────────────────────────
@app.route("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "provider": STATE.get("provider"),
        "model": STATE.get("model"),
        "gemini_set": bool(STATE["keys"].get("gemini"))
    })

# ───────────────────────── Firebase 로그인 세션 동기화 ─────────────────────────
if not firebase_admin._apps:
    cred_path = os.path.join(os.path.dirname(__file__), "firebase-admin-key.json")
    if os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    else:
        log.warning("⚠️ firebase-key.json not found → Firebase auth disabled")

@app.route("/api/login", methods=["POST"])
def api_login():
    try:
        data = request.get_json()
        id_token = data.get("idToken")
        if not id_token: return jsonify({"success": False, "error": "missing idToken"}), 400
        decoded = firebase_admin.auth.verify_id_token(id_token)
        uid = decoded["uid"]
        session["user"] = {"uid": uid, "email": decoded.get("email"), "name": decoded.get("name")}
        log.info(f"[LOGIN] Firebase user {uid} authenticated.")
        return jsonify({"success": True})
    except Exception as e:
        log.warning(f"[LOGIN ERROR] {e}")
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True})

@app.context_processor
def inject_user():
    return dict(user=session.get("user"))

@app.route("/logout")
def logout_page():
    session.clear()
    return redirect(url_for("main"))

# ───────────────────────── 서비스/FAQ/구독 관련 페이지 ─────────────────────────
@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/faq-index")
def faq_index():
    return render_template("faq-index.html")

@app.route("/faq-learning")
def faq_learning():
    return render_template("faq-learning.html")

@app.route("/faq-progress")
def faq_progress():
    return render_template("faq-progress.html")

@app.route("/faq-subscription")
def faq_subscription():
    return render_template("faq-subscription.html")

@app.route("/faq-video")
def faq_video():
    return render_template("faq-video.html")

@app.route("/inquiry")
def inquiry():
    return render_template("inquiry.html")

@app.route("/qna")
def qna():
    return render_template("qna.html")

@app.route("/terms")
def terms():
    return render_template("terms.html")

@app.route("/feed")
def feed_page():
    return render_template("feed.html")

# ───────────────────────── 기타 페이지 라우트 ─────────────────────────
@app.route("/community-new")
def community_new():
    return render_template("community_new.html")

@app.route("/notice-detail")
def notice_detail():
    return render_template("notice_detail.html")

@app.route("/feedback-dashboard")
def feedback_dashboard():
    return render_template("feedback_dashboard.html")

@app.route("/feedback-report")
def feedback_report():
    return render_template("feedback_report.html")

@app.route("/mypage")
def mypage():
    return render_template("mypage.html")

@app.route("/success")
def success():
    return render_template("success.html")

@app.route("/tip")
def tip():
    return render_template("tip.html")


# 고객센터 페이지 (FAQ 메인)
@app.route("/service")
def service_page():
    return render_template("faq-index.html")

# ───────────────────────── 실행부 ─────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)