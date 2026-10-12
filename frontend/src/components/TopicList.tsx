// 主题列表（Plan §8「页面结构」）：侧边栏列出全部主题，点选**手动切换**；
// 「＋ 新建」进入「新建笔记」态，下次提问即**显式新建主题**（模式A 命名版）。
//
// 主题归属**由用户决定**——不再由 AI 判定；本组件是新建主题的**唯一入口**。

import { useEffect, useState } from 'react'
import { getNote, getNotes } from '../api'
import type { NoteSummary } from '../types'
import { useStore } from '../store'
import './TopicList.css'

export default function TopicList() {
  const noteSlug = useStore((s) => s.noteSlug)
  const newTopicMode = useStore((s) => s.newTopicMode)
  const phase = useStore((s) => s.phase)
  const openNote = useStore((s) => s.openNote)
  const startNewTopic = useStore((s) => s.startNewTopic)

  const [notes, setNotes] = useState<NoteSummary[]>([])
  const [error, setError] = useState('')

  // 重新拉取列表的时机（关键：**新主题的记录在 `/api/answer` 流结束后才落库**）：
  // 切换/新建主题后（`noteSlug` 变化）或一次流式/扩写结束（`phase` 回到 `idle`/`done`）
  // 时刷新；流式/优化/扩写**进行中**跳过（此时新主题尚未落库，拉了也不全）。
  useEffect(() => {
    if (phase === 'optimizing' || phase === 'streaming' || phase === 'annotating') {
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const list = await getNotes()
        if (!cancelled) setNotes(list)
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [noteSlug, phase])

  // 流式/优化/扩写期间禁止切换主题，避免状态错乱。
  const busy = phase === 'optimizing' || phase === 'streaming' || phase === 'annotating'

  async function handleOpen(slug: string) {
    if (busy || slug === noteSlug) return
    setError('')
    try {
      const detail = await getNote(slug)
      openNote(detail.slug, detail.title, detail.markdown)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    }
  }

  return (
    <aside className="topic-list">
      <div className="topic-head">
        <span className="topic-head-label">我的笔记</span>
        <button
          type="button"
          className="topic-new"
          disabled={busy || newTopicMode}
          onClick={() => startNewTopic()}
        >
          ＋ 新建
        </button>
      </div>

      {newTopicMode && <p className="topic-new-tip">新建中：提问后即创建新主题</p>}

      {notes.length === 0 ? (
        <p className="topic-empty">还没有笔记</p>
      ) : (
        <ul className="topic-items">
          {notes.map((n) => (
            <li key={n.slug}>
              <button
                type="button"
                className={`topic-item${n.slug === noteSlug ? ' topic-item--on' : ''}`}
                disabled={busy}
                onClick={() => void handleOpen(n.slug)}
              >
                <span className="topic-item-title">{n.title}</span>
                <code className="topic-item-slug">{n.slug}</code>
              </button>
            </li>
          ))}
        </ul>
      )}

      {error !== '' && <p className="topic-error">加载失败：{error}</p>}
    </aside>
  )
}
