// 主题相关的 React 绑定，逻辑全部在 theme.ts。

import { useEffect, useState } from 'react'
import {
  getThemeMode,
  resolveTheme,
  subscribeThemeMode,
  type ResolvedTheme,
  type ThemeMode,
} from './theme'

/** 当前选择的模式（含 'system'），供设置页的分段控件使用。 */
export function useThemeMode(): ThemeMode {
  const [mode, setMode] = useState<ThemeMode>(getThemeMode)
  useEffect(() => subscribeThemeMode(setMode), [])
  return mode
}

/** 当前实际生效的主题（'light' | 'dark'），供切换按钮决定图标与下一步动作。 */
export function useResolvedTheme(): ResolvedTheme {
  const [resolved, setResolved] = useState<ResolvedTheme>(() => resolveTheme())
  useEffect(
    () => subscribeThemeMode(() => setResolved(resolveTheme())),
    [],
  )
  return resolved
}
