from __future__ import annotations

import html
import json
import secrets
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .release_service import ALLOWED_NOTE_KEYS, collect_release_index, publish_release
from .schemas import HealthResponse

settings = get_settings()
app = FastAPI(title=settings.app_name)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=False,
)


def is_authenticated(request: Request) -> bool:
    return request.session.get("authenticated") is True


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


def render_page(content: str, *, title: str) -> HTMLResponse:
    page = f"""<!DOCTYPE html>
<html lang="cs">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f6efe4;
      --panel: rgba(255, 252, 246, 0.92);
      --ink: #1b1916;
      --muted: #6b6258;
      --line: rgba(27, 25, 22, 0.12);
      --accent: #d96c2d;
      --accent-strong: #af4c17;
      --ok: #1f7a4c;
      --error: #9f2d22;
      --shadow: 0 24px 70px rgba(54, 37, 21, 0.14);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(217,108,45,0.18), transparent 34%),
        linear-gradient(135deg, #f4ead8 0%, #f8f4ea 42%, #efe4d2 100%);
      min-height: 100vh;
    }}
    .shell {{
      width: min(960px, calc(100vw - 32px));
      margin: 32px auto;
      padding: 28px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 28px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(2rem, 4vw, 3.4rem);
      line-height: 0.95;
      letter-spacing: -0.04em;
    }}
    p, label, li {{ font-size: 1rem; line-height: 1.5; }}
    .muted {{ color: var(--muted); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
    }}
    .field {{
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-bottom: 16px;
    }}
    input, textarea {{
      width: 100%;
      padding: 14px 16px;
      border-radius: 16px;
      border: 1px solid rgba(27, 25, 22, 0.14);
      background: rgba(255, 255, 255, 0.82);
      color: var(--ink);
      font: inherit;
    }}
    textarea {{ min-height: 180px; resize: vertical; }}
    .button-row {{
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
      margin-top: 24px;
    }}
    button, .link-button {{
      border: 0;
      border-radius: 999px;
      padding: 14px 22px;
      background: var(--accent);
      color: #fff9f3;
      cursor: pointer;
      font: inherit;
      text-decoration: none;
      transition: transform 140ms ease, background 140ms ease;
    }}
    button:hover, .link-button:hover {{
      background: var(--accent-strong);
      transform: translateY(-1px);
    }}
    .ghost {{
      background: transparent;
      color: var(--ink);
      border: 1px solid var(--line);
    }}
    .card {{
      padding: 18px;
      border-radius: 20px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.54);
      margin-top: 20px;
    }}
    .alert-ok {{
      border-color: rgba(31, 122, 76, 0.25);
      color: var(--ok);
      background: rgba(31, 122, 76, 0.08);
    }}
    .alert-error {{
      border-color: rgba(159, 45, 34, 0.25);
      color: var(--error);
      background: rgba(159, 45, 34, 0.08);
    }}
    code, pre {{
      font-family: "SFMono-Regular", Consolas, monospace;
      font-size: 0.95rem;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      margin: 0;
    }}
  </style>
</head>
<body>
  <main class="shell">
    {content}
  </main>
</body>
</html>"""
    return HTMLResponse(page)


def resolve_release_file(version: str, filename: str) -> Path:
    release_dir = (settings.release_output_root / version).resolve()
    try:
        release_dir.relative_to(settings.release_output_root)
    except ValueError as exc:
        raise FileNotFoundError("Invalid release path") from exc

    target = (release_dir / filename).resolve()
    try:
        target.relative_to(release_dir)
    except ValueError as exc:
        raise FileNotFoundError("Invalid file path") from exc

    if not target.is_file():
        raise FileNotFoundError("Release file not found")

    return target


def resolve_current_release_file(filename: str) -> Path:
    index = collect_release_index(settings)
    current = index.get("current")
    if not isinstance(current, dict) or not isinstance(current.get("version"), str):
        raise FileNotFoundError("No release is published")
    return resolve_release_file(current["version"], filename)


def render_login(error: str | None = None) -> HTMLResponse:
    error_block = (
        f'<div class="card alert-error">{html.escape(error)}</div>' if error else ""
    )
    content = f"""
      <h1>HADS Release Desk</h1>
      <p class="muted">Přihlášení do interního rozhraní pro nahrání a podepsání nové verze aplikace.</p>
      {error_block}
      <form method="post" action="/login" class="card">
        <div class="field">
          <label for="username">Uživatel</label>
          <input id="username" name="username" autocomplete="username" required>
        </div>
        <div class="field">
          <label for="password">Heslo</label>
          <input id="password" name="password" type="password" autocomplete="current-password" required>
        </div>
        <div class="button-row">
          <button type="submit">Přihlásit</button>
        </div>
      </form>
    """
    return render_page(content, title="HADS Release Desk | Login")


