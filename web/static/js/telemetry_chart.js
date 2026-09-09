/**
 * Telemetry Chart Controller for GPS Save Our Drivers.
 * Visualizes isolated segment speed profiles (v vs. distance),
 * apex points, and run comparison overlays using Chart.js.
 */

class TelemetryChart {
  constructor(canvasId) {
    this.canvasId = canvasId;
    this.chart = null;
    this.metricMode = 'speed'; // 'speed' or 'accel'
    this._crosshairX = null;   // seg_dist_m value to draw crosshair at, or null

    this._registerCrosshairPlugin();
    this.initChart();
  }

  // ── Crosshair Plugin ─────────────────────────────────────────────────────────

  _registerCrosshairPlugin() {
    // Inline Chart.js plugin — draws a vertical dashed red line + speed label
    // at the hovered seg_dist_m position.
    const self = this;

    Chart.register({
      id: 'mapHoverCrosshair',
      afterDraw(chart) {
        if (self._crosshairX === null) return;

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

        // Small dot on each dataset at the crosshair x
        chart.data.datasets.forEach((ds, dsIdx) => {
          const meta = chart.getDatasetMeta(dsIdx);
          if (!meta.visible) return;

          // Find the nearest data point to crosshairX
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

  // ── Chart Setup ──────────────────────────────────────────────────────────────

  initChart() {
    const ctx = document.getElementById(this.canvasId).getContext('2d');

    this.chart = new Chart(ctx, {
      type: 'line',
      data: {
        datasets: []
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: {
          duration: 350
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
              title: (items) => `Track Distance: ${items[0].parsed.x.toFixed(1)} m`,
              label: (item) => {
                const label = item.dataset.label || '';
                const val = item.parsed.y.toFixed(2);
                const unit = this.metricMode === 'speed' ? 'mph' : 'g';
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

  setMetricMode(mode) {
    this.metricMode = mode;
    const yAxisLabel = mode === 'speed' ? 'Speed (mph)' : 'Longitudinal Accel (g)';
    this.chart.options.scales.y.title.text = yAxisLabel;
    this.chart.update();
  }

  updateData(primaryRun, compareRun) {
    const datasets = [];

    // Helper to extract (x, y)
    const extractPoints = (run) => {
      if (!run || !run.records) return [];
      return run.records.map(r => ({
        x: r.seg_dist_m !== undefined ? r.seg_dist_m : r.cum_dist_m,
        y: this.metricMode === 'speed' ? r.speed_mph : r.accel_g
      }));
    };

    // Compare Run (Amber)
    if (compareRun && compareRun.records && compareRun.records.length > 0) {
      datasets.push({
        label: compareRun.display_name || 'Comparison Roll',
        data: extractPoints(compareRun),
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

    // Primary Run (Red)
    if (primaryRun && primaryRun.records && primaryRun.records.length > 0) {
      datasets.push({
        label: primaryRun.display_name || 'Current Roll',
        data: extractPoints(primaryRun),
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

    this.chart.data.datasets = datasets;
    this.chart.update();
  }
}

window.TelemetryChart = TelemetryChart;
