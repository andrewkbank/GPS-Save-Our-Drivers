/**
 * Telemetry Chart Controller for GPS Save Our Drivers.
 * Visualizes:
 * 1. Default: Segment Velocity Profile (v vs. Track Distance)
 * 2. Average Speed Mode: Speed Profile vs. Time with Segment Average Benchmarks
 * 3. Traveled Distance Mode: Distance Accumulation vs. Time
 * Apex & Multi-Watch comparison overlays using Chart.js.
 */

class TelemetryChart {
  constructor(canvasId) {
    this.canvasId = canvasId;
    this.chart = null;
    this.viewMode = 'velocity_profile'; // 'velocity_profile' | 'average_speed' | 'traveled_distance'
    this.metricMode = 'speed';          // 'speed' or 'accel'
    this._crosshairX = null;            // seg_dist_m value to draw crosshair at, or null

    this.primaryRun = null;
    this.compareRun = null;

    this._registerCrosshairPlugin();
    this.initChart();
  }

  // ── Crosshair Plugin ─────────────────────────────────────────────────────────

  _registerCrosshairPlugin() {
    const self = this;

    Chart.register({
      id: 'mapHoverCrosshair',
      afterDraw(chart) {
        if (self.viewMode !== 'velocity_profile' || self._crosshairX === null) return;

        const xScale = chart.scales.x;
        const yScale = chart.scales.y;
        if (!xScale || !yScale) return;

        const xPixel = xScale.getPixelForValue(self._crosshairX);
        if (xPixel < xScale.left || xPixel > xScale.right) return;

        const ctx = chart.ctx;
        ctx.save();

        // Vertical dashed line
        ctx.beginPath();
        ctx.moveTo(xPixel, yScale.top);
        ctx.lineTo(xPixel, yScale.bottom);
        ctx.strokeStyle = '#ff2d2d';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([5, 4]);
        ctx.globalAlpha = 0.85;
        ctx.stroke();

        // Small dot on primary/comparison datasets at crosshair x
        chart.data.datasets.forEach((ds, dsIdx) => {
          const meta = chart.getDatasetMeta(dsIdx);
          if (!meta.visible || !ds.data || ds.borderDash) return;

          let nearest = null;
          let bestDist = Infinity;
          ds.data.forEach(pt => {
            const d = Math.abs(pt.x - self._crosshairX);
            if (d < bestDist) { bestDist = d; nearest = pt; }
          });

          if (nearest) {
            const yPixel = yScale.getPixelForValue(nearest.y);
            ctx.beginPath();
            ctx.arc(xPixel, yPixel, 5, 0, Math.PI * 2);
            ctx.fillStyle = ds.borderColor || '#ff2d2d';
            ctx.globalAlpha = 1;
            ctx.fill();
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 1.5;
            ctx.setLineDash([]);
            ctx.stroke();
          }
        });

        ctx.restore();
      }
    });
  }

  // ── Public API ───────────────────────────────────────────────────────────────

  setCrosshair(seg_dist_m) {
    this._crosshairX = seg_dist_m;
    if (this.chart) this.chart.draw();
  }

  clearCrosshair() {
    this._crosshairX = null;
    if (this.chart) this.chart.draw();
  }

  setViewMode(mode) {
    if (this.viewMode === mode) return;
    this.viewMode = mode;
    this._updateAxes();
    this.renderCurrentData();
  }

  getViewMode() {
    return this.viewMode;
  }

  // ── Chart Setup ──────────────────────────────────────────────────────────────

