// 设置页（Plan §8「背景展示与刷新」/ Spec FR-18）：
// - 显示当前用户背景（`GET /api/memory`），可**编辑覆盖**——保存即经 `POST /api/memory`
//   写库并置 `background_locked=1`，此后**自动推断不再覆盖**；
// - 提供「刷新背景」按钮 → `POST /api/memory/refresh`，手动重跑一次背景推断
//   （已锁定则后端直接返回已存背景、不调 LLM）。
//
// 会话开始的自动刷新（页面加载/刷新时调一次）在 App 启动逻辑里，不在此组件内。

import { useEffect, useState } from 'react'
import { getMemory, refreshBackground, updateMemory } from '../api'
import './SettingsPanel.css'

/** 设置页为浮动面板，由 App 控制显隐（`onClose` 关闭）。 */
export default function SettingsPanel({ onClose }: { onClose: () => void }) {
  /** 编辑框中的背景文本。 */
  const [background, setBackground] = useState('')
  /** 已落盘的背景（用于判断是否有改动，避免误点保存触发锁定）。 */
  const [saved, setSaved] = useState('')
  const [loading, setLoading] = useState(true)
  /** 保存 / 刷新请求进行中（禁用按钮，避免重复提交）。 */
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  // 打开设置页时拉取最新记忆，仅取背景用于展示与编辑。
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const mem = await getMemory()
        if (cancelled) return
        setBackground(mem.background)
        setSaved(mem.background)
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  /** 「保存」：手改覆盖 → 写库并**锁定**（此后自动推断不再覆盖）。 */
  async function handleSave() {
    if (busy) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await updateMemory({ background })
      setSaved(background)
      setNotice('已保存，此后自动推断不再覆盖（可在下方再次「刷新背景」）')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setBusy(false)
    }
  }

  /** 「刷新背景」：手动重跑一次背景推断（已锁定则返回已存背景）。 */
  async function handleRefresh() {
    if (busy) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const resp = await refreshBackground()
      setBackground(resp.background)
      setSaved(resp.background)
      setNotice(
        resp.background === ''
          ? '暂无可推断的记忆（概念与主题均为空），背景为空'
          : '已刷新背景',
      )
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setBusy(false)
    }
  }

  const dirty = background !== saved

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div
        className="settings-panel"
        role="dialog"
        aria-label="设置"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="settings-head">
          <span className="settings-title">设置</span>
          <button type="button" className="settings-close" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="settings-field">
          <label className="settings-label" htmlFor="settings-background">
            用户背景
          </label>
          {loading ? (
            <p className="settings-hint">正在加载…</p>
          ) : (
            <textarea
              id="settings-background"
              className="settings-textarea"
              rows={3}
              placeholder="（暂未推断出背景；也可在此手动填写你熟悉的领域，例如：熟悉 Java 后端、正在学 Docker）"
              value={background}
              onChange={(e) => setBackground(e.target.value)}
            />
          )}
          <p className="settings-hint">
            由概念记忆自动推断；手动修改并保存后将锁定，自动推断不再覆盖。
          </p>
        </div>

        {error !== '' && (
          <div className="settings-error">
            <span>操作失败：{error}</span>
            <button type="button" onClick={() => setError('')}>
              关闭
            </button>
          </div>
        )}
        {notice !== '' && <p className="settings-notice">{notice}</p>}

        <div className="settings-actions">
          <button
            type="button"
            className="settings-btn"
            disabled={busy || loading}
            onClick={() => void handleRefresh()}
          >
            刷新背景
          </button>
          <button
            type="button"
            className="settings-btn settings-btn--primary"
            disabled={busy || loading || !dirty}
            onClick={() => void handleSave()}
          >
            {busy ? '处理中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}