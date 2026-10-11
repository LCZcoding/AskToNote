// 问题确认区（Plan §8）：**同一区域按状态复用**——
// - 模式A（提问态）：优化后的问题**可编辑**；主题**只读**、`slug` 不可改。
//   点「确认并作答」→ `POST /api/answer` 流式作答，回答流入笔记区。
// - 模式B（标注态，T3.5）：候选问题列表，每条**可编辑/删减**、单选**简单/深入**；
//   点「开始扩写」进入 `annotating`。
// - 下钻（T3.5）：`annotating` 期间在笔记区**新划的词**立即**单独再走一遍模式B**，
//   结果**插到队列最前、优先处理**（Plan §8）。
//
// 说明：本组件在 App 中**常驻渲染**（不随确认卡一起卸载），故作答失败的错误提示
// 能在卡片消失后仍然显示；`annotating` 期间也靠它承载下钻监听。

import { useEffect, useRef, useState } from 'react'
import { getNote, optimizeAnnotation, streamAnswer } from '../api'
import type { PendingQuestion } from '../store'
import { useStore } from '../store'
import './QuestionConfirm.css'

/** 候选问题里某个深度的按钮样式（选中高亮）。 */
function depthClass(selected: boolean): string {
  return `cand-depth-btn${selected ? ' cand-depth-btn--on' : ''}`
}

/** 待处理队列项（`annotating` 期间只读展示，串行执行在 T3.6 接入）。 */
function QueueItem({ item }: { item: PendingQuestion }) {
  return (
    <li className="queue-item">
      <span className={`queue-depth queue-depth--${item.depth ?? 'none'}`}>
        {item.depth === 'deep' ? '深入' : item.depth === 'brief' ? '简单' : '待选'}
      </span>
      <span className="queue-text">{item.text}</span>
    </li>
  )
}