  initChart() {
    const ctx = document.getElementById(this.canvasId).getContext('2d');
    const self = this;

    this.chart = new Chart(ctx, {
      type: 'line',
      data: {
        datasets: []
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: {
          duration: 300
        },
        interaction: {
          mode: 'index',
          intersect: false
        },
        plugins: {
          legend: {
            position: 'top',
            labels: {
              color: '#d1d5db',
              font: { family: 'Inter', size: 12, weight: '600' }
            }
          },
          tooltip: {
            backgroundColor: '#1a1010',
            titleColor: '#ff2d2d',
            bodyColor: '#f3f4f6',
            borderColor: '#3d1f1f',
            borderWidth: 1,
            padding: 10,
            displayColors: true,
            callbacks: {
              title: (items) => {
                if (!items || !items.length) return '';
                const xVal = items[0].parsed.x;
                if (self.viewMode === 'velocity_profile') {
                  return `Track Distance: ${xVal.toFixed(1)} m`;
                } else {
                  return `Elapsed Time: ${xVal.toFixed(2)} s`;
                }
              },
              label: (item) => {
                const label = item.dataset.label || '';
                const val = item.parsed.y !== undefined ? item.parsed.y.toFixed(2) : '--';
                let unit = 'mph';
                if (self.viewMode === 'traveled_distance') {
                  unit = 'm';
                } else if (self.metricMode === 'accel') {
                  unit = 'g';
                }
                return ` ${label}: ${val} ${unit}`;
              }
            }
          }
        },
        scales: {
          x: {
            type: 'linear',
            title: {
              display: true,
              text: 'Segment Distance (m)',
              color: '#9ca3af',
              font: { family: 'Inter', size: 12, weight: '700' }
            },
            grid: { color: 'rgba(255, 255, 255, 0.06)' },
            ticks: { color: '#9ca3af' }
          },
          y: {
            title: {
              display: true,
              text: 'Speed (mph)',
              color: '#9ca3af',
              font: { family: 'Inter', size: 12, weight: '700' }
            },
            grid: { color: 'rgba(255, 255, 255, 0.06)' },
            ticks: { color: '#9ca3af' }
          }
        }
      }
    });
  }

  _updateAxes() {
    if (!this.chart) return;
    const scales = this.chart.options.scales;

    if (this.viewMode === 'velocity_profile') {
      scales.x.title.text = 'Segment Distance (m)';
      scales.y.title.text = this.metricMode === 'speed' ? 'Speed (mph)' : 'Longitudinal Accel (g)';
    } else if (this.viewMode === 'average_speed') {
      scales.x.title.text = 'Elapsed Time (s)';
      scales.y.title.text = 'Speed (mph)';
    } else if (this.viewMode === 'traveled_distance') {
      scales.x.title.text = 'Elapsed Time (s)';
      scales.y.title.text = 'Traveled Distance (m)';
    }
  }

  updateData(primaryRun, compareRun) {
    this.primaryRun = primaryRun;
    this.compareRun = compareRun;
    this.renderCurrentData();
  }

