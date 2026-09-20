import { ImageOff, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useProtectedFile } from '@/hooks/useData'
import { cn } from '@/lib/utils'

/**
 * Image served by the authenticated /evidence/files mount. Downloads only once visible (galleries would
 * otherwise spend the per-client rate limit) and never places the JWT in the image URL.
 */
export function ProtectedImage({
  requestPath,
  alt,
  className,
  imgClassName,
  eager = false,
}: {
  requestPath: string | null
  alt: string
  className?: string
  imgClassName?: string
  eager?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(eager)

  useEffect(() => {
    if (eager || visible || !ref.current) return undefined
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true)
          observer.disconnect()
        }
      },
      { rootMargin: '120px' },
    )
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [eager, visible])

  const { url, loading, error } = useProtectedFile(requestPath, visible)

  return (
    <div ref={ref} className={cn('relative flex items-center justify-center overflow-hidden bg-slate-900', className)}>
      {url ? (
        <img src={url} alt={alt} className={cn('h-full w-full object-contain', imgClassName)} draggable={false} />
      ) : loading || (!error && requestPath && !visible) ? (
        <Loader2 className="size-5 animate-spin text-slate-500" aria-label="Loading image" />
      ) : (
        <div className="flex flex-col items-center gap-1 text-[10px] text-slate-500">
          <ImageOff className="size-5" aria-hidden />
          {requestPath ? (error ?? 'Unavailable') : 'No snapshot'}
        </div>
      )}
    </div>
  )
}
