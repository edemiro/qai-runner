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

/* "Newest first", built from what a device row actually carries.
   BrowserStack offers hundreds in one list and the model a tester wants is
   almost always a recent one, so it should not have to be hunted for.

   The OS version leads because it is unambiguous and comparable across
   brands. The number in the model name breaks the tie, which is what orders
   an iPhone 16 above an iPhone 11 on the same iOS. Four digits and up are a
   year ("iPhone SE 2022"), not a model number, so they are left out of that
   comparison rather than ranking a 2022 above everything. */
function compareVersion(a, b) {
  const left = String(a || '').split('.').map((part) => parseInt(part, 10) || 0);
  const right = String(b || '').split('.').map((part) => parseInt(part, 10) || 0);
  for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
    if ((left[i] || 0) !== (right[i] || 0)) return (right[i] || 0) - (left[i] || 0);
  }
  return 0;
}

function modelNumber(name) {
  const numbers = (String(name || '').match(/\d+/g) || [])
    .map(Number)
    .filter((value) => value < 1000);
  return numbers.length ? Math.max(...numbers) : 0;
}

export function byNewest(a, b) {
  return compareVersion(a.version, b.version)
    || modelNumber(b.name) - modelNumber(a.name)
    || String(a.name || '').localeCompare(String(b.name || ''));
}

/* Read off the model name, which is the only place it is written — neither
   BrowserStack's device list nor a local adb scan says what shape a device is.
   Every tablet on this account is an iPad or a Galaxy Tab, and "tab" is
   matched as a whole word so it does not catch a phone whose name merely
   contains those letters.

   Anything unrecognised counts as a phone, so a tablet QAi has not seen
   before turns up in the default list rather than vanishing from both. */
const TABLET_NAMES = /\bipad\b|\btablets?\b|\btab\b|mediapad/i;
export const isTablet = (device) => TABLET_NAMES.test(String(device?.name || ''));

/** Phones first, newest first — the order a picker should offer them in.
 *  Tablets are a quarter of this account's catalogue and a different job. */
export function forPicking(devices, os) {
  return devicesFor(devices, os)
    .slice()
    .sort((a, b) => (isTablet(a) - isTablet(b)) || byNewest(a, b));
}
