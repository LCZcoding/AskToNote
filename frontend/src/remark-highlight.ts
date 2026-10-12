// `==...==` 强调高亮插件（Plan §8「强调高亮渲染」，Spec FR-15）：把 `==名词==`
// 渲染为 `<mark class="note-mark">`。
//
// 为什么用 `==...==`：这是 Typora / Obsidian 等通用 Markdown 编辑器原生支持的
// **高亮语法**，笔记导出成 `.md` 后在这些程序里同样能渲染出强调效果；行内代码
// （`` `名词` ``）表示「本主题首次提及」，`==名词==` 表示「本笔记已解释过」。
//
// 实现说明：这是 **remark 插件**，在 mdast 阶段把文本节点里的 `==...==` 换成
// mdast `html` 节点；`NoteView` 的 `react-markdown` 已开启 `rehype-raw`，会把这段
// 原始 HTML 解析成真实的 `<mark>` 元素。插件**只处理 `text` 节点**，故行内代码
// （`inlineCode`）、代码块（`code`）里的 `==` 不会被误处理。

/** 只声明用到的 mdast 字段，避免引入额外类型依赖。 */
interface MdNode {
  type: string
  value?: string
  children?: MdNode[]
}

// 匹配 `==文本==`：内容**不以 `=` 或空白开头/结尾**、且**不跨行**——避免把
// `a == b == c` 这类普通比较表达式误判为高亮（首尾 `=` 相邻时不匹配）。
const HIGHLIGHT_RE = /==([^=\s](?:[^=\n]*[^=\s])?)==/g

/** 转义 HTML 文本，避免笔记内容里的特殊字符破坏结构或注入标签。 */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** 把一段含 `==...==` 的文本拆成「普通文本 + 高亮 mark」的节点序列。 */
function splitText(value: string): MdNode[] {
  const nodes: MdNode[] = []
  let last = 0

  for (const match of value.matchAll(HIGHLIGHT_RE)) {
    const start = match.index ?? 0
    if (start > last) nodes.push({ type: 'text', value: value.slice(last, start) })

    nodes.push({
      type: 'html',
      value: `<mark class="note-mark">${escapeHtml(match[1])}</mark>`,
    })

    last = start + match[0].length
  }

  if (last < value.length) nodes.push({ type: 'text', value: value.slice(last) })
  return nodes
}

/** 深度优先遍历，替换文本节点里的高亮标记。 */
function walk(node: MdNode): void {
  if (!node.children) return
  const next: MdNode[] = []
  for (const child of node.children) {
    if (child.type === 'text' && child.value && child.value.includes('==')) {
      next.push(...splitText(child.value))
    } else {
      walk(child)
      next.push(child)
    }
  }
  node.children = next
}

/** remark 插件：返回 mdast 转换器（交给 unified 在解析后调用）。 */
export default function remarkHighlight() {
  return (tree: unknown): void => {
    walk(tree as MdNode)
  }
}