  renderCurrentData() {
    if (!this.chart) return;

    this._updateAxes();
    const datasets = [];
    const p = this.primaryRun;
    const c = this.compareRun;

    if (this.viewMode === 'velocity_profile') {
      // ── Mode 1: Velocity Profile (v vs Track Distance) ──
      // 5-point median filter: eliminates up to 2-consecutive-point GPS spikes.
      // A 3-point median can't fix 2-point spikes (both neighbors are elevated so
      // the spike wins the vote). A 5-point window always has ≥3 normal values vs
      // ≤2 spike values, so both spike samples get correctly replaced.
      // Monotone ramps are unchanged: median([26,27,28,29,30]) = 28 at each step.
      const medianFilter5 = (arr) => arr.map((v, i) => {
        const lo = Math.max(0, i - 2);
        const hi = Math.min(arr.length - 1, i + 2);
        const window = arr.slice(lo, hi + 1).sort((a, b) => a - b);
        return window[Math.floor(window.length / 2)];
      });

      const extractProfilePoints = (run) => {
        if (!run || !run.records || run.records.length < 2) return [];
        const recs = run.records;
        const MPS_TO_MPH = 2.23694;

        if (this.metricMode !== 'speed') {
          // accel_g is already position-derived in the parser — use as-is
          return recs.map(r => ({
            x: r.seg_dist_m !== undefined ? r.seg_dist_m : r.cum_dist_m,
            y: r.accel_g
          }));
        }

        // Compute position-derived instantaneous speed at each point.
        // v[i] = Δdist / Δtime between record i-1 and record i+1 (central difference),
        // falling back to a one-sided difference at the endpoints.
        // This avoids the Garmin's Kalman-filtered speed_mph lag.
        const rawSpeeds = new Array(recs.length);
        for (let i = 0; i < recs.length; i++) {
          const iL = Math.max(0, i - 1);
          const iR = Math.min(recs.length - 1, i + 1);
          if (iL === iR) {
            rawSpeeds[i] = recs[i].speed_mph; // single-point fallback
            continue;
          }
          const dd = recs[iR].seg_dist_m - recs[iL].seg_dist_m;
          const dt = recs[iR].seg_time_sec - recs[iL].seg_time_sec;
          rawSpeeds[i] = dt > 0 ? (dd / dt) * MPS_TO_MPH : recs[i].speed_mph;
        }

        const speeds = medianFilter5(rawSpeeds);

        return recs.map((r, i) => ({
          x: r.seg_dist_m !== undefined ? r.seg_dist_m : r.cum_dist_m,
          y: speeds[i]
        }));
      };

      if (c && c.records && c.records.length > 0) {
        datasets.push({
          label: `${c.display_name || 'Comparison Roll'}`,
          data: extractProfilePoints(c),
          borderColor: '#f59e0b',
          backgroundColor: 'rgba(245, 158, 11, 0.05)',
          borderWidth: 2.5,
          borderDash: [5, 5],
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: '#f59e0b',
          tension: 0.25,
          fill: false
        });
      }

      if (p && p.records && p.records.length > 0) {
        datasets.push({
          label: `${p.display_name || 'Current Roll'}`,
          data: extractProfilePoints(p),
          borderColor: '#ff2d2d',
          backgroundColor: 'rgba(255, 45, 45, 0.08)',
          borderWidth: 3,
          pointRadius: 0,
          pointHoverRadius: 6,
          pointHoverBackgroundColor: '#ff2d2d',
          tension: 0.25,
          fill: true
        });
      }

    } else if (this.viewMode === 'average_speed') {
      // ── Mode 2: Speed vs. Time with Average Benchmarks ──
      const extractTimeSpeedPoints = (run) => {
        if (!run || !run.records || run.records.length < 2) return [];
        const recs = run.records;
        const MPS_TO_MPH = 2.23694;

        // Central-difference position-derived speed, same method as velocity profile
        const rawSpeeds = new Array(recs.length);
        for (let i = 0; i < recs.length; i++) {
          const iL = Math.max(0, i - 1);
          const iR = Math.min(recs.length - 1, i + 1);
          if (iL === iR) { rawSpeeds[i] = recs[i].speed_mph; continue; }
          const dd = recs[iR].seg_dist_m - recs[iL].seg_dist_m;
          const dt = recs[iR].seg_time_sec - recs[iL].seg_time_sec;
          rawSpeeds[i] = dt > 0 ? (dd / dt) * MPS_TO_MPH : recs[i].speed_mph;
        }

        // 5-point median filter — same as velocity profile, handles 2-point spikes
        const speeds = rawSpeeds.map((v, i) => {
          const lo = Math.max(0, i - 2);
          const hi = Math.min(rawSpeeds.length - 1, i + 2);
          const window = rawSpeeds.slice(lo, hi + 1).sort((a, b) => a - b);
          return window[Math.floor(window.length / 2)];
        });

        return recs.map((r, i) => ({
          x: r.seg_time_sec !== undefined ? r.seg_time_sec : r.elapsed_sec,
          y: speeds[i]
        }));
      };

      // Comparison Speed Curve
      if (c && c.records && c.records.length > 0) {
        const cPts = extractTimeSpeedPoints(c);
        datasets.push({
          label: `${c.display_name || 'Comparison Roll'}`,
          data: cPts,
          borderColor: '#f59e0b',
          backgroundColor: 'transparent',
          borderWidth: 2.5,
          borderDash: [5, 5],
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: '#f59e0b',
          tension: 0.25
        });

        // Comparison Average Speed Reference Line
        const cAvg = c.metrics && c.metrics.avg_speed_mph !== undefined
          ? c.metrics.avg_speed_mph
          : (cPts.reduce((sum, pt) => sum + pt.y, 0) / (cPts.length || 1));

        const maxT = Math.max(...cPts.map(pt => pt.x), 1);
        datasets.push({
          label: `Comp Avg (${cAvg.toFixed(1)} mph)`,
          data: [{ x: 0, y: cAvg }, { x: maxT, y: cAvg }],
          borderColor: 'rgba(245, 158, 11, 0.8)',
          borderWidth: 2,
          borderDash: [8, 4],
          pointRadius: 0,
          fill: false
        });
      }

      // Primary Speed Curve
      if (p && p.records && p.records.length > 0) {
        const pPts = extractTimeSpeedPoints(p);
        datasets.push({
          label: `${p.display_name || 'Current Roll'}`,
          data: pPts,
          borderColor: '#ff2d2d',
          backgroundColor: 'rgba(255, 45, 45, 0.06)',
          borderWidth: 3,
          pointRadius: 0,
          pointHoverRadius: 6,
          pointHoverBackgroundColor: '#ff2d2d',
          tension: 0.25,
          fill: true
        });

        // Primary Average Speed Reference Line
        const pAvg = p.metrics && p.metrics.avg_speed_mph !== undefined
          ? p.metrics.avg_speed_mph
          : (pPts.reduce((sum, pt) => sum + pt.y, 0) / (pPts.length || 1));

        const maxT = Math.max(...pPts.map(pt => pt.x), 1);
        datasets.push({
          label: `Current Avg (${pAvg.toFixed(1)} mph)`,
          data: [{ x: 0, y: pAvg }, { x: maxT, y: pAvg }],
          borderColor: 'rgba(255, 45, 45, 0.85)',
          borderWidth: 2,
          borderDash: [8, 4],
          pointRadius: 0,
          fill: false
        });
      }

    } else if (this.viewMode === 'traveled_distance') {
      // ── Mode 3: Traveled Distance Progression vs. Time ──
      const extractDistancePoints = (run) => {
        if (!run || !run.records) return [];
        return run.records.map(r => ({
          x: r.seg_time_sec !== undefined ? r.seg_time_sec : r.elapsed_sec,
          y: r.seg_dist_m !== undefined ? r.seg_dist_m : r.cum_dist_m
        }));
      };

      if (c && c.records && c.records.length > 0) {
        datasets.push({
          label: `${c.display_name || 'Comparison Roll'}`,
          data: extractDistancePoints(c),
          borderColor: '#f59e0b',
          backgroundColor: 'transparent',
          borderWidth: 2.5,
          borderDash: [5, 5],
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: '#f59e0b',
          tension: 0.15
        });
      }

      if (p && p.records && p.records.length > 0) {
        datasets.push({
          label: `${p.display_name || 'Current Roll'}`,
          data: extractDistancePoints(p),
          borderColor: '#38bdf8',
          backgroundColor: 'rgba(56, 189, 248, 0.08)',
          borderWidth: 3,
          pointRadius: 0,
          pointHoverRadius: 6,
          pointHoverBackgroundColor: '#38bdf8',
          tension: 0.15,
          fill: true
        });
      }
    }

    this.chart.data.datasets = datasets;
    this.chart.update();
  }
}

window.TelemetryChart = TelemetryChart;
