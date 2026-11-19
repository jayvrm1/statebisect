/**
 * StateBisect Local Dashboard Client Controller
 * Pure vanilla ES2022, zero dependencies, accessible DOM manipulation, anti-CSRF headers.
 */

// Global State
const state = {
  currentView: "overview",
  currentInvId: null,
  currentSubTab: "timeline",
  investigations: [],
  environments: [],
  timelineItems: [],
  timelineFormat: "visual", // "visual" or "table"
  activeReportFormat: "text",
};

// Defensive Anti-CSRF Fetch Wrapper
async function apiFetch(url, options = {}) {
  const defaultHeaders = {
    "X-StateBisect-Request": "1",
    "Accept": "application/json",
  };
  if (options.body && typeof options.body === "object") {
    defaultHeaders["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  options.headers = { ...defaultHeaders, ...(options.headers || {}) };

  const res = await fetch(url, options);
  if (!res.ok) {
    let errData = {};
    try {
      errData = await res.json();
    } catch {
      errData = { error: `HTTP ${res.status}: ${res.statusText}` };
    }
    throw new Error(errData.error || `Request failed with HTTP ${res.status}`);
  }
  return res.json();
}

// Format ISO string to local readable date and UTC
function formatDateTime(isoStr) {
  if (!isoStr) return { local: "N/A", utc: "N/A" };
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return { local: isoStr, utc: isoStr };
    return {
      local: d.toLocaleString(undefined, { dateStyle: "short", timeStyle: "medium" }),
      utc: d.toISOString(),
    };
  } catch {
    return { local: isoStr, utc: isoStr };
  }
}

// Safe string escaping for HTML
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Generate Outcome / Status Badges with patterns and labels
function getOutcomeBadge(outcome) {
  if (!outcome) return "";
  const upper = String(outcome).toUpperCase();
  if (upper === "PASS") {
    return `<span class="badge badge-pass" role="status"><span aria-hidden="true">✔</span> PASS</span>`;
  }
  if (upper === "FAIL") {
    return `<span class="badge badge-fail" role="status"><span aria-hidden="true">✖</span> FAIL</span>`;
  }
  if (upper === "INCONCLUSIVE") {
    return `<span class="badge badge-inconclusive" role="status"><span aria-hidden="true">?</span> INCONCLUSIVE</span>`;
  }
  if (upper.includes("VERIFIED_SET")) {
    return `<span class="badge badge-verified-set" role="status"><span aria-hidden="true">★</span> 1-Minimal Set</span>`;
  }
  if (upper.includes("VERIFIED_INTERACTION")) {
    return `<span class="badge badge-verified-interaction" role="status"><span aria-hidden="true">◆</span> Interaction Set</span>`;
  }
  if (upper.includes("PARTIALLY_SUPPORTED")) {
    return `<span class="badge badge-inconclusive" role="status"><span aria-hidden="true">▲</span> Partially Supported</span>`;
  }
  if (upper.includes("INVALID_BASELINE")) {
    return `<span class="badge badge-fail" role="status"><span aria-hidden="true">⚠</span> Invalid Baseline</span>`;
  }
  if (upper.includes("CORRUPTED")) {
    return `<span class="badge badge-corrupted" role="status"><span aria-hidden="true">☣</span> Corrupted</span>`;
  }
  return `<span class="badge badge-unavailable" role="status">${escapeHtml(outcome)}</span>`;
}

// Tab Switching
function switchView(viewName) {
  state.currentView = viewName;
  document.querySelectorAll(".view-panel").forEach((el) => (el.style.display = "none"));
  document.querySelectorAll(".main-nav .nav-btn").forEach((btn) => {
    btn.classList.remove("active");
    btn.setAttribute("aria-selected", "false");
  });

  const targetPanel = document.getElementById(`view-${viewName}`);
  const targetNav = document.getElementById(`tab-nav-${viewName}`);
  if (targetPanel) targetPanel.style.display = "block";
  if (targetNav) {
    targetNav.classList.add("active");
    targetNav.setAttribute("aria-selected", "true");
  }

  if (viewName === "overview") loadOverview();
  else if (viewName === "investigations") loadInvestigations();
  else if (viewName === "environments") loadEnvironments();
}

function switchSubTab(subTabName) {
  state.currentSubTab = subTabName;
  document.querySelectorAll(".subview-panel").forEach((el) => (el.style.display = "none"));
  document.querySelectorAll("nav[aria-label='Investigation Views'] .nav-btn").forEach((btn) => {
    btn.classList.remove("active");
    btn.setAttribute("aria-selected", "false");
  });

  const targetSubPanel = document.getElementById(`subview-${subTabName}`);
  const targetSubNav = document.getElementById(`subtab-${subTabName}`);
  if (targetSubPanel) targetSubPanel.style.display = "block";
  if (targetSubNav) {
    targetSubNav.classList.add("active");
    targetSubNav.setAttribute("aria-selected", "true");
  }

  if (!state.currentInvId) return;

  if (subTabName === "timeline") loadTimeline(state.currentInvId);
  else if (subTabName === "candidates") loadCandidates(state.currentInvId);
  else if (subTabName === "trials") loadTrials(state.currentInvId);
  else if (subTabName === "verification") loadVerifications(state.currentInvId);
  else if (subTabName === "report") loadReport(state.currentInvId);
}

// --- Data Loading Functions ---

// 1. Overview
async function loadOverview() {
  try {
    const data = await apiFetch("/api/overview");
    document.getElementById("stat-investigations").textContent = data.counts.investigations;
    document.getElementById("stat-collections").textContent = data.counts.collection_runs;
    document.getElementById("stat-monitoring").textContent = data.counts.monitoring_sessions;
    document.getElementById("stat-verifications").textContent = data.counts.verification_runs;
    document.getElementById("stat-gaps").textContent = data.counts.active_coverage_gaps;

    // Render Recent Investigations
    const tbody = document.getElementById("recent-investigations-tbody");
    if (!data.recent_investigations || data.recent_investigations.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--text-muted);">No investigations recorded yet. Click '+ New Investigation' to begin.</td></tr>`;
    } else {
      tbody.innerHTML = data.recent_investigations
        .map((inv) => {
          const dt = formatDateTime(inv.created_at);
          return `
          <tr class="clickable" onclick="openInvestigation('${escapeHtml(inv.id)}')">
            <td><strong style="font-family:var(--font-mono); color:var(--accent-blue);">${escapeHtml(inv.id)}</strong></td>
            <td><strong>${escapeHtml(inv.target_system)}</strong></td>
            <td style="max-width:320px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(inv.symptom)}</td>
            <td>${getOutcomeBadge(inv.status)}</td>
            <td title="UTC: ${escapeHtml(dt.utc)}">${escapeHtml(dt.local)}</td>
            <td><button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); openInvestigation('${escapeHtml(inv.id)}')">Inspect</button></td>
          </tr>
        `;
        })
        .join("");
    }

    // Render Active Coverage Gaps
    const gapsContainer = document.getElementById("overview-coverage-gaps-container");
    if (!data.active_coverage_gaps || data.active_coverage_gaps.length === 0) {
      gapsContainer.innerHTML = `<p style="color:var(--text-muted); font-size:0.85rem;">No active telemetry gaps recorded across recent sessions.</p>`;
    } else {
      gapsContainer.innerHTML = `
        <div class="table-responsive">
          <table class="data-table">
            <thead>
              <tr>
                <th scope="col">Source</th>
                <th scope="col">Category</th>
                <th scope="col">Reason</th>
                <th scope="col">Details</th>
              </tr>
            </thead>
            <tbody>
              ${data.active_coverage_gaps
                .map(
                  (g) => `
                <tr>
                  <td><span class="badge badge-unavailable">${escapeHtml(g.source)}</span></td>
                  <td><strong>${escapeHtml(g.category)}</strong></td>
                  <td style="color:#f87171;">${escapeHtml(g.reason)}</td>
                  <td style="font-size:0.8rem; color:var(--text-secondary);">${escapeHtml(g.details || "N/A")}</td>
                </tr>
              `
                )
                .join("")}
            </tbody>
          </table>
        </div>
      `;
    }
  } catch (err) {
    console.error("Failed to load overview:", err);
  }
}

