// 笔记区（Plan §8）：用 react-markdown 渲染当前主题的笔记 Markdown。
//
// 关键点：
// - 开启 **rehype-raw**，以渲染后端标注写入的**行内 HTML 锚点**
//   （`<a id="anno-{n}">`）与 `[↩]` 回跳链接（Spec FR-9c）；
// - 「原词 ⇄ 解释」的跳转由浏览器**原生锚点导航**完成，无需额外 JS；
// - 外挂 `remark-wikilink`，把 `[[概念名]]` / `[[slug#概念]]` 高亮（v1 不可点击）；
// - **流式打字机（T2.6）**：`delta` 累积在 `streamingText`，在笔记区**底部**以
//   `## {问题}` 为题实时长出新的 Q/A 块（带光标）；`done` 用后端 `note_markdown`
//   覆盖校正；流式期间禁止划词标注（Plan §8）。

import { useEffect, useRef } from 'react'
import Markdown from 'react-markdown'
import rehypeRaw from 'rehype-raw'
import remarkWikilink from '../remark-wikilink'
import { useIsStreaming, useStore } from '../store'
import './NoteView.css'

export default function NoteView() {
  const markdown = useStore((s) => s.noteMarkdown)
  const streamingQuestion = useStore((s) => s.streamingQuestion)
  const streamingText = useStore((s) => s.streamingText)
  const isStreaming = useIsStreaming()
  const bottomRef = useRef<HTMLDivElement | null>(null)

  // 流式期间自动滚到底部，让新长出的内容始终可见（Plan §8）。
  useEffect(() => {
    if (isStreaming) bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [isStreaming, streamingText])

  return (
    <div className={`note-view${isStreaming ? ' note-view--streaming' : ''}`}>
      <Markdown remarkPlugins={[remarkWikilink]} rehypePlugins={[rehypeRaw]}>
        {markdown}
      </Markdown>

      {isStreaming && (
        <section className="note-streaming">
          <div className="note-streaming-status">正在作答…（暂不可标注）</div>
          {streamingQuestion !== '' && <h2>{streamingQuestion}</h2>}
          <Markdown remarkPlugins={[remarkWikilink]} rehypePlugins={[rehypeRaw]}>
            {streamingText}
          </Markdown>
          <span className="note-cursor" aria-hidden="true" />
        </section>
      )}

      <div ref={bottomRef} />
    </div>
  )
}
