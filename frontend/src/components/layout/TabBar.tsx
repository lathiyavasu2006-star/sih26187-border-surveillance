import { Plus, X } from 'lucide-react'
import type { MouseEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { iconForPath, NAV_ITEMS } from '@/lib/navigation'
import { cn } from '@/lib/utils'
import { useUiStore } from '@/stores/uiStore'

/** Browser-style tab strip. Every visited page opens a tab; the Dashboard tab cannot be closed. */
export function TabBar() {
  const tabs = useUiStore((state) => state.tabs)
  const closeTab = useUiStore((state) => state.closeTab)
  const menuOpen = useUiStore((state) => state.newTabMenuOpen)
  const setMenuOpen = useUiStore((state) => state.setNewTabMenuOpen)
  const location = useLocation()
  const navigate = useNavigate()

  const close = (path: string, event?: MouseEvent) => {
    event?.stopPropagation()
    event?.preventDefault()
    const next = closeTab(path)
    if (next && location.pathname === path) navigate(next)
  }

  return (
    <div className="flex h-9 shrink-0 items-end gap-0.5 border-b border-line bg-slate-100/80 px-2 pt-1" role="tablist" aria-label="Open pages">
      <div className="scrollbar-thin flex min-w-0 flex-1 items-end gap-0.5 overflow-x-auto">
        {tabs.map((tab) => {
          const active = location.pathname === tab.path
          const Icon = iconForPath(tab.path)
          const closable = tab.path !== '/dashboard'
          return (
            <div
              key={tab.path}
              role="tab"
              aria-selected={active}
              tabIndex={0}
              onClick={() => navigate(tab.path)}
              onAuxClick={(event) => {
                if (event.button === 1 && closable) close(tab.path, event)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') navigate(tab.path)
              }}
              className={cn(
                'group relative flex h-8 min-w-[120px] max-w-[200px] cursor-pointer select-none items-center gap-2 rounded-t-lg px-3 text-xs font-medium transition-colors',
                active
                  ? 'z-10 bg-white text-slate-900 shadow-[0_-1px_0_#e5e7eb,1px_0_0_#e5e7eb,-1px_0_0_#e5e7eb]'
                  : 'text-slate-500 hover:bg-white/60 hover:text-slate-800',
              )}
              data-testid={`tab-${tab.path}`}
            >
              {active ? <span className="absolute inset-x-2 top-0 h-0.5 rounded-b bg-hud" aria-hidden /> : null}
              <Icon className={cn('size-3.5 shrink-0', active ? 'text-cyan-700' : '')} aria-hidden />
              <span className="flex-1 truncate">{tab.title}</span>
              {closable ? (
                <button
                  type="button"
                  aria-label={`Close ${tab.title}`}
                  onClick={(event) => close(tab.path, event)}
                  className={cn(
                    'rounded p-0.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700',
                    active ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
                  )}
                >
                  <X className="size-3" />
                </button>
              ) : null}
            </div>
          )
        })}
      </div>
      <DropdownMenu.Root open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenu.Trigger asChild>
          <button type="button" className="mb-1 rounded-md p-1.5 text-slate-500 hover:bg-white hover:text-slate-900" aria-label="Open page in new tab" title="New tab (Ctrl+T / Alt+T)">
            <Plus className="size-3.5" />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content align="end" sideOffset={4} className="z-[1100] min-w-48 rounded-xl border border-line bg-white p-1 shadow-xl">
            {NAV_ITEMS.map((item) => (
              <DropdownMenu.Item
                key={item.path}
                onSelect={() => navigate(item.path)}
                className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-xs text-slate-700 outline-none data-[highlighted]:bg-slate-100"
              >
                <item.icon className="size-3.5 text-slate-500" />
                {item.title}
              </DropdownMenu.Item>
            ))}
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  )
}