// 2. Investigations Directory
async function loadInvestigations() {
  const search = document.getElementById("inv-search-input").value;
  const status = document.getElementById("inv-status-filter").value;
  const query = new URLSearchParams();
  if (search) query.set("search", search);
  if (status && status !== "ALL") query.set("status", status);

  try {
    const data = await apiFetch(`/api/investigations?${query.toString()}`);
    state.investigations = data.investigations || [];
    const tbody = document.getElementById("all-investigations-tbody");

    if (state.investigations.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color:var(--text-muted);">No investigations match the selected filters.</td></tr>`;
      return;
    }

    tbody.innerHTML = state.investigations
      .map((inv) => {
        const createdDt = formatDateTime(inv.created_at);
        const onsetDt = formatDateTime(inv.onset_time);
        return `
        <tr class="clickable" onclick="openInvestigation('${escapeHtml(inv.id)}')">
          <td><strong style="font-family:var(--font-mono); color:var(--accent-blue);">${escapeHtml(inv.id)}</strong></td>
          <td><strong>${escapeHtml(inv.target_system)}</strong></td>
          <td style="max-width:350px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(inv.symptom)}</td>
          <td>${getOutcomeBadge(inv.status)}</td>
          <td title="UTC: ${escapeHtml(onsetDt.utc)}">${escapeHtml(onsetDt.local)}</td>
          <td title="UTC: ${escapeHtml(createdDt.utc)}">${escapeHtml(createdDt.local)}</td>
          <td><button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); openInvestigation('${escapeHtml(inv.id)}')">Inspect</button></td>
        </tr>
      `;
      })
      .join("");
  } catch (err) {
    console.error("Failed to load investigations:", err);
  }
}

