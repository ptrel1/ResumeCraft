"""ResumeCraft 后端入口（FastAPI）。

提供接口：
- GET  /                前端页面
- GET  /api/health      健康检查（免鉴权）
- POST /api/auth/login  用户登录
- POST /api/auth/logout 用户注销
- GET  /api/auth/status 登录状态检查
- GET  /api/templates   列出可用模板（需登录）
- POST /api/preview     传入 md → 返回渲染后的 HTML（需登录）
- POST /api/export      传入 md → 生成并下载 PDF（需登录）
- POST /api/parse       传入 md → 返回结构化 JSON（需登录）
- POST /api/pdf2html    上传 PDF → 返回提取的定位式 HTML（需登录）
"""
from __future__ import annotations

import hmac
import secrets
import tempfile
import time
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9 兼容
    import tomli as tomllib

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .exporter.pdf_exporter import html_to_pdf
from .parser.markdown_parser import parse_md
from .parser.pdf_parser import to_html as pdf_to_html
from .render.git_chart import generate_local_git_chart_svg
from .render.renderer import render_resume

# 目录定位
BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
RESUMES_DIR = BASE_DIR.parent / "data" / "resumes"
PAGES_DIR = BASE_DIR.parent / "data" / "pages"
BACKUP_DIR = BASE_DIR.parent / "backup" / "resumes"
UPLOADS_DIR = BASE_DIR.parent / "data" / "uploads"
CONFIG_PATH = BASE_DIR.parent / "config" / "config.toml"
ENV_PATH = BASE_DIR.parent / "config" / "env.toml"
SESSIONS_FILE = BASE_DIR.parent / "data" / "sessions.json"

# 确保简历、页面、数据、上传与备份目录存在
RESUMES_DIR.mkdir(parents=True, exist_ok=True)
PAGES_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)

# 自动备份记录 (stem -> last_backup_timestamp)
LAST_BACKUP_TIMES: dict[str, float] = {}
MAX_BACKUPS_PER_FILE = 30
BACKUP_INTERVAL_SECONDS = 300  # 5 分钟 (300 秒)


def _load_persisted_sessions() -> dict[str, dict]:
    """从本地文件加载持久化 Session，并剔除过期项。"""
    if not SESSIONS_FILE.exists():
        return {}
    try:
        import json
        data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        now = time.time()
        valid = {k: v for k, v in data.items() if v.get("expires_at", 0) > now}
        return valid
    except Exception:
        return {}


