// 问题确认区（Plan §8）：**同一区域按状态复用**——
// - 模式A（提问态）：优化后的问题**可编辑**；主题**只读**、`slug` 不可改。
//   点「确认并作答」→ `POST /api/answer` 流式作答，回答流入笔记区。
// - 模式B（标注态，T3.5）：候选问题列表，每条**可编辑/删减**、单选**简单/深入**；
//   点「开始扩写」进入 `annotating`。
// - 串行扩写（T3.6）：`annotating` 期间**一次一个**——扩写当前条 → 用户读 →
//   点「已理解」/「跳过」→ 下一条；**单条失败即中断**、提示**该条重试**（不推进队列）。
//   「已理解」另调 `POST /api/memory/confirm` 记「已学会」（T4.6②；失败也推进、仅提示）；
//   「跳过」不调 `confirm`、记忆不变。
//   下钻项（`depth=null`）在轮到它时先让用户选深度。
// - 下钻（T3.5）：`annotating` 期间在笔记区**新划的词**立即**单独再走一遍模式B**，
//   结果**插到队列最前、优先处理**（Plan §8）。
//
// 说明：本组件在 App 中**常驻渲染**（不随确认卡一起卸载），故作答失败的错误提示
// 能在卡片消失后仍然显示；`annotating` 期间也靠它承载下钻监听。

import { useEffect, useRef, useState } from 'react'
import { annotate, confirmConcept, optimizeAnnotation, streamAnswer } from '../api'
import type { AnnotationDepth } from '../types'
import type { PendingQuestion } from '../store'
import { useStore } from '../store'
import './QuestionConfirm.css'

/** 候选问题里某个深度的按钮样式（选中高亮）。 */
function depthClass(selected: boolean): string {
  return `cand-depth-btn${selected ? ' cand-depth-btn--on' : ''}`
}

