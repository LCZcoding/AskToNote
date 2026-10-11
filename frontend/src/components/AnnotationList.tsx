// 标注列表（Plan §8 / T3.4）：展示划词收集到的标注片段，展示在**提问/确认区上方**。
//
// 仅负责展示与移除；「整批一次性调模式B → 候选问题列表」在 T3.5 接入。

import { useStore } from '../store'
import './AnnotationList.css'

export default function AnnotationList() {
  const annotations = useStore((s) => s.annotations)
  const removeAnnotation = useStore((s) => s.removeAnnotation)

  if (annotations.length === 0) return null

  return (
    <div className="anno-list">
      <div className="anno-list-head">
        <span className="anno-list-label">已标注 {annotations.length} 处</span>
      </div>
      <ul className="anno-list-items">
        {annotations.map((a) => (
          <li key={a.id} className="anno-item">
            <span className="anno-item-text">{a.snippet}</span>
            <button
              type="button"
              className="anno-item-remove"
              aria-label={`移除标注：${a.snippet}`}
              onClick={() => removeAnnotation(a.id)}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