def _save_persisted_sessions(sessions: dict[str, dict]):
    """持久化保存 Session 到本地文件。"""
    try:
        import json
        now = time.time()
        valid = {k: v for k, v in sessions.items() if v.get("expires_at", 0) > now}
        SESSIONS_FILE.write_text(json.dumps(valid, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# 会话存储 (token -> {username, expires_at})
SESSIONS: dict[str, dict] = _load_persisted_sessions()
COOKIE_NAME = "rc_session"


def auto_backup_document(safe_name: str, new_content: str, old_content: str | None = None):
    """自动备份机制：每隔 5 分钟若内容有变动则产生一份时间戳备份，最多保留 30 份自动滚动。"""
    stem = Path(safe_name).stem
    now = time.time()

    # 如果有原文件内容且内容未发生实质变动，则不产生新备份
    if old_content is not None and old_content == new_content:
        return

    last_time = LAST_BACKUP_TIMES.get(stem, 0.0)
    if now - last_time < BACKUP_INTERVAL_SECONDS:
        return

    # 生成备份文件：文件名_年月日_时分秒.md
    time_tag = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
    backup_filename = f"{stem}_{time_tag}.md"
    backup_file = BACKUP_DIR / backup_filename
    
    # 写入本次备份
    backup_file.write_text(new_content, encoding="utf-8")
    LAST_BACKUP_TIMES[stem] = now

    # 自动滚动清理：保留最新的 MAX_BACKUPS_PER_FILE 份
    history_files = sorted(
        BACKUP_DIR.glob(f"{stem}_*.md"),
        key=lambda p: p.stat().st_mtime
    )
    if len(history_files) > MAX_BACKUPS_PER_FILE:
        for p in history_files[:-MAX_BACKUPS_PER_FILE]:
            try:
                p.unlink()
            except Exception:
                pass

app = FastAPI(title="ResumeCraft", version="0.1.1")

# 会话存储已在上方初始化：SESSIONS: dict[str, dict] = _load_persisted_sessions()
COOKIE_NAME = "rc_session"


def load_config() -> dict:
    """加载配置：优先读取 config.toml，再合并本地环境配置 env.toml。"""
    cfg = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            cfg = tomllib.load(f)
    if ENV_PATH.exists():
        with open(ENV_PATH, "rb") as f:
            env_cfg = tomllib.load(f)
            # 递归/覆盖合并
            for k, v in env_cfg.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
    return cfg


def get_current_user(request: Request) -> Optional[str]:
    """验证用户 Cookie 会话，返回用户名。若未开启 auth 则直接返回 admin。支持自动从持久化存储重新加载。"""
    global SESSIONS
    cfg = load_config()
    auth_cfg = cfg.get("auth", {})
    if not auth_cfg.get("enabled", True):
        return "admin"

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None

    # 如果内存中没有，尝试重新从持久化文件中拉取（防多 worker / 重启间隙）
    if token not in SESSIONS:
        SESSIONS = _load_persisted_sessions()

    if token not in SESSIONS:
        return None

    sess = SESSIONS[token]
    if sess.get("expires_at", 0) < time.time():
        del SESSIONS[token]
        _save_persisted_sessions(SESSIONS)
        return None

    return sess.get("username", "admin")


def require_auth(request: Request):
    """依赖注入守卫：验证当前请求是否已登录。"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
        )
    return user


class LoginRequest(BaseModel):
    username: str
    password: str


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
async def health():
    return {"status": "ok", "name": "ResumeCraft", "version": "0.1.1"}


@app.get("/api/auth/status")
async def auth_status(request: Request):
    """查询当前登录状态。"""
    cfg = load_config()
    auth_enabled = cfg.get("auth", {}).get("enabled", True)
    user = get_current_user(request)
    return {
        "ok": True,
        "auth_enabled": auth_enabled,
        "authenticated": user is not None,
        "username": user or "",
    }


@app.post("/api/auth/login")
async def login(req: LoginRequest, response: Response):
    """用户登录接口，设置会话 Cookie。"""
    cfg = load_config()
    auth_cfg = cfg.get("auth", {})
    if not auth_cfg.get("enabled", True):
        return {"ok": True, "message": "鉴权已禁用，直接通行"}

    expected_user = auth_cfg.get("username", "admin")
    # 真实密码仅从 config.toml 读取（已 gitignore），仓库默认空 → 未配置则拒绝登录
    expected_pass = auth_cfg.get("password", "")

    # 安全常数时间比对
    user_ok = hmac.compare_digest(req.username.strip(), expected_user)
    pass_ok = hmac.compare_digest(req.password.strip(), expected_pass)

    if not (user_ok and pass_ok):
        raise HTTPException(status_code=400, detail="用户名或密码错误")

    # 生成安全随机 Token
    token = secrets.token_hex(24)
    ttl_hours = cfg.get("server", {}).get("session_ttl_hours", 72)
    expires_at = time.time() + ttl_hours * 3600
    SESSIONS[token] = {
        "username": req.username,
        "expires_at": expires_at,
    }
    _save_persisted_sessions(SESSIONS)

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=int(ttl_hours * 3600),
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"ok": True, "message": "登录成功", "username": req.username}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    """注销登录，清理 Cookie 与 Session。"""
    token = request.cookies.get(COOKIE_NAME)
    if token and token in SESSIONS:
        del SESSIONS[token]
        _save_persisted_sessions(SESSIONS)
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"ok": True, "message": "已注销"}


@app.get("/view", response_class=HTMLResponse)
async def view_page():
    """独立全屏展示与分析页面。"""
    return (STATIC_DIR / "view.html").read_text(encoding="utf-8")


# ===== 免登录公开展示个人主页 (Public Profile) =====

def _is_public(content: str) -> bool:
    """通过解析 front matter 判断该简历是否被标记为公开 (public: true)。"""
    try:
        import re
        m = re.match(r"^---\s*\n([\s\S]*?)\n---", content)
        if m:
            fm = m.group(1)
            if re.search(r"^\s*public\s*:\s*(true|yes|1)\s*$", fm, re.M):
                return True
    except Exception:
        pass
    return False


@app.get("/p", response_class=HTMLResponse)
async def public_home():
    """免登录个人主页入口：默认展示第一篇已公开的简历。"""
    # 查找第一篇 public:true 的简历作为默认主页展示
    public_file = None
    for p in sorted(RESUMES_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            if _is_public(p.read_text(encoding="utf-8")):
                public_file = p.name
                break
        except Exception:
            continue
    if not public_file:
        # 无公开简历时展示一个引导页
        return HTMLResponse(
            (STATIC_DIR / "public_view.html").read_text(encoding="utf-8")
            .replace("__PUBLIC_DOC__", "")
        )
    # 否则展示选中文档
    return HTMLResponse(
        (STATIC_DIR / "public_view.html").read_text(encoding="utf-8")
        .replace("__PUBLIC_DOC__", public_file)
    )


@app.get("/p/{slug}", response_class=HTMLResponse)
async def public_profile(slug: str):
    """免登录公开访问：优先命中 data/pages/ 下的 .html 页面直接托管；否则解析公开简历(.md)。"""
    # 1. 优先托管 data/pages/ 里的 .html 页面
    page_file = PAGES_DIR / f"{Path(slug).name}.html"
    if page_file.exists() and page_file.is_file():
        return HTMLResponse(page_file.read_text(encoding="utf-8"))

    # 2. 其次解析公开简历摘要 (.md) -> 经 public.html 渲染
    # 尝试直接匹配简历文件名
    candidates = [slug + ".md", slug]
    target = None
    for cand in candidates:
        cand = Path(cand).name
        f = RESUMES_DIR / cand
        if f.exists() and f.is_file() and _is_public(f.read_text(encoding="utf-8")):
            target = f.name
            break
    if target is None:
        # 兜底：遍历查找 public:true 且 url-safe slug 匹配的简历
        for f in RESUMES_DIR.glob("*.md"):
            try:
                if _is_public(f.read_text(encoding="utf-8")):
                    stem = Path(f.stem).name
                    if stem == slug or slug in f.stem:
                        target = f.name
                        break
            except Exception:
                continue
    if target is None:
        raise HTTPException(status_code=404, detail="未找到公开页面或公开简历")
    return HTMLResponse(
        (STATIC_DIR / "public_view.html").read_text(encoding="utf-8")
        .replace("__PUBLIC_DOC__", target)
    )


@app.get("/api/public/data")
async def public_data(doc: str = ""):
    """免登录返回公开简历的结构化数据与渲染，供公开页直接展示。"""
    if not doc:
        return JSONResponse({"ok": False, "detail": "缺少 doc 参数"}, status_code=400)
    safe_name = Path(doc).name
    target = RESUMES_DIR / safe_name
    if not target.exists():
        return JSONResponse({"ok": False, "detail": "简历不存在"}, status_code=404)
    content = target.read_text(encoding="utf-8")
    if not _is_public(content):
        return JSONResponse({"ok": False, "detail": "该简历未公开，无法访问"}, status_code=403)
    try:
        resume = parse_md(content)
        html = render_resume(resume)
    except Exception as e:
        return JSONResponse({"ok": False, "detail": f"解析失败: {e}"}, status_code=400)
    return {
        "ok": True,
        "filename": safe_name,
        "content": content,
        "resume": resume.model_dump(),
        "html": html,
    }




# ===== HTML 页面托管 (data/pages/) =====

# 页面文件上传接口（鉴权）
@app.post("/api/pages/upload")
async def upload_page(file: UploadFile = File(...), _: str = Depends(require_auth)):
    """上传一个 html 页面文件到 data/pages/ 目录。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名为空")
    if not file.filename.lower().endswith(".html"):
        raise HTTPException(status_code=400, detail="仅支持 .html 文件")
    safe_name = Path(file.filename).name
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大（超过 5MB）")
    target = PAGES_DIR / safe_name
    if target.exists():
        raise HTTPException(status_code=400, detail="同名页面已存在")
    with open(target, "wb") as f:
        f.write(content)
    return {"ok": True, "filename": safe_name, "url": f"/p/{Path(safe_name).stem}", "path": str(target)}


# 页面文件列表（鉴权）
@app.get("/api/pages")
async def list_pages(_: str = Depends(require_auth)):
    """列出所有已上传的 html 页面及其绝对路径、外网访问链接。"""
    pages = []
    for p in sorted(PAGES_DIR.glob("*.html"), key=lambda x: x.stat().st_mtime, reverse=True):
        pages.append({
            "filename": p.name,
            "stem": p.stem,
            "size": p.stat().st_size,
            "path": str(p),
            "url": f"/p/{p.stem}",
            "updated_at": int(p.stat().st_mtime * 1000),
        })
    return {"pages": pages}


# 页面内容读取（供后台在线预览；鉴权）
@app.get("/api/pages/preview")
async def preview_page(file: str = "", _: str = Depends(require_auth)):
    """返回指定 html 页面内容，供后台 iframe 在线预览。"""
    if not file:
        raise HTTPException(status_code=400, detail="缺少 file 参数")
    safe_name = Path(file).name
    target = PAGES_DIR / safe_name
    if not target.exists() or not safe_name.lower().endswith(".html"):
        raise HTTPException(status_code=404, detail="页面不存在")
    return HTMLResponse(target.read_text(encoding="utf-8"))


# 页面删除（鉴权）
@app.delete("/api/pages/{filename}")
async def delete_page(filename: str, _: str = Depends(require_auth)):
    """删除指定 html 页面。"""
    safe_name = Path(filename).name
    target = PAGES_DIR / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail="页面不存在")
    target.unlink()
    return {"ok": True, "message": "页面已删除"}


# ===== 前端日志上报与遥测 =====

class ClientLogItem(BaseModel):
    level: str = "error"
    message: str
    source: Optional[str] = None
    lineno: Optional[int] = None
    colno: Optional[int] = None
    stack: Optional[str] = None
    url: Optional[str] = None
    timestamp: Optional[str] = None


@app.post("/api/client/log")
async def report_client_log(req: ClientLogItem):
    """接收前端 JS 未捕获异常、语法错误与运行时诊断日志并持久化。"""
    log_line = (
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{req.level.upper()}] "
        f"{req.message} (at {req.source or 'unknown'}:{req.lineno or 0}:{req.colno or 0}) "
        f"URL: {req.url or ''}\n"
    )
    if req.stack:
        log_line += f"Stack: {req.stack}\n"

    try:
        log_file = BASE_DIR.parent / "logs" / "client_error.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception:
        pass

    return {"ok": True}