// 3. Open Single Investigation Detail
async function openInvestigation(invId) {
  state.currentInvId = invId;
  document.querySelectorAll(".view-panel").forEach((el) => (el.style.display = "none"));
  document.getElementById("view-investigation-detail").style.display = "block";

  try {
    const inv = await apiFetch(`/api/investigations/${encodeURIComponent(invId)}`);
    document.getElementById("detail-inv-id").textContent = inv.id;
    document.getElementById("detail-inv-target").textContent = inv.target_system;
    document.getElementById("detail-inv-symptom").textContent = inv.symptom;

    const statusBadge = document.getElementById("detail-inv-status-badge");
    statusBadge.innerHTML = getOutcomeBadge(inv.status);

    const onsetDt = formatDateTime(inv.onset_time);
    document.getElementById("detail-inv-onset").textContent = onsetDt.local;
    document.getElementById("detail-inv-utc-onset").textContent = `UTC: ${onsetDt.utc}`;

    // Load active sub-tab
    switchSubTab(state.currentSubTab || "timeline");
  } catch (err) {
    alert(`Error loading investigation: ${err.message}`);
  }
}

// 4. Timeline View
async function loadTimeline(invId) {
  const sourceFilter = document.getElementById("timeline-source-filter").value;
  const outcomeFilter = document.getElementById("timeline-outcome-filter").value;
  const query = new URLSearchParams();
  if (sourceFilter && sourceFilter !== "ALL") query.set("source", sourceFilter);
  if (outcomeFilter && outcomeFilter !== "ALL") query.set("outcome", outcomeFilter);

  try {
    const data = await apiFetch(`/api/investigations/${encodeURIComponent(invId)}/timeline?${query.toString()}`);
    state.timelineItems = data.items || [];

    const visualContainer = document.getElementById("timeline-visual-container");
    const tableBody = document.getElementById("timeline-accessible-tbody");

    if (state.timelineItems.length === 0) {
      visualContainer.innerHTML = `<p style="color:var(--text-muted); padding:1rem;">No events match the selected timeline filters.</p>`;
      tableBody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--text-muted);">No timeline items.</td></tr>`;
      return;
    }

    // Render Visual Timeline
    visualContainer.innerHTML = state.timelineItems
      .map((item) => {
        const dt = formatDateTime(item.timestamp);
        const postOnsetCls = item.is_post_onset ? "post-onset" : "";
        const onsetMarkerCls = item.event_type === "SYMPTOM_ONSET" ? "onset-marker" : "";
        const postOnsetNotice = item.is_post_onset
          ? `<span class="badge badge-inconclusive" style="font-size:0.7rem;">Observed Post-Onset (Potential Consequence)</span>`
          : "";

        return `
        <div class="timeline-item ${postOnsetCls} ${onsetMarkerCls}">
          <div class="timeline-marker" aria-hidden="true"></div>
          <div class="timeline-meta">
            <span style="font-family:var(--font-mono); font-weight:600;" title="UTC: ${escapeHtml(dt.utc)}">${escapeHtml(dt.local)}</span>
            <span class="badge badge-unavailable">${escapeHtml(item.source)}</span>
            <span>&bull;</span>
            <span style="font-family:var(--font-mono);">${escapeHtml(item.source_provenance || "N/A")}</span>
            ${item.outcome ? getOutcomeBadge(item.outcome) : ""}
            ${postOnsetNotice}
          </div>
          <div class="timeline-title">${escapeHtml(item.title)}</div>
          <div class="timeline-desc">${escapeHtml(item.description)}</div>
        </div>
      `;
      })
      .join("");

    // Render Accessible Table Alternative
    tableBody.innerHTML = state.timelineItems
      .map((item) => {
        const dt = formatDateTime(item.timestamp);
        return `
        <tr>
          <td style="font-family:var(--font-mono);" title="UTC: ${escapeHtml(dt.utc)}">${escapeHtml(dt.local)}</td>
          <td><span class="badge badge-unavailable">${escapeHtml(item.source)}</span></td>
          <td style="font-family:var(--font-mono); font-size:0.8rem;">${escapeHtml(item.source_provenance || "")}</td>
          <td><strong>${escapeHtml(item.title)}</strong><br><span style="font-size:0.8rem; color:var(--text-secondary);">${escapeHtml(item.description)}</span></td>
          <td>${item.outcome ? getOutcomeBadge(item.outcome) : "-"}</td>
          <td>${item.is_post_onset ? '<span style="color:#fbbf24; font-weight:600;">Yes</span>' : "No"}</td>
        </tr>
      `;
      })
      .join("");
  } catch (err) {
    console.error("Failed to load timeline:", err);
  }
}

