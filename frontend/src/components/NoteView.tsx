// 笔记区（Plan §8）：用 react-markdown 渲染当前主题的笔记 Markdown。
//
// 关键点：
// - 开启 **rehype-raw**，以渲染后端标注写入的**行内 HTML 锚点**
//   （`<a id="anno-{n}">`）与 `[↩]` 回跳链接（Spec FR-9c）；
// - 「原词 ⇄ 解释」的跳转由浏览器**原生锚点导航**完成，无需额外 JS；
// - 外挂 `remark-wikilink`，把 `[[概念名]]` / `[[slug#概念]]` 高亮（v1 不可点击）。

import Markdown from 'react-markdown'
import rehypeRaw from 'rehype-raw'
import remarkWikilink from '../remark-wikilink'
import { useStore } from '../store'
import './NoteView.css'

export default function NoteView() {
  const markdown = useStore((s) => s.noteMarkdown)

  return (
    <div className="note-view">
      <Markdown remarkPlugins={[remarkWikilink]} rehypePlugins={[rehypeRaw]}>
        {markdown}
      </Markdown>
    </div>
  )
}
