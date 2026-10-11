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

/** 候选问题（模式B 输出 + 前端补充的 `id` 与用户待选 `depth`）。 */
export interface PendingQuestion extends CandidateQuestion {
  id: string
  /** 用户待选了解深度；`null` = 尚未选择（确认前须逐条选定）。 */
  depth: AnnotationDepth | null
}

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
}

interface StoreActions {
  // —— 笔记 ——
  /** 打开某主题（启动加载 / 切换主题）。 */
  openNote: (slug: string, markdown: string) => void
  /** 流式期间累加正文增量（T2.6 打字机）。 */
  appendDelta: (text: string) => void

  // —— 阶段流转（Plan §8）——
  /** 发起模式A / 模式B 请求（清空旧确认卡）。 */
  startOptimize: () => void
  /** 取消确认卡，回到空闲。 */
  cancelOptimize: () => void
  /** 确认发起 `/api/answer`，进入流式。 */
  beginStreaming: () => void
  /** 流结束：用后端 `note_markdown` 覆盖校正，进入可标注态。 */
  finishStreaming: (noteMarkdown: string) => void
  /** 流式出错：回到空闲（错误提示由组件负责）。 */
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

  // —— 候选问题（模式B 之后）——
  setPendingQuestions: (questions: CandidateQuestion[]) => void
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

  openNote: (slug, markdown) => set({ noteSlug: slug, noteMarkdown: markdown }),
  appendDelta: (text) => set((s) => ({ noteMarkdown: s.noteMarkdown + text })),

  startOptimize: () => set({ phase: 'optimizing', optimizeCard: null }),
  cancelOptimize: () => set({ phase: 'idle', optimizeCard: null }),
  beginStreaming: () => set({ phase: 'streaming', optimizeCard: null }),
  finishStreaming: (noteMarkdown) => set({ phase: 'done', noteMarkdown }),
  failStreaming: () => set({ phase: 'idle' }),
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

  setPendingQuestions: (questions) =>
    set({
      pendingQuestions: questions.map((q) => ({
        ...q,
        id: newId(),
        depth: null,
      })),
    }),
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

/** 是否可划词标注（`idle` / `done` 两态可，其余阶段禁用）。 */
export const useCanAnnotate = (): boolean =>
  useStore((s) => s.phase === 'idle' || s.phase === 'done')