# ===== 多平台客观指标 API =====

@app.get("/api/metrics")
async def get_metrics_api(refresh: bool = False, _: str = Depends(require_auth)):
    """获取多平台连接器客观指标数据（DeepSeek / Git / Security / CEM）。"""
    from .connectors.storage import get_cached_metrics
    data = get_cached_metrics(force_refresh=refresh)
    return {"ok": True, "metrics": data}


# ===== 简历文档库 API (RESTful) =====

class SaveDocRequest(BaseModel):
    content: str


class CreateDocRequest(BaseModel):
    filename: str
    content: Optional[str] = None


class RenameDocRequest(BaseModel):
    new_filename: str


@app.get("/api/documents")
async def list_documents(_: str = Depends(require_auth)):
    """获取所有已保存的 Markdown 简历列表（含解析出的姓名、方向、字数、修改时间）。"""
    docs = []
    for p in sorted(RESUMES_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        content = p.read_text(encoding="utf-8")
        try:
            resume = parse_md(content)
            name = resume.contact.name or p.stem
            role = resume.contact.role or ""
            template = resume.meta.template or "minimal"
        except Exception as e:
            name = p.stem
            role = ""
            template = "minimal"
            
        docs.append({
            "filename": p.name,
            "title": p.stem,
            "name": name,
            "role": role,
            "template": template,
            "size": len(content),
            "public": _is_public(content),
            "updated_at": int(p.stat().st_mtime * 1000),
        })
    return {"documents": docs}


@app.get("/api/documents/{filename}/status")
async def get_document_status(filename: str, _: str = Depends(require_auth)):
    """轻量获取指定简历文件的最新修改时间与内容哈希（供前端热重载与变更感知）。"""
    safe_name = Path(filename).name
    target = RESUMES_DIR / safe_name
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="简历文档不存在")
    
    stat = target.stat()
    mtime = int(stat.st_mtime * 1000)
    size = stat.st_size
    # 简易高速版本标记 (mtime + size)
    version_tag = f"{mtime}_{size}"
    return {
        "ok": True,
        "filename": safe_name,
        "updated_at": mtime,
        "size": size,
        "version_tag": version_tag,
    }


