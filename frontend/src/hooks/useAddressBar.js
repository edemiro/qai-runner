import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Keep a piece of state in the address bar, so the app has somewhere to be.
 *
 * There were no URLs at all. Every page was the same address, so Back left the
 * product entirely, a reload always came home to the Web workspace, and a
 * failing run could not be sent to the person who needed to see it — which for
 * a team's test tool is most of the point of having a report.
 *
 * Written against the History API rather than a router: this is one address
 * with a page name and at most one open thing on it, and a routing library
 * would bring a tree of routes, a matcher and a bundle for a question that is
 * two fields wide.
 */

/**
 * `/executions/ab12` → { tab: 'executions', id: 'ab12' }.
 *
 * A page nobody has is the fallback, not whatever the app renders last. Any
 * unknown first segment used to arrive as a tab of its own and fall through
 * the page list to the Mobile workspace — so a typo or a stale link landed on
 * the screen that owns the live device session, one button away from
 * disconnecting a phone in the middle of a run.
 */
export function readAddress(fallback, known) {
  const parts = window.location.pathname.split('/').filter(Boolean);
  const tab = parts[0] || fallback;
  if (known && !known.includes(tab)) return { tab: fallback, id: null };
  return { tab, id: parts[1] || null };
}

function write(tab, id, replace) {
  const path = `/${tab}${id ? `/${id}` : ''}`;
  if (path === window.location.pathname) return;
  // `replace` for the first correction of an address nobody typed; `push` for
  // a move the reader made, which is the one Back should undo.
  window.history[replace ? 'replaceState' : 'pushState']({ tab, id }, '', path);
}

/**
 * @param fallback which page an address with nothing in it means.
 * @param known the pages there are. Anything else in the address is not one.
 * @returns [{tab, id}, go] — `go(tab, id)` moves, and the browser's own
 *   Back and Forward move it too.
 */
export function useAddressBar(fallback, known) {
  const [where, setWhere] = useState(() => readAddress(fallback, known));

  // The address is corrected to whatever we actually opened on, without
  // adding an entry — otherwise the first Back goes to the URL the reader
  // never chose.
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    write(where.tab, where.id, true);
  }, [where.tab, where.id]);

  useEffect(() => {
    const onPop = () => setWhere(readAddress(fallback, known));
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [fallback, known]);

  const go = useCallback((tab, id = null) => {
    setWhere((current) => {
      if (current.tab === tab && current.id === id) return current;
      write(tab, id, false);
      return { tab, id };
    });
  }, []);

  return [where, go];
}