// 5. Candidates View
async function loadCandidates(invId) {
  try {
    const data = await apiFetch(`/api/investigations/${encodeURIComponent(invId)}/candidates`);
    const tbody = document.getElementById("candidates-tbody");
    const candidates = data.candidates || [];

    if (candidates.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--text-muted);">No candidates generated yet. Click '+ Generate from Telemetry' to generate candidate changes.</td></tr>`;
      return;
    }

    tbody.innerHTML = candidates
      .map((c) => {
        const sb = c.score_breakdown || {};
        const reviewBadge =
          c.review_status === "EXCLUDED"
            ? `<span class="badge badge-fail">EXCLUDED</span>`
            : c.review_status === "INCLUDED"
            ? `<span class="badge badge-pass">INCLUDED</span>`
            : `<span class="badge badge-unavailable">PENDING</span>`;

        return `
        <tr>
          <td><strong style="font-size:1.1rem; color:var(--accent-blue); font-family:var(--font-mono);">#${c.rank}</strong></td>
          <td style="font-family:var(--font-mono); font-size:0.8rem;">${escapeHtml(c.id)}</td>
          <td>
            <strong>${escapeHtml(c.component_name)}</strong><br>
            <span style="font-size:0.75rem; font-family:var(--font-mono); color:var(--text-muted);">${escapeHtml(c.change_key)}</span>
          </td>
          <td>
            <div style="font-size:1.1rem; font-weight:700; font-family:var(--font-mono);">${c.score.toFixed(1)}<span style="font-size:0.75rem; color:var(--text-muted);">/100</span></div>
            <div class="score-breakdown-grid" style="font-size:0.7rem;">
              <div class="score-cell"><span class="score-cell-label">Evidence:</span><span class="score-cell-val">+${(sb.evidence_type_score || 0).toFixed(0)}</span></div>
              <div class="score-cell"><span class="score-cell-label">Proximity:</span><span class="score-cell-val">+${(sb.temporal_proximity_score || 0).toFixed(0)}</span></div>
              <div class="score-cell"><span class="score-cell-label">Relevance:</span><span class="score-cell-val">+${(sb.target_relevance_score || 0).toFixed(0)}</span></div>
              <div class="score-cell"><span class="score-cell-label">Process:</span><span class="score-cell-val">+${(sb.process_provenance_score || 0).toFixed(0)}</span></div>
              <div class="score-cell"><span class="score-cell-label">Corroboration:</span><span class="score-cell-val">+${(sb.corroboration_score || 0).toFixed(0)}</span></div>
              <div class="score-cell"><span class="score-cell-label">Gap Penalty:</span><span class="score-cell-val" style="color:#f87171;">${(sb.coverage_gap_penalty || 0).toFixed(0)}</span></div>
            </div>
            <p style="font-size:0.75rem; color:var(--text-secondary); margin-top:0.25rem;"><em>${escapeHtml(sb.rationale || "")}</em></p>
          </td>
          <td><span class="badge badge-unavailable">${escapeHtml(c.evidence_type)}</span></td>
          <td style="font-size:0.8rem; font-family:var(--font-mono);">${escapeHtml(c.process_provenance)}</td>
          <td>${reviewBadge}</td>
          <td>
            <button class="btn btn-secondary btn-sm" onclick="openReviewModal('${escapeHtml(c.id)}', '${escapeHtml(c.component_name)}')">Review</button>
          </td>
        </tr>
      `;
      })
      .join("");
  } catch (err) {
    console.error("Failed to load candidates:", err);
  }
}