@app.get("/api/documents/{filename}")
async def get_document(filename: str, _: str = Depends(require_auth)):
    """读取指定 Markdown 文档。"""
    # 路径穿越防御
    safe_name = Path(filename).name
    target = RESUMES_DIR / safe_name
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="简历文档不存在")
    content = target.read_text(encoding="utf-8")
    stat = target.stat()
    mtime = int(stat.st_mtime * 1000)
    return {
        "filename": safe_name,
        "content": content,
        "updated_at": mtime,
        "version_tag": f"{mtime}_{stat.st_size}",
    }


@app.post("/api/documents/{filename}")
async def save_document(filename: str, req: SaveDocRequest, _: str = Depends(require_auth)):
    """保存或覆盖指定的 Markdown 文档，并在有实质变动且满足 5 分钟间隔时触发自动滚动备份。"""
    safe_name = Path(filename).name
    if not safe_name.endswith(".md"):
        safe_name += ".md"
    target = RESUMES_DIR / safe_name
    old_content = target.read_text(encoding="utf-8") if target.exists() else None
    
    # 自动备份
    try:
        auto_backup_document(safe_name, req.content, old_content)
    except Exception:
        pass

    target.write_text(req.content, encoding="utf-8")
    return {"ok": True, "filename": safe_name, "message": "保存成功"}


