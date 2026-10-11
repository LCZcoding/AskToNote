// 应用外壳（Plan §8「页面结构」）：顶部标题栏 + 主区（笔记区）。
//
// 启动加载（Plan §8）：页面加载时调 `GET /api/notes`（按 `updated_at` 倒序）→
// 有主题则**自动打开最近更新的一篇**（“继续上次学习”）；无主题显示空态、引导提问。
// 提问/问题确认区与设置页在后续任务（T2.5/T2.6）接入。

import { useEffect, useState } from 'react'
import { getNote, getNotes } from './api'
import NoteView from './components/NoteView'
import { useStore } from './store'
import './App.css'

/** 启动加载状态；与 store 的交互阶段无关，仅管首屏。 */
type LoadState = 'loading' | 'ready' | 'empty' | 'error'

function App() {
  const noteSlug = useStore((s) => s.noteSlug)
  const openNote = useStore((s) => s.openNote)
  const [state, setState] = useState<LoadState>('loading')
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    async function loadLatestNote() {
      try {
        const notes = await getNotes()
        if (cancelled) return
        if (notes.length === 0) {
          setState('empty')
          return
        }
        // 列表已按 updated_at 倒序，取第一篇即最近更新
        const detail = await getNote(notes[0].slug)
        if (cancelled) return
        openNote(detail.slug, detail.markdown)
        setState('ready')
      } catch (exc) {
        if (cancelled) return
        setError(exc instanceof Error ? exc.message : String(exc))
        setState('error')
      }
    }

    void loadLatestNote()
    return () => {
      cancelled = true
    }
  }, [openNote])

  return (
    <div className="app">
      <header className="app-header">
        <h1 className="app-title">AskToNote</h1>
        <p className="app-subtitle">提问即笔记 · 一问一答长成知识</p>
      </header>

      <main className="app-main">
        {state === 'loading' && <p className="app-hint">正在加载…</p>}
        {state === 'error' && (
          <p className="app-hint app-hint--error">加载失败：{error}</p>
        )}
        {state === 'empty' && (
          <div className="app-empty">
            <p>还没有任何主题。</p>
            <p>提出你的第一个问题，长成第一篇笔记吧。</p>
          </div>
        )}
        {noteSlug !== '' && <NoteView />}
      </main>
    </div>
  )
}

export default App
