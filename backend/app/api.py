"""路由层（Plan §4.1 / §7）：

- `POST /api/answer`（T1.7）：SSE 流式作答 + 落盘；
- `GET /api/note/{slug}`、`GET /api/notes`（T1.9）：读接口；
- `POST /api/optimize/initial`（T1.10）：模式A 优化初始问题（`new_topic` 时另命名主题）；
- `POST /api/optimize/annotation`（T3.2）：模式B 标注 → 候选问题；
- `POST /api/annotate`（T3.3）：模式②标注扩写 + 落盘。

并发模型（Plan §7「接口实现约定」）：接口用**同步 `def`**，由 FastAPI 自动放入线程池，
同步 LLM/文件/DB 等待只占用工作线程、不阻塞事件循环；SSE 用 `StreamingResponse` 配
**同步生成器**，由 Starlette 逐块取数推送。
"""
import json
import re
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import memory, notes
from app.agents import question_optimizer
from app.agents.answer_note import annotate, answer_note_stream
from app.config import settings
from app.llm import DeepSeekClient, LLMClient
from app.parse import JsonParseError

router = APIRouter(prefix="/api")

# 行首标题符（`#`/`##`/…，含其后空白）；用于识别/剥离开头的重复问题标题行。
_HEADING_PREFIX_RE = re.compile(r"^#{1,6}\s*")


def get_llm() -> LLMClient:
    """LLM 依赖项（可被测试 `dependency_overrides` 替换，避免真实计费）。"""
    return DeepSeekClient(
        settings.deepseek_api_key, settings.model, settings.deepseek_base_url
    )


class AnswerRequest(BaseModel):
    """`POST /api/answer` 入参：已确认的优化问题 + 主题（slug/title 由模式A 给出）。

    `context`（Plan §4.1）：用户选中追问的一大块原文，可选；只进 Prompt，不进 `## 问题` 标题。
    """

    question: str
    slug: str
    title: str
    context: str = ""


def _sse(payload: dict) -> str:
    """序列化为 SSE 线格式：一行 `data: {JSON}` + 空行（Plan §4.1，不用 `event:` 名）。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _strip_code_fence(text: str) -> str:
    """整体被 ``` 代码围栏包裹时去掉围栏（Plan §7 步骤4）；否则原样返回。"""
    stripped = text.strip()
    if not (stripped.startswith("```") and stripped.endswith("```")):
        return text
    lines = stripped.split("\n")
    if len(lines) < 2:
        return text
    return "\n".join(lines[1:-1])


def _clean_body(raw: str, question: str) -> str:
    """正文清洗（Plan §7 步骤4）：行首标题压 `###`（围栏内除外）→ 去整体代码围栏 → 删开头重复问题标题。"""
    body = notes.compress_headings(raw).strip("\n")
    body = _strip_code_fence(body).strip("\n")
    lines = body.split("\n")
    first = lines[0].strip()
    if first.startswith("#") and _HEADING_PREFIX_RE.sub("", first).strip() == question.strip():
        body = "\n".join(lines[1:]).lstrip("\n")
    return body.strip("\n")


def _read_note_or_empty(slug: str) -> str:
    """读当前主题笔记全文；文件不存在（新主题首问）返回空串。"""
    path = notes.note_path(slug)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _persist_answer(req: AnswerRequest, body: str) -> str:
    """落盘 Q/A 区块与索引记录（Plan §7 步骤4），返回落盘后的笔记全文。

    - slug 已存在（**以 `notes` 表为准**）→ 追加到文件末尾，`title` 忽略请求值；
    - 新 slug → 先 `create_note` 写 `# {title}`，再新建 `notes` 记录（写 `created_at`）；
    - 两种情况均刷新 `updated_at`，并写 `questions(source='initial', concept=NULL)`。
    """
    existing = memory.get_note_by_slug(req.slug)
    if existing is None:
        notes.create_note(req.title, req.slug)
        note_id = memory.insert_note(req.title, req.slug, str(notes.note_path(req.slug)))
    else:
        note_id = existing["id"]

    note_markdown = notes.append_qa_block(req.slug, req.question, body)
    memory.touch_note(req.slug)
    memory.insert_question(note_id, req.question, None, "initial")
    return note_markdown


