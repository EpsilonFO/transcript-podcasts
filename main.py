import base64
import json
import os
import secrets
import tempfile
import time
import uuid
from pathlib import Path
from typing import Literal

import yt_dlp
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from mistralai.client import Mistral
from mistralai.client.errors.sdkerror import SDKError
from mistralai.client.models.file import File as MistralFile
from pydantic import BaseModel

API_KEY = os.environ.get("MISTRAL_API_KEY")
if not API_KEY:
    raise RuntimeError("MISTRAL_API_KEY is not set")

TRANSCRIPTION_MODEL = "voxtral-mini-latest"
LLM_MODEL = "ministral-8b-latest"
OCR_MODEL = "mistral-ocr-latest"

# Optional: which browser yt-dlp should pull cookies from (chrome, safari,
# firefox, brave, edge, opera, vivaldi, chromium). Useful when YouTube triggers
# its bot-check ("Sign in to confirm you're not a bot").
YTDLP_COOKIES_BROWSER = os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "").strip().lower()

def _parse_users() -> dict[str, str]:
    """Parse `BASIC_AUTH_USERS` (format: user1:pass1,user2:pass2,...) plus
    a single-user fallback via `BASIC_AUTH_USER` + `BASIC_AUTH_PASSWORD`.

    Returns a dict {username: password}. Empty when auth is disabled.
    """
    users: dict[str, str] = {}
    raw = os.environ.get("BASIC_AUTH_USERS", "").strip()
    if raw:
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            user, _, password = entry.partition(":")
            user = user.strip()
            if user and password:
                users[user] = password
    if not users:
        single_user = os.environ.get("BASIC_AUTH_USER", "").strip()
        single_pass = os.environ.get("BASIC_AUTH_PASSWORD", "")
        if single_user and single_pass:
            users[single_user] = single_pass
    return users


USERS = _parse_users()
ANON_USER = "_anon_"

# If the document is short enough we include it whole in chat context instead
# of just the summary. Ministral 8B has a 32k context window; keeping a wide
# margin avoids truncation issues when chat history grows.
FULL_TEXT_INLINE_THRESHOLD = 4000  # characters

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".webm", ".mp4", ".mpeg", ".mpga"}
TEXT_EXTENSIONS = {".txt", ".md"}
PDF_EXTENSIONS = {".pdf"}

DATA_DIR = Path(__file__).parent / "data"
DATA_FILE = DATA_DIR / "conversations.json"

client = Mistral(api_key=API_KEY)
app = FastAPI(title="Transcript Podcasts")


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    if not USERS:
        # No auth configured: still tag activity to a sentinel user so the
        # owner-scoped data model keeps working in local dev.
        request.state.user = ANON_USER
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            user, _, password = decoded.partition(":")
            stored = USERS.get(user)
            if stored is not None and secrets.compare_digest(password, stored):
                request.state.user = user
                return await call_next(request)
        except (ValueError, UnicodeDecodeError):
            pass
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Transcript Podcasts"'},
        content="Unauthorized",
    )


def current_user(request: Request) -> str:
    return getattr(request.state, "user", ANON_USER)


def _load_documents() -> dict[str, dict]:
    if not DATA_FILE.exists():
        return {}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_documents() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)


# Document store. Persisted to disk so past conversations survive restarts.
# Each entry: {text, summary, title, source_kind, created_at, messages}
documents: dict[str, dict] = _load_documents()


class ProcessResponse(BaseModel):
    document_id: str
    summary: str
    text_length: int
    source_kind: Literal["audio", "pdf", "text"]
    title: str


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    document_id: str
    messages: list[ChatMessage]


class ChatResponse(BaseModel):
    reply: str


class UrlRequest(BaseModel):
    url: str


class ConversationSummary(BaseModel):
    document_id: str
    title: str
    source_kind: Literal["audio", "pdf", "text"]
    created_at: float


class ConversationDetail(BaseModel):
    document_id: str
    title: str
    source_kind: Literal["audio", "pdf", "text"]
    created_at: float
    summary: str
    text_length: int
    messages: list[ChatMessage]


def extract_pdf_text(filename: str, data: bytes) -> str:
    """Run Mistral OCR on the PDF: upload → signed URL → OCR → cleanup."""
    uploaded = client.files.upload(
        file=MistralFile(fileName=filename or "document.pdf", content=data),
        purpose="ocr",
    )
    try:
        signed = client.files.get_signed_url(file_id=uploaded.id)
        result = client.ocr.process(
            model=OCR_MODEL,
            document={"type": "document_url", "document_url": signed.url},
        )
    finally:
        try:
            client.files.delete(file_id=uploaded.id)
        except SDKError:
            pass
    return "\n\n".join((page.markdown or "") for page in result.pages).strip()


