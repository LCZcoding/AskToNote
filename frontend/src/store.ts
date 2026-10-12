// 全局状态仓库（zustand，Plan §8 前端关键交互 / §8 状态机）。
//
// 设计原则：**阶段（phase）为单一真相源**——"是否正在流式"等判断由 phase 派生
// （见 `useIsStreaming`），不再单设布尔字段，避免两份状态不同步。

import { create } from 'zustand'
import type { CandidateQuestion, AnnotationDepth } from './types'

/**
 * 交互阶段（Plan §8 状态机）；同一时刻只处于一种。
 *
 * `idle` → 提交提问 → `optimizing` → 确认 → `streaming` → `done`
 *   → 标注收集完 → `optimizing` → 确认 → `annotating` → `idle`
 */
export type AppPhase =
  | 'idle' // 可提问 / 可标注
  | 'optimizing' // 模式A 出确认卡 / 模式B 拉候选问题（请求中或待用户确认）
  | 'streaming' // 模式①作答流式进行中（此期间禁止标注）
  | 'done' // 流式结束，可标注
  | 'annotating' // 标注「一次一个」串行执行中

/** 划词标注项（模式B 之前的原始片段）。 */
export interface AnnotationItem {
  id: string
  snippet: string
}

/** 候选问题（模式B 输出 + 前端补充的 `id`、用户待选 `depth` 与对应划词原文 `snippet`）。 */
export interface PendingQuestion extends CandidateQuestion {
  id: string
  /** 用户待选了解深度；`null` = 尚未选择（确认前须逐条选定）。 */
  depth: AnnotationDepth | null
  /**
   * 该候选问题对应的**划词原文**（与模式B 输入 `snippets` **按下标一一对应**）。
   * 作为 `/api/annotate` 的 `anchor` 用于笔记定位；**前端专有、非后端契约**。
   */
  snippet: string
}

/** 模式B 结果项 + 其划词原文（前端按下标 zip 得到；`store` 再补 `id` / `depth`）。 */
export type CandidateWithSnippet = CandidateQuestion & { snippet: string }

/**
 * 问题确认卡数据（Plan §8：模式A 与模式B **复用同一区域**，按 `mode` 判别）。
 * 模式B 的候选问题列表放在 `pendingQuestions`，此处只标记 mode。
 */
export type OptimizeCard =
  | { mode: 'initial'; question: string; slug: string; title: string }
  | { mode: 'annotation' }

interface StoreState {
  phase: AppPhase
  /** 当前打开主题的 slug（空串 = 未打开任何主题）。 */
  noteSlug: string
  /** 当前主题笔记全文（Markdown 源码）。 */
  noteMarkdown: string
  /** 当前确认卡；`null` = 未展示。 */
  optimizeCard: OptimizeCard | null
  /** 划词收集的标注片段（送模式B 之前）。 */
  annotations: AnnotationItem[]
  /** 模式B 产出的候选问题队列。 */
  pendingQuestions: PendingQuestion[]
  /**
   * 大块追问的引用片段（Spec FR-4a/FR-4b）。
   * 由笔记区划词（T3.4）写入、提问区（AskBar）读取并展示在输入框上方；
   * 空串 = 无引用片段。仅进 Prompt（`context`），不进 `## 问题` 标题。
   */
  pendingContext: string
  /**
   * 流式中「当前新 Q/A 块」的问题标题（Plan §6：`## {问题}`）。
   * 仅用于流式期间的**临时展示**；`done` 后被后端 `note_markdown` 覆盖校正。
   */
  streamingQuestion: string
  /**
   * 流式中「当前新 Q/A 块」的正文缓冲（逐 `delta` 追加，T2.6 打字机）。
   * **不写入 `noteMarkdown`**——避免每个 delta 重解析整篇笔记；
   * `done` 时用后端 `note_markdown` 覆盖 `noteMarkdown` 并清空本缓冲。
   */
  streamingText: string
  /**
   * 最近一次扩写成功的**解释侧锚点 id**（`anno-{n}`，空串 = 无）。
   * `NoteView` 监听它：笔记刷新后滚动到该解释并短暂高亮（Plan §8）；
   * 每条标注 id 唯一，故同一值只闪一次、无需清空。
   */
  flashAnchorId: string
}

interface StoreActions {
  // —— 笔记 ——
  /** 打开某主题（启动加载 / 切换主题）。 */
  openNote: (slug: string, markdown: string) => void
  /** 请求笔记区滚动到某解释锚点并短暂高亮（扩写成功后调用，Plan §8）。 */
  flashAnchor: (anchorId: string) => void
  /** 流式期间累加当前新 Q/A 块的正文增量（T2.6 打字机）。 */
  appendDelta: (text: string) => void

  // —— 阶段流转（Plan §8）——
  /** 发起模式A / 模式B 请求（清空旧确认卡）。 */
  startOptimize: () => void
  /** 取消确认卡，回到空闲。 */
  cancelOptimize: () => void
  /** 确认发起 `/api/answer`：进入流式，记下新块问题标题、清空正文缓冲。 */
  beginStreaming: (question: string) => void
  /** 流结束：用后端 `note_markdown` 覆盖校正，清空流式缓冲，进入可标注态。 */
  finishStreaming: (noteMarkdown: string) => void
  /** 流式出错：回到空闲、丢弃流式缓冲（错误提示由组件负责）。 */
  failStreaming: () => void
  /** 确认候选问题，进入「一次一个」串行扩写。 */
  beginAnnotating: () => void
  /** 队列清空，回到空闲并清理标注/候选。 */
  finishAnnotating: () => void

