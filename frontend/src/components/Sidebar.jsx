import {
  Activity,
  Bug,
  Globe,
  History,
  KeyRound,
  Layers,
  Moon,
  Settings as SettingsIcon,
  Smartphone,
  Sun,
  Wifi,
  WifiOff,
  ClipboardList,
} from 'lucide-react';

// One workspace per target, then the things that span targets: the suites CI
// runs, the history of every run, and what that history says about the suite.
const NAV = [
  { id: 'web', label: 'Web', icon: Globe, hint: 'Open a page and test it' },
  { id: 'mobile', label: 'Mobile', icon: Smartphone, hint: 'Connect a device and test it' },
  { id: 'suites', label: 'Test Sets', icon: Layers, hint: 'Scenarios grouped for repeatable execution' },
  { id: 'executions', label: 'Test Executions', icon: ClipboardList, hint: 'Test Set runs, scenario verdicts and reports' },
  { id: 'test-data', label: 'Test Data', icon: KeyRound, hint: 'The values scenarios reach by name' },
  { id: 'runs', label: 'Test Runs', icon: History, hint: 'Every run, with steps and script export' },
  { id: 'insights', label: 'Insights', icon: Activity, hint: 'Trends and flaky tests' },
  { id: 'bugs', label: 'Bug Report', icon: Bug, hint: 'Defects raised from failed scenarios' },
  { id: 'settings', label: 'Settings', icon: SettingsIcon, hint: 'Appium server and model provider' },
];

const byId = Object.fromEntries(NAV.map((item) => [item.id, item]));

/* Grouped rather than listed flat: seven equal rows made the reader scan for
   the one they wanted every time. The headings say what each part is for. */
const NAV_GROUPS = [
  { title: 'PLATFORM', items: ['web', 'mobile'].map((id) => byId[id]) },
  // Test Data last, under the executions: the two rows above are what a
  // tester opens every day, and the store is what they open when a card
  // expires. Putting it between them pushed the run list down the list for a
  // page most days never need.
  { title: 'TEST SUITE', items: ['suites', 'executions', 'test-data'].map((id) => byId[id]) },
  // Bugs above Insights: a defect is something to act on today, trends are
  // something to read at the end of the week.
  { title: 'REPORT', items: ['runs', 'bugs', 'insights'].map((id) => byId[id]) },
];

const USER = { name: 'Ergün Demiro', role: 'QA Engineer' };

function initials(name) {
  return name
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join('');
}

export function Sidebar({ activeTab, onTabChange, theme, onToggleTheme, connectedDevice, appiumStatus }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <div className="brand">
          {/* A check inside a play: the two things the product does — run
              something, and say whether it passed. */}
          {/* Motion lines plus a verdict: "runner" on the left, "pass" on the
              right. The lines are what keep it from being another check in a
              square at favicon size. */}
          {/* The Q of QA, whose tail is the verdict mark — one shape for the
              two things the product is about. */}
          <div className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
              <defs>
                <linearGradient id="qai-mark" x1="2" y1="0" x2="30" y2="32"
                                gradientUnits="userSpaceOnUse">
                  <stop stopColor="#5b8def" />
                  <stop offset="1" stopColor="#b06fe6" />
                </linearGradient>
              </defs>
              <rect width="32" height="32" rx="9" fill="url(#qai-mark)" />
              <circle cx="15" cy="15.2" r="7.4" fill="none" stroke="#fff" strokeWidth="3.1" />
              <path d="M13.7 16.7l3.6 3.7 7.6-8" fill="none" stroke="url(#qai-mark)"
                    strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M13.7 16.7l3.6 3.7 7.6-8" fill="none" stroke="#fff"
                    strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <div className="brand-text">
            <span className="brand-name">QAi&nbsp;Runner</span>
            <span className="brand-tag">Mobile &amp; Web QA</span>
          </div>
        </div>

        <nav className="nav">
          {NAV_GROUPS.map((group) => (
            <div className="nav-group" key={group.title ?? 'other'}>
              {group.title && <span className="nav-group-title">{group.title}</span>}
              {group.items.map(({ id, label, icon: Icon, hint }) => (
                <button
                  key={id}
                  className={`nav-item ${activeTab === id ? 'active' : ''}`}
                  onClick={() => onTabChange(id)}
                  title={hint}
                  aria-current={activeTab === id ? 'page' : undefined}
                >
                  <Icon size={17} />
                  <span>{label}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>
      </div>

      <div className="sidebar-bottom">
        {/* Settings is not a place you go to work — it is where you go once to
            configure and then leave alone, so it sits with the other standing
            controls rather than competing with the workspaces. */}
        <button
          className={`nav-item settings-item ${activeTab === 'settings' ? 'active' : ''}`}
          onClick={() => onTabChange('settings')}
          title={byId.settings.hint}
          aria-current={activeTab === 'settings' ? 'page' : undefined}
        >
          <SettingsIcon size={17} />
          <span>Settings</span>
        </button>

        <div className="status-strip">
          <div className={`status-line ${appiumStatus === 'running' ? 'ok' : appiumStatus === 'starting' ? 'warn' : 'off'}`}>
            <span className="status-dot" />
            <span>Appium {appiumStatus}</span>
          </div>
          <div className={`status-line ${connectedDevice ? 'ok' : 'off'}`}>
            {connectedDevice ? <Wifi size={12} /> : <WifiOff size={12} />}
            <span className="truncate">{connectedDevice ? connectedDevice.name : 'No device'}</span>
          </div>
        </div>

        <div className="user-card">
          <div className="avatar-initials" aria-hidden="true">
            {initials(USER.name)}
          </div>
          <div className="user-text">
            <span className="user-name">{USER.name}</span>
            <span className="user-role">{USER.role}</span>
          </div>
          <button
            className="theme-toggle"
            onClick={onToggleTheme}
            title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            aria-label="Toggle colour theme"
          >
            {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
          </button>
        </div>
      </div>
    </aside>
  );
}
