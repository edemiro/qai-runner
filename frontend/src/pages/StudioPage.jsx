import { useCallback, useEffect, useState } from 'react';
import {
  Boxes, Cpu, Loader2, MonitorSmartphone, RefreshCw, Smartphone, Wifi, WifiOff,
} from 'lucide-react';
import { api } from '../api';
import { useToast } from '../hooks/useToast';

function DeviceCard({ device, session, onConnect, onDisconnect, connecting }) {
  const toast = useToast();
  const [apps, setApps] = useState(null);
  const [appId, setAppId] = useState('');
  const [loadingApps, setLoadingApps] = useState(false);
  const connected = Boolean(session);

  const loadApps = async () => {
    if (apps || loadingApps) return;
    setLoadingApps(true);
    try {
      const data = await api.deviceApps(device.udid, device.platform);
      setApps(data.apps || []);
      if (!data.apps?.length) toast.info('No third-party apps were listed for this device.');
    } catch (err) {
      toast.error(err.message);
      setApps([]);
    } finally {
      setLoadingApps(false);
    }
  };

  return (
    <div className={`device-card ${connected ? 'connected' : ''}`}>
      <div className="device-card-top">
        <div className="device-icon">
          {device.isEmulator ? <MonitorSmartphone size={19} /> : <Smartphone size={19} />}
        </div>
        <div className="device-badges">
          <span className={`platform-badge ${device.platform.toLowerCase()}`}>{device.platform}</span>
          {device.isEmulator && <span className="platform-badge sim">virtual</span>}
        </div>
      </div>

      <div className="device-card-body">
        <h4 className="device-name">{device.name}</h4>
        <p className="device-meta">
          {device.platform} {device.version !== 'Unknown' ? device.version : ''} · <code>{device.udid}</code>
        </p>
      </div>

      <div className="device-app-picker">
        <label htmlFor={`app-${device.udid}`}>Target app</label>
        <select
          id={`app-${device.udid}`}
          value={appId}
          onFocus={loadApps}
          onChange={(event) => setAppId(event.target.value)}
          disabled={connected}
        >
          <option value="">Whatever is on screen</option>
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

export function StudioPage({ sessions, activeSessionId, onConnect, onDisconnect, onSelectSession, appiumRunning }) {
  const toast = useToast();
  const [devices, setDevices] = useState([]);
  const [scanning, setScanning] = useState(true);
  const [connectingUdid, setConnectingUdid] = useState(null);

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

  const handleConnect = async (device, appId) => {
    setConnectingUdid(device.udid);
    try {
      await onConnect(device, appId);
    } finally {
      setConnectingUdid(null);
    }
  };

  const sessionFor = (udid) => sessions.find((s) => s.device.udid === udid);

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

      {!appiumRunning && (
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

      {devices.length === 0 ? (
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
          {devices.map((device) => (
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