def transcribe_audio(filename: str, data: bytes) -> str:
    response = client.audio.transcriptions.complete(
        model=TRANSCRIPTION_MODEL,
        file=MistralFile(fileName=filename, content=data),
    )
    return response.text or ""


def summarize(text: str) -> str:
    response = client.chat.complete(
        model=LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Tu rédiges des résumés courts et accessibles en français, pour "
                    "quelqu'un qui n'a PAS lu/écouté le contenu. Format : 2 à 3 phrases "
                    "d'introduction qui plantent le décor (de quoi ça parle, qui s'exprime, "
                    "quel angle), puis 3 à 5 bullets sur les points-clés. Pour chaque "
                    "bullet, donne assez de contexte pour qu'un lecteur extérieur "
                    "comprenne sans avoir besoin du document source — explique brièvement "
                    "les termes ou références spécifiques. Reste synthétique : "
                    "l'utilisateur peut creuser via le chat."
                ),
            },
            {"role": "user", "content": f"Voici le document à résumer :\n\n{text}"},
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content or ""


def build_chat_system_prompt(doc: dict) -> str:
    base = (
        "Tu es un assistant qui répond à des questions sur le document fourni. "
        "Réponds uniquement à partir du contenu donné ci-dessous. Si l'information "
        "n'y figure pas, dis-le clairement. Réponds en français."
    )
    if len(doc["text"]) <= FULL_TEXT_INLINE_THRESHOLD:
        return f"{base}\n\n--- DOCUMENT COMPLET ---\n{doc['text']}"
    return (
        f"{base}\n\n--- RÉSUMÉ DU DOCUMENT ---\n{doc['summary']}\n\n"
        "Le document complet est long ; appuie-toi sur le résumé ci-dessus."
    )


def derive_title(filename: str | None) -> str:
    if not filename:
        return "Document sans nom"
    return Path(filename).stem or filename


def download_audio_from_url(url: str) -> tuple[str, bytes, str]:
    """Download the audio track of a remote URL via yt-dlp.

    Returns (filename, bytes, title). Raises HTTPException on failure.
    """
    with tempfile.TemporaryDirectory() as tmp:
        opts: dict = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmp, "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "restrictfilenames": True,
        }
        if YTDLP_COOKIES_BROWSER:
            opts["cookiesfrombrowser"] = (YTDLP_COOKIES_BROWSER,)
        else:
            # Without cookies, fall back to less-aggressively rate-limited
            # YouTube clients to dodge the bot-check.
            opts["extractor_args"] = {"youtube": {"player_client": ["ios", "web"]}}

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                path = ydl.prepare_filename(info)
                title = info.get("title") or info.get("id") or "Lien"
        except yt_dlp.utils.DownloadError as e:
            msg = str(e)
            if "Sign in to confirm" in msg or "bot" in msg.lower():
                hint = (
                    " — YouTube demande une vérification anti-bot. "
                    "Définis la variable d'environnement YTDLP_COOKIES_FROM_BROWSER "
                    "(ex. 'chrome', 'safari', 'firefox', 'brave') et redémarre le serveur."
                )
                raise HTTPException(status_code=422, detail=f"Impossible de récupérer l'audio : {msg}{hint}") from e
            raise HTTPException(
                status_code=422,
                detail=f"Impossible de récupérer l'audio depuis ce lien : {msg}",
            ) from e

        if not os.path.exists(path):
            raise HTTPException(status_code=422, detail="Téléchargement vide")
        with open(path, "rb") as f:
            data = f.read()
    return os.path.basename(path), data, title


def store_document(text: str, summary: str, title: str, source_kind: str, owner: str) -> str:
    document_id = uuid.uuid4().hex
    documents[document_id] = {
        "text": text,
        "summary": summary,
        "title": title,
        "source_kind": source_kind,
        "created_at": time.time(),
        "messages": [],
        "owner": owner,
    }
    _save_documents()
    return document_id


def get_owned_doc(document_id: str, user: str) -> dict:
    doc = documents.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document inconnu")
    if doc.get("owner") != user:
        # Présenter comme 404 plutôt que 403 pour ne pas révéler l'existence.
        raise HTTPException(status_code=404, detail="Document inconnu")
    return doc


