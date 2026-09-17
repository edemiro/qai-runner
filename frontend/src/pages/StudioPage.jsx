import { useCallback, useEffect, useState } from 'react';
import {
  Boxes, Cloud, Cpu, Loader2, MonitorSmartphone, RefreshCw, Search, Smartphone,
  Wifi, WifiOff,
} from 'lucide-react';
import { api } from '../api';
import { useToast } from '../hooks/useToast';

function DeviceCard({ device, session, onConnect, onDisconnect, connecting }) {
  const toast = useToast();
  const [apps, setApps] = useState(null);
  const [environments, setEnvironments] = useState([]);
  const [appId, setAppId] = useState('');
  const [loadingApps, setLoadingApps] = useState(true);
  const connected = Boolean(session);

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

  return (
    <div className={`device-card ${connected ? 'connected' : ''}`}>
      <div className="device-card-top">
        <div className="device-icon">
          {device.isEmulator ? <MonitorSmartphone size={19} /> : <Smartphone size={19} />}
        </div>
        <div className="device-badges">
          <span className={`platform-badge ${device.platform.toLowerCase()}`}>{device.platform}</span>
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
        <p className="device-meta">
          {device.platform} {device.version !== 'Unknown' ? device.version : ''}
          {device.source === 'browserstack' ? ' · BrowserStack' : <> · <code>{device.udid}</code></>}
        </p>
      </div>

      {environments.length > 0 && (
        <div className="env-picker">
          <span className="env-label">Open on connect</span>
          <div className="env-buttons">
            {environments.map((env) => (
              <button
                key={env.label}
                type="button"
                className={`env-button ${appId === env.appId ? 'active' : ''}`}
                onClick={() => setAppId(appId === env.appId ? '' : env.appId)}
                disabled={connected || !env.installed}
                title={env.installed
                  ? `Open ${env.label} when the session starts`
                  : `${env.label} is not installed on this device`}
              >
                {env.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="device-app-picker">
        <label htmlFor={`app-${device.udid}`}>Target app</label>
        <select
          id={`app-${device.udid}`}
          value={appId}
          onChange={(event) => setAppId(event.target.value)}
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
      </div>

      <div className="device-card-actions">
        {connected ? (
          <button className="btn btn-danger btn-block" onClick={() => onDisconnect(session.sessionId)}>
            <WifiOff size={14} />
            Disconnect
          </button>
        ) : (
          <button
            className="btn btn-primary btn-block"
            onClick={() => onConnect(device, appId)}
            disabled={connecting}
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
  const [cloud, setCloud] = useState({ configured: false, devices: [], loading: false });
  const [filter, setFilter] = useState('');

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
    if (next !== 'browserstack' || !cloud.configured || cloud.devices.length) return;
    setCloud((current) => (current.loading ? current : { ...current, loading: true }));
    try {
      const data = await api.browserstackDevices();
      setCloud((current) => ({ ...current, devices: data.devices || [], loading: false }));
    } catch (err) {
      toast.error(err.message);
      setCloud((current) => ({ ...current, loading: false }));
    }
  }, [cloud.configured, cloud.devices.length, toast]);

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
  const shown = effectiveSource === 'browserstack'
    ? cloud.devices.filter((device) =>
        !needle
        || device.name.toLowerCase().includes(needle)
        || `${device.platform} ${device.version}`.toLowerCase().includes(needle))
    : devices;

  return (
    <main className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">Mobile</h1>
          <p className="page-subtitle">
            Connect a device, then test it. Several devices can run at once.
          </p>
        </div>
        <button className="btn btn-ghost" onClick={scan} disabled={scanning}>
          <RefreshCw size={15} className={scanning ? 'spin' : ''} />
          {scanning ? 'Scanning…' : 'Scan devices'}
        </button>
      </header>

      {/* BrowserStack is always offered, whether or not it has been set up.
          It used to appear only once credentials were saved, which meant the
          one person who needed to know it existed — someone with no account
          configured — could not see it at all.

          The physical-device tab is the other way round: it is only there when
          a phone is actually plugged in, because an empty "This computer" tab
          is a dead end when the cloud is the way forward. */}
      <div className="device-source">
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
        {effectiveSource === 'browserstack' && cloud.configured && (
          <label className="device-filter">
            <Search size={13} />
            <input
              type="text"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="Filter by model or version — iPhone 15, Pixel, 14.0"
            />
          </label>
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
          {sessions.map((session) => (
            <button
              key={session.sessionId}
              className={`session-pill ${session.sessionId === activeSessionId ? 'active' : ''}`}
              onClick={() => onSelectSession(session.sessionId)}
            >
              <span className="status-dot ok" />
              {session.device.name}
            </button>
          ))}
        </section>
      )}

      {effectiveSource === 'browserstack' && !cloud.configured ? (
        <div className="empty-state">
          <Cloud size={34} />
          <h3>BrowserStack is not connected yet</h3>
          <p>
            Run on real devices without keeping them on a desk. Add the username and
            access key from your BrowserStack account settings, and the devices this
            account can book will be listed here.
          </p>
          <p className="muted-tiny">
            The app under test has to be uploaded to BrowserStack already — QAi picks
            from what is there rather than uploading anything itself.
          </p>
          {onOpenSettings && (
            <button className="btn btn-primary" onClick={onOpenSettings}>
              Open Settings
            </button>
          )}
        </div>
      ) : effectiveSource === 'browserstack' && cloud.loading ? (
        <div className="empty-state">
          <Loader2 size={30} className="spin" />
          <h3>Reading the device list…</h3>
          <p>BrowserStack is being asked which devices this account can book.</p>
        </div>
      ) : shown.length === 0 && effectiveSource === 'browserstack' ? (
        <div className="empty-state">
          <Cloud size={34} />
          <h3>{filter ? 'No device matches that' : 'No devices came back'}</h3>
          <p>
            {filter
              ? 'Try the model name on its own — “Pixel”, “iPhone 15”.'
              : 'This account listed no bookable devices. Check the plan on BrowserStack.'}
          </p>
        </div>
      ) : shown.length === 0 ? (
        <div className="empty-state">
          <Boxes size={34} />
          <h3>No devices detected</h3>
          <p>
            Plug in an Android device with USB debugging enabled (<code>adb devices</code>), or connect an
            iPhone and trust this computer (<code>idevice_id -l</code>). Booted emulators and simulators are
            picked up automatically.
          </p>
          <button className="btn btn-primary" onClick={scan} disabled={scanning}>
            {scanning ? 'Scanning…' : 'Scan again'}
          </button>
        </div>
      ) : (
        <div className="device-grid">
          {shown.map((device) => (
            <DeviceCard
              key={device.udid}
              device={device}
              session={sessionFor(device.udid)}
              connecting={connectingUdid === device.udid}
              onConnect={handleConnect}
              onDisconnect={onDisconnect}
            />
          ))}
        </div>
      )}
    </main>
  );
}