// 6. Experiments & Trials View
async function loadTrials(invId) {
  try {
    const data = await apiFetch(`/api/investigations/${encodeURIComponent(invId)}/trials`);
    const tbody = document.getElementById("trials-tbody");
    const trials = data.trials || [];

    if (trials.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color:var(--text-muted);">No trials recorded for this investigation. Run Delta Debugging via CLI to execute trials.</td></tr>`;
      return;
    }

    tbody.innerHTML = trials
      .map((t) => {
        const changesStr = t.selected_changes && t.selected_changes.length > 0 ? t.selected_changes.join(", ") : "&lt;clean baseline&gt;";
        const restoreBadge = t.is_baseline_verified
          ? `<span class="badge badge-pass">✔ Verified Clean</span>`
          : `<span class="badge badge-fail">✖ Failed</span>`;

        return `
        <tr>
          <td style="font-family:var(--font-mono); font-size:0.85rem;"><strong>${escapeHtml(t.trial_id.slice(0, 8))}</strong></td>
          <td><span class="badge badge-simulation">${escapeHtml(t.environment_id)}</span></td>
          <td style="font-family:var(--font-mono); font-size:0.85rem; color:var(--accent-cyan);">${escapeHtml(changesStr)}</td>
          <td>${getOutcomeBadge(t.health_outcome)}</td>
          <td>${t.attempt_count}</td>
          <td style="font-family:var(--font-mono);">${t.duration_ms.toFixed(1)} ms</td>
          <td>${restoreBadge}</td>
        </tr>
      `;
      })
      .join("");
  } catch (err) {
    console.error("Failed to load trials:", err);
  }
}

