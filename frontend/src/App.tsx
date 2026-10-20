// 应用外壳（Plan §8「页面结构」）：顶部标题栏 + 主区（笔记区）。
//
// 启动加载（Plan §8）：页面加载时调 `GET /api/notes`（按 `updated_at` 倒序）→
// 有主题则**自动打开最近更新的一篇**（“继续上次学习”）；无主题显示空态、引导提问。
// 底部为提问 / 问题确认区（AskBar + QuestionConfirm，Plan §8）；设置页由标题栏「设置」打开。
//
// 会话开始的背景刷新（Plan §8「背景展示与刷新」/ Spec FR-18）：**页面加载/刷新**
// （=会话开始）时调一次 `POST /api/memory/refresh`，**长会话中途不重算**；设置页另可手动刷新。

import { useEffect, useState } from 'react'
import { getNote, getNotes, refreshBackground } from './api'
import AnnotationList from './components/AnnotationList'
import AskBar from './components/AskBar'
import NoteView from './components/NoteView'
import QuestionConfirm from './components/QuestionConfirm'
import SettingsPanel from './components/SettingsPanel'
import ThemeToggle from './components/ThemeToggle'
import TopicList from './components/TopicList'
import { useStore } from './store'
import './App.css'

/** 启动加载状态；与 store 的交互阶段无关，仅管首屏。 */
type LoadState = 'loading' | 'ready' | 'empty' | 'error'

/**
 * 会话级背景刷新**只调一次**（Plan §8：长会话中途不重算）。
 *
 * 用**模块级标志**而非 ref：StrictMode 下 effect 会跑两遍（ref 会被重挂载重置），
 * 而本模块变量在**同一次页面加载**内保持不变；页面刷新会重新加载模块 → 自然重置。
 */
let sessionRefreshed = false

function App() {
  const noteSlug = useStore((s) => s.noteSlug)
  const newTopicMode = useStore((s) => s.newTopicMode)
  const cancelNewTopic = useStore((s) => s.cancelNewTopic)
  const openNote = useStore((s) => s.openNote)
  const [state, setState] = useState<LoadState>('loading')
  const [error, setError] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)

  // 会话开始：页面加载/刷新时后台刷新一次背景（失败不阻断首屏，可在设置页手动重试）。
  useEffect(() => {
    if (sessionRefreshed) return
    sessionRefreshed = true
    void refreshBackground().catch(() => {
      // 后台推断失败：静默忽略，不阻断首屏；用户可在设置页「刷新背景」手动重试。
    })
  }, [])

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
        openNote(detail.slug, detail.title, detail.markdown)
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
        <div className="app-header-text">
          <h1 className="app-title">AskToNote</h1>
          <p className="app-subtitle">提问即笔记 · 一问一答长成知识</p>
        </div>
        <div className="app-header-actions">
          {/* 明暗切换：一键翻转，偏好写入 localStorage（详见 theme.ts） */}
          <ThemeToggle />
          <button
            type="button"
            className="app-settings"
            onClick={() => setSettingsOpen(true)}
          >
            设置
          </button>
        </div>
      </header>

      <div className="app-body">
        {/* 主题列表：手动切换已有主题 / 「新建笔记」（Plan §8） */}
        <TopicList />

        <main className="app-main">
          {noteSlug === '' && state === 'loading' && (
            <p className="app-hint">正在加载…</p>
          )}
          {noteSlug === '' && state === 'error' && (
            <p className="app-hint app-hint--error">加载失败：{error}</p>
          )}
          {noteSlug === '' && state !== 'loading' && state !== 'error' && (
            <div className="app-empty">
              {newTopicMode ? (
                <>
                  <p>新笔记</p>
                  <p>在下方输入你的第一个问题，它会长成这篇新笔记。</p>
                  <button
                    type="button"
                    className="app-empty-action"
                    onClick={cancelNewTopic}
                  >
                    取消
                  </button>
                </>
              ) : state === 'empty' ? (
                <>
                  <p>还没有任何主题。</p>
                  <p>提出你的第一个问题，长成第一篇笔记吧。</p>
                </>
              ) : (
                <>
                  <p>未打开任何笔记。</p>
                  <p>从左侧选一篇继续，或点「＋ 新建」开始一篇新笔记。</p>
                </>
              )}
            </div>
          )}
          {noteSlug !== '' && <NoteView />}
        </main>
      </div>

      {/* 提问 / 问题确认区（Plan §8：同一区域按状态复用） */}
      <div className="app-ask">
        <AnnotationList />
        <QuestionConfirm />
        <AskBar />
      </div>

      {/* 设置页（Plan §8 / Spec FR-18）：背景展示、编辑覆盖、手动刷新 */}
      {settingsOpen && <SettingsPanel onClose={() => setSettingsOpen(false)} />}
    </div>
  )
}

export default App