def render_dashboard(
    *,
    error: str | None = None,
    success: str | None = None,
    release_path: str | None = None,
    package_sha256: str | None = None,
) -> HTMLResponse:
    status_block = ""
    if error:
        status_block = f'<div class="card alert-error">{html.escape(error)}</div>'
    elif success:
        details = ""
        if release_path:
            details += f"<p><strong>Složka:</strong> <code>{html.escape(release_path)}</code></p>"
        if package_sha256:
            details += f"<p><strong>SHA-256:</strong> <code>{html.escape(package_sha256)}</code></p>"
        status_block = (
            f'<div class="card alert-ok"><p>{html.escape(success)}</p>{details}</div>'
        )

    notes_example = {
        key: [f"Sem dopln {key} změny"]
        for key in sorted(ALLOWED_NOTE_KEYS)
    }
    notes_value = html.escape(json.dumps(notes_example, ensure_ascii=False, indent=2))
    token = secrets.token_urlsafe(16)

    content = f"""
      <div class="button-row" style="justify-content: space-between; margin-top: 0;">
        <div>
          <h1>HADS Release Desk</h1>
          <p class="muted">Nahraj ZIP nové verze, vyplň metadata a backend vytvoří release složku s <code>release.json</code> a <code>release.json.sig</code>.</p>
        </div>
        <a class="link-button ghost" href="/logout">Odhlásit</a>
      </div>
      {status_block}
      <form method="post" action="/releases" enctype="multipart/form-data" class="card">
        <input type="hidden" name="csrf_token" value="{token}">
        <div class="grid">
          <div class="field">
            <label for="version">Verze</label>
            <input id="version" name="version" placeholder="1.2.3" pattern="\\d+\\.\\d+\\.\\d+" required>
          </div>
          <div class="field">
            <label for="minimum_version">Minimální verze</label>
            <input id="minimum_version" name="minimum_version" placeholder="1.2.0" pattern="\\d+\\.\\d+\\.\\d+" required>
          </div>
          <div class="field">
            <label for="release_date">Datum vydání</label>
            <input id="release_date" name="release_date" type="date" value="{date.today().isoformat()}" required>
          </div>
          <div class="field">
            <label for="package_file">ZIP balíček</label>
            <input id="package_file" name="package_file" type="file" accept=".zip" required>
          </div>
        </div>
        <div class="field">
          <label for="title">Titulek</label>
          <input id="title" name="title" placeholder="HADS 1.2.3" required>
        </div>
        <div class="field">
          <label for="summary">Krátké shrnutí</label>
          <textarea id="summary" name="summary" required></textarea>
        </div>
        <div class="field">
          <label for="notes_json">Release notes JSON</label>
          <textarea id="notes_json" name="notes_json" required>{notes_value}</textarea>
        </div>
        <div class="field">
          <label><input name="mandatory" type="checkbox" value="1"> Povinný update</label>
        </div>
        <div class="button-row">
          <button type="submit">Publikovat release</button>
          <span class="muted">Výstup jde do <code>{html.escape(str(settings.release_output_root))}</code>.</span>
        </div>
      </form>
    """
    response = render_page(content, title="HADS Release Desk")
    response.set_cookie("hads_release_csrf", token, httponly=True, samesite="lax")
    return response


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        release_output_root=str(settings.release_output_root),
    )


@app.get("/releases.json")
def releases_index(request: Request) -> JSONResponse:
    payload = collect_release_index(settings, base_url=str(request.base_url).rstrip("/"))
    return JSONResponse(payload)


@app.get("/release.json")
def current_release() -> FileResponse:
    try:
        target = resolve_current_release_file("release.json")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="No release is published")
    return FileResponse(target, media_type="application/json", headers={"Cache-Control": "no-cache"})


@app.get("/release.json.sig")
def current_release_signature() -> FileResponse:
    try:
        target = resolve_current_release_file("release.json.sig")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="No release is published")
    return FileResponse(target, media_type="text/plain", headers={"Cache-Control": "no-cache"})


@app.get("/release-files/{version}/{filename:path}")
def release_file(version: str, filename: str) -> FileResponse:
    try:
        target = resolve_release_file(version, filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Release file not found")
    return FileResponse(target)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    if not is_authenticated(request):
        return render_login()
    return render_dashboard()


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    if is_authenticated(request):
        return render_dashboard()
    return render_login()


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    username_ok = secrets.compare_digest(username, settings.admin_username)
    password_ok = secrets.compare_digest(password, settings.admin_password)
    if not (username_ok and password_ok):
        return render_login("Neplatné přihlašovací údaje.")

    request.session["authenticated"] = True
    return RedirectResponse(url="/", status_code=303)


@app.get("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    response = redirect_to_login()
    response.delete_cookie("hads_release_csrf")
    return response


@app.post("/releases", response_class=HTMLResponse)
async def create_release(
    request: Request,
    version: str = Form(...),
    release_date: str = Form(...),
    minimum_version: str = Form(...),
    mandatory: str | None = Form(default=None),
    title: str = Form(...),
    summary: str = Form(...),
    notes_json: str = Form(...),
    csrf_token: str = Form(...),
    package_file: UploadFile | None = File(default=None),
) -> HTMLResponse:
    if not is_authenticated(request):
        return render_login("Session vypršela, přihlas se znovu.")

    cookie_token = request.cookies.get("hads_release_csrf", "")
    if not secrets.compare_digest(csrf_token, cookie_token):
        return render_dashboard(error="Neplatný CSRF token, obnov stránku a zkus to znovu.")

    if package_file is None:
        return render_dashboard(error="Chybí ZIP balíček.")

    try:
        result = await publish_release(
            settings,
            version=version.strip(),
            release_date=release_date.strip(),
            minimum_version=minimum_version.strip(),
            mandatory=mandatory == "1",
            title=title,
            summary=summary,
            notes_json=notes_json,
            package_upload=package_file,
        )
    except Exception as exc:
        detail = getattr(exc, "detail", str(exc))
        return render_dashboard(error=str(detail))

    return render_dashboard(
        success=f"Release {result.version} byl úspěšně publikován.",
        release_path=str(result.folder),
        package_sha256=result.sha256,
    )
