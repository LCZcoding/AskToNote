// 提问区（Plan §8）：输入原始问题 +「优化」→ 模式A 出确认卡。
//
// 支持引用片段（Spec FR-4a/FR-4b）：大块追问带入时在输入框上方显示引用文本；
// 问题**可留空**（有引用片段时默认「请详细讲解这段内容」），请求携带 `context`。

import { useState } from 'react'
import { optimizeInitial } from '../api'
import { useStore } from '../store'
import './AskBar.css'

/** 有引用片段但问题留空时的默认提问（Spec FR-4b）。 */
const DEFAULT_FOLLOWUP_QUESTION = '请详细讲解这段内容'

export default function AskBar() {
  const phase = useStore((s) => s.phase)
  const noteSlug = useStore((s) => s.noteSlug)
  const newTopicMode = useStore((s) => s.newTopicMode)
  const pendingContext = useStore((s) => s.pendingContext)
  const setPendingContext = useStore((s) => s.setPendingContext)
  const startOptimize = useStore((s) => s.startOptimize)
  const cancelOptimize = useStore((s) => s.cancelOptimize)
  const setInitialCard = useStore((s) => s.setInitialCard)

  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // 请求进行中 / 已有确认卡待处理时，禁用提问，避免重复提交与状态错乱（Plan §8）。
  const busy =
    phase === 'optimizing' ||
    phase === 'streaming' ||
    phase === 'annotating' ||
    loading
  const canSubmit = !busy && (question.trim() !== '' || pendingContext !== '')

  // **显式新建主题**：用户点了「新建笔记」，或当前无主题（首问）。否则默认**并入当前主题**。
  const newTopic = newTopicMode || noteSlug === ''

  async function handleOptimize() {
    if (!canSubmit) return
    setError('')
    setLoading(true)
    startOptimize()
    try {
      const resp = await optimizeInitial({
        question: question.trim() || DEFAULT_FOLLOWUP_QUESTION,
        new_topic: newTopic || undefined,
        context: pendingContext || undefined,
      })
      setInitialCard({
        question: resp.optimized_question,
        newTopic,
        slug: resp.slug ?? '',
        title: resp.title ?? '',
      })
      // 原始问题已交给确认卡，清空输入框，避免残留（引用片段在确认时才消费）
      setQuestion('')
    } catch (exc) {
      cancelOptimize()
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="ask-bar">
      {pendingContext !== '' && (
        <div className="ask-context">
          <div className="ask-context-head">
            <span className="ask-context-label">引用片段</span>
            <button
              type="button"
              className="ask-context-remove"
              onClick={() => setPendingContext('')}
            >
              移除
            </button>
          </div>
          <p className="ask-context-text">{pendingContext}</p>
        </div>
      )}

      <div className="ask-input-row">
        <textarea
          className="ask-input"
          rows={2}
          placeholder="提出你的问题（有引用片段时可留空）"
          value={question}
          disabled={busy}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button
          type="button"
          className="ask-submit"
          disabled={!canSubmit}
          onClick={() => void handleOptimize()}
        >
          {loading ? '优化中…' : '优化'}
        </button>
      </div>

      {error !== '' && <p className="ask-error">提问失败：{error}</p>}
    </div>
  )
}