// 7. Verification Matrix View
async function loadVerifications(invId) {
  try {
    const data = await apiFetch(`/api/investigations/${encodeURIComponent(invId)}/verifications`);
    const container = document.getElementById("verification-runs-container");
    const runs = data.verification_runs || [];

    if (runs.length === 0) {
      container.innerHTML = `<p style="color:var(--text-muted);">No Phase 8 verification runs recorded for this investigation. Run <code>python statebisect_cli.py run-verification</code> to verify candidates.</p>`;
      return;
    }

    container.innerHTML = runs
      .map((v) => {
        const startDt = formatDateTime(v.start_time);
        const diffText = v.differential_baseline_id
          ? `<p style="font-size:0.85rem; margin-top:0.5rem;"><strong>Differential Baseline Validation:</strong> Tested against baseline <code>${escapeHtml(v.differential_baseline_id)}</code> &rarr; Verdict: ${getOutcomeBadge(v.differential_verdict || "N/A")}</p>`
          : "";
        const revText = v.reversal_verified !== null
          ? `<p style="font-size:0.85rem;"><strong>Safe Reversal Validation:</strong> ${v.reversal_verified ? '<span class="badge badge-pass">Verified Reversible</span>' : '<span class="badge badge-fail">Not Reversible</span>'}</p>`
          : "";

        const trialsRows = (v.trials || [])
          .map(
            (t) => `
            <tr>
              <td style="font-family:var(--font-mono); font-size:0.8rem;">${escapeHtml(t.trial_id.slice(0, 8))}</td>
              <td><span class="badge badge-unavailable">${escapeHtml(t.role)}</span></td>
              <td style="font-family:var(--font-mono);">${escapeHtml(t.changes_tested.join(", ") || "<baseline>")}</td>
              <td>${getOutcomeBadge(t.final_outcome)}</td>
              <td style="font-family:var(--font-mono);">${t.duration_ms.toFixed(1)} ms</td>
              <td style="font-size:0.8rem; color:var(--text-secondary);">${escapeHtml(t.notes || "")}</td>
            </tr>
          `
          )
          .join("");

        const necessityRows = (v.necessity_details || [])
          .map(
            (n) => `
            <tr>
              <td style="font-family:var(--font-mono); font-weight:600;">${escapeHtml(n.candidate_id)}</td>
              <td>${n.is_necessary ? '<span class="badge badge-pass">NECESSARY</span>' : '<span class="badge badge-fail">BYSTANDER</span>'}</td>
              <td>${getOutcomeBadge(n.trial_outcome)}</td>
              <td style="font-size:0.8rem; color:var(--text-secondary);">${escapeHtml(n.rationale)}</td>
            </tr>
          `
          )
          .join("");

        return `
        <div style="background-color:var(--bg-card); border:1px solid var(--border-color); border-radius:var(--radius-md); padding:1.25rem; margin-bottom:1.5rem;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem;">
            <div>
              <span style="font-family:var(--font-mono); color:var(--accent-blue); font-weight:700;">${escapeHtml(v.run_id)}</span>
              <span style="margin-left:0.5rem;">${getOutcomeBadge(v.verdict)}</span>
            </div>
            <span style="font-size:0.75rem; color:var(--text-muted);" title="UTC: ${escapeHtml(startDt.utc)}">${escapeHtml(startDt.local)}</span>
          </div>

          <p style="font-size:0.9rem; color:var(--text-primary); margin-bottom:0.75rem;"><strong>Rationale:</strong> ${escapeHtml(v.rationale)}</p>
          ${diffText}
          ${revText}

          <h4 style="font-size:0.85rem; color:var(--text-secondary); text-transform:uppercase; margin:1rem 0 0.5rem 0;">Necessity &amp; Minimality Analysis</h4>
          <div class="table-responsive" style="margin-bottom:1rem;">
            <table class="data-table">
              <thead>
                <tr>
                  <th scope="col">Candidate ID</th>
                  <th scope="col">Classification</th>
                  <th scope="col">Omitted Subset Trial Outcome</th>
                  <th scope="col">Rationale</th>
                </tr>
              </thead>
              <tbody>${necessityRows || '<tr><td colspan="4" style="color:var(--text-muted);">No necessity tests recorded.</td></tr>'}</tbody>
            </table>
          </div>

          <h4 style="font-size:0.85rem; color:var(--text-secondary); text-transform:uppercase; margin:1rem 0 0.5rem 0;">Trial Execution Audit Matrix</h4>
          <div class="table-responsive">
            <table class="data-table">
              <thead>
                <tr>
                  <th scope="col">Trial ID</th>
                  <th scope="col">Role</th>
                  <th scope="col">Changes Tested</th>
                  <th scope="col">Final Outcome</th>
                  <th scope="col">Duration</th>
                  <th scope="col">Notes</th>
                </tr>
              </thead>
              <tbody>${trialsRows || '<tr><td colspan="6" style="color:var(--text-muted);">No trial steps recorded.</td></tr>'}</tbody>
            </table>
          </div>
        </div>
      `;
      })
      .join("");
  } catch (err) {
    console.error("Failed to load verifications:", err);
  }
}

// 8. Diagnostic Report View
async function loadReport(invId) {
  try {
    const data = await apiFetch(`/api/investigations/${encodeURIComponent(invId)}/report?format=${state.activeReportFormat}`);
    document.getElementById("diagnostic-report-content").textContent = data.content;
  } catch (err) {
    document.getElementById("diagnostic-report-content").textContent = `Error generating report: ${err.message}`;
  }
}

