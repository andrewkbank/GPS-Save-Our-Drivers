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

  // Metric HUD Elements
  const valEntry = document.getElementById('val-entry-speed');
  const valApex = document.getElementById('val-apex-speed');
  const valExit = document.getElementById('val-exit-speed');
  const valDeltaV = document.getElementById('val-delta-v');
  const valTime = document.getElementById('val-transit-time');
  const valDist = document.getElementById('val-distance');

  const deltaEntry = document.getElementById('delta-entry-speed');
  const deltaApex = document.getElementById('delta-apex-speed');
  const deltaExit = document.getElementById('delta-exit-speed');
  const deltaTime = document.getElementById('delta-transit-time');

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
      const res = await fetch('/api/rolls');
      const data = await res.json();
      allRolls = data.rolls || [];

      populateSelects();

      if (allRolls.length > 0) {
        currentRollId = allRolls[0].roll_id;
        compareRollId = allRolls.length > 1 ? allRolls[1].roll_id : null;
        primarySelect.value = currentRollId;
        compareSelect.value = compareRollId || '';
        await loadRollDetail(currentRollId);
        await updateComparisonView();
      }
    } catch (e) {
      console.error('Error fetching rolls:', e);
    }
  }

  function populateSelects() {
    primarySelect.innerHTML = '';
    compareSelect.innerHTML = '<option value="">(None - Solo Roll)</option>';

    allRolls.forEach((r, idx) => {
      const opt1 = document.createElement('option');
      opt1.value = r.roll_id;
      opt1.textContent = `${r.display_name} - Max ${r.max_speed_mph} mph`;
      primarySelect.appendChild(opt1);

      const opt2 = document.createElement('option');
      opt2.value = r.roll_id;
      opt2.textContent = `${r.display_name} - Max ${r.max_speed_mph} mph`;
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
      btn.className = `segment-btn ${activeSegmentId === seg.id ? 'active' : ''}`;
      btn.innerHTML = `<span class="dot" style="background-color: ${seg.color}"></span> ${seg.name}`;
      btn.addEventListener('click', () => selectSegment(seg.id));
      segmentBar.appendChild(btn);
    });
  }

  function selectSegment(segId) {
    activeSegmentId = segId;
    document.querySelectorAll('.segment-btn').forEach(btn => btn.classList.remove('active'));
    renderSegmentButtons();
    updateSegmentNotesInput();
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

  async function updateComparisonView() {
    if (!currentRollId) return;

    try {
      let url = `/api/compare?roll1=${encodeURIComponent(compareRollId || currentRollId)}&roll2=${encodeURIComponent(currentRollId)}&segment=${activeSegmentId}`;
      const res = await fetch(url);
      const data = await res.json();

      const rollPrimary = data.roll2; // Current roll
      const rollCompare = compareRollId ? data.roll1 : null; // Comparison roll
      const deltas = data.deltas;

      // Update HUD Metrics
      const m = rollPrimary.metrics;
      valEntry.textContent = m.entry_speed_mph.toFixed(1);
      valApex.textContent = m.min_speed_mph.toFixed(1);
      valExit.textContent = m.exit_speed_mph.toFixed(1);
      valDeltaV.textContent = (m.exit_speed_mph - m.entry_speed_mph >= 0 ? '+' : '') + (m.exit_speed_mph - m.entry_speed_mph).toFixed(1);
      valTime.textContent = m.transit_time_sec.toFixed(2);
      valDist.textContent = m.distance_m.toFixed(1);

      // Render Deltas if comparison exists
      if (rollCompare) {
        renderDeltaBadge(deltaEntry, deltas.delta_entry_speed_mph, 'mph', true);
        renderDeltaBadge(deltaApex, deltas.delta_min_speed_mph, 'mph', true);
        renderDeltaBadge(deltaExit, deltas.delta_exit_speed_mph, 'mph', true);
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
    elem.style.display = 'inline-flex';
    const isPositive = deltaVal > 0;
    const isGood = higherIsBetter ? isPositive : !isPositive;
    const sign = isPositive ? '+' : '';

    elem.className = `metric-delta ${deltaVal === 0 ? 'delta-neutral' : (isGood ? 'delta-faster' : 'delta-slower')}`;
    elem.textContent = `${sign}${deltaVal.toFixed(2)} ${unit} vs comp`;
  }

  function clearDeltaBadges() {
    [deltaEntry, deltaApex, deltaExit, deltaTime].forEach(el => el.style.display = 'none');
  }

  function setupEventListeners() {
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
              showToast('Google Drive connected! Syncing test data...');
              driveSyncText.textContent = 'Syncing...';
              const syncRes = await fetch('/api/drive/sync', { method: 'POST' });
              const syncData = await syncRes.json();
              const res = syncData.result || {};
              showToast(res.message || 'Synced to Drive!');
            } else {
              showToast('Authentication cancelled or failed: ' + (authData.error || ''));
            }
          } else {
            driveSyncText.textContent = 'Syncing...';
            showToast('Syncing all rolls and notes to Drive...');
            const syncRes = await fetch('/api/drive/sync', { method: 'POST' });
            const syncData = await syncRes.json();
            const res = syncData.result || {};
            if (res.success) {
              showToast(`Synced ${res.synced_count} file(s) to Google Drive!`);
            } else {
              showToast('Sync error: ' + (res.error || res.message));
            }
          }
        } catch (e) {
          showToast('Drive sync error: ' + e);
        } finally {
          await fetchDriveStatus();
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
