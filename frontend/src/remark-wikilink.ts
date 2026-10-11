// [[...]] 内链插件（Plan §8「内链渲染」）：把 `[[概念名]]` / `[[slug#概念]]`
// 渲染为高亮 `<span class="wikilink">`。v1 只做**轻量高亮、不可点击跳转**
// （跳转 / 知识图谱留 v2）。
//
// 实现说明：这是 **remark 插件**，在 mdast 阶段把文本节点里的 `[[...]]` 换成
// mdast `html` 节点；`NoteView` 的 `react-markdown` 已开启 `rehype-raw`，
// 会把这段原始 HTML 解析成真实的 `<span>` 元素。

/** 只声明用到的 mdast 字段，避免引入额外类型依赖。 */
interface MdNode {
  type: string
  value?: string
  children?: MdNode[]
}

/** 匹配 `[[概念名]]` 或 `[[slug#概念]]`（不支持中括号嵌套）。 */
const WIKILINK_RE = /\[\[([^[\]]+?)\]\]/g

/** 转义 HTML 文本，避免笔记内容里的特殊字符破坏结构或注入标签。 */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** 把一段含 `[[...]]` 的文本拆成「普通文本 + 内链 span」的节点序列。 */
function splitText(value: string): MdNode[] {
  const nodes: MdNode[] = []
  let last = 0

  for (const match of value.matchAll(WIKILINK_RE)) {
    const start = match.index ?? 0
    if (start > last) nodes.push({ type: 'text', value: value.slice(last, start) })

    const inner = match[1].trim()
    const hash = inner.indexOf('#')
    const slug = hash >= 0 ? inner.slice(0, hash).trim() : ''
    const concept = hash >= 0 ? inner.slice(hash + 1).trim() : inner
    const title = slug ? `${slug}#${concept}` : concept
    const dataSlug = slug ? ` data-slug="${escapeHtml(slug)}"` : ''

    nodes.push({
      type: 'html',
      value:
        `<span class="wikilink" title="${escapeHtml(title)}"${dataSlug}>` +
        `${escapeHtml(concept)}</span>`,
    })

    last = start + match[0].length
  }

  if (last < value.length) nodes.push({ type: 'text', value: value.slice(last) })
  return nodes
}

/** 深度优先遍历，替换文本节点里的内链标记。 */
function walk(node: MdNode): void {
  if (!node.children) return
  const next: MdNode[] = []
  for (const child of node.children) {
    if (child.type === 'text' && child.value && child.value.includes('[[')) {
      next.push(...splitText(child.value))
    } else {
      walk(child)
      next.push(child)
    }
  }
  node.children = next
}

/** remark 插件：返回 mdast 转换器（交给 unified 在解析后调用）。 */
export default function remarkWikilink() {
  return (tree: unknown): void => {
    walk(tree as MdNode)
  }
}
