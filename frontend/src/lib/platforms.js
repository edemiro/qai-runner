/**
 * The axes the app is read along.
 *
 * Web against mobile is the first cut a tester makes; within mobile, iOS
 * against Android is the second. They are the same kind of choice, so they are
 * drawn with the same control — these are the option lists it takes.
 *
 * Their own module because a component file may only export components, and
 * because three pages now ask the second question.
 */

// No icons for these two: lucide ships no platform logos, and the two words
// read perfectly well side by side without one invented for them.
export const OS_TABS = [
  { id: 'ios', label: 'iOS' },
  { id: 'android', label: 'Android' },
];

/** Which OS tab a record belongs on. A record that has not said shows on both,
 *  rather than being hidden by a field it predates. */
export const matchesOs = (item, os) => !item?.os || item.os === os;

/** The OS a device or an open session is on, from the only field that says so.
 *  Anything that is not iOS is Android — the two are all there is here, and a
 *  device that named its platform some other way is far likelier to be an
 *  Android build than an iPhone. */
export const osOf = (device) => (
  String(device?.platform || '').toLowerCase() === 'ios' ? 'ios' : 'android'
);

/**
 * The devices that belong on a platform tab.
 *
 * Without this the Android tab offered an iPhone and the iOS tab a Pixel —
 * pickable, and wrong the moment it ran: an iOS recording replayed into an
 * Android build matches nothing, and the run fails for a reason that has
 * nothing to do with the app. `os` of null means "any phone", for the places
 * that have not asked which yet.
 */
export const devicesFor = (devices, os) => (
  (devices || []).filter((device) => !os || osOf(device) === os)
);

/** The same cut over open sessions: web sessions on Web, and on Mobile only
 *  the phones running the OS the tab is about. */
export function sessionsFor(sessions, kind, os = null) {
  return (sessions || []).filter((item) => {
    const device = item?.device || {};
    const isMobile = device.kind === 'mobile';
    if (kind === 'mobile') return isMobile && (!os || osOf(device) === os);
    return !isMobile;
  });
}
