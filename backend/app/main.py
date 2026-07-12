from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
import os
import json
import requests
from dotenv import load_dotenv

# 可选：尝试加载 OpenAI SDK，失败自动走 requests 兜底（龙芯常见场景）
_HAS_OPENAI_SDK = False
try:
    from openai import OpenAI  # type: ignore
    _HAS_OPENAI_SDK = True
except Exception:  # pragma: no cover - 龙芯架构无预编译 wheel 时会走到这里
    OpenAI = None  # type: ignore
    _HAS_OPENAI_SDK = False

# 加载环境变量（多路径兼容：deploy/venv 场景 + 普通场景）
_env_paths = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), ".env"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
    os.path.abspath(".env"),
]
for _p in _env_paths:
    if os.path.exists(_p):
        load_dotenv(_p)
        break
else:
    load_dotenv()

app = FastAPI()

from .database import database_health, init_database
from .routers.documents import router as documents_router
from .routers.search import router as search_router
from .routers.rag import router as rag_router
from .routers.images import router as images_router


@app.on_event("startup")
def startup_database():
    init_database()


app.include_router(documents_router)
app.include_router(search_router)
app.include_router(rag_router)
app.include_router(images_router)

# CORS 从环境变量读取
cors_origins = os.getenv("CORS_ORIGINS", '["http://localhost:8000"]')
app.add_middleware(
    CORSMiddleware,
    allow_origins=json.loads(cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== 双模式 LLM 客户端初始化（SDK 可用时用 SDK，否则走 requests） =====
LLM_BACKEND = os.getenv("LLM_BACKEND", "longcat")

def _build_llm_ctx():
    if LLM_BACKEND == "ollama":
        base_url = os.getenv("OLLAMA_API_URL", "http://localhost:11434/v1")
        api_key = "ollama"
        model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    elif os.getenv("QWEN_API_KEY") or LLM_BACKEND == "qwen":
        base_url = os.getenv("QWEN_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        api_key = os.getenv("QWEN_API_KEY", "")
        model = os.getenv("QWEN_TEXT_MODEL") or os.getenv("QWEN_MODEL", "qwen-plus")
    else:
        base_url = os.getenv("LONGCAT_API_URL", "https://api.longcat.chat/openai/v1")
        api_key = os.getenv("LONGCAT_API_KEY", "")
        model = os.getenv("LONGCAT_MODEL", "LongCat-2.0")
    temperature = float(os.getenv("LLM_TEMPERATURE", "0.3"))
    return base_url, api_key, model, temperature

_LLM_BASE, _LLM_KEY, LLM_MODEL, LLM_TEMPERATURE = _build_llm_ctx()

client = None
if _HAS_OPENAI_SDK and OpenAI is not None:
    try:
        client = OpenAI(base_url=_LLM_BASE, api_key=_LLM_KEY)
    except Exception:
        client = None

from .users import verify_user


class LoginForm(BaseModel):
    username: str
    password: str


class AIRequest(BaseModel):
    text: str


# ===== 健康检查端点 =====
@app.get("/health")
def health():
    """存活检查"""
    return {"status": "ok"}


@app.get("/health/ready")
def readiness():
    """就绪检查：验证 LLM 后端可达"""
    db_check = database_health()
    checks = {"api": True, "database": db_check["ok"], "llm": False}
    try:
        if LLM_BACKEND == "ollama":
            base = os.getenv("OLLAMA_API_URL", "http://localhost:11434/v1").replace("/v1", "")
            resp = requests.get(f"{base}/api/tags", timeout=5)
            checks["llm"] = resp.status_code == 200
        else:
            checks["llm"] = bool(os.getenv("QWEN_API_KEY") or os.getenv("LONGCAT_API_KEY"))
    except Exception:
        pass
    all_ready = all(checks.values())
    return {
        "status": "ready" if all_ready else "not_ready",
        "checks": checks,
        "database": db_check,
    }


def _llm_chat_request(prompt: str, user_text: str):
    """统一的 LLM 调用：优先 SDK，再走 requests（龙芯无 openai SDK 时兜底）"""
    messages = [
        {"role": "system", "content": "你是工业设备检修专家"},
        {"role": "user", "content": prompt},
    ]
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": LLM_TEMPERATURE,
    }
    # 通道1：SDK（x86 / 装了 openai 包的场景）
    if client is not None:
        try:
            resp = client.chat.completions.create(**payload)  # type: ignore[union-attr]
            return resp.choices[0].message.content, "openai-sdk"
        except Exception:
            pass
    # 通道2：requests（龙芯通用兜底 / SDK 失败二次尝试）
    base = _LLM_BASE.rstrip("/")
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {_LLM_KEY}", "Content-Type": "application/json"}
    resp = requests.post(url, json=payload, headers=headers, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"], "requests-fallback"


# ===== 业务 API =====
@app.post("/api/login")
def login(form: LoginForm):
    user_info = verify_user(form.username, form.password)
    if not user_info:
        return {"code": 400, "msg": "账号或密码错误"}
    return {
        "code": 200,
        "token": "mock-token-123",
        "user": user_info
    }


@app.post("/api/ai/ask")
def ai_ask(req: AIRequest):
    prompt = f"""
你是工业设备检修AI，请按结构回答：

【故障现象】
【原因分析】
【处理步骤】
【风险提示】

用户问题：
{req.text}
"""
    try:
        result, via = _llm_chat_request(prompt, req.text)
        return {"code": 200, "data": result, "llm_via": via}
    except Exception as e:
        return {"code": 500, "msg": str(e), "llm_via": "error"}


@app.get("/api/hello")
def hello():
    llm_via = "openai-sdk" if (client is not None) else "requests-fallback"
    return {
        "code": 200,
        "msg": "success",
        "data": "设备检修AI系统 FastAPI后端服务运行正常",
        "llm_backend": LLM_BACKEND,
        "llm_via": llm_via,
        "llm_model": LLM_MODEL,
        "has_openai_sdk": _HAS_OPENAI_SDK,
    }


# ===== 前端静态文件托管（FastAPI 一体化部署，无需 Nginx 也能打开前端） =====
def _find_frontend_dist() -> str | None:
    """兼容两种场景：deploy 目录结构 / x86 本地开发目录结构"""
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
    candidates = [
        os.path.join(os.path.dirname(backend_dir), "frontend", "dist"),  # deploy: deploy/frontend/dist
        os.path.join(os.path.dirname(backend_dir), "..", "frontend", "dist"),  # local: project/frontend/dist
        os.path.join(backend_dir, "frontend", "dist"),
        os.path.abspath(os.path.join("frontend", "dist")),
    ]
    for c in candidates:
        norm = os.path.normpath(c)
        if os.path.isdir(norm) and os.path.exists(os.path.join(norm, "index.html")):
            return norm
    return None


frontend_dist = _find_frontend_dist()
if frontend_dist:
    # 静态资源 /assets/* 直接走文件系统
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(frontend_dist, "assets")),
        name="frontend-assets",
    )

    # 图标、SVG、favicon 等根目录下的静态文件
    for static_name in ["favicon.svg", "icons.svg"]:
        _fp = os.path.join(frontend_dist, static_name)
        if os.path.exists(_fp):
            @app.get(f"/{static_name}", name=f"static-{static_name}")
            def _serve_static(fp=_fp):
                return FileResponse(fp)

    _index_html_path = os.path.join(frontend_dist, "index.html")

    @app.get("/", response_class=HTMLResponse)
    def serve_index():
        return FileResponse(_index_html_path)

    # SPA 兜底：所有 /api 之外未匹配的 GET 请求都返回 index.html（否则 /home /search 刷新 404）
    @app.api_route("/{path_name:path}", methods=["GET"])
    async def spa_catch_all(request: Request, path_name: str):
        if path_name.startswith("api") or path_name.startswith("health") or path_name.startswith("docs") or path_name.startswith("openapi.json") or path_name.startswith("redoc"):
            return {"detail": "Not Found"}
        if path_name.startswith("assets/") or path_name in ("favicon.svg", "icons.svg"):
            return {"detail": "Not Found"}
        return FileResponse(_index_html_path)
else:
    @app.get("/")
    def no_frontend_tip():
        return {
            "msg": "前端 dist 未找到，仅后端 API 可用",
            "tip": "在 deploy 目录结构下，前端路径应为 ../frontend/dist（相对于 backend/app/main.py）",
            "frontend_paths_checked": [
                os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend", "dist")),
                os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "frontend", "dist")),
            ],
        }