export default function QuestionConfirm() {
  const card = useStore((s) => s.optimizeCard)
  const phase = useStore((s) => s.phase)
  const noteSlug = useStore((s) => s.noteSlug)
  const noteMarkdown = useStore((s) => s.noteMarkdown)
  const pendingContext = useStore((s) => s.pendingContext)
  const pendingQuestions = useStore((s) => s.pendingQuestions)
  const annotations = useStore((s) => s.annotations)
  const updateInitialQuestion = useStore((s) => s.updateInitialQuestion)
  const cancelOptimize = useStore((s) => s.cancelOptimize)
  const beginStreaming = useStore((s) => s.beginStreaming)
  const appendDelta = useStore((s) => s.appendDelta)
  const finishStreaming = useStore((s) => s.finishStreaming)
  const failStreaming = useStore((s) => s.failStreaming)
  const setPendingContext = useStore((s) => s.setPendingContext)
  const openNote = useStore((s) => s.openNote)
  const updatePendingQuestion = useStore((s) => s.updatePendingQuestion)
  const removePendingQuestion = useStore((s) => s.removePendingQuestion)
  const prependPendingQuestions = useStore((s) => s.prependPendingQuestions)
  const beginAnnotating = useStore((s) => s.beginAnnotating)
  const clearAnnotations = useStore((s) => s.clearAnnotations)

  const [error, setError] = useState('')

  // 仅处理模式A 的初始确认卡。
  const initial = card?.mode === 'initial' ? card : null
  const isMerge =
    initial !== null && initial.slug !== '' && initial.slug === noteSlug

  // 下钻去重（StrictMode 下 effect 会跑两次）：记下已送去模式B 的标注 id。
  const drillSentRef = useRef<Set<string>>(new Set())

  async function handleConfirm() {
    if (!initial) return
    const question = initial.question.trim()
    if (question === '') return

    setError('')
    beginStreaming(question) // 进入流式：笔记区底部以该问题为标题长出新的 Q/A 块
    setPendingContext('') // 引用片段已消费

    // 目标主题与当前打开的不同（新主题 / 并入其它已有主题）：先切换笔记区
    if (initial.slug !== noteSlug) {
      try {
        const detail = await getNote(initial.slug)
        openNote(detail.slug, detail.markdown)
      } catch {
        openNote(initial.slug, '')
      }
    }

    await streamAnswer(
      {
        question,
        slug: initial.slug,
        title: initial.title,
        context: pendingContext || undefined,
      },
      {
        onDelta: appendDelta,
        onDone: finishStreaming,
        onError: (message) => {
          failStreaming()
          setError(message)
        },
      },
    )
  }

  // 下钻（Plan §8）：`annotating` 期间新增的标注立即单独走一遍模式B → 插到队首。
  useEffect(() => {
    if (phase !== 'annotating') return
    const fresh = annotations.filter((a) => !drillSentRef.current.has(a.id))
    if (fresh.length === 0) return
    for (const a of fresh) drillSentRef.current.add(a.id)

    void (async () => {
      try {
        const resp = await optimizeAnnotation({
          snippets: fresh.map((a) => a.snippet),
          note_markdown: noteMarkdown,
        })
        prependPendingQuestions(resp.questions)
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc))
      } finally {
        clearAnnotations()
      }
    })()
  }, [
    phase,
    annotations,
    noteMarkdown,
    prependPendingQuestions,
    clearAnnotations,
  ])

  /** 候选问题全部选定深度后才可「开始扩写」。 */
  const allDepthsChosen =
    pendingQuestions.length > 0 && pendingQuestions.every((q) => q.depth !== null)

  return (
    <>
      {error !== '' && (
        <div className="confirm-error">
          <span>操作失败：{error}</span>
          <button type="button" onClick={() => setError('')}>
            关闭
          </button>
        </div>
      )}

      {initial && (
        <div className="confirm-card">
          <div className="confirm-head">
            <span className="confirm-title">确认问题</span>
            <span className="confirm-target">
              {isMerge ? `并入《${initial.title}》` : `新建《${initial.title}》`}
              <code className="confirm-slug">{initial.slug}</code>
            </span>
          </div>

          <textarea
            className="confirm-input"
            rows={2}
            value={initial.question}
            onChange={(e) => updateInitialQuestion(e.target.value)}
          />

          <div className="confirm-actions">
            <button
              type="button"
              className="confirm-cancel"
              onClick={() => cancelOptimize()}
            >
              取消
            </button>
            <button
              type="button"
              className="confirm-ok"
              disabled={initial.question.trim() === ''}
              onClick={() => void handleConfirm()}
            >
              确认并作答
            </button>
          </div>
        </div>
      )}

      {card?.mode === 'annotation' && (
        <div className="confirm-card">
          <div className="confirm-head">
            <span className="confirm-title">确认候选问题</span>
            <span className="confirm-target">逐条选择了解深度</span>
          </div>

          <ul className="cand-list">
            {pendingQuestions.map((q) => (
              <li key={q.id} className="cand-item">
                <textarea
                  className="cand-text"
                  rows={2}
                  value={q.text}
                  onChange={(e) =>
                    updatePendingQuestion(q.id, { text: e.target.value })
                  }
                />
                <div className="cand-meta">
                  <span className={`cand-kind cand-kind--${q.kind}`}>
                    {q.kind === 'concept' ? '概念' : '说法'}
                  </span>
                  {q.concept !== '' && (
                    <span className="cand-concept">{q.concept}</span>
                  )}
                  {q.reason !== '' && (
                    <span className="cand-reason">{q.reason}</span>
                  )}
                </div>
                <div className="cand-foot">
                  <div className="cand-depth">
                    <button
                      type="button"
                      className={depthClass(q.depth === 'brief')}
                      onClick={() =>
                        updatePendingQuestion(q.id, { depth: 'brief' })
                      }
                    >
                      简单了解
                    </button>
                    <button
                      type="button"
                      className={depthClass(q.depth === 'deep')}
                      onClick={() =>
                        updatePendingQuestion(q.id, { depth: 'deep' })
                      }
                    >
                      深入了解
                    </button>
                  </div>
                  <button
                    type="button"
                    className="cand-remove"
                    onClick={() => removePendingQuestion(q.id)}
                  >
                    删除
                  </button>
                </div>
              </li>
            ))}
          </ul>

          <div className="confirm-actions">
            <button
              type="button"
              className="confirm-cancel"
              onClick={() => cancelOptimize()}
            >
              取消
            </button>
            <button
              type="button"
              className="confirm-ok"
              disabled={!allDepthsChosen}
              onClick={() => {
                clearAnnotations() // 标注已转为候选问题，清空收集区
                beginAnnotating() // → annotating（串行扩写在 T3.6 接入）
              }}
            >
              开始扩写
            </button>
          </div>
        </div>
      )}

      {phase === 'annotating' && (
        <div className="confirm-card">
          <div className="confirm-head">
            <span className="confirm-title">
              待处理队列（{pendingQuestions.length}）
            </span>
            <span className="confirm-target">串行扩写将在下一步接入</span>
          </div>
          <ol className="queue-list">
            {pendingQuestions.map((q) => (
              <QueueItem key={q.id} item={q} />
            ))}
          </ol>
        </div>
      )}
    </>
  )
}
