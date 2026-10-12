// 标注收集区（Plan §8 / T3.4–T3.5）：展示划词收集到的标注片段（每条可移除），
// 并在用户点「生成候选问题」时**整批一次性**调模式B → 切到候选问题列表。
//
// 触发方式（Plan §8）：**手动按钮**，避免每划一次词都调一次 LLM。
// 候选问题卡展示期间本区隐藏，让位给确认区（同一区域按状态复用）。

import { useState } from 'react'
import { optimizeAnnotation } from '../api'
import { useStore } from '../store'
import './AnnotationList.css'

export default function AnnotationList() {
  const annotations = useStore((s) => s.annotations)
  const phase = useStore((s) => s.phase)
  const optimizeCard = useStore((s) => s.optimizeCard)
  const noteMarkdown = useStore((s) => s.noteMarkdown)
  const removeAnnotation = useStore((s) => s.removeAnnotation)
  const setPendingQuestions = useStore((s) => s.setPendingQuestions)
  const setAnnotationCard = useStore((s) => s.setAnnotationCard)
  const startOptimize = useStore((s) => s.startOptimize)
  const cancelOptimize = useStore((s) => s.cancelOptimize)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // 无标注、或已有确认卡（模式A/B）待处理时不展示本区。
  if (annotations.length === 0 || optimizeCard !== null) return null

  const canSubmit =
    !loading && (phase === 'idle' || phase === 'done') && annotations.length > 0

  async function handleGenerate() {
    if (!canSubmit) return
    setError('')
    setLoading(true)
    startOptimize() // phase → optimizing（Plan §8：模式B 拉取中）
    try {
      // 模式B 每个候选问题回带 `snippet_index`（1-based），**按它取原文**——配对不依赖输出顺序（Plan §6.4/§8）：
      // 把划词原文并入候选项的 `snippet`，供 `/api/annotate` 作 `anchor`。
      const snippets = annotations.map((a) => a.snippet)
      const resp = await optimizeAnnotation({
        snippets,
        note_markdown: noteMarkdown,
      })
      setPendingQuestions(
        resp.questions.map((q) => ({
          ...q,
          snippet: snippets[q.snippet_index - 1] ?? q.concept,
        })),
      )
      setAnnotationCard()
    } catch (exc) {
      cancelOptimize()
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="anno-list">
      <div className="anno-list-head">
        <span className="anno-list-label">已标注 {annotations.length} 处</span>
        <button
          type="button"
          className="anno-generate"
          disabled={!canSubmit}
          onClick={() => void handleGenerate()}
        >
          {loading ? '生成中…' : '生成候选问题'}
        </button>
      </div>
      <ul className="anno-list-items">
        {annotations.map((a) => (
          <li key={a.id} className="anno-item">
            <span className="anno-item-text">{a.snippet}</span>
            <button
              type="button"
              className="anno-item-remove"
              aria-label={`移除标注：${a.snippet}`}
              onClick={() => removeAnnotation(a.id)}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      {error !== '' && <p className="anno-list-error">生成候选问题失败：{error}</p>}
    </div>
  )
}
