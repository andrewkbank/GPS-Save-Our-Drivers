/**
 * Course Map Controller for GPS Save Our Drivers.
 * Visualizes Schenley Park freeroll course, segment boundaries,
 * and multi-watch buggy trajectories using Leaflet.
 */

class CourseMap {
  constructor(elementId) {
    this.elementId = elementId;
    this.map = null;
    this.trackLayerGroup = null;
    this.segmentGatesGroup = null;
    this.segmentsMeta = [];

    // Hover state
    this._primaryRecords = [];   // [{lat, lon, seg_dist_m, speed_mph}, ...]
    this._hoverMarker = null;    // Leaflet circleMarker snapped to nearest point
    this._primaryPolyline = null;

    this.initMap();
  }

  initMap() {
    // Default center on Schenley Park freeroll course
    const center = [40.4401, -79.9455];

    this.map = L.map(this.elementId, {
      center: center,
      zoom: 16,
      zoomControl: true
    });

    // Bright OpenStreetMap tiles — no API key required, clear road detail
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 20
    }).addTo(this.map);

    this.trackLayerGroup = L.layerGroup().addTo(this.map);
    this.segmentGatesGroup = L.layerGroup().addTo(this.map);

    // Shared hover snap marker (hidden until first hover)
    this._hoverMarker = L.circleMarker([0, 0], {
      radius: 7,
      fillColor: '#ff2d2d',
      color: '#ffffff',
      weight: 2,
      fillOpacity: 1,
      pane: 'markerPane'   // always on top
    });
    // Not added to map yet — only shown when hovering
  }

  setSegmentsMeta(segments) {
    this.segmentsMeta = segments || [];
    this.renderSegmentGates();
  }

  renderSegmentGates() {
    this.segmentGatesGroup.clearLayers();

    const drawnGateKeys = new Set();

    const drawGate = (gate, color) => {
      if (!gate) return;
      const key = `${gate.lat.toFixed(5)}_${gate.lon.toFixed(5)}`;
      if (drawnGateKeys.has(key)) return;
      drawnGateKeys.add(key);

      const name = gate.name || 'Gate Checkpoint';
      const gateLine = gate.gate_line;

      if (gateLine && gateLine.length >= 2) {
        // Physical timing gate line across the road (curb to curb)
        const line = L.polyline(
          [[gateLine[0].lat, gateLine[0].lon], [gateLine[1].lat, gateLine[1].lon]],
          {
            color: color || '#ffffff',
            weight: 4,
            opacity: 0.9,
            dashArray: '3, 3',
            interactive: false
          }
        );
        this.segmentGatesGroup.addLayer(line);
      }

      // Midpoint timing beacon
      const marker = L.circleMarker([gate.lat, gate.lon], {
        radius: 4.5,
        fillColor: color || '#ffffff',
        color: '#1e293b',
        weight: 1.5,
        fillOpacity: 0.95
      }).bindTooltip(`🏁 ${name}`, { permanent: false, direction: 'top' });

      this.segmentGatesGroup.addLayer(marker);
    };

    this.segmentsMeta.forEach(seg => {
      drawGate(seg.start_gate, seg.color);
      if (seg.apex_gate) {
        drawGate(seg.apex_gate, '#f43f5e');
      }
      drawGate(seg.end_gate, seg.color);
    });
  }

  updateTrajectories(primaryRun, compareRun, activeSegmentId) {
    this.trackLayerGroup.clearLayers();
    this._primaryRecords = [];
    this._primaryPolyline = null;
    this._clearHoverMarker();

    const bounds = [];

    // Helper to extract lat/lon array
    const extractCoords = (run) => {
      if (!run || !run.records) return [];
      return run.records.map(r => [r.lat, r.lon]);
    };

    const coordsPrimary = extractCoords(primaryRun);
    const coordsCompare = extractCoords(compareRun);

    // Render comparison run (amber dashed line)
    // non-interactive so mouse events pass through to the map hover handler
    if (coordsCompare.length > 0) {
      const compPoly = L.polyline(coordsCompare, {
        color: '#f59e0b',
        weight: 4,
        opacity: 0.75,
        dashArray: '6, 6',
        interactive: false
      });
      this.trackLayerGroup.addLayer(compPoly);
      coordsCompare.forEach(c => bounds.push(c));
    }

    // Render primary run (red solid line)
    if (coordsPrimary.length > 0) {
      this._primaryPolyline = L.polyline(coordsPrimary, {
        color: '#ff2d2d',
        weight: 5,
        opacity: 0.95
      }).bindTooltip(primaryRun.display_name || 'Primary Roll');
      this.trackLayerGroup.addLayer(this._primaryPolyline);
      coordsPrimary.forEach(c => bounds.push(c));

      // Store records for hover snapping
      this._primaryRecords = primaryRun.records.map(r => ({
        lat: r.lat,
        lon: r.lon,
        seg_dist_m: r.seg_dist_m !== undefined ? r.seg_dist_m : r.cum_dist_m,
        speed_mph: r.speed_mph
      }));

      // Wire map-level hover (more reliable than polyline mousemove)
      this._bindMapHover();

      // Add Start & End Markers
      const startPt = coordsPrimary[0];
      const endPt = coordsPrimary[coordsPrimary.length - 1];

      const startIcon = L.circleMarker(startPt, {
        radius: 6,
        fillColor: '#10b981',
        color: '#ffffff',
        weight: 2,
        fillOpacity: 1
      }).bindPopup('<b>Segment Entry / Start</b>');

      const endIcon = L.circleMarker(endPt, {
        radius: 6,
        fillColor: '#ff2d2d',
        color: '#ffffff',
        weight: 2,
        fillOpacity: 1
      }).bindPopup('<b>Segment Exit / Finish</b>');

      this.trackLayerGroup.addLayer(startIcon);
      this.trackLayerGroup.addLayer(endIcon);
    }

    // Fit map bounds to trajectory
    if (bounds.length > 1) {
      this.map.fitBounds(L.latLngBounds(bounds), { padding: [30, 30], maxZoom: 18 });
    }
  }

  // ── Hover Logic ──────────────────────────────────────────────────────────────

  /** Bind hover on both the map (background) and primary polyline (stroke).
   *  This is necessary because Leaflet stops event propagation when the cursor
   *  is directly over an interactive layer, so we cover both surfaces. */
  _bindMapHover() {
    // Pixel distance threshold — within this many px of a GPS point, show crosshair
    const THRESHOLD_PX = 25;
    let _wasActive = false;

    const handleMove = (e) => {
      if (!this._primaryRecords.length) return;

      const { nearest, distPx } = this._nearestRecord(e.latlng);
      if (!nearest) return;

      if (distPx <= THRESHOLD_PX) {
        // Snap glowing dot to the nearest GPS point
        this._hoverMarker.setLatLng([nearest.lat, nearest.lon]);
        if (!this.map.hasLayer(this._hoverMarker)) {
          this._hoverMarker.addTo(this.map);
        }
        window.dispatchEvent(new CustomEvent('mapHover', {
          detail: { seg_dist_m: nearest.seg_dist_m, speed_mph: nearest.speed_mph }
        }));
        _wasActive = true;
      } else if (_wasActive) {
        this._clearHoverMarker();
        window.dispatchEvent(new CustomEvent('mapHoverEnd'));
        _wasActive = false;
      }
    };

    const handleOut = () => {
      this._clearHoverMarker();
      window.dispatchEvent(new CustomEvent('mapHoverEnd'));
      _wasActive = false;
    };

    this._mapMoveHandler = handleMove;
    this._mapOutHandler  = handleOut;

    // Map-level: fires when cursor is over the tile background or a
    // non-interactive layer
    this.map.off('mousemove', this._mapMoveHandler);
    this.map.off('mouseout',  this._mapOutHandler);
    this.map.on('mousemove',  this._mapMoveHandler);
    this.map.on('mouseout',   this._mapOutHandler);

    // Polyline-level: fires when cursor is pixel-perfect on the primary stroke
    if (this._primaryPolyline) {
      this._primaryPolyline.off('mousemove', this._mapMoveHandler);
      this._primaryPolyline.off('mouseout',  this._mapOutHandler);
      this._primaryPolyline.on('mousemove',  this._mapMoveHandler);
      this._primaryPolyline.on('mouseout',   this._mapOutHandler);
    }
  }

  /** Find the record in _primaryRecords closest to the given LatLng.
   *  Returns { nearest, distPx } so the caller can apply a threshold. */
  _nearestRecord(latlng) {
    if (!this._primaryRecords.length) return { nearest: null, distPx: Infinity };

    let bestIdx = 0;
    let bestDistSq = Infinity;
    const pt = this.map.latLngToLayerPoint(latlng);

    this._primaryRecords.forEach((rec, i) => {
      const rPt = this.map.latLngToLayerPoint(L.latLng(rec.lat, rec.lon));
      const dx = pt.x - rPt.x;
      const dy = pt.y - rPt.y;
      const dSq = dx * dx + dy * dy;
      if (dSq < bestDistSq) { bestDistSq = dSq; bestIdx = i; }
    });

    return {
      nearest: this._primaryRecords[bestIdx],
      distPx: Math.sqrt(bestDistSq)
    };
  }

  _clearHoverMarker() {
    if (this.map && this.map.hasLayer(this._hoverMarker)) {
      this.map.removeLayer(this._hoverMarker);
    }
  }
}

window.CourseMap = CourseMap;
