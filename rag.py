from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_voyageai import VoyageAIEmbeddings
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
import hashlib
import os

def get_pdf_hash(pdf_path):
    with open(pdf_path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()[:10]

def check_pdf_embeddings(pdf_path: str, embeddings: VoyageAIEmbeddings):
    pdf_index = f"indexes/index_{get_pdf_hash(pdf_path)}"
    loader = PyPDFLoader(pdf_path)
    pages = loader.load()

    if not pages or not any(page.page_content.strip() for page in pages):
        raise ValueError("PDF has no readable text content")

    if os.path.exists(pdf_index):
        print("Loading existing index...")
        vector_store = FAISS.load_local(
            pdf_index,
            embeddings,
            allow_dangerous_deserialization=True,
        )
        print("Index loaded.")
    else:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=100,
        )
        chunks = splitter.split_documents(pages)
        if not chunks:
            raise ValueError("PDF could not be split into searchable chunks")

        vector_store = FAISS.from_documents(chunks, embeddings)
        vector_store.save_local(pdf_index)
        print("Index saved.")
    return vector_store


def _extract_pages(docs) -> list[int]:
    pages = []
    for doc in docs:
        page = doc.metadata.get("page")
        if isinstance(page, int):
            pages.append(page + 1)
    return sorted(set(pages))

def format_docs(docs) -> str:
    output = ""
    for doc in docs:
        page = doc.metadata.get("page", "?")
        page_label = (page + 1) if isinstance(page, int) else "?"
        output += f"[Page {page_label}]\n{doc.page_content}\n\n"
    return output

def ask(body, sessions, api_key: str) -> tuple[str, list[int]]:
    vector_store = sessions[body.session_id].get("vector_store")
    if vector_store is None:
        raise ValueError("No vector store found for this session")

    model = ChatAnthropic(model="claude-haiku-4-5", api_key=api_key)
    parser = StrOutputParser()
    retriever = vector_store.as_retriever(search_kwargs={"k": 4})

    system_prompt = """You are a helpful CV assistant. Answer questions using ONLY the context below from the uploaded CV.

FORMATTING — always reply in clean Markdown and pick the shape that fits the question:
- **Lists** (skills, projects, jobs, education, tools): use a Markdown bullet (`-`) or numbered list with one item per line. Start with one short intro sentence, then the list.
- **Summaries & narratives** (work experience overview, background, career story): use 1–3 short paragraphs with blank lines between them.
- **Single facts** (highest qualification, years of experience, location): one concise paragraph or a single line.
- **Comparisons or multi-part answers**: brief intro, then bullets or sub-sections with `###` headings if needed.

STYLE:
- Use **bold** for names, job titles, companies, project names, and degrees.
- Use *italic* sparingly for emphasis.
- Rewrite CV content clearly; never dump raw text from the context.
- Do NOT include page numbers or `[Page x]` references — the app shows sources separately.
- If the answer is not in the context, reply exactly: I don't have info on that.

Context:
{context}"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{question}"),
    ])

    question = body.question
    source_docs = retriever.invoke(question)
    pages = _extract_pages(source_docs)
    context = format_docs(source_docs)
    answer = (prompt | model | parser).invoke({
        "context": context,
        "question": question,
    })
    return answer, pages