@app.post("/api/documents/{filename}/rename")
async def rename_document(filename: str, req: RenameDocRequest, _: str = Depends(require_auth)):
    """重命名指定的 Markdown 简历文件。"""
    old_safe = Path(filename).name
    new_safe = Path(req.new_filename).name
    if not new_safe.endswith(".md"):
        new_safe += ".md"

    old_target = RESUMES_DIR / old_safe
    new_target = RESUMES_DIR / new_safe

    if not old_target.exists():
        raise HTTPException(status_code=404, detail="原文件不存在")
    if new_target.exists() and new_safe != old_safe:
        raise HTTPException(status_code=400, detail="目标同名简历已存在")

    old_target.rename(new_target)
    return {"ok": True, "old_filename": old_safe, "new_filename": new_safe, "message": "重命名成功"}


@app.post("/api/documents/{filename}/copy")
async def copy_document(filename: str, _: str = Depends(require_auth)):
    """复制一份指定的 Markdown 简历副本。"""
    old_safe = Path(filename).name
    old_target = RESUMES_DIR / old_safe
    if not old_target.exists():
        raise HTTPException(status_code=404, detail="原文件不存在")

    stem = old_target.stem
    content = old_target.read_text(encoding="utf-8")
    
    # 自动推导不冲突的副本文件名
    idx = 1
    new_safe = f"{stem}_副本.md"
    while (RESUMES_DIR / new_safe).exists():
        idx += 1
        new_safe = f"{stem}_副本{idx}.md"

    (RESUMES_DIR / new_safe).write_text(content, encoding="utf-8")
    return {"ok": True, "new_filename": new_safe, "message": "复制成功"}


@app.post("/api/documents_new")
async def create_document(req: CreateDocRequest, _: str = Depends(require_auth)):
    """新建 Markdown 简历。"""
    safe_name = Path(req.filename).name
    if not safe_name.endswith(".md"):
        safe_name += ".md"
    target = RESUMES_DIR / safe_name
    if target.exists():
        raise HTTPException(status_code=400, detail="同名简历已存在")
    
    default_content = req.content or (
        "---\n"
        "template: minimal\n"
        "layout: full\n"
        "theme:\n"
        '  accent: "#111827"\n'
        "---\n\n"
        "# 我的名字\n\n"
        "**求职方向**：某某岗位 · 某某方向\n"
        "**所在地**：城市\n"
        "**电话**：138-0000-0000\n"
        "**邮箱**：email@example.com\n\n"
        "> 个人亮点与优势总结。\n\n"
        "## 工作经历\n\n"
        "### 某某公司 · 2023.01 - 至今\n"
        "**岗位名称**\n\n"
        "- **核心业绩 1**：具体描述与数据成果\n\n"
        "## 专业技能\n\n"
        "- **技能大类**：技能点 1 / 技能点 2\n"
    )
    target.write_text(default_content, encoding="utf-8")
    return {"ok": True, "filename": safe_name, "content": default_content}


