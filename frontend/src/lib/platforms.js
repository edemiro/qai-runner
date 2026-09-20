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
