// 笔记区（Plan §8）：用 react-markdown 渲染当前主题的笔记 Markdown。
//
// 关键点：
// - 开启 **rehype-raw**，以渲染后端标注写入的**行内 HTML 锚点**
//   （`<a id="anno-{n}">`）与 `[↩]` 回跳链接（Spec FR-9c）；
// - 「原词 ⇄ 解释」的跳转由浏览器**原生锚点导航**完成，无需额外 JS；
// - 外挂 `remark-gfm`（**表格**/删除线/任务列表/自动链接——表格不属 CommonMark，缺它会退化成段落）
//   与 `remark-wikilink`，把 `[[概念名]]` / `[[slug#概念]]` 高亮（v1 不可点击）；
// - 再挂 `remark-highlight`（T3.12，Spec FR-15），把 `==名词==` 渲染为 `<mark class="note-mark">`
//   ——这是 Typora/Obsidian 通用的高亮语法，表示「本笔记已解释过」的名词（首次提及用行内代码）；
// - **一键导出（T3.10，Spec FR-13b）**：笔记区顶部工具条右侧「导出 MD」——**纯前端**用
//   `noteMarkdown` 造 Blob 直接下载，**不新增接口**；文件名取标题（清洗非法字符，空则回退 slug）。
// - **流式打字机（T2.6）**：`delta` 累积在 `streamingText`，在笔记区**底部**以
//   `## {问题}` 为题实时长出新的 Q/A 块（带光标）；`done` 用后端 `note_markdown`
//   覆盖校正；流式期间禁止划词标注（Plan §8）。
// - **划词标注（T3.4）**：流式结束且渲染稳定后，在**笔记区**内 `mouseup` 捕获
//   `window.getSelection()`，按 Spec FR-4a 的**字符数**分流——无关跨行、只看
//   去首尾空白后的长度：**> 60 字 = 大块** → 不进标注列表，作为**引用片段**
//   移交提问区（`pendingContext`，走 T2.5 追问链路）；**≤ 60 字** → 在选区上方
//   浮出小气泡「标注」，用户点击才写入 `annotations[]`（避免选字复制被误标）。

import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import rehypeRaw from 'rehype-raw'
import remarkGfm from 'remark-gfm'
import remarkWikilink from '../remark-wikilink'
import remarkHighlight from '../remark-highlight'
import { useCanAnnotate, useIsStreaming, useStore } from '../store'
import './NoteView.css'

/** 大块选区阈值（Spec FR-4a）：去首尾空白后**字符数 > 60** 判为大块 → 追问。 */
const BIG_BLOCK_MAX = 60

// `remark-gfm` 提供**表格**、删除线、任务列表、自动链接等 GFM 语法（表格不属 CommonMark，
// 缺了它 `| a | b |` 会退化成普通段落）——回答里表格很常见，必须开启（Plan §8）。
// `remark-wikilink` 渲染 `[[概念名]]`，`remark-highlight` 渲染 `==名词==`（与 Typora 兼容）。
const REMARK_PLUGINS = [remarkGfm, remarkWikilink, remarkHighlight]
const REHYPE_PLUGINS = [rehypeRaw]

