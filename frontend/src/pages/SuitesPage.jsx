import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowRight,
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  Layers,
  Loader2,
  Play,
  Plus,
  Repeat,
  Sparkles,
  Trash2,
} from 'lucide-react';

import { EmptyState } from '../components/EmptyState';
import { DEFAULT_PLATFORM, PlatformTabs } from '../components/PlatformTabs';
import { ScenarioGenerator } from '../components/ScenarioGenerator';
import { StepEditor } from '../components/StepEditor';
import { api } from '../api';
import { DEFAULT_ENV_URL, ENV_GROUPS } from '../lib/environments';
import { OS_TABS, devicesFor, matchesOs, osOf, sessionsFor } from '../lib/platforms';
import { useToast } from '../hooks/useToast';

// Sentinel for "a Test Set that does not exist yet" in the move-to picker.
const MOVE_NEW = '__new__';

const EMPTY_CASE = { name: '', goal: '', url: '', tags: '', dataset: '', steps: [] };

/** Parse the tag input the same way everywhere: comma or space separated. */
function parseTags(raw) {
  return (raw || '')
    .split(/[,\s]+/)
    .map((tag) => tag.trim().toLowerCase())
    .filter(Boolean);
}

/**
 * A dataset is entered as CSV or JSON, because that is what teams already have.
 * Returns {rows} or {error} rather than throwing — the caller shows the error
 * inline next to the field instead of as a toast.
 */
function parseDataset(raw) {
  const text = (raw || '').trim();
  if (!text) return { rows: null };

  if (text.startsWith('[') || text.startsWith('{')) {
    try {
      const parsed = JSON.parse(text);
      const rows = Array.isArray(parsed) ? parsed : parsed.rows;
      if (!Array.isArray(rows)) return { error: 'JSON must be a list of rows.' };
      return { rows: rows.filter((row) => row && typeof row === 'object') };
    } catch (err) {
      return { error: `That is not valid JSON: ${err.message}` };
    }
  }

  const lines = text.split(/\r?\n/).filter((line) => line.trim());
  if (lines.length < 2) return { error: 'CSV needs a header row and at least one data row.' };
  const headers = lines[0].split(',').map((h) => h.trim());
  const rows = lines.slice(1).map((line) => {
    const cells = line.split(',');
    return Object.fromEntries(headers.map((header, i) => [header, (cells[i] ?? '').trim()]));
  });
  return { rows };
}

const BANDS = ['Critical', 'High', 'Medium', 'Low'];

/** What a Test Set actually protects, at a glance. A set that is all Low and
 *  one that is half Critical are different objects, and the count alone hides
 *  that. */
function PriorityStrip({ cases }) {
  const counts = BANDS.map((band) => ({
    band,
    n: cases.filter((item) => item.priority === band).length,
  })).filter((entry) => entry.n);
  if (!counts.length) return null;
  return (
    <div className="priority-strip">
      {counts.map(({ band, n }) => (
        <span key={band} className={`priority-tag p-${band.toLowerCase()}`}>
          {band} {n}
        </span>
      ))}
    </div>
  );
}

/**
 * Where a run points: an environment for web, a phone and a build for mobile.
 *
 * Its own component because there are two places a run starts from — the set's
 * own Run panel and the bar that appears when scenarios are picked — and only
 * the first had this. Picking mobile scenarios and pressing Run produced
 * "Pick the device this execution should run on" with nothing on screen to
 * pick it with, because the control was below the scenario list the tester had
 * just scrolled past.
 */