@router.post("/answer")
def answer(req: AnswerRequest, llm: LLMClient = Depends(get_llm)) -> StreamingResponse:
    """模式①作答：SSE 逐块推送 `delta`，流结束后落盘并推送 `done`（Plan §7）。"""

    def event_stream() -> Iterator[str]:
        try:
            background = memory.get_background()
            concepts_mastered, concepts_learning = memory.concepts_by_mastery()
            topics = [
                {"slug": n["slug"], "title": n["title"]}
                for n in memory.list_notes()
                if n["slug"] != req.slug
            ]
            note_markdown = _read_note_or_empty(req.slug)

            chunks: list[str] = []
            for delta in answer_note_stream(
                llm,
                title=req.title,
                slug=req.slug,
                background=background,
                concepts_mastered=concepts_mastered,
                concepts_learning=concepts_learning,
                topics=topics,
                note_markdown=note_markdown,
                question=req.question,
                context=req.context,
            ):
                chunks.append(delta)
                yield _sse({"type": "delta", "text": delta})

            body = _clean_body("".join(chunks), req.question)
            note_md = _persist_answer(req, body)
            yield _sse({"type": "done", "note_markdown": note_md})
        except Exception as exc:  # 统一以 error 事件收尾（Plan §4.1）
            yield _sse({"type": "error", "message": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/note/{slug}")
def get_note(slug: str) -> dict:
    """读某主题笔记全文（Plan §4.1）：`{slug, title, markdown}`；主题未落盘 → 404。"""
    record = memory.get_note_by_slug(slug)
    if record is None:
        raise HTTPException(status_code=404, detail=f"主题不存在：{slug}")
    try:
        markdown = notes.read_note(slug)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"笔记文件缺失：{slug}")
    return {"slug": slug, "title": record["title"], "markdown": markdown}


@router.get("/notes")
def get_notes() -> list[dict]:
    """主题列表（Plan §4.1）：`[{slug, title}]`，按 `updated_at` 倒序（最新在前）。"""
    return [{"slug": n["slug"], "title": n["title"]} for n in memory.list_notes()]


class OptimizeInitialRequest(BaseModel):
    """`POST /api/optimize/initial` 入参（Plan §4.1）：原始问题 + 是否显式新建主题。

    `context`（引用片段）可选；只进 Prompt。`new_topic=True` 表示用户**显式新建主题**
    （点「新建笔记」或无主题时首次提问），模式A 才额外命名 slug/title；**模式A 不判定主题归属**。
    """

    question: str
    context: str = ""
    new_topic: bool = False


@router.post("/optimize/initial")
def optimize_initial(
    req: OptimizeInitialRequest, llm: LLMClient = Depends(get_llm)
) -> dict:
    """模式A：**只优化问题**；`new_topic=True` 时另命名主题（Plan §7）。

    组装 = 背景 + 两类概念 + 用户问题（+ 引用片段）；命名版另加**全部**现有主题（供 slug 去重）。
    `new_topic` 时对产出 slug 做清洗 + 去重：合法化 `[a-z0-9-]`，与已有主题重复则加 `-2`/`-3` 后缀。
    LLM 输出两层兜底后仍解析失败 → `502`（前端提示）。
    """
    background = memory.get_background()
    concepts_mastered, concepts_learning = memory.concepts_by_mastery()

    if not req.new_topic:  # 只优化问题，不涉及主题归属
        try:
            result = question_optimizer.optimize_initial(
                llm,
                question=req.question,
                background=background,
                concepts_mastered=concepts_mastered,
                concepts_learning=concepts_learning,
                context=req.context,
            )
        except JsonParseError as exc:
            raise HTTPException(status_code=502, detail=f"问题优化失败：{exc}")
        return {"optimized_question": result.optimized_question}

    topics = [{"slug": n["slug"], "title": n["title"]} for n in memory.list_notes()]
    try:
        result = question_optimizer.optimize_initial(
            llm,
            question=req.question,
            background=background,
            concepts_mastered=concepts_mastered,
            concepts_learning=concepts_learning,
            context=req.context,
            new_topic=True,
            topics=topics,
        )
    except JsonParseError as exc:
        raise HTTPException(status_code=502, detail=f"问题优化失败：{exc}")

    slug = _dedupe_slug(notes.sanitize_slug(result.slug), {t["slug"] for t in topics})
    return {
        "optimized_question": result.optimized_question,
        "slug": slug,
        "title": result.title,
    }


def _dedupe_slug(base: str, taken: set[str]) -> str:
    """把 slug 与已有主题去重：重复则依次尝试 `-2`/`-3`…（Plan §7）。"""
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


class OptimizeAnnotationRequest(BaseModel):
    """`POST /api/optimize/annotation` 入参（Plan §4.1）：划词片段 + 笔记全文。"""

    snippets: list[str]
    note_markdown: str


@router.post("/optimize/annotation")
def optimize_annotation(
    req: OptimizeAnnotationRequest, llm: LLMClient = Depends(get_llm)
) -> dict:
    """模式B：为每个标注片段生成候选问题（一标注一问题、不合并，Plan §7）。

    组装 = 笔记全文 + **编号片段** + 背景 + **仅名称**概念清单（供优先复用）；
    LLM 输出两层兜底后仍解析失败 → `502`（前端提示）。
    """
    background = memory.get_background()
    concepts_existing = memory.list_concept_names()
    try:
        result = question_optimizer.optimize_annotation(
            llm,
            snippets=req.snippets,
            note_markdown=req.note_markdown,
            background=background,
            concepts_existing=concepts_existing,
        )
    except JsonParseError as exc:
        raise HTTPException(status_code=502, detail=f"候选问题生成失败：{exc}")
    return {"questions": [q.model_dump() for q in result.questions]}


class AnnotateRequest(BaseModel):
    """`POST /api/annotate` 入参（Plan §4.1）：标注片段 + 片段类型/概念/深度（均由模式B 给出）。

    `question` 可选——为空时模式② Prompt 渲染 QUESTION_NA，`questions.text` 退回存 `anchor`。
    """

    slug: str
    anchor: str
    concept: str = ""
    kind: str = "concept"
    depth: str = "brief"
    question: str = ""


@router.post("/annotate")
def annotate_route(req: AnnotateRequest, llm: LLMClient = Depends(get_llm)) -> dict:
    """模式②标注扩写 + 落盘（Plan §7）。

    顺序 = 读笔记 → 模式② → 定位插入（brief 插简释 / deep 加 `####` 章节）→
    写 `annotations` 与 `questions(source='annotation')` → 刷新 `notes.updated_at` → 返回全文。
    **M3 暂不写概念记忆**（`concepts`/`pending` 由 M4 T4.6 接入）。
    """
    record = memory.get_note_by_slug(req.slug)
    if record is None:
        raise HTTPException(status_code=404, detail=f"主题不存在：{req.slug}")
    try:
        note_markdown = notes.read_note(req.slug)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"笔记文件缺失：{req.slug}")

    background = memory.get_background()
    concepts_mastered, concepts_learning = memory.concepts_by_mastery()
    try:
        result = annotate(
            llm,
            note_markdown=note_markdown,
            anchor=req.anchor,
            kind=req.kind,
            concept=req.concept,
            depth=req.depth,
            background=background,
            concepts_mastered=concepts_mastered,
            concepts_learning=concepts_learning,
            question=req.question,
        )
    except JsonParseError as exc:
        raise HTTPException(status_code=502, detail=f"标注扩写失败：{exc}")

    new_section = result.new_section.model_dump() if result.new_section else None
    try:
        updated, anchor_id = notes.insert_annotation(
            req.slug, req.anchor, result.insert.insert_text, req.depth, new_section
        )
    except notes.AnchorNotFoundError as exc:
        raise HTTPException(status_code=422, detail=f"锚点定位失败：{exc}")

    note_id = record["id"]
    concept = req.concept or None
    memory.insert_annotation(note_id, req.anchor, concept, req.depth)
    memory.insert_question(note_id, req.question or req.anchor, concept, "annotation")
    memory.touch_note(req.slug)
    return {"note_markdown": updated, "anchor_id": anchor_id}