// 9. Environments Directory
async function loadEnvironments() {
  try {
    const data = await apiFetch("/api/environments");
    state.environments = data.environments || [];
    const tbody = document.getElementById("environments-tbody");

    if (state.environments.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--text-muted);">No environments registered yet.</td></tr>`;
      return;
    }

    tbody.innerHTML = state.environments
      .map((e) => {
        const modeBadge = e.is_simulation
          ? `<span class="badge badge-simulation">In-Memory Simulation</span>`
          : e.is_disposable
          ? `<span class="badge badge-vm">Disposable Hyper-V Guest</span>`
          : `<span class="badge badge-corrupted">Non-Disposable</span>`;

        const baselinesStr = (e.baselines || []).map((b) => b.baseline_id).join(", ") || "None";

        return `
        <tr>
          <td style="font-family:var(--font-mono); font-weight:600;">${escapeHtml(e.environment_id)}</td>
          <td>${escapeHtml(e.name)}</td>
          <td><code>${escapeHtml(e.adapter_type)}</code></td>
          <td>${modeBadge}</td>
          <td>${getOutcomeBadge(e.status)}</td>
          <td style="font-family:var(--font-mono); font-size:0.85rem;">${escapeHtml(baselinesStr)}</td>
        </tr>
      `;
      })
      .join("");
  } catch (err) {
    console.error("Failed to load environments:", err);
  }
}

// --- Modals & Actions ---

function openReviewModal(candId, compName) {
  document.getElementById("review-candidate-id").value = candId;
  document.getElementById("review-candidate-label").textContent = `${candId} (${compName})`;
  document.getElementById("input-review-reason").value = "";
  document.getElementById("modal-candidate-review").style.display = "flex";
}

function closeReviewModal() {
  document.getElementById("modal-candidate-review").style.display = "none";
}

