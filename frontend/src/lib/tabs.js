/**
 * The pages of the app, in the order the sidebar lists them.
 *
 * Their own module because a component file may only export components: the
 * address bar needs the list too, and exporting it from Sidebar.jsx cost that
 * file Fast Refresh and failed the lint gate.
 */

import {
  Activity,
  Bug,
  ClipboardList,
  Globe,
  History,
  KeyRound,
  Layers,
  Settings as SettingsIcon,
  Smartphone,
} from 'lucide-react';

// One workspace per target, then the things that span targets: the suites CI
// runs, the history of every run, and what that history says about the suite.
export const NAV = [
  { id: 'web', label: 'Web', icon: Globe, hint: 'Open a page and test it' },
  { id: 'mobile', label: 'Mobile', icon: Smartphone, hint: 'Connect a device and test it' },
  { id: 'suites', label: 'Test Sets', icon: Layers, hint: 'Scenarios grouped for repeatable execution' },
  { id: 'executions', label: 'Test Executions', icon: ClipboardList, hint: 'Test Set runs, scenario verdicts and reports' },
  { id: 'test-data', label: 'Test Data', icon: KeyRound, hint: 'The values scenarios reach by name' },
  { id: 'runs', label: 'Test Runs', icon: History, hint: 'Every run, with steps and script export' },
  { id: 'insights', label: 'Insights', icon: Activity, hint: 'Trends and flaky tests' },
  { id: 'bugs', label: 'Bug Report', icon: Bug, hint: 'Defects raised from failed scenarios' },
  { id: 'settings', label: 'Settings', icon: SettingsIcon, hint: 'Devices, models and integrations' },
];

// The pages there are, for the address bar: anything else in a URL is not one
// of them, and must not be rendered as though it were.
export const TABS = NAV.map((item) => item.id);
