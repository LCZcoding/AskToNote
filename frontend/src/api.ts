// 请求层（Plan §4.1 接口契约 / §8 前端交互）：封装 fetch + SSE 流式读取。
// dev 期由 vite proxy 把 `/api` 转发到 http://127.0.0.1:8000（vite.config.ts）。
//
// SSE 说明：`POST /api/answer` 用 `fetch` + `ReadableStream` 读取，
// **不能用 EventSource**——它只支持 GET、无法带请求体。
// 解析必须**按行缓冲**：chunk 是任意切分的字节段，先 `TextDecoder({stream:true})`
// 解码并追加到 buffer，再按 `\n\n` 切出**完整事件**后才 `JSON.parse`，
// 尾部残片留待下一 chunk，**严禁对每个 chunk 直接 parse**。

import type {
  AnnotateRequest,
  AnnotateResponse,
  AnswerRequest,
  ConfirmConceptResponse,
  Memory,
  NoteDetail,
  NoteSummary,
  OkResponse,
  OptimizeAnnotationRequest,
  OptimizeAnnotationResponse,
  OptimizeInitialRequest,
  OptimizeInitialResponse,
  RefreshBackgroundResponse,
  SseEvent,
  UpdateMemoryRequest,
} from './types'

const API_BASE = '/api'

/** 从错误响应里取后端 `detail`（FastAPI `HTTPException`）；非 JSON 则退回状态文本。 */
async function readError(resp: Response): Promise<string> {
  try {
    const data: unknown = await resp.json()
    if (data && typeof data === 'object' && 'detail' in data) {
      const detail = (data as { detail: unknown }).detail
      if (typeof detail === 'string') return detail
    }
  } catch {
    // 非 JSON 响应：忽略，退回状态文本
  }
  return `请求失败（${resp.status} ${resp.statusText}）`
}

async function getJson<TRes>(path: string): Promise<TRes> {
  const resp = await fetch(`${API_BASE}${path}`)
  if (!resp.ok) throw new Error(await readError(resp))
  return (await resp.json()) as TRes
}

async function postJson<TReq, TRes>(path: string, body: TReq): Promise<TRes> {
  const resp = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error(await readError(resp))
  return (await resp.json()) as TRes
}

/* ---- 读接口 ---- */

/** `GET /api/notes`：主题列表（按 `updated_at` 倒序、最新在前）。 */
export function getNotes(): Promise<NoteSummary[]> {
  return getJson<NoteSummary[]>('/notes')
}

/** `GET /api/note/{slug}`：笔记全文；主题不存在 → 抛错（404）。 */
export function getNote(slug: string): Promise<NoteDetail> {
  return getJson<NoteDetail>(`/note/${encodeURIComponent(slug)}`)
}

/** `GET /api/memory`：背景 + 概念列表（含等级）。 */
export function getMemory(): Promise<Memory> {
  return getJson<Memory>('/memory')
}

/* ---- 提问（模式A / 模式①） ---- */

/** `POST /api/optimize/initial`：优化初始问题（`new_topic=true` 时另命名主题，模式A）。 */
export function optimizeInitial(
  req: OptimizeInitialRequest,
): Promise<OptimizeInitialResponse> {
  return postJson<OptimizeInitialRequest, OptimizeInitialResponse>(
    '/optimize/initial',
    req,
  )
}

/** `POST /api/optimize/annotation`：标注 → 候选问题（模式B）。 */
export function optimizeAnnotation(
  req: OptimizeAnnotationRequest,
): Promise<OptimizeAnnotationResponse> {
  return postJson<OptimizeAnnotationRequest, OptimizeAnnotationResponse>(
    '/optimize/annotation',
    req,
  )
}

/** `POST /api/annotate`：标注扩写并落盘，返回更新后的笔记全文。 */
export function annotate(req: AnnotateRequest): Promise<AnnotateResponse> {
  return postJson<AnnotateRequest, AnnotateResponse>('/annotate', req)
}

/* ---- 记忆 ---- */

/** `POST /api/memory`：手动写背景（写后即锁定，自动推断不再覆盖）。 */
export function updateMemory(req: UpdateMemoryRequest): Promise<OkResponse> {
  return postJson<UpdateMemoryRequest, OkResponse>('/memory', req)
}

/** `POST /api/memory/confirm`：点「已理解」确认某概念（幂等）。 */
export function confirmConcept(concept: string): Promise<ConfirmConceptResponse> {
  return postJson<{ concept: string }, ConfirmConceptResponse>('/memory/confirm', {
    concept,
  })
}

/** `POST /api/memory/refresh`：会话开始/手动刷新背景（会话级一次）。 */
export function refreshBackground(): Promise<RefreshBackgroundResponse> {
  return postJson<Record<string, never>, RefreshBackgroundResponse>(
    '/memory/refresh',
    {},
  )
}

/* ---- 流式作答 ---- */

/** `POST /api/answer` 的回调：`delta` 累积渲染、`done` 覆盖校正、`error` 提示。 */
export interface AnswerStreamHandlers {
  onDelta?: (text: string) => void
  onDone?: (noteMarkdown: string) => void
  onError?: (message: string) => void
}

/** 解析一条完整的 SSE 事件块（已按 `\n\n` 切出），按 `type` 分发。 */
function dispatch(rawEvent: string, handlers: AnswerStreamHandlers): void {
  for (const line of rawEvent.split('\n')) {
    if (!line.startsWith('data:')) continue
    const data = line.slice('data:'.length).trim()
    if (data.length === 0) continue

    let event: SseEvent
    try {
      event = JSON.parse(data) as SseEvent
    } catch {
      // 完整事件内的坏数据行（正常不会出现）：跳过，不中断整条流
      continue
    }

    if (event.type === 'delta') handlers.onDelta?.(event.text)
    else if (event.type === 'done') handlers.onDone?.(event.note_markdown)
    else if (event.type === 'error') handlers.onError?.(event.message)
  }
}

/**
 * `POST /api/answer`：SSE 流式作答。
 *
 * - 按行缓冲解析：解码 → 追加 buffer → 按 `\n\n` 切完整事件 → 分发；
 * - 流结束后 flush 解码器与尾部残片；
 * - 非 2xx 或无可读流 → 走 `onError`。
 */
export async function streamAnswer(
  req: AnswerRequest,
  handlers: AnswerStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let resp: Response
  try {
    resp = await fetch(`${API_BASE}/answer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
      signal,
    })
  } catch (exc) {
    handlers.onError?.(exc instanceof Error ? exc.message : String(exc))
    return
  }

  if (!resp.ok) {
    handlers.onError?.(await readError(resp))
    return
  }
  if (!resp.body) {
    handlers.onError?.('响应无可读流')
    return
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    // 切出所有**完整**事件；尾部残片留在 buffer 等下一 chunk
    let sepIndex = buffer.indexOf('\n\n')
    while (sepIndex !== -1) {
      const rawEvent = buffer.slice(0, sepIndex)
      buffer = buffer.slice(sepIndex + 2)
      dispatch(rawEvent, handlers)
      sepIndex = buffer.indexOf('\n\n')
    }
  }

  // flush 解码器残余字节，并处理末尾无空行收尾的残存事件
  buffer += decoder.decode()
  if (buffer.trim().length > 0) {
    dispatch(buffer, handlers)
  }
}