// --- Event Listeners Initialization ---
document.addEventListener("DOMContentLoaded", () => {
  // Navigation Tabs
  document.getElementById("tab-nav-overview").addEventListener("click", () => switchView("overview"));
  document.getElementById("tab-nav-investigations").addEventListener("click", () => switchView("investigations"));
  document.getElementById("tab-nav-environments").addEventListener("click", () => switchView("environments"));
  document.getElementById("brand-home-link").addEventListener("click", (e) => {
    e.preventDefault();
    switchView("overview");
  });

  // Back to Investigations button
  document.getElementById("btn-back-to-investigations").addEventListener("click", () => switchView("investigations"));

  // Investigation Sub-Tabs
  document.getElementById("subtab-timeline").addEventListener("click", () => switchSubTab("timeline"));
  document.getElementById("subtab-candidates").addEventListener("click", () => switchSubTab("candidates"));
  document.getElementById("subtab-trials").addEventListener("click", () => switchSubTab("trials"));
  document.getElementById("subtab-verification").addEventListener("click", () => switchSubTab("verification"));
  document.getElementById("subtab-report").addEventListener("click", () => switchSubTab("report"));

  // Timeline Filter & Format Toggle
  document.getElementById("btn-apply-timeline-filters").addEventListener("click", () => {
    if (state.currentInvId) loadTimeline(state.currentInvId);
  });
  document.getElementById("btn-toggle-timeline-format").addEventListener("click", () => {
    const visual = document.getElementById("timeline-visual-container");
    const table = document.getElementById("timeline-table-container");
    const btn = document.getElementById("btn-toggle-timeline-format");
    if (state.timelineFormat === "visual") {
      state.timelineFormat = "table";
      visual.style.display = "none";
      table.style.display = "block";
      btn.textContent = "Switch to Visual View";
    } else {
      state.timelineFormat = "visual";
      visual.style.display = "block";
      table.style.display = "none";
      btn.textContent = "Switch to Table View";
    }
  });

  // Report View Toggles
  document.getElementById("btn-report-view-text").addEventListener("click", () => {
    state.activeReportFormat = "text";
    if (state.currentInvId) loadReport(state.currentInvId);
  });
  document.getElementById("btn-report-view-json").addEventListener("click", () => {
    state.activeReportFormat = "json";
    if (state.currentInvId) loadReport(state.currentInvId);
  });
  document.getElementById("btn-download-report-txt").addEventListener("click", () => {
    if (!state.currentInvId) return;
    window.location.href = `/api/investigations/${encodeURIComponent(state.currentInvId)}/report/download?format=text`;
  });
  document.getElementById("btn-download-report-json").addEventListener("click", () => {
    if (!state.currentInvId) return;
    window.location.href = `/api/investigations/${encodeURIComponent(state.currentInvId)}/report/download?format=json`;
  });

  // Modal: Create Investigation
  const modalCreate = document.getElementById("modal-create-investigation");
  const openCreateModal = () => (modalCreate.style.display = "flex");
  const closeCreateModal = () => (modalCreate.style.display = "none");
  document.getElementById("btn-open-create-inv-modal").addEventListener("click", openCreateModal);
  document.getElementById("btn-open-create-inv-modal-2").addEventListener("click", openCreateModal);
  document.getElementById("btn-close-create-inv-modal").addEventListener("click", closeCreateModal);
  document.getElementById("btn-cancel-create-inv").addEventListener("click", closeCreateModal);

  document.getElementById("form-create-investigation").addEventListener("submit", async (e) => {
    e.preventDefault();
    const targetSystem = document.getElementById("input-target-system").value.trim();
    const symptom = document.getElementById("input-symptom").value.trim();
    const onsetTime = document.getElementById("input-onset-time").value.trim();
    const baselineId = document.getElementById("input-baseline-id").value.trim() || "baseline-001";

    try {
      const created = await apiFetch("/api/investigations", {
        method: "POST",
        body: { target_system: targetSystem, symptom, onset_time: onsetTime, baseline_id: baselineId },
      });
      closeCreateModal();
      document.getElementById("form-create-investigation").reset();
      openInvestigation(created.id);
    } catch (err) {
      alert(`Error creating investigation: ${err.message}`);
    }
  });

  // Modal: Candidate Review
  document.getElementById("btn-close-cand-review-modal").addEventListener("click", closeReviewModal);
  document.getElementById("btn-cancel-cand-review").addEventListener("click", closeReviewModal);
  document.getElementById("form-candidate-review").addEventListener("submit", async (e) => {
    e.preventDefault();
    const candId = document.getElementById("review-candidate-id").value;
    const action = document.getElementById("select-review-action").value;
    const reason = document.getElementById("input-review-reason").value.trim();
    const reviewer = document.getElementById("input-reviewer-name").value.trim() || "operator";

    if (action === "exclude" && (!reason || reason.length < 3)) {
      alert("Excluding a candidate requires a descriptive audit reason of at least 3 characters.");
      return;
    }

    try {
      await apiFetch(`/api/candidates/${encodeURIComponent(candId)}/review`, {
        method: "POST",
        body: { action, reason, reviewer },
      });
      closeReviewModal();
      if (state.currentInvId) loadCandidates(state.currentInvId);
    } catch (err) {
      alert(`Review error: ${err.message}`);
    }
  });

  // Modal: Generate Candidates
  const modalGen = document.getElementById("modal-generate-candidates");
  const openGenModal = () => (modalGen.style.display = "flex");
  const closeGenModal = () => (modalGen.style.display = "none");
  document.getElementById("btn-open-gen-candidates-modal").addEventListener("click", openGenModal);
  document.getElementById("btn-close-gen-cand-modal").addEventListener("click", closeGenModal);
  document.getElementById("btn-cancel-gen-cand").addEventListener("click", closeGenModal);

  document.getElementById("form-generate-candidates").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.currentInvId) return;

    const beforeRun = document.getElementById("input-gen-before-run").value.trim() || null;
    const afterRun = document.getElementById("input-gen-after-run").value.trim() || null;
    const monitorSess = document.getElementById("input-gen-monitor-sess").value.trim() || null;

    try {
      const res = await apiFetch(`/api/investigations/${encodeURIComponent(state.currentInvId)}/generate-candidates`, {
        method: "POST",
        body: { before_run_id: beforeRun, after_run_id: afterRun, monitor_session_id: monitorSess },
      });
      closeGenModal();
      alert(`Successfully generated ${res.count} candidate changes.`);
      loadCandidates(state.currentInvId);
    } catch (err) {
      alert(`Generation error: ${err.message}`);
    }
  });

  // Filter button for Investigations list
  document.getElementById("btn-apply-inv-filters").addEventListener("click", loadInvestigations);

  // Initial load
  loadOverview();
});