@app.post("/api/process", response_model=ProcessResponse)
async def process(file: UploadFile = File(...), user: str = Depends(current_user)):
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Fichier vide")

    suffix = Path(file.filename or "").suffix.lower()

    try:
        if suffix in AUDIO_EXTENSIONS:
            text = transcribe_audio(file.filename or "audio", raw)
            source_kind: Literal["audio", "pdf", "text"] = "audio"
        elif suffix in PDF_EXTENSIONS:
            text = extract_pdf_text(file.filename or "document.pdf", raw)
            source_kind = "pdf"
        elif suffix in TEXT_EXTENSIONS:
            text = raw.decode("utf-8", errors="replace")
            source_kind = "text"
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Extension non supportée : {suffix or '(aucune)'}",
            )

        text = text.strip()
        if not text:
            raise HTTPException(status_code=422, detail="Aucun texte extrait du fichier")

        summary = summarize(text)
    except SDKError as e:
        raise HTTPException(status_code=502, detail=f"Erreur API Mistral : {e}") from e

    title = derive_title(file.filename)
    document_id = store_document(text, summary, title, source_kind, user)

    return ProcessResponse(
        document_id=document_id,
        summary=summary,
        text_length=len(text),
        source_kind=source_kind,
        title=title,
    )


@app.post("/api/process-url", response_model=ProcessResponse)
def process_url(request: UrlRequest, user: str = Depends(current_user)):
    url = request.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL vide")

    filename, data, title = download_audio_from_url(url)

    try:
        text = transcribe_audio(filename, data).strip()
        if not text:
            raise HTTPException(status_code=422, detail="Aucune transcription produite")
        summary = summarize(text)
    except SDKError as e:
        raise HTTPException(status_code=502, detail=f"Erreur API Mistral : {e}") from e

    document_id = store_document(text, summary, title, "audio", user)
    return ProcessResponse(
        document_id=document_id,
        summary=summary,
        text_length=len(text),
        source_kind="audio",
        title=title,
    )


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, user: str = Depends(current_user)):
    doc = get_owned_doc(request.document_id, user)
    if not request.messages:
        raise HTTPException(status_code=400, detail="Aucun message")

    messages = [{"role": "system", "content": build_chat_system_prompt(doc)}]
    messages.extend({"role": m.role, "content": m.content} for m in request.messages)

    try:
        response = client.chat.complete(
            model=LLM_MODEL,
            messages=messages,
            temperature=0.3,
        )
    except SDKError as e:
        raise HTTPException(status_code=502, detail=f"Erreur API Mistral : {e}") from e
    reply = response.choices[0].message.content or ""

    doc["messages"] = [m.model_dump() for m in request.messages]
    doc["messages"].append({"role": "assistant", "content": reply})
    _save_documents()

    return ChatResponse(reply=reply)


@app.get("/api/documents", response_model=list[ConversationSummary])
def list_documents(q: str | None = None, user: str = Depends(current_user)):
    needle = (q or "").strip().lower()
    items: list[ConversationSummary] = []
    for doc_id, doc in documents.items():
        if doc.get("owner") != user:
            continue
        if needle:
            haystack = " ".join([
                doc.get("title", ""),
                doc.get("summary", ""),
                doc.get("text", ""),
            ]).lower()
            if needle not in haystack:
                continue
        items.append(
            ConversationSummary(
                document_id=doc_id,
                title=doc.get("title") or "Sans titre",
                source_kind=doc.get("source_kind", "text"),
                created_at=doc.get("created_at", 0.0),
            )
        )
    items.sort(key=lambda c: c.created_at, reverse=True)
    return items


@app.get("/api/documents/{document_id}", response_model=ConversationDetail)
def get_document(document_id: str, user: str = Depends(current_user)):
    doc = get_owned_doc(document_id, user)
    return ConversationDetail(
        document_id=document_id,
        title=doc.get("title") or "Sans titre",
        source_kind=doc.get("source_kind", "text"),
        created_at=doc.get("created_at", 0.0),
        summary=doc.get("summary", ""),
        text_length=len(doc.get("text", "")),
        messages=[ChatMessage(**m) for m in doc.get("messages", [])],
    )


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: str, user: str = Depends(current_user)):
    get_owned_doc(document_id, user)  # raises 404 if not owner
    documents.pop(document_id, None)
    _save_documents()
    return {"ok": True}


STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