/** 待处理队列中的后续项（只读展示）。 */
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
  const noteTitle = useStore((s) => s.noteTitle)
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
  const flashAnchor = useStore((s) => s.flashAnchor)
  const updatePendingQuestion = useStore((s) => s.updatePendingQuestion)
  const removePendingQuestion = useStore((s) => s.removePendingQuestion)
  const prependPendingQuestions = useStore((s) => s.prependPendingQuestions)
  const beginAnnotating = useStore((s) => s.beginAnnotating)
  const finishAnnotating = useStore((s) => s.finishAnnotating)
  const clearAnnotations = useStore((s) => s.clearAnnotations)

  const [error, setError] = useState('')

  // —— T3.6 串行执行（组件本地 UI 状态）——
  // `currentId`：当前唯一在执行/待确认的条目；不被下钻插队改变（Plan §8）。
  const [currentId, setCurrentId] = useState<string | null>(null)
  /** 「扩写』请求进行中。 */
  const [expanding, setExpanding] = useState(false)
  /** 当前条扩写失败（中断，等用户点「重试该条」）。 */
  const [failed, setFailed] = useState(false)
  /** 「已理解」的 `POST /api/memory/confirm` 进行中（期间禁用按钮，避免重复提交/竞态）。 */
  const [confirming, setConfirming] = useState(false)

  // 仅处理模式A 的初始确认卡。
  const initial = card?.mode === 'initial' ? card : null
  // 主题归属**由用户决定**（Plan §8）：`newTopic=true` → 新建；否则并入当前主题。
  const isMerge = initial !== null && !initial.newTopic

  /** 当前条（`annotating` 期间）。 */
  const current = pendingQuestions.find((q) => q.id === currentId) ?? null

  // 下钻去重（StrictMode 下 effect 会跑两次）：记下已送去模式B 的标注 id。
  const drillSentRef = useRef<Set<string>>(new Set())

  async function handleConfirm() {
    if (!initial) return
    const question = initial.question.trim()
    if (question === '') return

    setError('')
    beginStreaming(question) // 进入流式：笔记区底部以该问题为标题长出新的 Q/A 块
    setPendingContext('') // 引用片段已消费

    // 目标主题（Plan §8）：显式新建 → 用模式A 命名并切到空白新笔记；否则并入当前主题。
    const targetSlug = initial.newTopic ? initial.slug : noteSlug
    const targetTitle = initial.newTopic ? initial.title : noteTitle
    if (initial.newTopic) {
      openNote(targetSlug, targetTitle, '') // 新主题：先切到空白笔记区
    }

    await streamAnswer(
      {
        question,
        slug: targetSlug,
        title: targetTitle,
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

  /**
   * T3.6 串行扩写单条：`POST /api/annotate`（`anchor` 取该条 `snippet`）→ 成功用返回的
   * **最新 `note_markdown`** 覆盖，并按 `anchor_id` **滚动定位到解释处、短暂高亮**（Plan §8）；
   * 失败置 `failed`（**中断**、不推进队列）。
   */
  async function runExpand(q: PendingQuestion) {
    if (q.depth === null) return
    setError('')
    setFailed(false)
    setExpanding(true)
    try {
      const resp = await annotate({
        slug: noteSlug,
        anchor: q.snippet,
        concept: q.concept,
        kind: q.kind,
        depth: q.depth,
        question: q.text,
      })
      openNote(noteSlug, noteTitle, resp.note_markdown)
      flashAnchor(resp.anchor_id) // 让用户立即看到「解释插在哪」
    } catch (exc) {
      setFailed(true)
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setExpanding(false)
    }
  }

  /** 把某条设为当前条；深度已定则立即扩写，未定（下钻项）则等用户选深度。 */
  function startItem(q: PendingQuestion) {
    setCurrentId(q.id)
    setFailed(false)
    if (q.depth !== null) void runExpand(q)
  }

  /** 「开始扩写」：进入 `annotating` 并处理队首（候选已全部选好深度）。 */
  function handleStart() {
    clearAnnotations() // 标注已转为候选问题，清空收集区
    beginAnnotating()
    const head = pendingQuestions[0]
    if (head) startItem(head)
  }

  /**
   * 「已理解」/「跳过」：移除当前条并推进下一条（无则收尾回 `idle`）。
   * 二者均为**前端本地动作**——不回滚已写扩写。
   * 差异（Plan §8）：`已理解` 先 `POST /api/memory/confirm` 记「已学会」再推进；
   * `跳过` 不调 `confirm`、记忆不变（均在 `handleUnderstand`/本函数体现）。
   */
  function advance() {
    if (currentId === null) return
    removePendingQuestion(currentId)
    setFailed(false)
    setError('')
    // 读**最新**队列（异步确认期间可能有下钻插队），避免用渲染快照误判「队列已空」。
    const next = useStore.getState().pendingQuestions[0]
    if (!next) {
      setCurrentId(null)
      finishAnnotating()
      return
    }
    startItem(next)
  }

  /**
   * 「已理解」：先 `POST /api/memory/confirm`（清 `pending`、`+10`、**幂等**）记「已学会」，
   * 再推进下一条（Plan §8）。**失败也推进**——已理解属本地动作、队列必须能走完，
   * 失败原因仅经错误条提示、不阻断；`concept` 为空（未落库）则跳过调用。
   * 请求期间 `confirming=true` 禁用按钮，避免重复提交 / 与「跳过」竞态。
   */
  async function handleUnderstand() {
    if (confirming) return
    const q = current
    if (!q) return

    setConfirming(true)
    let message = ''
    if (q.concept.trim() !== '') {
      try {
        await confirmConcept(q.concept)
      } catch (exc) {
        message = exc instanceof Error ? exc.message : String(exc)
      }
    }
    advance()
    setConfirming(false)
    if (message !== '') setError(message)
  }

  /** 下钻项（`depth=null`）选定深度后立即扩写。 */
  function chooseDepth(depth: AnnotationDepth) {
    if (!current) return
    updatePendingQuestion(current.id, { depth })
    void runExpand({ ...current, depth })
  }

  // 下钻（Plan §8）：`annotating` 期间新增的标注立即单独走一遍模式B → 插到队首。
  useEffect(() => {
    if (phase !== 'annotating') return
    const fresh = annotations.filter((a) => !drillSentRef.current.has(a.id))
    if (fresh.length === 0) return
    for (const a of fresh) drillSentRef.current.add(a.id)

    void (async () => {
      // 每个候选问题回带 `snippet_index`（1-based），按它取原文配对（Plan §6.4/§8）。
      const snippets = fresh.map((a) => a.snippet)
      try {
        const resp = await optimizeAnnotation({
          snippets,
          note_markdown: noteMarkdown,
        })
        prependPendingQuestions(
          resp.questions.map((q) => ({
            ...q,
            snippet: snippets[q.snippet_index - 1] ?? q.concept,
          })),
        )
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
              {isMerge
                ? `并入《${noteTitle}》`
                : `新建《${initial.title}》`}
              <code className="confirm-slug">
                {isMerge ? noteSlug : initial.slug}
              </code>
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
              onClick={() => handleStart()}
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
            <span className="confirm-target">一次一条，读懂后再继续</span>
          </div>

          {current && (
            <div className="serial-current">
              <div className="serial-current-text">{current.text}</div>

              {current.depth === null ? (
                // 下钻项：轮到它时先选深度再扩写（Plan §8）。
                <div className="serial-depth">
                  <button
                    type="button"
                    className={depthClass(false)}
                    onClick={() => chooseDepth('brief')}
                  >
                    简单了解
                  </button>
                  <button
                    type="button"
                    className={depthClass(false)}
                    onClick={() => chooseDepth('deep')}
                  >
                    深入了解
                  </button>
                </div>
              ) : expanding ? (
                <div className="serial-status">正在扩写…</div>
              ) : failed ? (
                // 单条失败即**中断**：停在该条、只提示重试，不推进队列（Plan §8）。
                <div className="serial-failed">
                  <span>该条扩写失败，请重试</span>
                  <button
                    type="button"
                    className="serial-retry"
                    onClick={() => void runExpand(current)}
                  >
                    重试该条
                  </button>
                </div>
              ) : (
                <div className="serial-actions">
                  <button
                    type="button"
                    className="serial-understood"
                    disabled={confirming}
                    onClick={() => void handleUnderstand()}
                  >
                    {confirming ? '确认中…' : '已理解'}
                  </button>
                  <button
                    type="button"
                    className="serial-skip"
                    disabled={confirming}
                    onClick={() => advance()}
                  >
                    跳过
                  </button>
                </div>
              )}
            </div>
          )}

          {pendingQuestions.filter((q) => q.id !== currentId).length > 0 && (
            <ol className="queue-list">
              {pendingQuestions
                .filter((q) => q.id !== currentId)
                .map((q) => (
                  <QueueItem key={q.id} item={q} />
                ))}
            </ol>
          )}
        </div>
      )}
    </>
  )
}
