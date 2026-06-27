from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from langchain_voyageai import VoyageAIEmbeddings
from dotenv import load_dotenv
from rag import check_pdf_embeddings, ask
import logging
import uuid
import os

logger = logging.getLogger(__name__)

app = FastAPI()
load_dotenv()
embeddings_key = os.getenv("EMBEDDINGS_KEY")
anthropic_key = os.getenv("ANTHROPIC_KEY")

if not embeddings_key:
    raise RuntimeError("EMBEDDINGS_KEY is not set in the environment")
if not anthropic_key:
    raise RuntimeError("ANTHROPIC_KEY is not set in the environment")

embeddings = VoyageAIEmbeddings(
    voyage_api_key=embeddings_key,
    model="voyage-3-lite"
)
# Create folders if not exist
os.makedirs("uploads", exist_ok=True)
os.makedirs("indexes", exist_ok=True)

# Templates setup
templates = Jinja2Templates(directory="templates")

# In-memory session store
sessions = {}

class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)

class ChatResponse(BaseModel):
    answer: str
    pages: list[int] | None = None

@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if file.content_type not in ("application/pdf", "application/x-pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    session_id = str(uuid.uuid4())
    file_path = f"uploads/{session_id}.pdf"

    try:
        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        with open(file_path, "wb") as f:
            f.write(contents)

        vector_embed = check_pdf_embeddings(pdf_path=file_path, embeddings=embeddings)
        os.remove(file_path)

    except HTTPException:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise
    except ValueError as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        logger.exception("Upload failed for session %s", session_id)
        raise HTTPException(status_code=500, detail="Failed to process PDF. Please try again.") from e

    sessions[session_id] = {"history": [], "vector_store": vector_embed}
    return {"session_id": session_id}

@app.post("/chat", response_model = ChatResponse)
async def chat(body : ChatRequest):
    if body.session_id not in sessions:
        raise HTTPException(status_code=400, detail="Invalid session_id")

    try:
        answer, pages = ask(body, sessions, anthropic_key)
    except Exception as e:
        logger.exception("Chat failed for session %s", body.session_id)
        raise HTTPException(
            status_code=502,
            detail="Failed to generate an answer. Please try again.",
        ) from e

    sessions[body.session_id]["history"].append(
            {"q": body.question, "a": answer}
        )
    return ChatResponse(answer=answer, pages=pages or None)

@app.get("/", response_class=HTMLResponse)
async def frontend(request: Request):
    return templates.TemplateResponse(request = request, name = "index.html", context = {"request": request})
