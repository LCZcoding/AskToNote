// 明暗主题管理。
//
// 三种模式：
// - 'system'（默认）：跟随操作系统，系统外观变化时立即同步；
// - 'light' / 'dark'：手动固定，写入 localStorage，刷新后仍生效。
//
// 首屏不闪烁：index.html 里有一段等价的内联脚本，在 CSS 生效前就把
// <html data-theme> 写好；本模块只接管之后的交互与系统偏好变化。
// 两处使用的存储键名与默认值必须保持一致。

export type ThemeMode = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

/** localStorage 键；与 index.html 的内联脚本保持一致。 */
const STORAGE_KEY = 'asktonote.theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

/** 地址栏 / 状态栏配色；与 index.css 的 --canvas 保持一致。 */
const THEME_COLOR: Record<ResolvedTheme, string> = {
  light: '#f7f6f4',
  dark: '#0f1015',
}

const listeners = new Set<(mode: ThemeMode) => void>()

/** 读取已保存的模式；存储不可用（隐私模式等）时退回跟随系统。 */
export function getThemeMode(): ThemeMode {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw === 'light' || raw === 'dark' || raw === 'system' ? raw : 'system'
  } catch {
    return 'system'
  }
}

/** 把模式解析为实际生效的主题（'system' 时查询系统偏好）。 */
export function resolveTheme(mode: ThemeMode = getThemeMode()): ResolvedTheme {
  if (mode !== 'system') return mode
  return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light'
}

/** 写入 <html data-theme> 与 theme-color，并通知订阅者。 */
function paint(mode: ThemeMode) {
  const resolved = resolveTheme(mode)
  document.documentElement.dataset.theme = resolved
  // 手动固定主题后，地址栏不应继续跟着系统走，所以直接改写 theme-color。
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', THEME_COLOR[resolved])
  for (const listener of listeners) listener(mode)
}

/** 设置模式并持久化；'system' 也照存，便于随时回到「跟随系统」。 */
export function setThemeMode(mode: ThemeMode) {
  try {
    localStorage.setItem(STORAGE_KEY, mode)
  } catch {
    // 存不下不影响本次生效。
  }
  paint(mode)
}

/** 订阅模式变化（含系统偏好变化引起的变化）。 */
export function subscribeThemeMode(listener: (mode: ThemeMode) => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

// 系统偏好变化：仅当处于「跟随系统」时重绘。模块只加载一次，监听器常驻。
window
  .matchMedia(DARK_QUERY)
  .addEventListener('change', () => {
    if (getThemeMode() === 'system') paint('system')
  })