function RunTarget({
  isMobile, os = null, devices, sessions, deviceUdid, onDevice, deviceAppId, onBuild,
  deviceBuilds, loadingBuilds = false, loadingDevices = false,
  envUrl, onEnv, compact = false,
}) {
  if (!isMobile) {
    return (
      <div className={`run-target ${compact ? 'compact' : ''}`}>
        <label className="run-target-env">
          Environment
          <select value={envUrl} onChange={(e) => onEnv(e.target.value)}>
            <option value="">Each scenario's own address</option>
            {ENV_GROUPS.map((group) => (
              <optgroup key={group.label} label={group.label}>
                {group.items.map((item) => (
                  <option key={item.name} value={item.url} title={item.url}>
                    {item.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
      </div>
    );
  }

  /* Only the phones that can actually run this set. An Android set offered an
     iPhone here, and picking it booked an iPhone to run an Android build on —
     a failure with nothing to do with the app under test. */
  const onOs = devicesFor(devices, os);
  const open = sessionsFor(sessions, 'mobile', os);
  const unopened = onOs.filter(
    (d) => !open.some((s) => s.device?.udid === d.udid),
  );
  const osLabel = os === 'android' ? 'Android' : 'iOS';
  return (
    <div className={`run-target ${compact ? 'compact' : ''}`}>
      <label>
        Device
        <select
          value={deviceUdid}
          onChange={(e) => onDevice(e.target.value)}
        >
          {/* The cloud catalogue is a network call away, and this said "No iOS
              device found" for the whole of it — a settled answer to a
              question still being asked, on an account with sixty-four of
              them. */}
          <option value="">
            {onOs.length || open.length
              ? `Pick an ${osLabel} device`
              : loadingDevices ? 'Looking for devices…'
                : `No ${osLabel} device found`}
          </option>
          {open.length > 0 && (
            <optgroup label="Already connected">
              {open.map((item) => (
                <option key={item.sessionId} value={item.device.udid}>
                  {item.device.name} · {item.device.platform}
                </option>
              ))}
            </optgroup>
          )}
          {unopened.length > 0 && (
            <optgroup label="Available">
              {unopened.map((d) => (
                <option key={d.udid} value={d.udid}>
                  {d.name} · {d.platform}{d.source === 'browserstack' ? ' · cloud' : ''}
                </option>
              ))}
            </optgroup>
          )}
        </select>
      </label>
      <label>
        Build
        <select value={deviceAppId} onChange={(e) => onBuild(e.target.value)} disabled={!deviceUdid}>
          {/* "No TK build on this device" used to show while the list was
              still being fetched, which reads as a settled answer to a
              question nobody had asked yet — and on a cloud device the answer
              takes a few seconds and is usually "here are seventeen". */}
          <option value="">
            {!deviceUdid ? 'Pick a device first'
              : loadingBuilds ? 'Looking for builds…'
                : deviceBuilds.length ? 'Whatever is open'
                  : 'No TK build on this device'}
          </option>
          {/* `appId`, not `id`. An environment row has no `id`, so the option
              fell back to its own text and BrowserStack was asked to launch an
              app called "ThyReg" — which is how every mobile run started from
              here died with INCOMPATIBLE_CAPABILITIES. One with no build
              behind it is offered greyed rather than hidden, so the picker
              still says the environment exists. */}
          {deviceBuilds.map((build) => (
            <option
              key={build.appId || build.label}
              value={build.appId || ''}
              disabled={!build.appId}
            >
              {build.label}{build.appId ? '' : ' — no build uploaded'}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

export function SuitesPage({
  onRunHere, onOpenExecution, onWatchExecution,
  // The phones already connected, and the way to connect another. A mobile
  // execution runs on a session the tester opened, so the build on it — and
  // the permissions it was granted — are the ones they picked.
  sessions = [], onConnectDevice = null,
}) {
  const toast = useToast();

  const [suites, setSuites] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [platform, setPlatform] = useState(DEFAULT_PLATFORM);
  // Which phone, once Mobile is the platform. Meaningless on Web, so it is
  // simply not shown there rather than kept in step with something.
  const [os, setOs] = useState('ios');
  // null while nobody has touched the picker, which means "whichever platform
  // the page is on". Held as an override rather than synced to the tab from an
  // effect: a set created on the Mobile tab should be a mobile set without
  // having to say so twice, and saying so once should still win.
  const [newSuiteKind, setNewSuiteKind] = useState(null);
  const [suite, setSuite] = useState(null);
  const [loading, setLoading] = useState(true);

  const [newSuiteName, setNewSuiteName] = useState('');
  // Optional module ("Uçuş Arama") the new set files under; sets sharing one
  // are listed together under a heading.
  const [newSuiteModule, setNewSuiteModule] = useState('');
  const [draft, setDraft] = useState(EMPTY_CASE);
  const [datasetError, setDatasetError] = useState(null);

  /* Everything that writes something new lives at the foot of the page, folded
     away. A tester opens a set to run it and to read what it covers; the forms
     that name a set and write scenarios into it were the first thing on screen,
     and pushed the scenarios they came for a thousand pixels down. */
  const [showAdd, setShowAdd] = useState(false);
  const [showCaseForm, setShowCaseForm] = useState(false);
  const [showNewSet, setShowNewSet] = useState(false);
  /* Settings of a run rather than the decision to make one, and reference
     material rather than the job: closed until somebody asks. */
  const [showOptions, setShowOptions] = useState(false);
  const [showCi, setShowCi] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const addRef = useRef(null);

  /* Selection is held by case id, not by Test Set, and survives switching sets.
     That is what makes "combine two sets into one execution" the same gesture
     as "pick four scenarios out of this one" — no separate mode for it. */
  const [picked, setPicked] = useState(() => new Map());
  // Where picked scenarios go when moved: an existing set's id, or NEW_SET
  // with a name (and optional module) for one created on the spot.
  const [moveTarget, setMoveTarget] = useState('');
  const [moveNewName, setMoveNewName] = useState('');
  const [moveNewModule, setMoveNewModule] = useState('');
  const [moving, setMoving] = useState(false);
  const [executionName, setExecutionName] = useState('');

  // Where the run points. For web that is an environment whose origin replaces
  // each scenario's own; for mobile it is a device and the build to open on it.
  const [envUrl, setEnvUrl] = useState(DEFAULT_ENV_URL);
  const [deviceUdid, setDeviceUdid] = useState('');
  const [deviceAppId, setDeviceAppId] = useState('');
  const [connecting, setConnecting] = useState(false);
  // Which scenario's precondition data is being filled in, and what has been
  // typed so far. Held here rather than per row so only one form is open.
  const [dataFor, setDataFor] = useState(null);
  const [dataValues, setDataValues] = useState({});
  const [savingData, setSavingData] = useState(false);
  const [devices, setDevices] = useState([]);
  // The cloud catalogue is a network call away; without this the picker
  // said "No iOS device found" for the whole of it.
  const [loadingDevices, setLoadingDevices] = useState(true);
  const [deviceBuilds, setDeviceBuilds] = useState([]);
  // Whether the build list is still being fetched. Set where the device is
  // chosen rather than from the effect that fetches, because setting state
  // inside an effect is what this codebase avoids everywhere else.
  const [loadingBuilds, setLoadingBuilds] = useState(false);

  const [options, setOptions] = useState({
    workers: 2,
    tags: '',
    // Headed: the sites these suites run against drop a headless browser at
    // the network layer, which surfaces as every scenario failing at once.
    headless: false,
    trace: true,
    recordVideo: false,
    failOnPageError: true,
  });

  const [running, setRunning] = useState(false);
  const [history, setHistory] = useState([]);
  // Which scenarios have their steps *closed* — the set is empty to begin with,
  // so every scenario opens showing its steps. Tracking the exception rather
  // than the rule is what makes open the default without having to seed this
  // from the cases each time they load.
  const [closedSteps, setClosedSteps] = useState(() => new Set());

  const toggleSteps = (caseId) => {
    setClosedSteps((current) => {
      const next = new Set(current);
      if (next.has(caseId)) next.delete(caseId); else next.add(caseId);
      return next;
    });
  };

  /* The builds belong to the phone that was chosen, so choosing another one
     invalidates them. In one place because the run bar and the generator set
     the same phone, and a copy of this that forgot a line is how the two ended
     up naming different builds. */
  const pickDevice = (value) => {
    setDeviceUdid(value);
    setDeviceAppId('');
    setDeviceBuilds([]);
    setLoadingBuilds(Boolean(value));
  };

  /* The section is at the foot of a page that is mostly scenario list, so the
     button in the set's header takes you to it rather than silently unfolding
     something two screens down. */
  const openAdd = () => {
    setShowAdd(true);
    addRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const counts = {
    web: suites.filter((item) => (item.kind || 'web') !== 'mobile').length,
    mobile: suites.filter((item) => item.kind === 'mobile').length,
  };
  const onPlatform = suites.filter((item) => (item.kind || 'web') === platform);

  /* iOS and Android are the same product and not the same screen: different
     controls, different labels, different selectors. A set written on one does
     not read on the other, and a recording taken on one would be replayed into
     the wrong app — so within Mobile they are separated the way Web and Mobile
     are. A set that has not said which shows on both, rather than being hidden
     by a field it predates. */
  const osCounts = {
    ios: onPlatform.filter((item) => item.os !== 'android').length,
    android: onPlatform.filter((item) => item.os !== 'ios').length,
  };
  const visible = platform === 'mobile'
    ? onPlatform.filter((item) => matchesOs(item, os))
    : onPlatform;

  // What the platform picker in the create form means, as the two fields the
  // backend stores: a web set has no OS, a mobile one always does.
  const newSuiteTarget = newSuiteKind || (platform === 'mobile' ? os : 'web');
  const newSuiteFields = newSuiteTarget === 'web'
    ? { kind: 'web', os: null }
    : { kind: 'mobile', os: newSuiteTarget };

  /* Derived rather than synced through an effect: filtering to a platform the
     selected set is not on used to leave that set open on the right while the
     list beside it said there was nothing — two panes disagreeing about what is
     on screen. Falling through to the first visible set keeps them agreeing
     without a second copy of the selection to keep in step. */
  const shownId = visible.some((item) => item.id === selectedId)
    ? selectedId
    : (visible[0]?.id ?? null);

  const loadSuites = useCallback(async () => {
    try {
      const data = await api.suites();
      setSuites(data.suites);
      setSelectedId((current) => current || data.suites[0]?.id || null);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    let cancelled = false;
    // Wrapped rather than called directly: every setState inside loadSuites
    // happens after an await, so none of them land in this commit.
    (async () => {
      if (!cancelled) await loadSuites();
    })();
    return () => {
      cancelled = true;
    };
  }, [loadSuites]);

  useEffect(() => {
    let cancelled = false;

    if (!shownId) {
      queueMicrotask(() => {
        if (!cancelled) setSuite(null);
      });
      return () => {
        cancelled = true;
      };
    }

    (async () => {
      try {
        const [detail, runs] = await Promise.all([
          api.suite(shownId),
          api.suiteRuns(shownId, 10),
        ]);
        if (cancelled) return;
        setSuite(detail);
        setHistory(runs.suiteRuns);
      } catch (err) {
        if (!cancelled) toast.error(err.message);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [shownId, toast]);


  const createSuite = async (event) => {
    event.preventDefault();
    const name = newSuiteName.trim();
    if (!name) return;
    try {
      const created = await api.createSuite({
        name, ...newSuiteFields, tags: [], module: newSuiteModule.trim() || null,
      });
      setNewSuiteName('');
      setNewSuiteModule('');
      setNewSuiteKind(null);
      // Follow the new set to its own tab rather than leaving it filtered out
      // of the list it was just added to.
      setPlatform(newSuiteFields.kind);
      if (newSuiteFields.os) setOs(newSuiteFields.os);
      setSelectedId(created.id);
      await loadSuites();
      toast.success(`Test set “${name}” created.`);
    } catch (err) {
      toast.error(err.message);
    }
  };

  const addCase = async (event) => {
    event.preventDefault();
    if (!suite) return;
    if (!draft.name.trim() || !draft.goal.trim()) {
      toast.warning('A case needs a name and a goal.');
      return;
    }

    const { rows, error } = parseDataset(draft.dataset);
    if (error) {
      setDatasetError(error);
      return;
    }

    try {
      await api.addCase(suite.id, {
        name: draft.name.trim(),
        goal: draft.goal.trim(),
        url: draft.url.trim() || null,
        tags: parseTags(draft.tags),
        dataset: rows,
        // Blank rows are dropped rather than sent: an empty step is one the
        // runner would skip and the report would have to explain.
        steps: draft.steps.filter((step) => step.action.trim()),
      });
      setDraft(EMPTY_CASE);
      setDatasetError(null);
      setShowCaseForm(false);
      setSuite(await api.suite(suite.id));
      await loadSuites();
      toast.success('Case added.');
    } catch (err) {
      toast.error(err.message);
    }
  };

  const removeCase = async (caseId) => {
    try {
      await api.deleteCase(caseId);
      setSuite(await api.suite(suite.id));
      await loadSuites();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const toggleCase = async (item) => {
    try {
      await api.updateCase(item.id, {
        name: item.name, goal: item.goal, url: item.url,
        tags: item.tags, enabled: !item.enabled,
      });
      setSuite(await api.suite(suite.id));
      await loadSuites();
    } catch (err) {
      toast.error(err.message);
    }
  };

  const removeSuite = async () => {
    if (!suite) return;
    /* Asked before, not regretted after. The button that does this is a bare
       icon next to "Case", one slip away from a set somebody spent a morning
       writing. The count is in the question so what is about to go is clear —
       and so is what stays, because past executions are a record of what
       happened and are kept. */
    const n = suite.cases.length;
    const confirmed = window.confirm(
      `Delete “${suite.name}” and its ${n} scenario${n === 1 ? '' : 's'}?`
      + `${history.length ? '\nExecutions already run are kept in Test Executions.' : ''}`
      + '\n\nThis cannot be undone.',
    );
    if (!confirmed) return;
    try {
      await api.deleteSuite(suite.id);
      setSelectedId(null);
      setSuite(null);
      await loadSuites();
      toast.info('Test set deleted.');
    } catch (err) {
      toast.error(err.message);
    }
  };

  const togglePick = (item) => {
    setPicked((current) => {
      const next = new Map(current);
      if (next.has(item.id)) next.delete(item.id);
      else next.set(item.id, { name: item.name, suite: suite.name });
      return next;
    });
  };

  const pickAllInSet = () => {
    setPicked((current) => {
      const next = new Map(current);
      const enabled = suite.cases.filter((c) => c.enabled);
      const allIn = enabled.every((c) => next.has(c.id));
      enabled.forEach((c) => (allIn ? next.delete(c.id) : next.set(c.id, { name: c.name, suite: suite.name })));
      return next;
    });
  };

  const pickedSets = new Set([...picked.values()].map((p) => p.suite));

  /** Re-file the picked scenarios into another set — chosen, or named and
   *  created here — so a set that grew from several requests is split back
   *  into one set per request without retyping anything. */
  const movePicked = async () => {
    if (!picked.size || moving) return;
    let targetId = moveTarget;
    if (!targetId) {
      toast.warning('Pick a Test Set to move the scenarios into, or name a new one.');
      return;
    }
    setMoving(true);
    try {
      if (targetId === MOVE_NEW) {
        const name = moveNewName.trim();
        if (!name) {
          toast.warning('Name the new Test Set.');
          return;
        }
        const created = await api.createSuite({
          name, kind: suite.kind, tags: [], module: moveNewModule.trim() || suite.module || null,
        });
        targetId = created.id;
      }
      const result = await api.moveCases([...picked.keys()], targetId);
      toast.success(`${result.moved} scenario${result.moved === 1 ? '' : 's'} moved to “${result.suiteName}”.`);
      setPicked(new Map());
      setMoveTarget('');
      setMoveNewName('');
      setMoveNewModule('');
      setSuite(await api.suite(suite.id));
      await loadSuites();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setMoving(false);
    }
  };

  /** Start the picked scenarios as an execution and go watch it.
   *
   *  Started on the server rather than streamed to this page: a streamed run
   *  dies the moment you navigate away, which is what left executions sitting
   *  at "running" with nothing in them. The scenarios run in their own browsers
   *  — not in the workspace session — so Test Executions, not the Web or Mobile
   *  screen, is where the run is actually visible.
   */
  /* What to send as the run's target. For a mobile set this may have to open a
     session first: the tester picks a phone and a build, and the execution runs
     on a session — opening it through the same path the Mobile workspace uses
     means the permission prompts are answered and the app is in front before
     the first scenario starts. Returns null when the run must not go ahead. */
  /* Only for a mobile set, and only once one is open: the cloud catalogue is
     hundreds of devices and there is no reason to fetch it for someone running
     a web suite. */
  const isMobileSet = (suite?.kind || 'web') === 'mobile';

  // Fetched for the Mobile tab, not only for an open mobile set: the
  // picker in Write scenarios sits above the sets and is offered before
  // one is chosen. Declared here because `runOs` below reads it — it
  // was further down, and the page crashed on every render with
  // "Cannot access 'wantsDevices' before initialization".
  const wantsDevices = isMobileSet || platform === 'mobile';

  /* Which phones this set can run on: what the set itself says, and for one
     written before that field existed, the tab it is being read on. */
  const runOs = wantsDevices ? (suite?.os || os) : null;

  /* A device chosen on one OS must not survive into the other. Switching from
     the iOS set to the Android one used to keep the iPhone selected — gone
     from the list, still in the state, and still the phone Run would book.
     Derived, not cleared from an effect, so the picker and what Run does can
     never disagree for a render. */
  const pickedDevice = deviceUdid
    ? (devices.find((d) => d.udid === deviceUdid)
      || sessions.find((s) => s.device?.udid === deviceUdid)?.device || null)
    : null;
  const runUdid = pickedDevice && runOs && osOf(pickedDevice) !== runOs
    ? ''
    : deviceUdid;

  const resolveTarget = async (kind) => {
    if (kind !== 'mobile') return { envUrl: envUrl || null };

    const open = sessions.find((item) => item.device?.udid === runUdid);
    if (open && (!deviceAppId || open.device?.appId === deviceAppId)) {
      return { deviceSessionId: open.sessionId };
    }
    if (!runUdid) {
      toast.warning('Pick the device this execution should run on.');
      return null;
    }
    if (!onConnectDevice) {
      toast.warning('Connect the device in the Mobile workspace first.');
      return null;
    }
    setConnecting(true);
    try {
      const device = open?.device || devices.find((d) => d.udid === runUdid);
      if (!device) {
        toast.error('That device is no longer listed. Rescan in the Mobile workspace.');
        return null;
      }
      const session = await onConnectDevice(device, deviceAppId || null);
      if (!session) return null;
      return { deviceSessionId: session.sessionId };
    } finally {
      setConnecting(false);
    }
  };

  useEffect(() => {
    if (!wantsDevices) return undefined;
    let cancelled = false;
    (async () => {
      const found = [];
      for (const load of [api.devices, api.browserstackDevices]) {
        try {
          const data = await load();
          found.push(...(data.devices || []));
        } catch {
          /* one source being unavailable must not hide the other */
        }
      }
      if (!cancelled) {
        setDevices(found);
        setLoadingDevices(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [wantsDevices]);

  // The builds on the chosen phone, matched to ThyDev / ThyTest / ThyReg by the
  // backend — the same list the Mobile workspace offers.
  useEffect(() => {
    if (!wantsDevices || !runUdid) return undefined;
    const device = devices.find((d) => d.udid === runUdid);
    if (!device) return undefined;
    let cancelled = false;
    (async () => {
      // Asking a cloud account what it has uploaded takes a few seconds, and
      // the picker said "No TK build on this device" the whole time.
      try {
        const data = await api.deviceApps(device.udid, device.platform);
        if (!cancelled) setDeviceBuilds(data.environments || []);
      } catch {
        if (!cancelled) setDeviceBuilds([]);
      } finally {
        if (!cancelled) setLoadingBuilds(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [wantsDevices, runUdid, devices]);

  const openDataForm = (item) => {
    setDataFor(item.id);
    setDataValues({ ...(item.precondition_data || {}) });
  };

  /* Answering what the scenario asked for is what turns it on: it was disabled
     because it was waiting on exactly this. The backend decides that, so the
     rule lives in one place rather than being re-derived here. */
  const saveData = async (item) => {
    setSavingData(true);
    try {
      await api.setPreconditionData(item.id, dataValues);
      setDataFor(null);
      setSuite(await api.suite(suite.id));
      await loadSuites();
      toast.success('Saved. The scenario is ready to run.');
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSavingData(false);
    }
  };

  const runPicked = async () => {
    if (!picked.size || running) return;
    if (!executionName.trim()) {
      toast.warning('Name the execution so it can be told apart in the list.');
      return;
    }
    setRunning(true);
    try {
      const target = await resolveTarget(suite?.kind || 'web');
      if (!target) return;
      const { suiteRunId } = await api.startExecution({
        caseIds: [...picked.keys()],
        name: executionName.trim(),
        workers: Number(options.workers) || 1,
        headless: options.headless,
        trace: options.trace,
        recordVideo: options.recordVideo,
        failOnPageError: options.failOnPageError,
        ...target,
      });
      toast.success(`“${executionName.trim()}” started.`);
      setPicked(new Map());
      setExecutionName('');
      // Straight to the workspace, watching. A list of status chips is not
      // what someone who just pressed Run wants to look at.
      if (onWatchExecution) onWatchExecution(suiteRunId, suite?.kind || 'web');
      else onOpenExecution?.(suiteRunId);
    } catch (err) {
      toast.error(`Execution could not start: ${err.message}`);
    } finally {
      setRunning(false);
    }
  };

  /** Run the whole set — same server-side start as a hand-picked execution,
   *  so it survives leaving this page, and lands where it can be watched. */
  const runSuite = async () => {
    if (!suite || running) return;
    setRunning(true);
    try {
      const target = await resolveTarget(suite?.kind || 'web');
      if (!target) return;
      const { suiteRunId } = await api.startSuiteRun(suite.id, {
        workers: Number(options.workers) || 1,
        tags: parseTags(options.tags).length ? parseTags(options.tags) : null,
        headless: options.headless,
        trace: options.trace,
        recordVideo: options.recordVideo,
        failOnPageError: options.failOnPageError,
        ...target,
      });
      toast.success(`“${suite.name}” started.`);
      if (onWatchExecution) onWatchExecution(suiteRunId, suite?.kind || 'web');
      else onOpenExecution?.(suiteRunId);
    } catch (err) {
      toast.error(`Execution could not start: ${err.message}`);
    } finally {
      setRunning(false);
    }
  };

  const executionCount = useMemo(() => {
    if (!suite) return 0;
    return suite.cases
      .filter((item) => item.enabled)
      .reduce((total, item) => total + (item.dataset?.length || 1), 0);
  }, [suite]);

  if (loading) {
    return (
      <main className="page">
        <p className="muted">Loading suites…</p>
      </main>
    );
  }

  return (
    <main className="page">
      <header className="page-header">
        <div>
          <h1 className="page-title">Test Sets</h1>
          <p className="page-subtitle">
            Scenarios live here. Pull them into an execution to run them — locally or from CI.
          </p>
        </div>
        <PlatformTabs value={platform} onChange={setPlatform} counts={counts} />
        {platform === 'mobile' && (
          <PlatformTabs
            options={OS_TABS} value={os} onChange={setOs} counts={osCounts} sub
          />
        )}
      </header>

      <div className="suites-layout">
        {/* ------------------------------------------------------- list ---- */}
        <aside className="suite-list card">
          {suites.length === 0 ? (
            <EmptyState icon={Layers} title="No Test Sets yet" compact>
              Name one below to get started, then generate scenarios into it — or
              save a run you already like from Test Runs.
            </EmptyState>
          ) : visible.length === 0 ? (
            <EmptyState icon={Layers} title={`No ${platform} Test Sets`} compact>
              Create one with the platform picker set to {platform}.
            </EmptyState>
          ) : (
            <ul className="suite-items">
              {/* Sets file under a module ("Uçuş Arama" holding one-way,
                  round-trip, multi-city…), so the list is drawn as groups with a
                  heading each. Sets with no module come first, unheaded. */}
              {Object.entries(
                visible.reduce((groups, item) => {
                  const key = (item.module || '').trim();
                  (groups[key] ||= []).push(item);
                  return groups;
                }, {}),
              )
                .sort(([a], [b]) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b)))
                .map(([module, items]) => (
                  <li key={module || '__none__'} className="suite-group">
                    {module && (
                      <div className="suite-group-label" title={`Module: ${module}`}>
                        {module}
                        <span className="suite-group-count">{items.length}</span>
                      </div>
                    )}
                    <ul className="suite-items">
                      {items.map((item) => (
                        <li key={item.id}>
                          <button
                            className={`suite-item ${item.id === shownId ? 'active' : ''}`}
                            onClick={() => setSelectedId(item.id)}
                          >
                            <Layers size={15} />
                            {/* No platform badge on the row: the list is one
                                platform now, so it said the same thing on every
                                line and took the width off the name — which is
                                the one thing on the row that differs. */}
                            <span className="suite-item-name">{item.name}</span>
                            <span className="pill">{item.case_count}</span>
                            <ChevronRight size={14} className="suite-item-chevron" />
                          </button>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
            </ul>
          )}

          {/* A set is named once and read from every day after, so the form
              that names one sits under the list it adds to. */}
          <div className="suite-new">
            <button
              type="button"
              className="card-toggle sub"
              onClick={() => setShowNewSet((value) => !value)}
              aria-expanded={showNewSet}
            >
              {showNewSet ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              New Test Set
            </button>
            {showNewSet && (
              <form className="suite-create" onSubmit={createSuite}>
                <input
                  value={newSuiteName}
                  onChange={(event) => setNewSuiteName(event.target.value)}
                  placeholder="New test set name"
                  aria-label="New test set name"
                />
                <input
                  className="suite-create-module"
                  value={newSuiteModule}
                  onChange={(event) => setNewSuiteModule(event.target.value)}
                  placeholder="Module (optional) — e.g. Uçuş Arama"
                  aria-label="Module the new test set belongs to"
                  title="Sets sharing a module are listed together"
                />
                {/* Chosen at creation, not later: the platform decides which
                    scenarios can go in and which device can run them, so a set
                    that changed platform afterwards would strand its own cases. */}
                <select
                  value={newSuiteTarget}
                  onChange={(event) => setNewSuiteKind(event.target.value)}
                  aria-label="Platform for the new test set"
                >
                  <option value="web">Web</option>
                  <option value="ios">iOS</option>
                  <option value="android">Android</option>
                </select>
                <button className="btn btn-primary btn-sm" type="submit" disabled={!newSuiteName.trim()}>
                  <Plus size={14} /> Add
                </button>
              </form>
            )}
          </div>
        </aside>

        {/* ---------------------------------------------------- detail ----- */}
        <section className="suite-detail">
          {!suite ? (
            <div className="card">
              <EmptyState icon={Layers} title="Nothing selected">
                {suites.length
                  ? 'Pick a Test Set on the left to see and run its scenarios.'
                  : 'Create a Test Set to start collecting scenarios.'}
              </EmptyState>
            </div>
          ) : (
            <>
              <div className="card">
                <div className="card-head">
                  <div>
                    {suite.module && (
                      <div className="suite-module-label" title="Module this set belongs to">
                        {suite.module}
                      </div>
                    )}
                    <h2 className="card-title">{suite.name}</h2>
                    <p className="muted small">
                      {suite.cases.filter((c) => c.enabled).length} enabled ·{' '}
                      {executionCount} execution{executionCount === 1 ? '' : 's'}
                      {executionCount !== suite.cases.filter((c) => c.enabled).length
                        && ' (dataset rows expand)'}
                    </p>
                    <PriorityStrip cases={suite.cases} />
                  </div>
                  <div className="row-actions">
                    <button className="btn btn-sm" onClick={pickAllInSet} disabled={!suite.cases.length}>
                      Select all
                    </button>
                    <button className="btn btn-sm" onClick={openAdd}>
                      <Plus size={14} /> Add scenarios
                    </button>
                    <button className="btn btn-danger btn-sm" onClick={removeSuite}>
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>

                {/* Where this run points, and the button that starts it — the
                    first thing under the set's name because it is what the page
                    is opened to do. A set carries the address each scenario was
                    written against and whatever phone happened to be plugged in;
                    neither is a choice anyone made at the moment they pressed
                    Run, which is when it matters. */}
                <div className="run-bar">
                  <RunTarget
                    isMobile={isMobileSet}
                    os={runOs}
                    devices={devices}
                    sessions={sessions}
                    deviceUdid={runUdid}
                    onDevice={pickDevice}
                    deviceAppId={deviceAppId}
                    onBuild={setDeviceAppId}
                    deviceBuilds={deviceBuilds}
                    loadingBuilds={loadingBuilds}
                    loadingDevices={loadingDevices}
                    envUrl={envUrl}
                    onEnv={setEnvUrl}
                  />

                  {/* Workers, tags and the recordings are settings of a run, not
                      the decision to make one: they keep their value between runs
                      and are changed once in a while, so they fold away. */}
                  <button
                    type="button"
                    className="run-bar-options"
                    onClick={() => setShowOptions((value) => !value)}
                    aria-expanded={showOptions}
                  >
                    {showOptions ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    Options
                  </button>

                  {running ? (
                    <button className="btn btn-primary btn-sm" disabled>
                      <Loader2 size={14} className="spin" /> Starting…
                    </button>
                  ) : (
                    <button
                      className="btn btn-primary btn-sm"
                      onClick={runSuite}
                      disabled={executionCount === 0}
                    >
                      <Play size={14} /> Run suite
                    </button>
                  )}
                </div>

                {showOptions && (
                  <div className="run-options">
                    <label>
                      Workers
                      <input
                        type="number"
                        min={1}
                        max={8}
                        value={options.workers}
                        onChange={(e) => setOptions({ ...options, workers: e.target.value })}
                      />
                    </label>
                    <label>
                      Only these tags
                      <input
                        value={options.tags}
                        onChange={(e) => setOptions({ ...options, tags: e.target.value })}
                        placeholder="smoke"
                      />
                    </label>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={options.headless}
                        onChange={(e) => setOptions({ ...options, headless: e.target.checked })}
                      />
                      Headless
                    </label>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={options.trace}
                        onChange={(e) => setOptions({ ...options, trace: e.target.checked })}
                      />
                      Record trace
                    </label>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={options.recordVideo}
                        onChange={(e) => setOptions({ ...options, recordVideo: e.target.checked })}
                      />
                      Record video
                    </label>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={options.failOnPageError}
                        onChange={(e) => setOptions({ ...options, failOnPageError: e.target.checked })}
                      />
                      Fail on console/network errors
                    </label>
                  </div>
                )}

                {picked.size > 0 && (
                  <div className="pick-bar">
                    <span className="pick-count">
                      <strong>{picked.size}</strong> scenario{picked.size === 1 ? '' : 's'} picked
                      {pickedSets.size > 1 && ` from ${pickedSets.size} Test Sets`}
                    </span>
                    <input
                      className="pick-name"
                      type="text"
                      value={executionName}
                      onChange={(event) => setExecutionName(event.target.value)}
                      placeholder={pickedSets.size > 1
                        ? `Execution name — e.g. “${[...pickedSets].join(' + ')}”`
                        : 'Execution name — required'}
                      disabled={running}
                    />

                    {/* No target control here: the run bar above this one owns
                        that choice for the whole page, and it is the same state.
                        Two of them meant one screen could show NUAT in one place
                        and PROD in the other, both live. */}

                    {/* Disabled rather than warned about after the click: both
                        the name and, on mobile, the device are required, so say
                        so before the button is pressed rather than after. */}
                    <button
                      className="btn btn-primary btn-sm"
                      onClick={runPicked}
                      disabled={running || connecting || !executionName.trim()
                        || (isMobileSet && !runUdid)}
                      title={
                        !executionName.trim() ? 'Name the execution first'
                          : (isMobileSet && !runUdid) ? 'Pick the device to run on'
                            : undefined
                      }
                    >
                      <Play size={14} /> Run execution
                    </button>
                    <button className="btn btn-ghost btn-sm" onClick={() => setPicked(new Map())} disabled={running}>
                      Clear
                    </button>

                    {/* Splitting a set: the picked scenarios go to another set,
                        existing or named here, so one set that collected several
                        requests can be broken back into one set per request. */}
                    <div className="pick-move">
                      <select
                        value={moveTarget}
                        onChange={(event) => setMoveTarget(event.target.value)}
                        disabled={moving || running}
                        aria-label="Test Set to move the picked scenarios into"
                      >
                        <option value="">Move to…</option>
                        {suites.filter((s) => s.id !== suite.id && s.kind === suite.kind).map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.module ? `${s.module} / ` : ''}{s.name}
                          </option>
                        ))}
                        <option value={MOVE_NEW}>＋ New Test Set…</option>
                      </select>
                      {moveTarget === MOVE_NEW && (
                        <>
                          <input
                            type="text"
                            value={moveNewName}
                            onChange={(event) => setMoveNewName(event.target.value)}
                            placeholder="New set name — e.g. Tek yön uçuş ara"
                            disabled={moving}
                          />
                          <input
                            type="text"
                            value={moveNewModule}
                            onChange={(event) => setMoveNewModule(event.target.value)}
                            placeholder={suite.module ? `Module — ${suite.module}` : 'Module (optional)'}
                            disabled={moving}
                          />
                        </>
                      )}
                      {moveTarget && (
                        <button className="btn btn-sm" onClick={movePicked} disabled={moving || running}>
                          {moving ? <Loader2 size={14} className="spin" /> : <ArrowRight size={14} />}
                          Move {picked.size}
                        </button>
                      )}
                    </div>
                  </div>
                )}

                {suite.cases.length === 0 ? (
                  <p className="muted small">No cases yet.</p>
                ) : (
                  <ul className="case-list">
                    {suite.cases.map((item) => (
                      /* A card per scenario rather than a table row. These titles
                         are a full standard-format sentence; on one line they were
                         clipped to an ellipsis, which hides exactly the part that
                         tells two scenarios apart. Given its own block the title
                         wraps in full, and every scenario is bounded by its own
                         edge instead of a hairline shared with its neighbour. */
                      <li
                        key={item.id}
                        className={`case-card ${item.enabled ? '' : 'disabled'} ${item.needsData ? 'awaiting' : ''}`}
                      >
                        <div className="case-card-head">
                          <input
                            type="checkbox"
                            checked={picked.has(item.id)}
                            onChange={() => togglePick(item)}
                            disabled={item.needsData}
                            aria-label={`Select ${item.name}`}
                            title={item.needsData
                              ? 'Waiting on its precondition data — fill that in first'
                              : 'Pick this scenario for an execution'}
                          />
                          <span className="case-idx" title="Scenario number in this Test Set">
                            #{item.idx}
                          </span>
                          <h3 className="case-name">{item.name}</h3>
                          <div className="case-meta">
                            {/* The step count lives on the toggle now, so it is not
                                repeated here. */}
                            {item.priority && (
                              <span className={`priority-tag p-${item.priority.toLowerCase()}`}>
                                {item.priority}
                              </span>
                            )}
                            {item.layer && <span className="layer-tag">{item.layer}</span>}
                            {item.scenario_type && (
                              <span className={`type-tag t-${item.scenario_type.toLowerCase()}`}>
                                {item.scenario_type}
                              </span>
                            )}
                            {item.dataset && <span className="pill">×{item.dataset.length}</span>}

                            {/* What the next run already knows how to do. A step
                                with a recording is replayed rather than reasoned
                                about, so a fully recorded scenario runs without
                                costing a single model call — and a tester should
                                be able to see that on the scenario rather than
                                work it out from a bill. */}
                            {item.recordedSteps > 0 && (
                              <span
                                className={`recorded-tag ${
                                  item.recordedSteps === item.stepCount ? 'full' : 'partial'
                                }`}
                                title={item.recordedSteps === item.stepCount
                                  ? 'Every step was recorded on a green run — the next run replays it and asks the model nothing'
                                  : `${item.recordedSteps} of ${item.stepCount} steps replay from a recording; the rest are worked out again`}
                              >
                                <Repeat size={11} />
                                {item.recordedSteps === item.stepCount
                                  ? 'Recorded'
                                  : `Recorded ${item.recordedSteps}/${item.stepCount}`}
                              </span>
                            )}
                            {/* A tag that just repeats the layer is noise — the layer
                                chip already says it, so it is dropped here. */}
                            {item.tags
                              .filter((tag) => tag.toLowerCase() !== (item.layer || '').toLowerCase())
                              .map((tag) => (
                                <span key={tag} className="tag">{tag}</span>
                              ))}
                          </div>
                          {/* Actions stay grouped on the right so a long title or a
                              stack of chips never pushes them onto their own line. */}
                          <div className="case-actions">
                          {/* Trying one scenario against the browser or device
                              already open, without waiting for a whole Test Set
                              run — this is how a scenario gets debugged while it
                              is being written. */}
                          {onRunHere && (
                            <button
                              className="btn-icon"
                              disabled={connecting}
                              onClick={async () => {
                                /* Whatever this scenario needs to run, get it —
                                   the same way pressing Run on the whole set
                                   does. A web one opens a browser at the chosen
                                   environment; a mobile one books the phone
                                   picked in the Run panel, connecting it if it
                                   is not open yet. Asking the tester to go and
                                   connect something first was asking for a step
                                   they had already taken by pressing this. */
                                const scenario = { ...item, kind: suite.kind || 'web' };
                                if (!isMobileSet) {
                                  onRunHere(scenario, { envUrl });
                                  return;
                                }
                                const target = await resolveTarget('mobile');
                                if (!target) return;
                                onRunHere(scenario, {
                                  sessionId: target.deviceSessionId,
                                });
                              }}
                              title={item.steps?.length
                                ? 'Run here, step by step — connects what it needs'
                                : 'Run here — connects what it needs'}
                              aria-label={`Run ${item.name} here`}
                            >
                              <Play size={14} />
                            </button>
                          )}
                          {/* Picking and enabling are different decisions: one is
                              "run this now", the other is "this scenario is out of
                              service". They get separate controls. */}
                          <button
                            className="btn-icon"
                            onClick={() => toggleCase(item)}
                            title={item.enabled
                              ? 'Disable — leave it out of runs'
                              : 'Enable — include it in runs'}
                            aria-label={item.enabled ? `Disable ${item.name}` : `Enable ${item.name}`}
                          >
                            {item.enabled ? <Eye size={14} /> : <EyeOff size={14} />}
                          </button>
                            <button
                              className="btn-icon danger"
                              onClick={() => removeCase(item.id)}
                              aria-label={`Delete ${item.name}`}
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </div>

                        {/* The state the scenario needs before step 1. Shown above
                            the goal because it is what has to be true first, and
                            a scenario that silently assumes it fails on the setup
                            while the report names the feature. */}
                        {item.precondition && (
                          <p className="case-precondition">
                            <span className="case-precondition-label">Precondition</span>
                            {item.precondition}
                          </p>
                        )}

                        {/* A scenario waiting on its setup data is not run — it
                            would fail on the missing member number and the report
                            would name the feature. It says what it is waiting for
                            and offers the form to end the wait. */}
                        {item.needsData && (
                          <div className="case-awaiting">
                            <div className="case-awaiting-head">
                              <span className="awaiting-chip">Needs data</span>
                              <span className="muted small">
                                Not run until {item.missingData.length} field
                                {item.missingData.length === 1 ? '' : 's'} below
                                {item.missingData.length === 1 ? ' is' : ' are'} filled in.
                              </span>
                              {dataFor !== item.id && (
                                <button
                                  className="btn btn-primary btn-sm"
                                  onClick={() => openDataForm(item)}
                                >
                                  Provide data
                                </button>
                              )}
                            </div>

                            {dataFor === item.id && (
                              <div className="case-data-form">
                                {(item.required_data || []).map((field) => (
                                  <label key={field.key}>
                                    {field.label}
                                    <input
                                      value={dataValues[field.key] || ''}
                                      placeholder={field.example || ''}
                                      onChange={(e) => setDataValues({
                                        ...dataValues, [field.key]: e.target.value,
                                      })}
                                    />
                                  </label>
                                ))}
                                <div className="case-data-actions">
                                  <button
                                    className="btn btn-ghost btn-sm"
                                    onClick={() => setDataFor(null)}
                                  >
                                    Cancel
                                  </button>
                                  <button
                                    className="btn btn-primary btn-sm"
                                    onClick={() => saveData(item)}
                                    disabled={savingData}
                                  >
                                    Save and enable
                                  </button>
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                        {item.goal && <p className="case-goal">{item.goal}</p>}

                        {/* Open by default. The steps are the scenario — what a
                            reviewer checks, and what the run is judged against —
                            so keeping them behind a click made the page look
                            tidier and the work harder. Collapsing stays, for a
                            set being scanned rather than read. */}
                        {item.steps?.length > 0 && (
                          <div className="case-steps-block">
                            <button
                              type="button"
                              className="case-steps-toggle"
                              onClick={() => toggleSteps(item.id)}
                              aria-expanded={!closedSteps.has(item.id)}
                            >
                              {closedSteps.has(item.id)
                                ? <ChevronRight size={12} />
                                : <ChevronDown size={12} />}
                              {item.steps.length} step{item.steps.length === 1 ? '' : 's'}
                            </button>
                            {!closedSteps.has(item.id) && (
                              <ol className="case-steps">
                                {item.steps.map((step, i) => (
                                  <li key={i}>
                                    <span className="case-step-action">
                                      {step.action}
                                      {step.optional && (
                                        <span
                                          className="step-optional-tag"
                                          title="Carried out, but a failed check does not fail the run"
                                        >
                                          optional
                                        </span>
                                      )}
                                    </span>
                                    {step.expected && (
                                      <span className="case-step-expected">{step.expected}</span>
                                    )}
                                    {/* What "Recorded" is actually made of. The
                                        badge said a step replays; it did not say
                                        what it replays, so the one thing a tester
                                        would want to check before trusting it —
                                        which element, which value — was only
                                        readable out of the database. */}
                                    {step.recorded?.length > 0 && (
                                      <ol className="case-step-recorded">
                                        {step.recorded.map((action, j) => (
                                          <li key={j}>
                                            <span className="recorded-verb">{action.action}</span>
                                            {action.selector && (
                                              <code className="recorded-target">{action.selector}</code>
                                            )}
                                            {action.value && (
                                              <span className="recorded-value">“{action.value}”</span>
                                            )}
                                            {!action.selector && !action.value && action.label && (
                                              <span className="recorded-value">{action.label}</span>
                                            )}
                                          </li>
                                        ))}
                                      </ol>
                                    )}
                                  </li>
                                ))}
                              </ol>
                            )}
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* --------------------------------------------- CI recipe -----
                  Reference rather than the job, so it costs a line until it is
                  asked for. Progress and the report live on Test Executions,
                  which is where a run is opened the moment it starts. */}
              <div className="card">
                <button
                  type="button"
                  className="card-toggle"
                  onClick={() => setShowCi((value) => !value)}
                  aria-expanded={showCi}
                >
                  {showCi ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  Run this from CI
                </button>
                {showCi && (
                  <>
                    <p className="muted small">
                      The same suite, headless, with a report your build server already
                      knows how to read. A non-zero exit code fails the build.
                    </p>
                    <pre className="code-block">
  {`python -m cli run --suite ${suite.id} \\
      ${parseTags(options.tags).map((t) => `--tag ${t} `).join('')}--workers ${options.workers} \\
      --junit results.xml --json report.json`}
                    </pre>
                  </>
                )}
              </div>

              {history.length > 0 && (
                <div className="card">
                  <button
                    type="button"
                    className="card-toggle"
                    onClick={() => setShowHistory((value) => !value)}
                    aria-expanded={showHistory}
                  >
                    {showHistory ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    Recent suite runs
                    <span className="pill">{history.length}</span>
                  </button>
                  {showHistory && (
                    <ul className="history-list">
                      {history.map((row) => (
                        <li key={row.id} className="history-row">
                          <span className={`badge ${row.status === 'passed' ? 'ok' : 'bad'}`}>
                            {row.status}
                          </span>
                          <span className="muted small">
                            {row.passed}/{row.total} passed
                          </span>
                          <span className="muted small">
                            {new Date(row.started_at * 1000).toLocaleString()}
                          </span>
                          <a
                            className="btn-link"
                            href={api.suiteReportUrl(row.id, 'junit')}
                            target="_blank"
                            rel="noreferrer"
                          >
                            report
                          </a>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </>
          )}

          {/* ------------------------------------------------- add --------
              The one place on this page that writes something new, and the
              last thing on it. Folded rather than unmounted: a review of
              twenty generated scenarios is half an hour of somebody's
              reading, and collapsing the section must not throw it away. */}
          <div className="card add-card" ref={addRef}>
            <button
              type="button"
              className="card-toggle"
              onClick={() => setShowAdd((value) => !value)}
              aria-expanded={showAdd}
            >
              {showAdd ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <Sparkles size={14} className="card-toggle-mark" />
              Add scenarios
            </button>

            <div className="add-body" hidden={!showAdd}>
              <p className="muted small">
                Name a module — “Uçuş Arama”, “Check-in” — or upload the analysis
                document it was specified in. Nothing is saved until you have read
                it{suite ? `, and what you keep lands in “${suite.name}”` : ''}.
              </p>

              <ScenarioGenerator
                suiteId={suite?.id || null}
                kind={suite ? (suite.kind || 'web') : platform}
                os={suite ? (suite.os || (isMobileSet ? os : null)) : (platform === 'mobile' ? os : null)}
                onConnectDevice={onConnectDevice}
                devices={devices}
                deviceUdid={runUdid}
                deviceAppId={deviceAppId}
                deviceBuilds={deviceBuilds}
                loadingBuilds={loadingBuilds}
                onPickDevice={pickDevice}
                onPickBuild={setDeviceAppId}
                envUrl={envUrl}
                onEnvUrl={setEnvUrl}
                onOpenExecution={onOpenExecution}
                onAdded={async () => {
                  if (suite) setSuite(await api.suite(suite.id));
                  await loadSuites();
                }}
              />

              {/* A scenario nobody needs the model for. Only with a set open,
                  because unlike the generator this form has no picker for
                  where the case goes. */}
              {suite && (
                <>
                  <button
                    type="button"
                    className="card-toggle sub"
                    onClick={() => setShowCaseForm((value) => !value)}
                    aria-expanded={showCaseForm}
                  >
                    {showCaseForm ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                    Write one by hand
                  </button>

                  {showCaseForm && (
                    <form className="case-form" onSubmit={addCase}>
                      <div className="field-row">
                        <label>
                          Name
                          <input
                            value={draft.name}
                            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                            placeholder="Sepete ürün eklenebiliyor"
                          />
                        </label>
                        <label>
                          URL
                          <input
                            value={draft.url}
                            onChange={(e) => setDraft({ ...draft, url: e.target.value })}
                            placeholder="https://example.com/"
                          />
                        </label>
                      </div>

                      <label>
                        Goal
                        <textarea
                          rows={3}
                          value={draft.goal}
                          onChange={(e) => setDraft({ ...draft, goal: e.target.value })}
                          placeholder={'Ara kutusuna {{terim}} yaz, Ara\'ya bas, sonuç çıktığını doğrula.'}
                        />
                        <span className="field-hint">
                          Use <code>{'{{placeholders}}'}</code> to pull values from the dataset below.
                        </span>
                      </label>

                      <StepEditor
                        steps={draft.steps}
                        onChange={(steps) => setDraft({ ...draft, steps })}
                      />

                      <div className="field-row">
                        <label>
                          Tags
                          <input
                            value={draft.tags}
                            onChange={(e) => setDraft({ ...draft, tags: e.target.value })}
                            placeholder="smoke, checkout"
                          />
                        </label>
                        <label>
                          Dataset (CSV or JSON, optional)
                          <textarea
                            rows={3}
                            value={draft.dataset}
                            onChange={(e) => {
                              setDraft({ ...draft, dataset: e.target.value });
                              setDatasetError(null);
                            }}
                            placeholder={'terim\nistanbul\nankara'}
                          />
                          {datasetError && <span className="field-error">{datasetError}</span>}
                        </label>
                      </div>

                      <div className="row-actions end">
                        <button type="button" className="btn btn-sm" onClick={() => setShowCaseForm(false)}>
                          Cancel
                        </button>
                        <button type="submit" className="btn btn-primary btn-sm">Add case</button>
                      </div>
                    </form>
                  )}
                </>
              )}
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