@app.delete("/api/documents/{filename}")
async def delete_document(filename: str, _: str = Depends(require_auth)):
    """删除指定的 Markdown 简历。"""
    safe_name = Path(filename).name
    target = RESUMES_DIR / safe_name
    if target.exists():
        target.unlink()
        return {"ok": True, "message": "文档已删除"}
    raise HTTPException(status_code=404, detail="文件不存在")


@app.get("/api/git/chart.svg")
async def git_chart_svg():
    """动态聚合本机全部真实 Git 仓库（CEM、ServerDefender、DDNS 等）生成的贡献热力图 SVG。"""
    svg = generate_local_git_chart_svg()
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/api/git/data")
async def git_chart_data():
    """返回 Git 贡献日历的结构化数据（JSON），供编辑页导出/分析。数据源与热力图一致。"""
    from .render.git_chart import get_git_calendar_data
    data = get_git_calendar_data()
    return JSONResponse(data)


@app.get("/api/templates")
async def list_templates(_: str = Depends(require_auth)):
    """列出可选模板（排除 base.html 基础模板）。"""
    return {"templates": sorted(
        p.stem for p in TEMPLATES_DIR.glob("*.html") if p.stem != "base"
    )}


@app.post("/api/preview")
async def preview(request: Request, _: str = Depends(require_auth)):
    """md → 渲染后的完整简历 HTML。body 直接传 md 文本。"""
    md = (await request.body()).decode("utf-8")
    try:
        resume = parse_md(md)
        return HTMLResponse(render_resume(resume))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析/渲染失败: {e}")


@app.post("/api/parse")
async def parse(request: Request, _: str = Depends(require_auth)):
    """md → 结构化 JSON（调试用）。"""
    md = (await request.body()).decode("utf-8")
    try:
        resume = parse_md(md)
        return resume.model_dump()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析失败: {e}")


@app.post("/api/export")
async def export(request: Request, _: str = Depends(require_auth)):
    """md → 生成 PDF 并返回下载。playwright 用同步 API，在 async 端点中直接调用。"""
    md = (await request.body()).decode("utf-8")
    cfg = load_config()
    pdf_cfg = cfg.get("pdf", {})
    try:
        resume = parse_md(md)
        html_content = render_resume(resume)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析/渲染失败: {e}")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await html_to_pdf(
            html_content, tmp_path,
            page_size=pdf_cfg.get("page_size", "A4"),
            margin_mm=pdf_cfg.get("margin", 10),
            print_background=pdf_cfg.get("print_background", True),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF 导出失败: {e}")

    fname = f"resume_{resume.contact.name or 'export'}.pdf"
    return FileResponse(tmp_path, media_type="application/pdf", filename=fname)


@app.post("/api/pdf2html")
async def pdf2html(file: UploadFile = File(...), _: str = Depends(require_auth)):
    """上传 PDF → 返回提取的定位式 HTML。"""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        html = pdf_to_html(tmp_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF 提取失败: {e}")
    return HTMLResponse(html)


@app.post("/api/upload/image")
async def upload_image(file: UploadFile = File(...), _: str = Depends(require_auth)):
    """上传简历照片/配图（支持 jpg/png/webp/jpeg/gif，最大 5MB）。"""
    allowed_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    suffix = Path(file.filename or "image.png").suffix.lower()
    if suffix not in allowed_exts:
        raise HTTPException(status_code=400, detail=f"不支持的图片格式（仅支持 {', '.join(allowed_exts)}）")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片大小不能超过 5MB")

    # 生成带时间戳与随机后缀的安全文件名
    safe_filename = f"img_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}{suffix}"
    target_path = UPLOADS_DIR / safe_filename
    target_path.write_bytes(content)

    url = f"/uploads/{safe_filename}"
    return {"ok": True, "url": url, "filename": safe_filename}


# 挂载静态资源（前端 js/css 与用户上传图片）
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
