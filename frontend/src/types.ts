// 与后端 JSON 契约对齐的类型定义（Plan §4.0 字段对照表 / §4.1 接口 / §8 前端交互）。
// 仅描述契约，不含运行期逻辑。

/* ---- 读接口 ---- */

/** 主题列表项（`GET /api/notes`，按 `updated_at` 倒序、最新在前）。 */
export interface NoteSummary {
  slug: string
  title: string
}

/** 笔记全文（`GET /api/note/{slug}`）。 */
export interface NoteDetail {
  slug: string
  title: string
  markdown: string
}

/** 概念掌握度等级（后端按 `score` 推导，单一真相源；前端不重算）。 */
export type ConceptLevel = 'unfamiliar' | 'blurry' | 'familiar' | 'mastered'

/** 概念条目（`GET /api/memory`）：`pending` 为内部字段，不返回前端。 */
export interface Concept {
  name: string
  score: number
  level: ConceptLevel
}

/** 记忆总览（`GET /api/memory`）。 */
export interface Memory {
  background: string
  concepts: Concept[]
}

/* ---- 枚举型字段 ---- */

/** 标注对象类型：`concept`=概念（词/术语） / `statement`=说法（一句话/判断）。 */
export type AnnotationKind = 'concept' | 'statement'

/** 了解深度：`brief`=简单 / `deep`=深入。 */
export type AnnotationDepth = 'brief' | 'deep'

/** 候选问题（模式B，`POST /api/optimize/annotation` 返回项）。 */
export interface CandidateQuestion {
  /** 该问题所答的片段在请求 `snippets` 中的编号（1-based）；配对一律以它为准（Plan §6.4/§8）。 */
  snippet_index: number
  text: string
  concept: string
  kind: AnnotationKind
  reason: string
}

/* ---- 请求 / 响应体 ---- */

/** `POST /api/optimize/initial` 请求（模式A）。`context`=引用片段，可选、只进 Prompt。 */
export interface OptimizeInitialRequest {
  question: string
  slug?: string
  context?: string
}

/** `POST /api/optimize/initial` 响应。 */
export interface OptimizeInitialResponse {
  optimized_question: string
  slug: string
  title: string
}

/** `POST /api/answer` 请求（模式①作答，SSE）。 */
export interface AnswerRequest {
  question: string
  slug: string
  title: string
  context?: string
}

/** `POST /api/optimize/annotation` 请求（模式B）。 */
export interface OptimizeAnnotationRequest {
  snippets: string[]
  note_markdown: string
}

/** `POST /api/optimize/annotation` 响应。 */
export interface OptimizeAnnotationResponse {
  questions: CandidateQuestion[]
}

/** `POST /api/annotate` 请求。 */
export interface AnnotateRequest {
  slug: string
  anchor: string
  concept: string
  kind: AnnotationKind
  depth: AnnotationDepth
  question?: string
}

/** `POST /api/annotate` 响应：更新后的笔记全文 + 本次解释侧锚点 id（供滚动定位并高亮）。 */
export interface AnnotateResponse {
  note_markdown: string
  anchor_id: string
}

/** 通用成功响应（`{ ok }`）。 */
export interface OkResponse {
  ok: boolean
}

/** `POST /api/memory` 请求：带 `background` 时手改并锁定。 */
export interface UpdateMemoryRequest {
  background?: string
}

/** `POST /api/memory/confirm` 响应。 */
export interface ConfirmConceptResponse {
  ok: boolean
  score: number
}

/** `POST /api/memory/refresh` 响应。 */
export interface RefreshBackgroundResponse {
  background: string
}

/* ---- SSE 事件（`POST /api/answer`，Plan §4.1 线格式）---- */

/** 正文增量。 */
export interface SseDeltaEvent {
  type: 'delta'
  text: string
}

/** 流结束：携带落盘后的笔记全文。 */
export interface SseDoneEvent {
  type: 'done'
  note_markdown: string
}

/** 出错收尾。 */
export interface SseErrorEvent {
  type: 'error'
  message: string
}

/** SSE 事件联合类型（按 `type` 分发）。 */
export type SseEvent = SseDeltaEvent | SseDoneEvent | SseErrorEvent
