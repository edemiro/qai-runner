import { useCallback, useEffect, useState } from 'react';
import {
  Cloud, Cpu, Loader2, MonitorSmartphone, RefreshCw, Search, Smartphone, Wifi, WifiOff, X,
} from 'lucide-react';
import { api } from '../api';
import { PlatformTabs } from '../components/PlatformTabs';
import { OS_TABS, byNewest, isTablet, osOf } from '../lib/platforms';
import { useToast } from '../hooks/useToast';
import './studio.css';

function DeviceCard({ device, session, onConnect, onDisconnect, connecting, busy }) {
  const toast = useToast();
  const [apps, setApps] = useState(null);
  const [environments, setEnvironments] = useState([]);
  const [appId, setAppId] = useState('');
  // A bundle id / package typed by hand, for an app that is on the device but
  // not in the list — a pre-installed enterprise build on a cloud device, say.
  const [customId, setCustomId] = useState('');
  const [loadingApps, setLoadingApps] = useState(true);
  /* Whether the fold under the build buttons is open. null means nobody has
     said, which lets the default below decide; a click writes a boolean and
     from then on the reader's answer stands. */
  const [opened, setOpened] = useState(null);
  const connected = Boolean(session);
  const targetId = customId.trim() || appId;

  /* Read as soon as the card is on screen rather than when the dropdown is
     focused: the TK build buttons below are the point of this card, and they
     cannot be drawn before the phone has said which builds it has. */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.deviceApps(device.udid, device.platform);
        if (cancelled) return;
        setApps(data.apps || []);
        setEnvironments(data.environments || []);
        setLoadingApps(false);
      } catch (err) {
        if (cancelled) return;
        toast.error(err.message);
        setApps([]);
        setLoadingApps(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [device.udid, device.platform, toast]);

  /* With none of the three builds on the device every button above is greyed
     and the list inside the fold is the only way to pick anything, so it opens
     itself rather than leaving a card whose every control is dead. */
  const noBuildFound = !loadingApps && environments.length > 0
    && !environments.some((env) => env.installed);
  const open = opened === null ? noBuildFound : opened;

  /* What the fold is holding, for its own shut line. A target that one of the
     buttons above already names does not belong here — it would be the same
     answer written twice on one card. */
  const named = environments.some((env) => env.appId && env.appId === targetId)
    ? ''
    : (apps || []).find((app) => app.id === targetId)?.name || targetId;

  const chooseApp = (id) => {
    setAppId(id);
    // One target, chosen in one place: a typed id used to beat a highlighted
    // button silently, so the card showed one answer and started another.
    setCustomId('');
  };

  return (
    <div className={`device-card ${connected ? 'connected' : ''}`}>
      <div className="device-card-top">
        <div className="device-icon">
          {device.isEmulator ? <MonitorSmartphone size={19} /> : <Smartphone size={19} />}
        </div>
        <div className="device-badges">
          {/* The version rides in the platform badge. Written under the name as
              well it was the same fact twice on a card 268px wide — three
              times, counting the OS tab the whole grid is filtered by. */}
          <span className={`platform-badge ${device.platform.toLowerCase()}`}>
            {device.platform}
            {device.version && device.version !== 'Unknown' ? ` ${device.version}` : ''}
          </span>
          {device.isEmulator && <span className="platform-badge sim">virtual</span>}
          {device.source === 'browserstack' && (
            <span className="platform-badge cloud" title="Booked from BrowserStack">
              <Cloud size={11} /> cloud
            </span>
          )}
        </div>
      </div>

      <div className="device-card-body">
        <h4 className="device-name">{device.name}</h4>
        {/* Only a local phone needs an identity beyond its name: two identical
            Pixels on one desk are told apart by the udid, and it is what gets
            pasted into adb. A cloud device is a model and a version. */}
        {device.source !== 'browserstack' && (
          <p className="device-meta"><code>{device.udid}</code></p>
        )}
      </div>

      {(loadingApps || environments.length > 0) && (
        <div className="env-picker">
          <span className="env-label">Open on connect</span>
          <div className="env-buttons">
            {loadingApps
              /* Held open while the device answers. The row arriving late used
                 to push Connect down under the hand already reaching for it. */
              ? [0, 1, 2].map((slot) => <span key={slot} className="env-slot" />)
              : environments.map((env) => (
                <button
                  key={env.label}
                  type="button"
                  className={`env-button ${appId && appId === env.appId ? 'active' : ''}`}
                  onClick={() => chooseApp(appId === env.appId ? '' : env.appId)}
                  disabled={connected || !env.installed}
                  title={env.installed
                    ? (env.source && env.source !== 'installed' && env.source !== 'configured'
                      ? `Installs ${env.source} on the device and opens it`
                      : `Open ${env.label} when the session starts`)
                    : (device.source === 'browserstack'
                      ? `No ${env.label} build uploaded to BrowserStack App Automate for ${device.platform}`
                      : `${env.label} is not installed on this device`)}
                >
                  {env.label}
                </button>
              ))}
          </div>
        </div>
      )}

      {/* Everything else about the target app, behind one line. On a screen of
          forty cloud devices this is the same question asked forty times over,
          and the answer is one of the three buttons above nearly every time. */}
      <details
        className="app-fold"
        open={open}
        onToggle={(event) => setOpened(event.currentTarget.open)}
      >
        <summary>
          Another app
          {!open && named && <span className="app-fold-choice">{named}</span>}
        </summary>
        <div className="device-app-picker">
          <label htmlFor={`app-${device.udid}`}>Target app</label>
          <select
            id={`app-${device.udid}`}
            value={appId}
            onChange={(event) => chooseApp(event.target.value)}
            disabled={connected}
          >
            <option value="">
              {device.source === 'browserstack'
                ? 'Pick an uploaded app'
                : 'Whatever is on screen'}
            </option>
            {loadingApps && <option disabled>Loading…</option>}
            {(apps || []).map((app) => (
              <option key={app.id} value={app.id}>
                {app.name} ({app.id})
              </option>
            ))}
          </select>
          {/* An app that is on the device but not in the list — e.g. a THY build
              pre-installed on a cloud device — is launched by typing its id. */}
          <input
            className="device-app-custom"
            type="text"
            value={customId}
            onChange={(event) => {
              setCustomId(event.target.value);
              if (event.target.value.trim()) setAppId('');
            }}
            disabled={connected}
            placeholder={device.platform.toLowerCase() === 'ios'
              ? 'or a bundle id — e.g. com.thy.thytest'
              : 'or a package — e.g. com.thy.thytest'}
            autoComplete="off"
            spellCheck="false"
          />
        </div>
      </details>

      <div className="device-card-actions">
        {connected ? (
          <button className="btn btn-danger btn-block" onClick={() => onDisconnect(session.sessionId)}>
            <WifiOff size={14} />
            Disconnect
          </button>
        ) : (
          <button
            className="btn btn-primary btn-block"
            onClick={() => onConnect(device, targetId)}
            /* Every card locks while any one of them is starting: booking a
               device takes seconds, and the second press used to start a
               second session on whichever card it landed on. */
            disabled={connecting || busy}
          >
            {connecting ? <Loader2 size={14} className="spin" /> : <Wifi size={14} />}
            {connecting ? 'Starting session…' : 'Connect'}
          </button>
        )}
      </div>
    </div>
  );
}

