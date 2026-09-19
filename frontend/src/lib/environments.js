/**
 * The TK web environments, grouped the way the test team lists them.
 *
 * Kept as data rather than something the tester retypes each time: these hosts
 * differ by one token and a wrong one produces a run against the wrong stack
 * that still looks plausible in the report. Picking one fills the address bar,
 * which stays editable — a scenario often needs a path or query on top of the
 * host, so the list is a starting point, not a cage.
 */

export const DEFAULT_ENV_URL = 'https://nuat.turkishairlines.com/';

export const ENV_GROUPS = [
  {
    label: 'Ortak Paketler',
    items: [
      { name: 'PROD', url: 'https://www.turkishairlines.com/' },
      { name: 'NSIT', url: 'https://nsit.turkishairlines.com/' },
      { name: 'NUAT', url: 'https://nuat.turkishairlines.com/' },
      { name: 'NSTAGING', url: 'https://nstaging.turkishairlines.com/' },
      { name: 'NUAT-REDESIGN', url: 'https://nuat-redesign.turkishairlines.com' },
      { name: 'NUAT-CORESERVICES', url: 'https://nuat-coreservices.turkishairlines.com' },
      { name: 'NUAT2-CORESERVICES', url: 'https://nuat2-coreservices.turkishairlines.com/' },
      { name: 'NUAT3-CORESERVICES', url: 'https://nuat3-coreservices.turkishairlines.com' },
    ],
  },
  {
    label: 'Alışveriş & İçerik',
    items: [
      { name: 'NSIT4', url: 'https://nsit4.turkishairlines.com/' },
      { name: 'NUAT4', url: 'https://nuat4.turkishairlines.com/' },
      { name: 'NUAT8', url: 'https://nuat8.turkishairlines.com/' },
      { name: 'NUATSHOP', url: 'https://nuat-shopping.turkishairlines.com/' },
      { name: 'NUATSHOP2', url: 'https://nuat2-shopping.turkishairlines.com/' },
      { name: 'NUATSHOP3', url: 'https://nuat3-shopping.turkishairlines.com/' },
    ],
  },
  {
    label: 'Biletleme & Ek Hizmetler',
    items: [
      { name: 'NSIT3', url: 'https://nsit3.turkishairlines.com/' },
      { name: 'NUAT3', url: 'https://nuat3.turkishairlines.com/' },
      { name: 'NUAT7', url: 'https://nuat7.turkishairlines.com/' },
      { name: 'NUATADTK', url: 'https://nuat-additionalservices-ticketing.turkishairlines.com/' },
      { name: 'NUATADTK2', url: 'https://nuat2-additionalservices-ticketing.turkishairlines.com/' },
      { name: 'NUATADTK3', url: 'https://nuat3-additionalservices-ticketing.turkishairlines.com/' },
    ],
  },
  {
    label: 'Satış Sonrası',
    items: [
      { name: 'NSIT6', url: 'https://nsit6.turkishairlines.com/' },
      { name: 'NUAT6', url: 'https://nuat6.turkishairlines.com/' },
      { name: 'NUATPSTB', url: 'https://nuat-postbooking.turkishairlines.com/' },
      { name: 'NUATPSTB2', url: 'https://nuat2-postbooking.turkishairlines.com/' },
      { name: 'NUATPSTB3', url: 'https://nuat3-postbooking.turkishairlines.com/' },
    ],
  },
  {
    label: 'Miles&Smiles',
    items: [
      { name: 'NSIT2', url: 'https://nsit2.turkishairlines.com/' },
      { name: 'NUAT2', url: 'https://nuat2.turkishairlines.com/' },
      { name: 'NSIT5', url: 'https://nsit5.turkishairlines.com/' },
      { name: 'NUAT5', url: 'https://nuat5.turkishairlines.com/' },
      { name: 'NUAT9', url: 'https://nuat9.turkishairlines.com/' },
      { name: 'NUATMS', url: 'https://nuat-milessmiles.turkishairlines.com/' },
      { name: 'NUATMS2', url: 'https://nuat2-milessmiles.turkishairlines.com/' },
      { name: 'NUATMS3', url: 'https://nuat3-milessmiles.turkishairlines.com/' },
      { name: 'NUATMS4', url: 'https://nuat4-milessmiles.turkishairlines.com/' },
    ],
  },
];

/** The environment whose host the address currently points at, if any. */
export function matchEnv(url) {
  let host;
  try {
    host = new URL(url).host;
  } catch {
    return null;
  }
  for (const group of ENV_GROUPS) {
    for (const item of group.items) {
      if (new URL(item.url).host === host) return item;
    }
  }
  return null;
}