/** 文件名非法字符（Spec FR-13b）：`\ / : * ? " < > |`。 */
const ILLEGAL_FILENAME_CHARS = /[\\/:*?"<>|]/g

/** 下载文件名：取标题清洗非法字符，**清洗后为空则回退 `{slug}.md`**（Spec FR-13b）。 */
function exportFileName(title: string, slug: string): string {
  const cleaned = title.replace(ILLEGAL_FILENAME_CHARS, '').trim()
  return `${cleaned || slug}.md`
}

/**
 * 一键导出笔记为 `.md`（Spec FR-13b）：**纯前端**——用内存中的笔记原文造 Blob 直接下载，
 * 不新增后端接口（原文与磁盘 `data/notes/{slug}.md` 逐字一致）。
 */
function downloadMarkdown(markdown: string, filename: string): void {
  const url = URL.createObjectURL(
    new Blob([markdown], { type: 'text/markdown;charset=utf-8' }),
  )
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** 划词气泡（`position: fixed`，坐标相对视口）。 */
interface Bubble {
  left: number
  top: number
  text: string
}

export default function NoteView() {
  const markdown = useStore((s) => s.noteMarkdown)
  const noteSlug = useStore((s) => s.noteSlug)
  const noteTitle = useStore((s) => s.noteTitle)
  const streamingQuestion = useStore((s) => s.streamingQuestion)
  const streamingText = useStore((s) => s.streamingText)
  const flashAnchorId = useStore((s) => s.flashAnchorId)
  const isStreaming = useIsStreaming()
  const canAnnotate = useCanAnnotate()
  const addAnnotation = useStore((s) => s.addAnnotation)
  const setPendingContext = useStore((s) => s.setPendingContext)
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const viewRef = useRef<HTMLDivElement | null>(null)
  const [bubble, setBubble] = useState<Bubble | null>(null)

  // 流式期间自动滚到底部，让新长出的内容始终可见（Plan §8）。
  useEffect(() => {
    if (isStreaming) bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [isStreaming, streamingText])

  // 扩写成功后滚动到解释处并短暂高亮（Plan §8）：`flashAnchorId` 与 `noteMarkdown` 由同一批
  // 更新一起提交，故 effect 运行时 DOM 已是**刷新后**的笔记，可直接按 id 定位。
  useEffect(() => {
    if (flashAnchorId === '') return
    const el = document.getElementById(flashAnchorId)
    if (!el) return
    el.scrollIntoView({ block: 'center' })
    // 锚点是空的行内 `<a>`，高亮它所在的段落/列表项/单元格/标题，让用户一眼看到「解释在哪」。
    // （`brief` 落在正文/列表项里；`deep` 的锚点在文末 `####` 标题行内。）
    const target = el.closest('p, li, td, th, h1, h2, h3, h4, h5, h6') ?? el.parentElement
    if (!target) return
    target.classList.add('anno-flash')
    const timer = window.setTimeout(
      () => target.classList.remove('anno-flash'),
      1800,
    )
    return () => {
      window.clearTimeout(timer)
      target.classList.remove('anno-flash')
    }
  }, [flashAnchorId])

  // 气泡为 `position: fixed`，无法跟随选区位移：页面一滚动、或点击气泡以外任意处即关闭（Plan §8）。
  useEffect(() => {
    if (!bubble) return
    const onScroll = () => setBubble(null)
    const onMouseDown = (e: MouseEvent) => {
      // 点在气泡自身（含「标注」按钮）时不关闭，交给其 `onClick` 处理。
      if (e.target instanceof Element && e.target.closest('.note-bubble')) return
      setBubble(null)
    }
    window.addEventListener('scroll', onScroll, true)
    document.addEventListener('mousedown', onMouseDown, true)
    return () => {
      window.removeEventListener('scroll', onScroll, true)
      document.removeEventListener('mousedown', onMouseDown, true)
    }
  }, [bubble])

  /** 笔记区划词后分流（Spec FR-4a/FR-4b）。 */
  function handleMouseUp() {
    // 仅 `idle` / `done` 两态可标注（流式期间由 `--streaming` 的 `user-select:none` 禁选）。
    if (!canAnnotate) return

    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
      setBubble(null)
      return
    }
    const range = sel.getRangeAt(0)

    // 仅限**笔记区**内的选区（Plan §8）：起止点都须落在笔记区容器内。
    const view = viewRef.current
    if (
      !view ||
      !view.contains(range.startContainer) ||
      !view.contains(range.endContainer)
    ) {
      setBubble(null)
      return
    }

    const text = sel.toString().trim()
    if (text === '') {
      setBubble(null)
      return
    }

    // 大块（> 60 字）→ 不作标注，作为引用片段移交提问区（Spec FR-4b）。
    if (text.length > BIG_BLOCK_MAX) {
      setPendingContext(text)
      sel.removeAllRanges()
      setBubble(null)
      return
    }

    // 小块 → 浮出气泡等用户点「标注」确认（避免误标）。
    const rect = range.getBoundingClientRect()
    setBubble({ left: rect.left + rect.width / 2, top: rect.top - 8, text })
  }

  /** 点气泡「标注」：写入标注列表并收起气泡。 */
  function handleAnnotate() {
    if (!bubble) return
    addAnnotation(bubble.text)
    window.getSelection()?.removeAllRanges()
    setBubble(null)
  }

  return (
    <>
      {/* 顶部工具条（Spec FR-13b）：左侧主题标题，右侧「导出 MD」；流式期间/笔记为空时禁用。 */}
      <div className="note-toolbar">
        <span className="note-toolbar-title" title={noteTitle}>
          {noteTitle}
        </span>
        <button
          type="button"
          className="note-export"
          disabled={isStreaming || markdown === ''}
          onClick={() =>
            downloadMarkdown(markdown, exportFileName(noteTitle, noteSlug))
          }
        >
          导出 MD
        </button>
      </div>

      <div
        ref={viewRef}
        className={`note-view${isStreaming ? ' note-view--streaming' : ''}`}
        onMouseUp={handleMouseUp}
      >
        <Markdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS}>
          {markdown}
        </Markdown>

        {isStreaming && (
          <section className="note-streaming">
            <div className="note-streaming-status">正在作答…（暂不可标注）</div>
            {streamingQuestion !== '' && <h2>{streamingQuestion}</h2>}
            <Markdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS}>
              {streamingText}
            </Markdown>
            <span className="note-cursor" aria-hidden="true" />
          </section>
        )}

        <div ref={bottomRef} />
      </div>

      {bubble && (
        <button
          type="button"
          className="note-bubble"
          style={{ left: bubble.left, top: bubble.top }}
          // 阻止 mousedown 默认行为（否则点击会先清空选区、气泡丢失）
          onMouseDown={(e) => e.preventDefault()}
          onClick={handleAnnotate}
        >
          标注
        </button>
      )}
    </>
  )
}
