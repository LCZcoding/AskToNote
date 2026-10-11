// 问题确认区（Plan §8，模式A 态）：优化后的问题**可编辑**；主题**只读**、`slug` 不可改。
// 点「确认」→ 发起 `POST /api/answer` 流式作答，回答流入笔记区（Plan §8）。
//
// 说明：本组件在 App 中**常驻渲染**（不随确认卡一起卸载），故作答失败的错误提示
// 能在卡片消失后仍然显示。

import { useState } from 'react'
import { getNote, streamAnswer } from '../api'
import { useStore } from '../store'
import './QuestionConfirm.css'

export default function QuestionConfirm() {
  const card = useStore((s) => s.optimizeCard)
  const noteSlug = useStore((s) => s.noteSlug)
  const pendingContext = useStore((s) => s.pendingContext)
  const updateInitialQuestion = useStore((s) => s.updateInitialQuestion)
  const cancelOptimize = useStore((s) => s.cancelOptimize)
  const beginStreaming = useStore((s) => s.beginStreaming)
  const appendDelta = useStore((s) => s.appendDelta)
  const finishStreaming = useStore((s) => s.finishStreaming)
  const failStreaming = useStore((s) => s.failStreaming)
  const setPendingContext = useStore((s) => s.setPendingContext)
  const openNote = useStore((s) => s.openNote)

  const [error, setError] = useState('')

  // 仅处理模式A 的初始确认卡（模式B 的候选问题列表在 T3.5 接入）。
  const initial = card?.mode === 'initial' ? card : null
  const isMerge =
    initial !== null && initial.slug !== '' && initial.slug === noteSlug

  async function handleConfirm() {
    if (!initial) return
    const question = initial.question.trim()
    if (question === '') return

    setError('')
    beginStreaming()
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

  return (
    <>
      {error !== '' && (
        <div className="confirm-error">
          <span>作答失败：{error}</span>
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
    </>
  )
}