export function StudioPage({
  sessions, activeSessionId, onConnect, onDisconnect, onSelectSession,
  appiumRunning, onOpenSettings,
}) {
  const toast = useToast();
  const [devices, setDevices] = useState([]);
  const [scanning, setScanning] = useState(true);
  const [connectingUdid, setConnectingUdid] = useState(null);

  /* Where the devices come from. Local is the default because a phone on the
     desk needs no account; BrowserStack is offered only once it is configured,
     since an empty picker explains nothing. */
  const [source, setSource] = useState('local');
  /* `loading` starts true because the ask below starts with the page. Starting
     it false made the first paint claim there was no BrowserStack account
     while the question was still out — a settled answer to something nobody
     had answered yet, and the one sentence a tester with an account should
     never be shown. */
  const [cloud, setCloud] = useState({ configured: false, devices: [], loading: true });
  const [filter, setFilter] = useState('');
  // iOS first and selected, which is where the mobile work starts here.
  const [os, setOs] = useState('ios');
  // Phones by default: they are what most testing is against, and a tablet
  // picked by accident is a run against a layout nobody meant to test.
  const [form, setForm] = useState('phone');

  // The button flips `scanning` and bumps the key; the effect only reads. That
  // keeps every synchronous setState in an event handler rather than an effect.
  const [scanKey, setScanKey] = useState(0);
  const scan = useCallback(() => {
    setScanning(true);
    setScanKey((key) => key + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.devices();
        if (!cancelled) setDevices(data.devices || []);
      } catch (err) {
        if (!cancelled) toast.error(err.message);
      } finally {
        if (!cancelled) setScanning(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [scanKey, toast]);

  /* Asked once, and the catalogue is fetched in the same pass when the account
     is set up — with no phone plugged in the page opens straight onto the cloud
     tab, so waiting for a click to load it would leave it empty on arrival.
     A cloud account that is not configured must not turn this page into an
     error: the tab still appears and explains itself. */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await api.browserstackStatus();
        if (cancelled) return;
        if (!status.configured) {
          setCloud((current) => ({ ...current, configured: false, loading: false }));
          return;
        }
        setCloud((current) => ({ ...current, configured: true, loading: true }));
        const data = await api.browserstackDevices();
        if (!cancelled) {
          setCloud({ configured: true, devices: data.devices || [], loading: false });
        }
      } catch {
        // An unreachable or unconfigured backend: local devices still work and
        // the cloud tab says what is missing.
        if (!cancelled) setCloud((current) => ({ ...current, loading: false }));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  /* Loaded when the tab is chosen rather than from an effect watching it: the
     catalogue is hundreds of devices and there is no reason to fetch it for
     someone who only ever uses the phone on their desk. */
  const chooseSource = useCallback(async (next) => {
    setSource(next);
    // Nothing to fetch for an account that has not been set up — the tab
    // explains itself instead, and asking anyway only logs a failed request.
    // Nothing to fetch either while the ask from page load is still out.
    if (next !== 'browserstack' || !cloud.configured || cloud.loading || cloud.devices.length) return;
    setCloud((current) => ({ ...current, loading: true }));
    try {
      const data = await api.browserstackDevices();
      setCloud((current) => ({ ...current, devices: data.devices || [], loading: false }));
    } catch (err) {
      toast.error(err.message);
      setCloud((current) => ({ ...current, loading: false }));
    }
  }, [cloud.configured, cloud.loading, cloud.devices.length, toast]);

  const handleConnect = async (device, appId) => {
    setConnectingUdid(device.udid);
    try {
      await onConnect(device, appId);
    } finally {
      setConnectingUdid(null);
    }
  };

  const sessionFor = (udid) => sessions.find((s) => s.device.udid === udid);

  /* Derived, not stored: with no phone plugged in there is no physical tab to
     be on, so the page falls through to the cloud rather than showing a tab
     that is not there. */
  const effectiveSource = devices.length === 0 ? 'browserstack' : source;

  const needle = filter.trim().toLowerCase();
  const catalogue = effectiveSource === 'browserstack' ? cloud.devices : devices;
  /* Searched on the cloud tab only. A desk holds two or three phones, and a
     field over a list that short is a control with nothing to do. */
  const matching = effectiveSource === 'browserstack'
    ? catalogue.filter((device) =>
        !needle
        || device.name.toLowerCase().includes(needle)
        || `${device.platform} ${device.version}`.toLowerCase().includes(needle))
    : catalogue;

  /* An iPhone and a Pixel have nothing to do with each other — different
     builds, different gestures, different bugs — and BrowserStack hands back
     both in one list of hundreds. The counts sit on the tabs so an empty one
     explains itself: with a single Android plugged in, the Android tab says 1
     rather than the page looking broken on the iOS tab it opens on. */
  const osCounts = {
    ios: matching.filter((device) => osOf(device) === 'ios').length,
    android: matching.filter((device) => osOf(device) === 'android').length,
  };

  /* Tablets are a quarter of the catalogue — 29 of the 111 on this account —
     and a different job: a layout that reflows, gestures with room to miss,
     an app that may not even ship for them. They are kept out of the way
     rather than off the page, counted so the choice is visible. */
  const onThisOs = matching.filter((device) => osOf(device) === os);
  const formCounts = {
    phone: onThisOs.filter((device) => !isTablet(device)).length,
    tablet: onThisOs.filter((device) => isTablet(device)).length,
  };
  const shown = onThisOs
    .filter((device) => isTablet(device) === (form === 'tablet'))
    .slice()
    .sort(byNewest);

  /* Read off the whole catalogue rather than off the search results: counted
     after the search, the switch would appear and disappear under the hand as
     the word "iPad" was typed, moving the field being typed into. */
  const hasTablets = catalogue.some((device) => osOf(device) === os && isTablet(device));

  const otherOs = os === 'ios' ? 'android' : 'ios';
  const otherForm = form === 'phone' ? 'tablet' : 'phone';
  const elsewhereOs = osCounts[otherOs];
  const elsewhereForm = formCounts[otherForm];

  /* The list this page is showing has not come back yet. Every state below is
     a settled statement — no account, nothing matches, none on this OS — and
     none of them is true while the asking is still going on. */
  const looking = effectiveSource === 'browserstack'
    && (cloud.loading || (scanning && devices.length === 0));

  return (
    <main className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">Mobile</h1>
          <p className="page-subtitle">
            Pick a device and connect. The mirror and the inspector open on it.
          </p>
        </div>
        <button className="btn btn-ghost" onClick={scan} disabled={scanning}>
          <RefreshCw size={15} className={scanning ? 'spin' : ''} />
          {scanning ? 'Scanning…' : 'Scan devices'}
        </button>
        {/* No counts until there is a list to count. A tab reading 0 is a
            statement — this OS has nothing — and while the scan is still out
            it is one the page has no way of making. */}
        <PlatformTabs
          options={OS_TABS}
          value={os}
          onChange={setOs}
          counts={looking ? null : osCounts}
        />
      </header>

      {/* BrowserStack is always offered, whether or not it has been set up.
          It used to appear only once credentials were saved, which meant the
          one person who needed to know it existed — someone with no account
          configured — could not see it at all.

          The physical-device tab is the other way round: it is only there when
          a phone is actually plugged in, because an empty "This computer" tab
          is a dead end when the cloud is the way forward. */}
      <div className="device-source studio-tools">
        <div className="source-tabs">
          {devices.length > 0 && (
            <button
              className={`source-tab ${effectiveSource === 'local' ? 'active' : ''}`}
              onClick={() => chooseSource('local')}
            >
              <Smartphone size={13} /> Physical device
              <span className="source-count">{devices.length}</span>
            </button>
          )}
          <button
            className={`source-tab ${effectiveSource === 'browserstack' ? 'active' : ''}`}
            onClick={() => chooseSource('browserstack')}
          >
            <Cloud size={13} /> BrowserStack
            {cloud.devices.length > 0 && (
              <span className="source-count">{cloud.devices.length}</span>
            )}
          </button>
        </div>

        {/* Only where there is a second answer. A desk with two phones on it
            has no tablet question, and a switch reading "Tablets 0" is a
            control that exists to be wrong. */}
        {(hasTablets || form === 'tablet') && (
          <div className="segmented">
            {[['phone', 'Phones'], ['tablet', 'Tablets']].map(([id, label]) => (
              <button
                key={id}
                className={form === id ? 'active' : ''}
                onClick={() => setForm(id)}
              >
                {label}
                <span className="tab-count">{formCounts[id]}</span>
              </button>
            ))}
          </div>
        )}

        {/* A span rather than a label, the way the search on Test Runs is
            built: the clear button beside the field is labelable too, and a
            label with two of those in it is a label for whichever of them the
            browser picks. */}
        {effectiveSource === 'browserstack' && cloud.configured && (
          <span className="device-filter">
            <Search size={13} />
            <input
              type="text"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="Search by model or version — iPhone 15, Pixel, 14.0"
              aria-label="Search devices"
            />
            {filter && (
              <button
                type="button"
                className="studio-search-clear"
                onClick={() => setFilter('')}
                aria-label="Clear search"
              >
                <X size={13} />
              </button>
            )}
          </span>
        )}
      </div>

      {/* Only a local session needs the Appium server; a cloud device is
          booked from BrowserStack's own hub. */}
      {!appiumRunning && effectiveSource === 'local' && (
        <div className="banner warning">
          <Cpu size={15} />
          <span>The Appium server is not running. Start it in Settings before connecting a device.</span>
        </div>
      )}

      {sessions.length > 0 && (
        <section className="session-strip">
          <span className="strip-label">Active sessions</span>
          {/* The sessions on the OS being looked at. A Pixel under the iOS tab
              was as wrong here as in the catalogue below it — and the phones on
              the other one are not lost, they are one click away. */}
          {sessions.filter((s) => osOf(s.device) === os).map((session) => (
            <button
              key={session.sessionId}
              className={`session-pill ${session.sessionId === activeSessionId ? 'active' : ''}`}
              onClick={() => onSelectSession(session.sessionId)}
            >
              <span className="status-dot ok" />
              {session.device.name}
            </button>
          ))}
          {sessions.some((s) => osOf(s.device) !== os) && (
            <button
              className="session-pill elsewhere"
              onClick={() => setOs(os === 'ios' ? 'android' : 'ios')}
            >
              {sessions.filter((s) => osOf(s.device) !== os).length} on
              {' '}{os === 'ios' ? 'Android' : 'iOS'} →
            </button>
          )}
        </section>
      )}

      {looking ? (
        <div className="empty-state">
          <Loader2 size={30} className="spin" />
          <h3>Looking for devices…</h3>
          <p>
            {cloud.loading
              ? 'BrowserStack is being asked which devices this account can book.'
              : 'This computer is being checked for a phone on the desk.'}
          </p>
        </div>
      ) : effectiveSource === 'browserstack' && !cloud.configured ? (
        <div className="empty-state">
          <Cloud size={34} />
          <h3>BrowserStack is not connected yet</h3>
          <p>
            Run on real devices without keeping them on a desk. Add the username and
            access key from your BrowserStack account settings, and the devices this
            account can book will be listed here.
          </p>
          <div className="studio-actions">
            {onOpenSettings && (
              <button className="btn btn-primary" onClick={onOpenSettings}>
                Open Settings
              </button>
            )}
            <button className="btn btn-ghost" onClick={scan} disabled={scanning}>
              {scanning ? 'Scanning…' : 'Scan for a connected device'}
            </button>
          </div>
          {/* Both of these are read once and true every day after that, so they
              cost a line here rather than the paragraph each they used to. */}
          <details className="studio-note">
            <summary>What about the app under test?</summary>
            <p>
              It has to be uploaded to BrowserStack already — QAi picks from what is
              there rather than uploading anything itself.
            </p>
          </details>
          {/* The physical-device tab is hidden while nothing is plugged in, so
              this is the only place left to say that a phone on the desk is
              also an option. */}
          <details className="studio-note">
            <summary>No account? Use a phone on your desk</summary>
            <p>
              Plug in an Android device with USB debugging enabled (<code>adb devices</code>),
              or an iPhone you have trusted this computer on (<code>idevice_id -l</code>) —
              booted emulators and simulators are picked up automatically.
            </p>
          </details>
        </div>
      ) : shown.length === 0 ? (
        /* One state for both sources. The devices a tester is looking for are
           almost always on the page already, one tab across — so it says where
           they are rather than asking again for a search that is not what
           excluded them. */
        <div className="empty-state">
          <Search size={30} />
          <h3>{needle ? 'Nothing here matches that' : 'Nothing on this tab'}</h3>
          {effectiveSource === 'browserstack' && !elsewhereOs && !elsewhereForm && (
            <p>
              {needle
                ? 'Try the model name on its own — “Pixel”, “iPhone 15”.'
                : 'This account listed no bookable devices. Check the plan on BrowserStack.'}
            </p>
          )}
          <div className="studio-actions">
            {elsewhereForm > 0 && (
              <button className="btn btn-ghost btn-sm" onClick={() => setForm(otherForm)}>
                See {elsewhereForm} {otherForm === 'tablet' ? 'tablet' : 'phone'}
                {elsewhereForm === 1 ? '' : 's'}
              </button>
            )}
            {elsewhereOs > 0 && (
              <button className="btn btn-ghost btn-sm" onClick={() => setOs(otherOs)}>
                See {elsewhereOs} on {otherOs === 'ios' ? 'iOS' : 'Android'}
              </button>
            )}
            {needle && (
              <button className="btn btn-ghost btn-sm" onClick={() => setFilter('')}>
                Clear search
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="device-grid">
          {shown.map((device) => (
            <DeviceCard
              key={device.udid}
              device={device}
              session={sessionFor(device.udid)}
              connecting={connectingUdid === device.udid}
              busy={Boolean(connectingUdid)}
              onConnect={handleConnect}
              onDisconnect={onDisconnect}
            />
          ))}
        </div>
      )}
    </main>
  );
}