  // —— 确认卡 ——
  /** 模式A 结果：展示初始确认卡（问题可编辑、主题只读）。 */
  setInitialCard: (card: { question: string; slug: string; title: string }) => void
  /** 模式B 结果：确认区切换为候选问题列表。 */
  setAnnotationCard: () => void
  /** 编辑初始卡里优化后的问题。 */
  updateInitialQuestion: (question: string) => void

  // —— 划词标注（模式B 之前）——
  addAnnotation: (snippet: string) => void
  removeAnnotation: (id: string) => void
  clearAnnotations: () => void

  // —— 引用片段（大块追问）——
  /** 写入/清空引用片段（清空传空串）。 */
  setPendingContext: (text: string) => void

  // —— 候选问题（模式B 之后）——
  /** 整批模式B 结果：**替换**候选队列（入参按下标带上划词原文 `snippet`）。 */
  setPendingQuestions: (questions: CandidateWithSnippet[]) => void
  /** 下钻模式B 结果：**插到队列最前、优先处理**（Plan §8）。 */
  prependPendingQuestions: (questions: CandidateWithSnippet[]) => void
  updatePendingQuestion: (
    id: string,
    patch: Partial<Pick<PendingQuestion, 'text' | 'concept' | 'depth'>>,
  ) => void
  removePendingQuestion: (id: string) => void
}

export type Store = StoreState & StoreActions

/** 生成列表项 id（用于 React key 与逐项编辑）。 */
function newId(): string {
  return crypto.randomUUID()
}

export const useStore = create<Store>()((set) => ({
  // 初始态：空闲，未打开笔记，无卡、无标注、无候选。
  phase: 'idle',
  noteSlug: '',
  noteMarkdown: '',
  optimizeCard: null,
  annotations: [],
  pendingQuestions: [],
  pendingContext: '',
  streamingQuestion: '',
  streamingText: '',
  flashAnchorId: '',

  openNote: (slug, markdown) => set({ noteSlug: slug, noteMarkdown: markdown }),
  flashAnchor: (anchorId) => set({ flashAnchorId: anchorId }),
  appendDelta: (text) =>
    set((s) => ({ streamingText: s.streamingText + text })),

  startOptimize: () => set({ phase: 'optimizing', optimizeCard: null }),
  cancelOptimize: () => set({ phase: 'idle', optimizeCard: null }),
  beginStreaming: (question) =>
    set({
      phase: 'streaming',
      optimizeCard: null,
      streamingQuestion: question,
      streamingText: '',
    }),
  finishStreaming: (noteMarkdown) =>
    set({
      phase: 'done',
      noteMarkdown,
      streamingQuestion: '',
      streamingText: '',
    }),
  failStreaming: () =>
    set({ phase: 'idle', streamingQuestion: '', streamingText: '' }),
  beginAnnotating: () => set({ phase: 'annotating', optimizeCard: null }),
  finishAnnotating: () =>
    set({ phase: 'idle', annotations: [], pendingQuestions: [] }),

  setInitialCard: (card) => set({ optimizeCard: { mode: 'initial', ...card } }),
  setAnnotationCard: () => set({ optimizeCard: { mode: 'annotation' } }),
  updateInitialQuestion: (question) =>
    set((s) =>
      s.optimizeCard?.mode === 'initial'
        ? { optimizeCard: { ...s.optimizeCard, question } }
        : {},
    ),

  addAnnotation: (snippet) =>
    set((s) => ({ annotations: [...s.annotations, { id: newId(), snippet }] })),
  removeAnnotation: (id) =>
    set((s) => ({ annotations: s.annotations.filter((a) => a.id !== id) })),
  clearAnnotations: () => set({ annotations: [] }),

  setPendingContext: (text) => set({ pendingContext: text }),

  setPendingQuestions: (questions) =>
    set({
      pendingQuestions: questions.map((q) => ({
        ...q,
        id: newId(),
        depth: null,
      })),
    }),
  prependPendingQuestions: (questions) =>
    set((s) => ({
      pendingQuestions: [
        ...questions.map((q) => ({ ...q, id: newId(), depth: null })),
        ...s.pendingQuestions,
      ],
    })),
  updatePendingQuestion: (id, patch) =>
    set((s) => ({
      pendingQuestions: s.pendingQuestions.map((q) =>
        q.id === id ? { ...q, ...patch } : q,
      ),
    })),
  removePendingQuestion: (id) =>
    set((s) => ({
      pendingQuestions: s.pendingQuestions.filter((q) => q.id !== id),
    })),
}))

/** 是否正在流式作答（从 `phase` 派生，Plan §8：流式期间禁止标注）。 */
export const useIsStreaming = (): boolean =>
  useStore((s) => s.phase === 'streaming')

/**
 * 是否可划词标注。
 *
 * `idle` / `done`：正常收集标注；`annotating`：串行扩写期间的**下钻**（Plan §8
 * 「用户可在新扩写内容上继续划词」）；其余阶段（`optimizing` 拉取中/确认卡待处理、
 * `streaming` 作答中）禁用。
 */
export const useCanAnnotate = (): boolean =>
  useStore(
    (s) => s.phase === 'idle' || s.phase === 'done' || s.phase === 'annotating',
  )
