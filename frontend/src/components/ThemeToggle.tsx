// 标题栏的明暗切换：一键在亮色 / 深色之间翻转，选择会被记住。
//
// 「跟随系统」这种更细的控制放在设置页（见 SettingsPanel），
// 标题栏只提供最常用的一次点击。

import { setThemeMode } from '../theme'
import { useResolvedTheme, useThemeMode } from '../useTheme'

export default function ThemeToggle() {
  const resolved = useResolvedTheme()
  const mode = useThemeMode()
  const next = resolved === 'dark' ? '亮色' : '深色'
  const current = resolved === 'dark' ? '深色' : '亮色'

  return (
    <button
      type="button"
      className="app-theme"
      data-mode={resolved}
      title={`当前为${current}${mode === 'system' ? '（跟随系统）' : ''}，点击切换到${next}`}
      aria-label={`切换到${next}主题`}
      onClick={() => setThemeMode(resolved === 'dark' ? 'light' : 'dark')}
    >
      <span className="app-theme-icon" aria-hidden="true" />
    </button>
  )
}
