/**
 * Main Application Coordinator for GPS Save Our Drivers.
 * Handles roll selection, multi-watch display, segment isolation,
 * HUD metric updates, driver notes, and Bluetooth auto-sync.
 */

document.addEventListener('DOMContentLoaded', () => {
  let allRolls = [];
  let currentRollId = null;
  let compareRollId = null;
  let activeSegmentId = 'full';
  let segmentsMeta = [];
  let currentRollDetail = null;

  // Initialize Map and Chart
  const courseMap = new CourseMap('course-map');
  const telemetryChart = new TelemetryChart('telemetry-canvas');

  // Map hover → chart crosshair bridge
  window.addEventListener('mapHover', (e) => {
    telemetryChart.setCrosshair(e.detail.seg_dist_m);
  });
  window.addEventListener('mapHoverEnd', () => {
    telemetryChart.clearCrosshair();
  });

  // DOM Elements
  const primarySelect = document.getElementById('primary-roll-select');
  const compareSelect = document.getElementById('compare-roll-select');
  const watchBadge = document.getElementById('watch-count-badge');
  const segmentBar = document.getElementById('segment-bar');
  const toast = document.getElementById('toast');
  const btnRescan = document.getElementById('btn-rescan');
  const btnUploadModal = document.getElementById('btn-upload-modal');
  const modalOverlay = document.getElementById('upload-modal');
  const btnCloseModal = document.getElementById('btn-close-modal');
  const fileInput = document.getElementById('file-input');
  const dropZone = document.getElementById('drop-zone');

  // Notes Elements
  const driverNameInput = document.getElementById('driver-name');
  const buggyNameInput = document.getElementById('buggy-name');
  const generalNotesInput = document.getElementById('general-notes');
  const segmentNotesInput = document.getElementById('segment-notes');
  const btnSaveNotes = document.getElementById('btn-save-notes');

  // Debug Files Elements
  const debugRollId = document.getElementById('debug-roll-id');
  const debugGpsFiles = document.getElementById('debug-gps-files');
  const debugNotesFiles = document.getElementById('debug-notes-files');

  // Metric HUD Elements (2-card focused comparison)
  const cardAvgSpeed = document.getElementById('card-avg-speed');
  const cardDistance = document.getElementById('card-distance');
  const valAvgSpeed = document.getElementById('val-avg-speed');
  const valCompAvgSpeed = document.getElementById('val-comp-avg-speed');
  const deltaAvgSpeed = document.getElementById('delta-avg-speed');
  const pillAvgSpeed = document.getElementById('pill-avg-speed');

  const valDist = document.getElementById('val-distance');
  const valCompDist = document.getElementById('val-comp-distance');
  const deltaDist = document.getElementById('delta-distance');
  const pillDistance = document.getElementById('pill-distance');

  const valTime = document.getElementById('val-transit-time');
  const valCompTime = document.getElementById('val-comp-transit-time');
  const deltaTime = document.getElementById('delta-transit-time');

  // Bad GPS Notice Elements
  const badGpsAlert = document.getElementById('bad-gps-alert');
  const badGpsDesc = document.getElementById('bad-gps-desc');

  // Chart Header Elements
  const chartTitle = document.getElementById('chart-title');
  const chartSubtitle = document.getElementById('chart-subtitle');
  const btnResetChart = document.getElementById('btn-reset-chart');

  let currentChartMode = 'velocity_profile';

  // Toast notifier
  function showToast(msg) {
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3500);
  }

  // Load Initial Data
  async function initApp() {
    await fetchStatus();
    await fetchRolls();
    setupEventListeners();

    // Periodic check for new Bluetooth files every 5s
    setInterval(fetchStatus, 5000);
    await fetchDriveStatus();
  }

  const btnDriveSync = document.getElementById('btn-drive-sync');
  const driveSyncText = document.getElementById('drive-sync-text');

  async function fetchDriveStatus() {
    if (!btnDriveSync || !driveSyncText) return;
    try {
      const res = await fetch('/api/drive/status');
      const data = await res.json();
      const status = data.status || {};
      if (status.authenticated) {
        driveSyncText.textContent = 'Sync to Drive';
        btnDriveSync.title = status.folder_name ? `Google Drive: '${status.folder_name}'` : 'Sync files to Google Drive';
      } else {
        driveSyncText.textContent = 'Connect Drive';
        btnDriveSync.title = 'Authenticate Google Drive via browser';
      }
    } catch (e) {
      console.warn('Drive status error:', e);
    }
  }

  async function fetchStatus() {
    try {
      const res = await fetch('/api/status');
      const data = await res.json();
      const statusText = document.getElementById('bt-status-text');
      if (data.bluetooth && data.bluetooth.bluetooth_active) {
        statusText.textContent = `Bluetooth Priority Active (${data.total_rolls} rolls recorded)`;
      }
    } catch (e) {
      console.warn('Status fetch error:', e);
    }
  }

  async function fetchRolls() {
    try {
      const prevPrimary = primarySelect.value;
      const prevCompare = compareSelect.value;

      const res = await fetch('/api/rolls');
      const data = await res.json();
      allRolls = data.rolls || [];

      populateSelects();

      if (allRolls.length > 0) {
        currentRollId = prevPrimary && allRolls.some(r => r.roll_id === prevPrimary) ? prevPrimary : allRolls[0].roll_id;
        compareRollId = prevCompare && allRolls.some(r => r.roll_id === prevCompare) ? prevCompare : (allRolls.length > 1 ? allRolls[1].roll_id : null);
        primarySelect.value = currentRollId;
        compareSelect.value = compareRollId || '';
        await loadRollDetail(currentRollId);
        await updateComparisonView();
      }
    } catch (e) {
      console.error('Error fetching rolls:', e);
    }
  }

  function formatRollTime(sec) {
    if (sec === undefined || sec === null || isNaN(sec) || sec <= 0) return '--:--';
    const mins = Math.floor(sec / 60);
    const remSec = (sec % 60).toFixed(2);
    const remStr = (sec % 60 < 10 ? '0' : '') + remSec;
    return `${mins}:${remStr}`;
  }

  function formatRollLabel(r) {
    const driver = (r.driver_name && r.driver_name !== 'Unknown Driver') ? r.driver_name.trim() : '';
    const buggy = (r.buggy_name && r.buggy_name !== 'Apex Buggy') ? r.buggy_name.trim() : (r.buggy_name ? r.buggy_name.trim() : '');

    let metaTag = '';
    if (driver && buggy) {
      metaTag = ` [${driver} • ${buggy}]`;
    } else if (driver) {
      metaTag = ` [Driver: ${driver}]`;
    } else if (buggy && buggy !== 'Apex Buggy') {
      metaTag = ` [Buggy: ${buggy}]`;
    }

    const freerollSec = r.freeroll_time_sec || r.duration_sec;
    const timeDisplay = freerollSec ? `${formatRollTime(freerollSec)} (${freerollSec.toFixed(1)}s)` : '--:--';

    return `${r.display_name}${metaTag} — Time: ${timeDisplay}`;
  }

  function populateSelects() {
    primarySelect.innerHTML = '';
    compareSelect.innerHTML = '<option value="">(None - Solo Roll)</option>';

    allRolls.forEach((r) => {
      const label = formatRollLabel(r);

      const opt1 = document.createElement('option');
      opt1.value = r.roll_id;
      opt1.textContent = label;
      primarySelect.appendChild(opt1);

      const opt2 = document.createElement('option');
      opt2.value = r.roll_id;
      opt2.textContent = label;
      compareSelect.appendChild(opt2);
    });
  }

  async function loadRollDetail(rollId) {
    if (!rollId) return;
    try {
      const res = await fetch(`/api/roll/${rollId}`);
      currentRollDetail = await res.json();

      // Render watch badge
      const count = currentRollDetail.roll.watch_count || 1;
      const watchSummary = currentRollDetail.roll.watch_devices.map(d => d.device_name).join(' + ');
      watchBadge.innerHTML = `<span>🛰️</span> <b>${count} Watch${count > 1 ? 'es Fused' : ''}:</b> ${watchSummary}`;

      // Update files debug component
      if (debugRollId) debugRollId.textContent = rollId;
      const assoc = currentRollDetail.associated_files || {};
      const gpsList = (assoc.gps_files && assoc.gps_files.length)
        ? assoc.gps_files.join(', ')
        : (currentRollDetail.roll && currentRollDetail.roll.watch_devices ? currentRollDetail.roll.watch_devices.map(d => d.source_file).filter(Boolean).join(', ') : '(None)');
      const notesList = (assoc.notes_files && assoc.notes_files.length)
        ? assoc.notes_files.join(', ')
        : '(None on disk)';

      if (debugGpsFiles) debugGpsFiles.textContent = gpsList || '(None)';
      if (debugNotesFiles) debugNotesFiles.textContent = notesList || '(None)';

      // Render segment bar buttons if not done
      if (segmentsMeta.length === 0) {
        segmentsMeta = currentRollDetail.course_segments_meta || [];
        courseMap.setSegmentsMeta(segmentsMeta);
        renderSegmentButtons();
      }

      // Populate notes
      const notes = currentRollDetail.notes || {};
      driverNameInput.value = notes.driver_name || '';
      buggyNameInput.value = notes.buggy_name || 'Apex Buggy';
      generalNotesInput.value = notes.general_notes || '';
      updateSegmentNotesInput();

    } catch (e) {
      console.error('Error loading roll details:', e);
    }
  }

  function renderSegmentButtons() {
    segmentBar.innerHTML = '';

    // Full Freeroll button
    const fullBtn = document.createElement('button');
    fullBtn.className = `segment-btn ${activeSegmentId === 'full' ? 'active' : ''}`;
    fullBtn.innerHTML = `<span>🏁</span> Full Freeroll`;
    fullBtn.addEventListener('click', () => selectSegment('full'));
    segmentBar.appendChild(fullBtn);

    segmentsMeta.forEach(seg => {
      const btn = document.createElement('button');
      btn.className = `segment-btn ${activeSegmentId === seg.id ? 'active' : ''} ${seg.bad_gps ? 'seg-bad-gps' : ''}`;
      const badGpsIcon = seg.bad_gps ? `<span class="seg-warning-badge" title="Known Tree Coverage (GPS degraded)">⚠️ 🌲</span>` : '';
      btn.innerHTML = `<span class="dot" style="background-color: ${seg.color}"></span> ${seg.name} ${badGpsIcon}`;
      btn.addEventListener('click', () => selectSegment(seg.id));
      segmentBar.appendChild(btn);
    });
  }

  function selectSegment(segId) {
    activeSegmentId = segId;
    document.querySelectorAll('.segment-btn').forEach(btn => btn.classList.remove('active'));
    renderSegmentButtons();
    updateSegmentNotesInput();

    // Bad GPS alert handling
    const curSeg = segmentsMeta.find(s => s.id === segId);
    if (curSeg && curSeg.bad_gps) {
      if (badGpsAlert) {
        badGpsAlert.style.display = 'flex';
        if (badGpsDesc) {
          badGpsDesc.textContent = `${curSeg.name}: ${curSeg.description || 'Pusher / rollout under dense tree canopy. GPS signal may experience drift, elevation spikes, or signal dropouts.'}`;
        }
      }
    } else {
      if (badGpsAlert) badGpsAlert.style.display = 'none';
    }

    updateComparisonView();
  }

  function updateSegmentNotesInput() {
    if (!currentRollDetail || !currentRollDetail.notes) return;
    const segNotes = currentRollDetail.notes.segment_notes || {};
    segmentNotesInput.value = segNotes[activeSegmentId] || '';
    const label = document.querySelector('label[for="segment-notes"]');
    if (label) {
      const segName = activeSegmentId === 'full' ? 'Full Roll' : (segmentsMeta.find(s => s.id === activeSegmentId)?.name || activeSegmentId);
      label.textContent = `Notes for [${segName}]`;
    }
  }

  function setChartMode(mode) {
    currentChartMode = mode;
    telemetryChart.setViewMode(mode);

    if (cardAvgSpeed) cardAvgSpeed.classList.toggle('active-card', mode === 'average_speed');
    if (cardDistance) cardDistance.classList.toggle('active-card', mode === 'traveled_distance');
    if (pillAvgSpeed) pillAvgSpeed.style.display = (mode === 'average_speed' ? 'inline-block' : 'none');
    if (pillDistance) pillDistance.style.display = (mode === 'traveled_distance' ? 'inline-block' : 'none');
    if (btnResetChart) btnResetChart.style.display = (mode !== 'velocity_profile' ? 'inline-flex' : 'none');

    if (chartTitle && chartSubtitle) {
      if (mode === 'velocity_profile') {
        chartTitle.innerHTML = '<span>📊</span> Segment Velocity Profile (v vs. Track Distance)';
        chartSubtitle.textContent = 'Red = Current Roll | Amber = Comparison Roll • Hover map to sync crosshair';
      } else if (mode === 'average_speed') {
        chartTitle.innerHTML = '<span>⚡</span> Speed & Time Profile (v vs. Elapsed Time)';
        chartSubtitle.textContent = 'Includes average speed benchmark lines • Red = Current Roll | Amber = Comparison Roll';
      } else if (mode === 'traveled_distance') {
        chartTitle.innerHTML = '<span>📏</span> Traveled Distance Progression (Distance vs. Time)';
        chartSubtitle.textContent = 'Compares path length accumulation • Steeper curve indicates higher ground speed';
      }
    }
  }

  function toggleChartMode(mode) {
    if (currentChartMode === mode) {
      setChartMode('velocity_profile');
    } else {
      setChartMode(mode);
    }
  }

  async function updateComparisonView() {
    if (!currentRollId) return;

    try {
      let url = `/api/compare?roll1=${encodeURIComponent(compareRollId || currentRollId)}&roll2=${encodeURIComponent(currentRollId)}&segment=${activeSegmentId}`;
      const res = await fetch(url);
      const data = await res.json();

      const rollPrimary = data.roll2; // Current roll
      const rollCompare = compareRollId ? data.roll1 : null; // Comparison roll
      const deltas = data.deltas;

      // Update HUD Metrics (2 cards: Average Speed & Traveled Distance)
      const m = rollPrimary.metrics;
      const mComp = rollCompare ? rollCompare.metrics : null;

      // 1. Average Speed
      if (valAvgSpeed) {
        valAvgSpeed.textContent = (m.avg_speed_mph !== undefined) ? m.avg_speed_mph.toFixed(1) : '--.-';
      }
      if (valCompAvgSpeed) {
        valCompAvgSpeed.textContent = (mComp && mComp.avg_speed_mph !== undefined) ? mComp.avg_speed_mph.toFixed(1) : '--.-';
      }

      // 2. Traveled Distance
      if (valDist) {
        valDist.textContent = (m.distance_m !== undefined) ? m.distance_m.toFixed(1) : '---.-';
      }
      if (valCompDist) {
        valCompDist.textContent = (mComp && mComp.distance_m !== undefined) ? mComp.distance_m.toFixed(1) : '---.-';
      }

      // 3. Segment Time
      if (valTime) {
        valTime.textContent = (m.transit_time_sec !== undefined) ? m.transit_time_sec.toFixed(2) : '--.--';
      }
      if (valCompTime) {
        valCompTime.textContent = (mComp && mComp.transit_time_sec !== undefined) ? mComp.transit_time_sec.toFixed(2) : '--.--';
      }

      // Render Deltas if comparison exists
      if (rollCompare && deltas) {
        renderDeltaBadge(deltaAvgSpeed, deltas.delta_avg_speed_mph, 'mph', true);
        renderDeltaBadge(deltaDist, deltas.delta_distance_m, 'm', false); // lower distance is tighter line
        renderDeltaBadge(deltaTime, deltas.delta_transit_time_sec, 's', false); // lower time is faster!
      } else {
        clearDeltaBadges();
      }

      // Update Chart & Map
      telemetryChart.updateData(rollPrimary, rollCompare);
      courseMap.updateTrajectories(rollPrimary, rollCompare, activeSegmentId);

    } catch (e) {
      console.error('Error updating comparison view:', e);
    }
  }

  function renderDeltaBadge(elem, deltaVal, unit, higherIsBetter) {
    if (!elem) return;
    if (deltaVal === undefined || deltaVal === null || isNaN(deltaVal)) {
      elem.style.display = 'none';
      return;
    }
    elem.style.display = 'inline-flex';
    const isPositive = deltaVal > 0;
    const isGood = higherIsBetter ? isPositive : !isPositive;
    const sign = isPositive ? '+' : '';

    elem.className = `metric-delta ${deltaVal === 0 ? 'delta-neutral' : (isGood ? 'delta-faster' : 'delta-slower')}`;
    elem.textContent = `${sign}${deltaVal.toFixed(2)} ${unit} vs comp`;
  }

  function clearDeltaBadges() {
    if (deltaAvgSpeed) deltaAvgSpeed.style.display = 'none';
    if (deltaDist) deltaDist.style.display = 'none';
    if (deltaTime) deltaTime.style.display = 'none';
  }

  function setupEventListeners() {
    // HUD Cards click-to-graph interactions
    if (cardAvgSpeed) {
      cardAvgSpeed.addEventListener('click', () => toggleChartMode('average_speed'));
      cardAvgSpeed.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          toggleChartMode('average_speed');
        }
      });
    }

    if (cardDistance) {
      cardDistance.addEventListener('click', () => toggleChartMode('traveled_distance'));
      cardDistance.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          toggleChartMode('traveled_distance');
        }
      });
    }

    if (btnResetChart) {
      btnResetChart.addEventListener('click', () => setChartMode('velocity_profile'));
    }

    primarySelect.addEventListener('change', async (e) => {
      currentRollId = e.target.value;
      await loadRollDetail(currentRollId);
      await updateComparisonView();
    });

    compareSelect.addEventListener('change', async (e) => {
      compareRollId = e.target.value || null;
      await updateComparisonView();
    });

    btnRescan.addEventListener('click', async () => {
      btnRescan.textContent = 'Scanning...';
      try {
        await fetch('/api/scan', { method: 'POST' });
        await fetchRolls();
        showToast('Rescan complete! Checked Bluetooth sync & USB paths.');
      } finally {
        btnRescan.innerHTML = `<span>🔄</span> Rescan Devices`;
      }
    });

    // Save Notes
    btnSaveNotes.addEventListener('click', async () => {
      if (!currentRollId) return;

      const currentSegNotes = (currentRollDetail && currentRollDetail.notes && currentRollDetail.notes.segment_notes) || {};
      if (segmentNotesInput.value.trim()) {
        currentSegNotes[activeSegmentId] = segmentNotesInput.value.trim();
      }

      const payload = {
        driver_name: driverNameInput.value.trim(),
        buggy_name: buggyNameInput.value.trim(),
        general_notes: generalNotesInput.value.trim(),
        segment_notes: currentSegNotes
      };

      try {
        const res = await fetch(`/api/notes/${currentRollId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const result = await res.json();
        if (result.success) {
          if (currentRollDetail) currentRollDetail.notes = result.notes;
          showToast('Driver notes saved successfully (saved locally & queued for Drive sync)!');
        }
      } catch (e) {
        showToast('Error saving notes');
      }
    });

    // Upload modal handlers
    btnUploadModal.addEventListener('click', () => modalOverlay.classList.add('open'));
    btnCloseModal.addEventListener('click', () => modalOverlay.classList.remove('open'));
    modalOverlay.addEventListener('click', (e) => {
      if (e.target === modalOverlay) modalOverlay.classList.remove('open');
    });

    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropZone.classList.add('dragover');
    });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('dragover');
      if (e.dataTransfer.files.length > 0) {
        handleFileUpload(e.dataTransfer.files[0]);
      }
    });

    fileInput.addEventListener('change', (e) => {
      if (e.target.files.length > 0) {
        handleFileUpload(e.target.files[0]);
      }
    });

    // Drive sync button handler
    if (btnDriveSync) {
      btnDriveSync.addEventListener('click', async () => {
        driveSyncText.textContent = 'Checking...';
        try {
          const statusRes = await fetch('/api/drive/status');
          const statusData = await statusRes.json();
          const status = statusData.status || {};

          if (!status.authenticated) {
            driveSyncText.textContent = 'Logging in...';
            showToast('Opening Google sign-in in your browser...');
            const authRes = await fetch('/api/drive/auth', { method: 'POST' });
            const authData = await authRes.json();
            if (authData.success) {
              showToast('Google Drive connected! Syncing data...');
              driveSyncText.textContent = 'Syncing...';
              const syncRes = await fetch('/api/drive/sync', { method: 'POST' });
              const syncData = await syncRes.json();
              const up = syncData.upload_result || {};
              const down = syncData.download_result || {};
              const upCount = up.synced_count || 0;
              const downCount = down.downloaded_count || 0;
              showToast(`Connected & synced: uploaded ${upCount}, downloaded ${downCount} file(s).`);
            } else {
              showToast('Authentication cancelled or failed: ' + (authData.error || ''));
            }
          } else {
            driveSyncText.textContent = 'Syncing...';
            showToast('Syncing rolls and notes with Google Drive...');
            const syncRes = await fetch('/api/drive/sync', { method: 'POST' });
            const syncData = await syncRes.json();
            const up = syncData.upload_result || {};
            const down = syncData.download_result || {};

            if (syncData.success) {
              const uploaded = up.uploaded_count || 0;
              const updated = up.updated_count || 0;
              const downloaded = down.downloaded_count || 0;
              const skipped = up.skipped_count || 0;

              const parts = [];
              if (uploaded > 0) parts.push(`uploaded ${uploaded}`);
              if (updated > 0) parts.push(`updated ${updated}`);
              if (downloaded > 0) parts.push(`downloaded ${downloaded}`);
              if (skipped > 0) parts.push(`${skipped} existing GPS files unchanged`);

              const msg = parts.length > 0 ? parts.join(', ') : 'everything up to date';
              showToast(`Sync complete: ${msg}.`);
            } else {
              const errMsg = up.error || down.error || up.message || down.message || 'Sync encountered an issue.';
              showToast('Sync notice: ' + errMsg);
            }
          }
        } catch (e) {
          showToast('Drive sync error: ' + e);
        } finally {
          await fetchDriveStatus();
          await fetchRolls();
        }
      });
    }
  }

  async function handleFileUpload(file) {
    const formData = new FormData();
    formData.append('file', file);

    try {
      showToast(`Uploading ${file.name}...`);
      const res = await fetch('/api/upload', {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      if (data.success) {
        showToast(`Imported ${data.filename}! Processing telemetry...`);
        modalOverlay.classList.remove('open');
        await fetchRolls();
      } else {
        showToast(`Upload failed: ${data.error}`);
      }
    } catch (e) {
      showToast('Error uploading file');
    }
  }

  initApp();
});
