import { useEffect, useRef, useState } from 'react'

// Track only this app visit: a disabled back arrow must not leave the workspace.
export function useAppHistory() {
  const scope = useRef(crypto.randomUUID())
  const cursor = useRef(0)
  const [position, setPosition] = useState({ current: 0, last: 0 })

  useEffect(() => {
    const previousRestoration = window.history.scrollRestoration
    window.history.scrollRestoration = 'manual'
    window.history.replaceState({ ...window.history.state, claimroomNavigation: { scope: scope.current, index: 0 } }, '')
    const onPop = () => {
      const entry = window.history.state?.claimroomNavigation
      cursor.current = entry?.scope === scope.current ? entry.index : 0
      setPosition(previous => ({ ...previous, current: cursor.current }))
    }
    window.addEventListener('popstate', onPop)
    return () => {
      window.removeEventListener('popstate', onPop)
      window.history.scrollRestoration = previousRestoration
    }
  }, [])

  function push(url: string) {
    if (`${window.location.pathname}${window.location.search}${window.location.hash}` === url) return
    cursor.current++
    window.history.pushState({ claimroomNavigation: { scope: scope.current, index: cursor.current } }, '', url)
    setPosition({ current: cursor.current, last: cursor.current })
  }

  return { push, canGoBack: position.current > 0, canGoForward: position.current < position.last }
}
