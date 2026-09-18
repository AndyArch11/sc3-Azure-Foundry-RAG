  const _TAB_KEY = 'rag_active_tab';
  const _CORPUS_B_LIST_RETRY_MS = 5000;
  const _CORPUS_B_LIST_MAX_RETRIES = 6;
  const _CORPUS_C_LIST_RETRY_MS = 5000;
  const _CORPUS_C_LIST_MAX_RETRIES = 6;
  let _graphExplorerPayload = null;
  let _graphStatusPayload = null;
  const _GRAPH_LAST_BUILD_KEY = 'rag_graph_last_build_at';
  let _graphLastBuildAtMemory = '';
  const _GRAPH_ZOOM_MIN = 0.5;
  const _GRAPH_ZOOM_MAX = 3.0;
  const _GRAPH_DENSE_NODE_THRESHOLD = 1200;
  const _GRAPH_DENSE_EDGE_THRESHOLD = 4000;
  const _GRAPH_SAFE_MAX_VISIBLE_EDGES_DEFAULT = 8000;
  const _GRAPH_3D_CAMERA_FIT_DURATION_MS = 700;
  const _GRAPH_3D_CAMERA_FIT_PADDING_PX = 36;
  const _GRAPH_3D_FOCUS_DISTANCE = 180;
  let _graphLastFilteredMeta = null;
  let _graphCanvasTransform = { scale: 1, tx: 0, ty: 0 };
  let _graphVisualState = {
    nodes: [],
    edges: [],
    adjacency: new Map(),
    focusedNodeId: '',
    hoveredNodeId: '',
    handlersBound: false,
    communities: new Map(),
    selectedCommunities: null,
  };
  let _graphPanState = { active: false, startX: 0, startY: 0, startTx: 0, startTy: 0 };
  let _graphCy = null;
  let _graph3d = null;
  let _graph3dData = { nodes: [], links: [] };

  const _GRAPH_EDGE_COLORS = [
    '#0f766e', '#2563eb', '#dc2626', '#7c3aed', '#ea580c', '#0891b2', '#65a30d', '#be123c', '#1d4ed8', '#9333ea'
  ];
  let _graphEdgeColorByType = new Map();
  let _corpusBListRetryTimeout = null;
  let _corpusCListRetryTimeout = null;

  /**
   * Cancel any pending retry for fetching the Corpus B list. This function clears the timeout if it exists and resets the retry timeout variable to null.
   * @returns {void}
   */
  function _cancelCorpusBListRetry() {
    if (_corpusBListRetryTimeout) {
      clearTimeout(_corpusBListRetryTimeout);
      _corpusBListRetryTimeout = null;
    }
  }

  /**
   * Cancel any pending retry for fetching the Corpus C list. This function clears the timeout if it exists and resets the retry timeout variable to null.
   * @returns {void}
   */
  function _cancelCorpusCListRetry() {
    if (_corpusCListRetryTimeout) {
      clearTimeout(_corpusCListRetryTimeout);
      _corpusCListRetryTimeout = null;
    }
  }

  /**
   * Append a status note to the Corpus C status element. This function updates the text content of the status element by appending the provided note.
   * @param {string} note - The note to append to the status element.
   * @returns {void}
   */
  function _appendCorpusCStatusNote(note) {
    const statusEl = document.getElementById('cc-status');
    if (!statusEl) return;
    statusEl.textContent += '\n\n' + note;
  }

  /**
   * Append a status note to the Corpus B status element. This function updates the text content of the status element by appending the provided note.
   * @param {string} note - The note to append to the status element.
   * @returns {void}
   */
  function _appendCorpusBStatusNote(note) {
    const statusEl = document.getElementById('cb-status');
    if (!statusEl) return;
    statusEl.textContent += '\n\n' + note;
  }

  /**
   * Switch to the specified tab. This function updates the active state of the tab buttons and panels.
   * @param {string} name - The name of the tab to switch to.
   * @returns {void}
   */
  function switchTab(name) {
    document.querySelectorAll('.top-tab').forEach(function (btn) {
      var active = btn.id === ('tab-btn-' + name);
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    document.querySelectorAll('.tab-panel').forEach(function (panel) {
      var active = panel.id === ('tab-' + name);
      panel.classList.toggle('active', active);
      panel.hidden = !active;
    });
    if (name === 'graph') {
      refreshGraphStatus();
    }
    try { localStorage.setItem(_TAB_KEY, name); } catch (_) {}
  }

  /**
   * Refresh the Confluence poll status. This function fetches the latest poll status from the server and updates the UI accordingly. It retrieves the authentication token, constructs the request parameters, and handles the response to display the poll status or any errors encountered during the fetch operation.
   * @returns {void}
   */
  function refreshConfluencePollStatus() {
    var token = _currentAuthToken();
    var hours = Number(document.getElementById('cf-since-hours').value || 24);
    var params = new URLSearchParams({ since_hours: String(hours) });
    if (token) params.set('auth_token', token);
    var target = document.getElementById('cf-status');
    target.classList.remove('markdown');
    target.textContent = 'Loading…';
    fetch('/api/confluence/poll-status?' + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (data) { _renderConfluencePollStatus(data); })
      .catch(function (err) { target.textContent = 'Error: ' + String(err); });
  }

  /**
   * Render the Confluence poll status. This function updates the UI with the latest poll status data.
   * @param {*} data - The poll status data to render.
   * @returns {void}
   */
  function _renderConfluencePollStatus(data) {
    var target = document.getElementById('cf-status');
    if (!data || data.error) {
      target.classList.remove('markdown');
      target.textContent = JSON.stringify(data, null, 2);
      return;
    }
    var RISK_CLASS = { Low: 'risk-low', Medium: 'risk-medium', High: 'risk-high', Critical: 'risk-critical' };
    var STATUS_CLASS = {
      assessed: 'status-assessed',
      no_change: 'status-no-change',
      failed_terminal: 'status-failed-terminal',
      failed_retryable: 'status-failed-retryable'
    };
    var html = '<div class="answer">';
    var lp = data.last_poll;
    var summary = data.summary || {};
    var statusCounts = summary.page_status_counts || {};
    var riskCounts = summary.risk_counts || {};
    var failureCounts = summary.failure_status_counts || {};
    if (lp) {
      html += '<p><strong>Last poll:</strong> ' + escHtml(lp.polled_at || 'unknown') +
        ' &nbsp;|&nbsp; <strong>Space:</strong> ' + escHtml(lp.space_key || '—') +
        ' &nbsp;|&nbsp; <strong>Mentions found:</strong> ' + escHtml(String(lp.mentions_found !== undefined ? lp.mentions_found : '—')) +
        ' &nbsp;|&nbsp; <strong>Jobs queued:</strong> ' + escHtml(String(lp.jobs_queued !== undefined ? lp.jobs_queued : '—')) +
        ' &nbsp;|&nbsp; <strong>Watermark:</strong> ' + escHtml(lp.watermark || '—') + '</p>';
      if (lp.error) {
        html += '<p style="color:#b91c1c"><strong>Poll error:</strong> ' + escHtml(lp.error) + '</p>';
      }
    } else {
      html += '<p class="muted">No poll status recorded yet.</p>';
    }
    html += '<div class="poll-summary-grid">' +
      '<div class="poll-summary-card"><strong>Pages</strong><span>' + escHtml(String((data.assessed_pages || []).length)) + '</span></div>' +
      '<div class="poll-summary-card"><strong>Assessed</strong><span>' + escHtml(String(statusCounts.assessed || 0)) + '</span></div>' +
      '<div class="poll-summary-card"><strong>No change</strong><span>' + escHtml(String(statusCounts.no_change || 0)) + '</span></div>' +
      '<div class="poll-summary-card"><strong>High/Critical</strong><span>' + escHtml(String((riskCounts.High || 0) + (riskCounts.Critical || 0))) + '</span></div>' +
      '<div class="poll-summary-card"><strong>Failures</strong><span>' + escHtml(String((failureCounts.failed_retryable || 0) + (failureCounts.failed_terminal || 0))) + '</span></div>' +
      '</div>';
    if (!data.configured) {
      html += '<p class="muted"><em>' + escHtml(data.message || 'Confluence poll status not yet connected to a data source.') + '</em></p>';
    }
    var pages = data.assessed_pages || [];
    if (pages.length) {
      html += '<p><strong>Assessed pages (' + pages.length + '):</strong></p>';
      pages.forEach(function (page) {
        var riskKey = page.overall_risk || 'Unknown';
        var riskClass = RISK_CLASS[riskKey] || 'risk-unknown';
        var statusKey = String(page.status || 'assessed');
        var statusLabel = statusKey.replace(/_/g, ' ');
        var statusClass = STATUS_CLASS[statusKey] || 'status-assessed';
        var pageTitle = escHtml(page.title || page.page_id || 'Untitled');
        if (page.target_url) {
          pageTitle = '<a href="' + escAttr(page.target_url) + '" target="_blank" rel="noopener noreferrer">' + pageTitle + '</a>';
        }
        html += '<div class="poll-page-row">' +
          '<div class="poll-page-title">' + pageTitle +
          ' <span class="risk-badge ' + riskClass + '">' + escHtml(riskKey) + '</span>' +
          ' <span class="status-badge ' + statusClass + '">' + escHtml(statusLabel) + '</span></div>' +
          '<div class="poll-page-meta">' +
          'ID: ' + escHtml(String(page.page_id || '—')) +
          ' &nbsp;|&nbsp; Assessed: ' + escHtml(page.assessed_at || '—') +
          (page.space_key ? ' &nbsp;|&nbsp; Space: ' + escHtml(page.space_key) : '') +
          (page.framework ? ' &nbsp;|&nbsp; Framework: ' + escHtml(page.framework) : '') +
          (page.page_version ? ' &nbsp;|&nbsp; Version: ' + escHtml(String(page.page_version)) : '') +
          (page.findings_count !== undefined ? ' &nbsp;|&nbsp; Findings: ' + page.findings_count : '') +
          '</div></div>';
      });
    } else {
      html += '<p class="muted">No pages assessed in the selected period.</p>';
    }
    var failures = data.recent_failures || [];
    if (failures.length) {
      html += '<div class="poll-failures"><p><strong>Recent failures (' + failures.length + '):</strong></p>';
      failures.forEach(function (failure) {
        var failureStatus = String(failure.status || 'pending').replace(/_/g, ' ');
        var failureClass = STATUS_CLASS[failure.status] || 'status-failed-retryable';
        html += '<div class="poll-failure-row">' +
          '<div class="poll-failure-title">Event ' + escHtml(failure.event_id || 'unknown') +
          ' <span class="status-badge ' + failureClass + '">' + escHtml(failureStatus) + '</span></div>' +
          '<div class="poll-failure-meta">Attempts: ' + escHtml(String(failure.attempt_count || 0)) +
          ' &nbsp;|&nbsp; Last attempt: ' + escHtml(failure.last_attempt_at || '—') +
          (failure.run_id ? ' &nbsp;|&nbsp; Run: ' + escHtml(failure.run_id) : '') +
          '</div>' +
          (failure.last_error ? '<div class="poll-failure-error">' + escHtml(failure.last_error) + '</div>' : '') +
          '</div>';
      });
      html += '</div>';
    }
    html += '</div>';
    target.classList.add('markdown');
    target.innerHTML = html;
  }

  const SESSION_KEY = 'rag_session';
  let _lastComplianceReport = null;
  let _lastAzureComplianceReport = null;
  let _lastAwsComplianceReport = null;

  /**
   * Escape HTML special characters in a string.
   * @param {*} s - The string to escape.
   * @returns {string} The escaped string.
   */
  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  /**
   * Escape HTML attribute special characters in a string.
   * @param {*} s - The string to escape.
   * @returns {string} The escaped string.
   */
  function escAttr(s) {
    return String(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /**
   * Fallback Markdown renderer. Converts basic Markdown syntax to HTML.
   * @param {*} text - The Markdown text to render.
   * @returns {string} The rendered HTML.
   */
  function mdFallbackRender(text) {
    const raw = String(text || '');
    const lines = raw.split(/\r?\n/);
    const html = [];
    let listType = '';
    let paragraph = [];

    /**
     * Flush the current paragraph to the HTML output. This function checks if there are any lines in the current paragraph, and if so, it joins them with line breaks and wraps them in a <p> tag before adding them to the HTML output. It then clears the paragraph array for the next set of lines.
     * @returns {void}
     */
    function flushParagraph() {
      if (!paragraph.length) return;
      html.push('<p>' + paragraph.map(escHtml).join('<br/>') + '</p>');
      paragraph = [];
    }

    /**
     * Close the current list if any. This function checks if there is an open list, and if so, it closes the list by adding the appropriate closing tag to the HTML output and resets the list type.
     * @returns {void}
     */
    function closeList() {
      if (!listType) return;
      html.push('</' + listType + '>');
      listType = '';
    }

    for (const line of lines) {
      const trimmed = line.trim();

      if (!trimmed) {
        flushParagraph();
        closeList();
        continue;
      }

      const heading = trimmed.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        flushParagraph();
        closeList();
        const level = Math.min(6, heading[1].length);
        html.push('<h' + level + '>' + escHtml(heading[2]) + '</h' + level + '>');
        continue;
      }

      const ul = trimmed.match(/^[-*]\s+(.*)$/);
      if (ul) {
        flushParagraph();
        if (listType !== 'ul') {
          closeList();
          listType = 'ul';
          html.push('<ul>');
        }
        html.push('<li>' + escHtml(ul[1]) + '</li>');
        continue;
      }

      const ol = trimmed.match(/^\d+[.)]\s+(.*)$/);
      if (ol) {
        flushParagraph();
        if (listType !== 'ol') {
          closeList();
          listType = 'ol';
          html.push('<ol>');
        }
        html.push('<li>' + escHtml(ol[1]) + '</li>');
        continue;
      }

      closeList();
      paragraph.push(line);
    }

    flushParagraph();
    closeList();
    return html.join('') || '<p></p>';
  }

  /**
   * Load the session data from localStorage.
   * @returns {Object|null} The session data or null if not found.
   */
  function loadSession() {
    try { return JSON.parse(localStorage.getItem(SESSION_KEY) || 'null'); }
    catch { return null; }
  }

  /**
   * Save the session data to localStorage.
   * @param {Object} s - The session data to save.
   * @returns {void}
   */
  function saveSession(s) {
    localStorage.setItem(SESSION_KEY, JSON.stringify(s));
  }

  /**
   * Start a new conversation by generating new session and conversation IDs, and redirecting to the home page.
   * @returns {void}
   */
  function newConversation() {
    const s = loadSession() || {};
    saveSession({
      session_id: s.session_id || _randomUUID(),
      conversation_id: _randomUUID(),
      user_id: s.user_id || '',
      auth_token: s.auth_token || '',
      turns: []
    });
    window.location.href = '/';
  }

  /**
   * Render Markdown text to HTML. Uses the marked library if available, otherwise falls back to a basic renderer.
   * @param {string} text - The Markdown text to render.
   * @returns {string} The rendered HTML.
   */
  function mdRender(text) {
    if (typeof marked !== 'undefined') return marked.parse(text || '', { breaks: true, gfm: true });
    return mdFallbackRender(text);
  }

  /**
   * Reset the advanced fields in the "Ask" section to their default values.
   * @returns {void}
   */
  function resetAskAdvancedFields() {
    const defaultsEl = document.getElementById('ask-defaults');
    if (!defaultsEl) return;
    let defaults;
    try { defaults = JSON.parse(defaultsEl.textContent); }
    catch (_) { return; }

    function setVal(id, val) {
      const el = document.getElementById(id);
      if (!el) return;
      el.value = val != null ? String(val) : '';
    }

    setVal('retrieve_k', defaults.retrieve_k);
    setVal('controls_context_cap', defaults.controls_context_cap);
    setVal('temperature', defaults.temperature);
    setVal('top_p', defaults.top_p);
    setVal('max_completion_tokens', defaults.max_completion_tokens);
    setVal('evaluator_max_completion_tokens', defaults.evaluator_max_completion_tokens);
    setVal('controls_semantic', defaults.controls_semantic);
    setVal('controls_framework', defaults.controls_framework);
    setVal('controls_comparison_mode', defaults.controls_comparison_mode);
    setVal('include_graph_expansion', defaults.include_graph_expansion);
    setVal('graph_expansion_depth', defaults.graph_expansion_depth);
    setVal('graph_expansion_max_edges', defaults.graph_expansion_max_edges);

    // Deselect all corpora options (default = all)
    const corporaEl = document.getElementById('evidence_corpora_include');
    if (corporaEl) {
      Array.from(corporaEl.options).forEach(function (o) { o.selected = false; });
    }

    // Clear persisted localStorage overrides for token fields
    ['max_completion_tokens', 'evaluator_max_completion_tokens'].forEach(function (id) {
      try { localStorage.removeItem('rag_' + id); } catch (_) {}
    });
  }

  /**
   * Apply a preset configuration for the "Ask" section based on the selected thinking mode. This function retrieves the preset values from a JSON element and updates the corresponding input fields in the UI.
   * @param {string} mode - The thinking mode to apply.
   * @returns {void}
   */
  function applyAskThinkingModePreset(mode) {
    const presetsEl = document.getElementById('ask-thinking-presets');
    if (!presetsEl) return;

    let presets;
    try { presets = JSON.parse(presetsEl.textContent); }
    catch (_) { return; }

    const preset = presets && (presets[mode] || presets.balanced);
    if (!preset) return;

    function setVal(id, val, persist) {
      const el = document.getElementById(id);
      if (!el) return;
      el.value = val != null ? String(val) : '';
      if (persist) {
        try { localStorage.setItem('rag_' + id, el.value); } catch (_) {}
      }
    }

    setVal('retrieve_k', preset.retrieve_k, false);
    setVal('controls_context_cap', preset.controls_top_k, false);
    setVal('temperature', preset.temperature, false);
    setVal('top_p', preset.top_p, false);
    setVal('max_completion_tokens', preset.max_completion_tokens, true);
    setVal('evaluator_max_completion_tokens', preset.evaluator_max_completion_tokens, true);
  }

  /**
   * Apply a preset configuration for the "Assess" section based on the selected thinking mode. This function retrieves the preset values from a JSON element and updates the corresponding input fields in the UI.
   * @param {string} prefix - The prefix for the input field IDs.
   * @param {string} mode - The thinking mode to apply.
   * @returns {void}
   */
  function applyAssessThinkingModePreset(prefix, mode) {
    const presetsEl = document.getElementById('ask-thinking-presets');
    if (!presetsEl) return;

    let presets;
    try { presets = JSON.parse(presetsEl.textContent); }
    catch (_) { return; }

    const preset = presets && (presets[mode] || presets.balanced);
    if (!preset) return;

    /**
     * Set the value of an input field by its ID. If the element is not found, the function does nothing. If the value is null or undefined, it sets the input value to an empty string.
     * @param {string} id - The ID of the input field.
     * @param {*} val - The value to set.
     * @returns {void}
     */
    function setVal(id, val) {
      const el = document.getElementById(id);
      if (!el) return;
      el.value = val != null ? String(val) : '';
    }

    if (prefix === 'cr') {
      setVal('cr-retrieve-k', preset.retrieve_k);
    }
    setVal(prefix + '-controls-top-k', preset.controls_top_k);
    setVal(prefix + '-temperature', preset.temperature);
    setVal(prefix + '-top-p', preset.top_p);
    setVal(prefix + '-max-completion-tokens', preset.max_completion_tokens);
    setVal(prefix + '-evaluator-max-completion-tokens', preset.evaluator_max_completion_tokens);
  }

  /**
   * Clamp numeric field value to min/max bounds.
   * @param {HTMLInputElement|null} el - Numeric input element.
   * @returns {void}
   */
  function _clampNumericField(el) {
    if (!el) return;
    const raw = String(el.value || '').trim();
    if (!raw) return;
    const value = parseInt(raw, 10);
    if (!Number.isFinite(value)) return;
    const min = parseInt(el.getAttribute('min') || '0', 10);
    const max = parseInt(el.getAttribute('max') || '999999', 10);
    const clamped = Math.max(min, Math.min(max, value));
    if (clamped !== value) {
      el.value = String(clamped);
    }
  }

  /**
   * Apply runtime model limits/hints to ask and assess token fields.
   * @returns {void}
   */
  function applyRuntimeUiHints() {
    const hintsEl = document.getElementById('runtime-hints-ui');
    if (!hintsEl) return;
    let hints;
    try { hints = JSON.parse(hintsEl.textContent || '{}'); }
    catch (_) { return; }
    if (!hints || typeof hints !== 'object') return;

    const maxCompletion = parseInt(String(hints.max_completion_tokens_effective || ''), 10);
    const maxEvaluator = parseInt(String(hints.evaluator_max_completion_tokens_effective || ''), 10);
    const contextWindow = parseInt(String(hints.context_window_tokens_hint || ''), 10);
    const maxSource = String(hints.max_completion_tokens_source || 'runtime_config');
    const contextSource = String(hints.context_window_source || 'unknown');

    const askQueryField = document.getElementById('max_completion_tokens');
    const askEvalField = document.getElementById('evaluator_max_completion_tokens');
    const assessQueryFields = [
      document.getElementById('cr-max-completion-tokens'),
      document.getElementById('az-max-completion-tokens'),
      document.getElementById('aws-max-completion-tokens'),
    ];
    const assessEvalFields = [
      document.getElementById('cr-evaluator-max-completion-tokens'),
      document.getElementById('az-evaluator-max-completion-tokens'),
      document.getElementById('aws-evaluator-max-completion-tokens'),
    ];

    if (Number.isFinite(maxCompletion) && maxCompletion > 0) {
      if (askQueryField) {
        askQueryField.setAttribute('max', String(maxCompletion));
        _clampNumericField(askQueryField);
      }
      assessQueryFields.forEach(function (el) {
        if (!el) return;
        el.setAttribute('max', String(maxCompletion));
        _clampNumericField(el);
      });
    }

    if (Number.isFinite(maxEvaluator) && maxEvaluator > 0) {
      if (askEvalField) {
        askEvalField.setAttribute('max', String(maxEvaluator));
        _clampNumericField(askEvalField);
      }
      assessEvalFields.forEach(function (el) {
        if (!el) return;
        el.setAttribute('max', String(maxEvaluator));
        _clampNumericField(el);
      });
    }

    const askMaxHint = document.getElementById('max-completion-hint');
    if (askMaxHint) {
      askMaxHint.textContent = Number.isFinite(maxCompletion) && maxCompletion > 0
        ? ('Effective runtime max: ' + String(maxCompletion) + ' tokens (' + maxSource + ').')
        : 'Effective runtime max: not available (using configured defaults).';
    }

    const askEvalHint = document.getElementById('evaluator-max-completion-hint');
    if (askEvalHint) {
      askEvalHint.textContent = Number.isFinite(maxEvaluator) && maxEvaluator > 0
        ? ('Effective evaluator max: ' + String(maxEvaluator) + ' tokens.')
        : 'Effective evaluator max: not available (using configured defaults).';
    }

    const contextHint = document.getElementById('runtime-context-window-hint');
    if (contextHint) {
      contextHint.textContent = Number.isFinite(contextWindow) && contextWindow > 0
        ? ('Advisory context window: ' + String(contextWindow) + ' tokens (' + contextSource + ').')
        : 'Advisory context window: not available for this runtime.';
    }
  }

  /**
   * Set the rating for a specific turn in the conversation.
   * @param {HTMLElement} btn - The button element that was clicked.
   * @param {number} turnIdx - The index of the turn in the conversation.
   * @param {number} stars - The number of stars to set.
   * @returns {void}
   */
  function setRating(btn, turnIdx, stars) {
    const widget = document.querySelector('.rating-widget[data-turn="' + turnIdx + '"]');
    if (!widget) return;
    widget.dataset.rating = stars;
    widget.querySelectorAll('.rating-stars button').forEach((b, i) => b.classList.toggle('lit', i < stars));
  }

  /**
   * Submit the rating for a specific turn in the conversation.
   * @param {number} turnIdx - The index of the turn in the conversation.
   * @returns {void}
   */
  function submitRating(turnIdx) {
    const session = loadSession();
    if (!session || !session.conversation_id) { alert('No active conversation.'); return; }
    const widget = document.querySelector('.rating-widget[data-turn="' + turnIdx + '"]');
    const rating = parseInt(widget.dataset.rating || '0', 10);
    if (!rating) { alert('Please select a star rating first.'); return; }
    const todo = widget.querySelector('.rating-todo').value.trim();
    const turn = (session.turns || [])[turnIdx];
    const body = new URLSearchParams({
      user_id: session.user_id || '',
      rating: rating,
      todo: todo,
      assistant_timestamp: (turn && turn.a_ts) || '',
      auth_token: session.auth_token || '',
    });
    fetch('/api/conversations/' + encodeURIComponent(session.conversation_id) + '/rating', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    })
    .then(r => r.json())
    .then(data => {
      if (data.error) { alert('Rating failed: ' + data.error); return; }
      widget.innerHTML = '<span class="rating-done">&#10003; Rated ' + rating + '★' + (todo ? ' — ' + escHtml(todo) : '') + '</span>';
    })
    .catch(() => alert('Failed to submit rating.'));
  }

  /**
   * Render the conversation thread in the UI. This function takes an array of turns (question-answer pairs) and updates the conversation thread element with the corresponding HTML. If there are no turns, it hides the conversation wrapper and shows a fallback message.
   * @param {Array} turns - An array of turn objects, each containing a question (q) and an answer (a).
   * @returns {void}
   */
  function renderThread(turns) {
    const wrapper = document.getElementById('conversation-wrapper');
    const thread = document.getElementById('conversation-thread');
    const fallback = document.getElementById('latest-answer-fallback');
    if (!turns.length) { wrapper.style.display = 'none'; return; }
    wrapper.style.display = '';
    if (fallback) fallback.style.display = 'none';
    thread.innerHTML = turns.map((t, i) =>
      `<div class="chat-turn">
        <div class="chat-bubble user"><div class="chat-meta">You</div>${escHtml(t.q)}</div>
        <div class="chat-bubble assistant">
          <div class="chat-meta">Assistant</div>
          <div class="answer">${mdRender(t.a)}</div>
          <div class="rating-widget" data-turn="${i}">
            <div class="rating-stars">${[1,2,3,4,5].map(s =>
              `<button type="button" onclick="setRating(this,${i},${s})" title="Set a ${s}-star rating for this answer.">&#9733;</button>`
            ).join('')}</div>
            <input type="text" class="rating-todo" placeholder="Optional improvement note…" />
            <button type="button" class="btn-secondary" style="font-size:.8rem;padding:3px 10px" onclick="submitRating(${i})" title="Submit your selected rating and optional feedback note for this response.">Rate</button>
          </div>
        </div>
      </div>`
    ).join('');
    thread.scrollTop = thread.scrollHeight;
  }

  /**
   * Toggle the visibility of the past conversations panel. If the panel is currently hidden, it will be shown and the past conversations will be loaded. If the panel is currently visible, it will be hidden.
   * @returns {void}
   */
  function togglePastConversations() {
    const panel = document.getElementById('past-conv-panel');
    if (panel.style.display === 'none') {
      panel.style.display = '';
      loadPastConversations();
    } else {
      panel.style.display = 'none';
    }
  }

  /**
   * Load the past conversations for the current user and update the UI.
   * @returns {void}
   */
  function loadPastConversations() {
    const session = loadSession();
    const list = document.getElementById('past-conv-list');
    if (!session || !session.user_id) {
      list.innerHTML = '<li style="color:var(--muted);font-size:.85rem;padding:4px 0">Submit a question first to enable conversation history.</li>';
      return;
    }
    list.innerHTML = '<li style="color:var(--muted);font-size:.85rem;padding:4px 0">Loading…</li>';
    fetch('/api/conversations/' + encodeURIComponent(session.user_id) + '?auth_token=' + encodeURIComponent(session.auth_token || ''))
      .then(r => r.json())
      .then(data => {
        const convs = data.conversations || [];
        if (!convs.length) {
          list.innerHTML = '<li style="color:var(--muted);font-size:.85rem;padding:4px 0">No past conversations found.</li>';
          return;
        }
        list.innerHTML = convs.map(c => {
          const firstMsg = (c.messages || []).find(m => m.role === 'user');
          const preview = firstMsg ? firstMsg.content.slice(0, 100) : '(empty)';
          const date = new Date(c.updated_at || c.created_at).toLocaleString();
          const isCurrent = c.conversation_id === (loadSession() || {}).conversation_id;
          return `<li class="past-conv-item${isCurrent ? ' active' : ''}" onclick="selectConversation('${escAttr(c.conversation_id)}')">` +
            `<div class="past-conv-date">${escHtml(date)}${isCurrent ? ' (current)' : ''}</div>` +
            `<div class="past-conv-preview">${escHtml(preview)}</div></li>`;
        }).join('');
      })
      .catch(() => {
        list.innerHTML = '<li style="color:#b91c1c;font-size:.85rem;padding:4px 0">Failed to load conversations.</li>';
      });
  }

  /**
   * Select a conversation by its ID and load its messages.
   * @param {string} convId - The ID of the conversation to select.
   * @returns {void}
   */
  function selectConversation(convId) {
    const session = loadSession();
    if (!session || !session.user_id) return;
    fetch('/api/conversations/' + encodeURIComponent(session.user_id) + '/' + encodeURIComponent(convId) +
          '?auth_token=' + encodeURIComponent(session.auth_token || ''))
      .then(r => r.json())
      .then(data => {
        const msgs = data.messages || [];
        const turns = [];
        for (let i = 0; i + 1 < msgs.length; i++) {
          if (msgs[i].role === 'user' && msgs[i + 1].role === 'assistant') {
            turns.push({ q: msgs[i].content, a: msgs[i + 1].content, a_ts: msgs[i + 1].timestamp || '' });
            i++;
          }
        }
        session.conversation_id = convId;
        session.turns = turns;
        saveSession(session);
        document.getElementById('conversation_id_field').value = convId;
        document.getElementById('past-conv-panel').style.display = 'none';
        renderThread(turns);
      })
      .catch(e => console.error('Failed to load conversation:', e));
  }

  /**
   * Get the current authentication token from the input field or session.
   * @returns {string} The current authentication token.
   */
  function _currentAuthToken() {
    const authField = document.getElementById('auth_token');
    if (authField && authField.value) return authField.value;
    const session = loadSession();
    return (session && session.auth_token) ? session.auth_token : '';
  }

  /**
   * Extract the execution name from the given payload. This function checks for the presence of an execution name in the payload's job or jobs array and returns it if found.
   * @param {Object} payload - The payload object containing job information.
   * @returns {string|null} The extracted execution name or null if not found.
   */
  function _extractExecutionName(payload) {
    return (payload && payload.job && payload.job.execution_name) ||
      (payload && payload.jobs && payload.jobs.length > 0 && payload.jobs[0].job && payload.jobs[0].job.execution_name) || null;
  }

  /**
   * Find an execution in the diagnostic data by its name.
   * @param {Object} diagData - The diagnostic data containing recent executions.
   * @param {string} executionName - The name of the execution to find.
   * @returns {Object|null} The found execution object or null if not found.
   */
  function _findExecution(diagData, executionName) {
    if (!diagData || !diagData.recent_executions || !executionName) return null;
    return diagData.recent_executions.find(
      e => e.id && (e.id.endsWith('/' + executionName) || e.id === executionName)
    ) || null;
  }

  /**
   * Get the selected Corpus A frameworks.
   * @returns {Array<string>} An array of selected framework values.
   */
  function _selectedCorpusAFrameworks() {
    const allChecked = document.getElementById('ca-all').checked;
    if (allChecked) return ['all'];
    return Array.from(document.querySelectorAll('.ca-fw:checked')).map(el => el.value);
  }

  /**
   * Synchronise the state of the Corpus A framework checkboxes. This function ensures that the "all" checkbox reflects the state of individual framework checkboxes and vice versa.
   * @returns {void}
   */
  function _syncCorpusAFrameworkCheckboxes() {
    const allEl = document.getElementById('ca-all');
    const frameworkEls = Array.from(document.querySelectorAll('.ca-fw'));
    if (!allEl || !frameworkEls.length) return;

    const applyAllState = function (checked) {
      frameworkEls.forEach(function (el) {
        el.checked = checked;
      });
    };

    allEl.addEventListener('change', function () {
      if (allEl.checked) {
        applyAllState(true);
      }
    });

    frameworkEls.forEach(function (el) {
      el.addEventListener('change', function () {
        const allFrameworksChecked = frameworkEls.every(function (fw) { return fw.checked; });
        allEl.checked = allFrameworksChecked;
      });
    });

    // Normalise initial state on load.
    const allFrameworksChecked = frameworkEls.every(function (fw) { return fw.checked; });
    allEl.checked = allFrameworksChecked;
  }

  /**
   * Render the status of Corpus A in the UI. This function updates the corresponding HTML element with the provided payload data.
   * @param {Object} payload - The payload containing Corpus A status information.
   * @returns {void}
   */
  function _renderCorpusAStatus(payload) {
    const target = document.getElementById('ca-status');
    target.textContent = JSON.stringify(payload, null, 2);
  }

  /**
   * Render the status of Corpus C in the UI. This function updates the corresponding HTML element with the provided payload data.
   * @param {Object} payload - The payload containing Corpus C status information.
   * @returns {void}
   */
  function _renderCorpusCStatus(payload) {
    const target = document.getElementById('cc-status');
    const batchField = document.getElementById('cr-upload-batch');
    if (batchField) {
      if (payload && payload.upload && payload.upload.upload_batch_id) {
        _ensureSelectOption(batchField, payload.upload.upload_batch_id);
        batchField.value = payload.upload.upload_batch_id;
      } else {
        batchField.value = '';
      }
    }
    let prefix = '';
    if (payload && payload.mode === 'corpus-c-list' && payload.upload_batch_filter) {
      const overall = typeof payload.overall_total_count === 'number' ? payload.overall_total_count : 'unknown';
      const filtered = typeof payload.total_count === 'number' ? payload.total_count : 'unknown';
      prefix = `[Corpus C filter active: upload_batch=${payload.upload_batch_filter}; matched=${filtered}; overall=${overall}]\n`;
    }
    target.textContent = prefix + JSON.stringify(payload, null, 2);
  }

  /**
   * Render the status of Corpus B in the UI. This function updates the corresponding HTML element with the provided payload data.
   * @param {Object} payload - The payload containing Corpus B status information.
   * @returns {void}
   */
  function _renderCorpusBStatus(payload) {
    const target = document.getElementById('cb-status');
    const batchField = document.getElementById('cr-b-upload-batch');
    if (batchField) {
      if (payload && payload.upload && payload.upload.upload_batch_id) {
        _ensureSelectOption(batchField, payload.upload.upload_batch_id);
        batchField.value = payload.upload.upload_batch_id;
      } else {
        batchField.value = '';
      }
    }
    let prefix = '';
    if (payload && payload.mode === 'corpus-b-list' && payload.upload_batch_filter) {
      const overall = typeof payload.overall_total_count === 'number' ? payload.overall_total_count : 'unknown';
      const filtered = typeof payload.total_count === 'number' ? payload.total_count : 'unknown';
      prefix = `[Corpus B filter active: upload_batch=${payload.upload_batch_filter}; matched=${filtered}; overall=${overall}]\n`;
    }
    target.textContent = prefix + JSON.stringify(payload, null, 2);
  }

  /**
   * Ensure that a select element contains an option with the specified value. If the option does not exist, it will be created and appended.
   * @param {HTMLSelectElement} selectEl - The select element to update.
   * @param {string} value - The value to ensure exists in the select element.
   * @returns {void}
   */
  function _ensureSelectOption(selectEl, value) {
    if (!selectEl || !value) return;
    const exists = Array.from(selectEl.options || []).some(o => o.value === value);
    if (!exists) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      selectEl.appendChild(option);
    }
  }

  /**
   * Set the options for a batch select element based on the provided items.
   * @param {string} selectId - The ID of the select element to update.
   * @param {Array<Object>} items - The items to populate the select element with.
   * @returns {void}
   */
  function _setBatchSelectOptions(selectId, items) {
    const selectEl = document.getElementById(selectId);
    if (!selectEl) return;

    const previousValue = (selectEl.value || '').trim();
    const latestByBatch = new Map();
    for (const item of (items || [])) {
      const batch = String((item && item.upload_batch) || '').trim();
      if (!batch) continue;
      const uploadedAt = String((item && item.uploaded_at) || '');
      const existing = latestByBatch.get(batch);
      if (!existing || uploadedAt > existing) {
        latestByBatch.set(batch, uploadedAt);
      }
    }

    const sortedBatches = Array.from(latestByBatch.entries())
      .sort((a, b) => String(b[1]).localeCompare(String(a[1])))
      .map(([batch]) => batch);

    selectEl.innerHTML = '';
    const allOpt = document.createElement('option');
    allOpt.value = '';
    allOpt.textContent = 'All batches';
    selectEl.appendChild(allOpt);

    for (const batch of sortedBatches) {
      const opt = document.createElement('option');
      opt.value = batch;
      opt.textContent = batch;
      selectEl.appendChild(opt);
    }

    if (previousValue) {
      _ensureSelectOption(selectEl, previousValue);
      selectEl.value = previousValue;
    }
  }

  /**
   * Refresh the options for the compliance report batch select elements by fetching the latest data from the server. This function retrieves the list of batches for both Corpus B and Corpus C, updates the corresponding select elements, and displays the availability status of Corpus C.
   * @returns {void}
   */
  function _refreshComplianceBatchOptions() {
    const token = _currentAuthToken();
    const params = new URLSearchParams();
    if (token) params.set('auth_token', token);
    params.set('limit', '200');
    const qs = params.toString();

    Promise.all([
      fetch('/api/corpus-b/list' + (qs ? ('?' + qs) : '')).then(r => r.json()),
      fetch('/api/corpus-c/list' + (qs ? ('?' + qs) : '')).then(r => r.json()),
    ])
      .then(([bData, cData]) => {
        _setBatchSelectOptions('cr-b-upload-batch', bData && bData.items ? bData.items : []);
        _setBatchSelectOptions('cr-upload-batch', cData && cData.items ? cData.items : []);

        const cTotal = cData && typeof cData.total_count === 'number' ? cData.total_count : null;
        const hintEl = document.getElementById('cr-corpus-c-availability');
        const generateBtn = document.getElementById('cr-generate-btn');
        const hasCorpusC = typeof cTotal === 'number' && cTotal > 0;
        const _crBtns = document.querySelectorAll('#compliance-report-ops .ops-row button');
        _crBtns.forEach(b => { b.disabled = !hasCorpusC; });
        if (hintEl) {
          if (cTotal === null) {
            hintEl.textContent = 'Unable to determine Corpus C availability right now.';
          } else if (!hasCorpusC) {
            hintEl.textContent = 'Compliance Report is disabled: there are no Corpus C documents to assess.';
          } else {
            hintEl.textContent = 'Corpus C documents available for assessment: ' + String(cTotal) + '.';
          }
        }
      })
      .catch(() => {
        // Keep existing values/options when batch discovery fails.
        const hintEl = document.getElementById('cr-corpus-c-availability');
        if (hintEl) {
          hintEl.textContent = 'Unable to determine Corpus C availability right now.';
        }
      });
  }

  /**
   * Render the compliance report in the UI. This function updates the corresponding HTML element with the provided payload data.
   * @param {Object} payload - The payload containing compliance report information.
   * @returns {void}
   */
  function _renderComplianceReport(payload) {
    const target = document.getElementById('cr-status');
    if (payload && !payload.error && payload.mode === 'compliance-report') {
      _lastComplianceReport = payload;
    }
    if (payload && payload.report) {
      target.classList.add('markdown');
      target.innerHTML = '<div class="answer">' + mdRender(payload.report) + '</div>';
      return;
    }
    target.classList.remove('markdown');
    target.textContent = JSON.stringify(payload, null, 2);
  }

  /**
   * Render the Azure compliance report in the UI. This function updates the corresponding HTML element with the provided payload data.
   * @param {Object} payload - The payload containing Azure compliance report information.
   * @returns {void}
   */
  function _renderAzureComplianceReport(payload) {
    const target = document.getElementById('azcr-status');
    if (!target) return;
    if (payload && !payload.error && payload.mode === 'azure-compliance-report') {
      _lastAzureComplianceReport = payload;
    }
    if (payload && payload.report) {
      target.classList.add('markdown');
      target.innerHTML = '<div class="answer">' + mdRender(payload.report) + '</div>';
      return;
    }
    target.classList.remove('markdown');
    target.textContent = JSON.stringify(payload, null, 2);
  }

  /**
   * Render the AWS compliance report in the UI. This function updates the corresponding HTML element with the provided payload data.
   * @param {Object} payload - The payload containing AWS compliance report information.
   * @returns {void}
   */
  function _renderAwsComplianceReport(payload) {
    const target = document.getElementById('awscr-status');
    if (!target) return;
    if (payload && !payload.error && payload.mode === 'aws-compliance-report') {
      _lastAwsComplianceReport = payload;
    }
    if (payload && payload.report) {
      target.classList.add('markdown');
      target.innerHTML = '<div class="answer">' + mdRender(payload.report) + '</div>';
      return;
    }
    target.classList.remove('markdown');
    target.textContent = JSON.stringify(payload, null, 2);
  }

  /**
   * Get the elements related to the graph explorer in the UI.
   * @returns {Object} An object containing references to the graph explorer elements.
   */
  function _graphExplorerElements() {
    return {
      status: document.getElementById('gx-status'),
      summary: document.getElementById('gx-summary'),
      results: document.getElementById('gx-results'),
      nodes: document.getElementById('gx-nodes'),
      edges: document.getElementById('gx-edges')
    };
  }

  /**
   * Get the current graph status payload.
   * @returns {Object|null} The current graph status payload or null if not available.
   */
  function _graphStatus() {
    return _graphStatusPayload || null;
  }

  /**
   * Check if the graph is ready.
   * @returns {boolean} True if the graph is ready, false otherwise.
   */
  function _graphReady() {
    const status = _graphStatus();
    return !!(status && status.graph_ready);
  }

  function _graphControlsLoaded() {
    const status = _graphStatus();
    return !!(status && (status.index_loaded || status.controls_loaded));
  }

  /**
   * Set the graph status payload and synchronise the graph console state.
   * @param {Object|null} statusPayload - The new graph status payload or null to clear it.
   * @returns {void}
   */
  function _setGraphStatus(statusPayload) {
    _graphStatusPayload = statusPayload || null;
    _syncGraphConsoleState();
  }

  /**
   * Get the last successful graph build timestamp from localStorage.
   * @returns {string} The ISO timestamp of the last successful graph build, or an empty string if not available.
   */
  function _graphLastBuildAt() {
    let stored = '';
    try {
      stored = localStorage.getItem(_GRAPH_LAST_BUILD_KEY) || '';
    } catch (_) {
      stored = '';
    }
    return stored || _graphLastBuildAtMemory || '';
  }

  /**
   * Set the last successful graph build timestamp in localStorage.
   * @param {string|null} isoValue - The ISO timestamp to set, or null to remove it.
   * @returns {void}
   */
  function _setGraphLastBuildAt(isoValue) {
    _graphLastBuildAtMemory = isoValue ? String(isoValue) : '';
    try {
      if (isoValue) {
        localStorage.setItem(_GRAPH_LAST_BUILD_KEY, String(isoValue));
      } else {
        localStorage.removeItem(_GRAPH_LAST_BUILD_KEY);
      }
    } catch (_) {}
  }

  /**
   * Format an ISO timestamp for display in the graph build banner. If the value is invalid or not provided, it returns 'Never'.
   * @param {string|null} isoValue - The ISO timestamp to format.
   * @returns {string} The formatted date string or 'Never' if invalid.
   */
  function _formatGraphBuildTime(isoValue) {
    if (!isoValue) return 'Never';
    const dt = new Date(String(isoValue));
    if (isNaN(dt.getTime())) return String(isoValue);
    return dt.toLocaleString();
  }

  /**
   * Determine the state of the graph banner based on the provided status.
   * @param {Object|null} status - The current graph status payload.
   * @returns {Object} An object containing the key and label for the graph banner state.
   */
  function _graphBannerState(status) {
    if (!status) return { key: 'unknown', label: 'Unknown' };
    if (status.error) return { key: 'error', label: 'Error' };
    if (status.graph_ready) return { key: 'ready', label: 'Ready' };
    if (status.allow_build) return { key: 'build-needed', label: 'Build Needed' };
    return { key: 'waiting-for-controls', label: 'Waiting for Controls' };
  }

  /**
   * Render the graph build banner based on the provided status.
   * @param {Object|null} status - The current graph status payload.
   * @returns {void}
   */
  function _renderGraphBuildBanner(status) {
    const banner = document.getElementById('gx-build-banner');
    if (!banner) return;
    const state = _graphBannerState(status);
    const lastBuild = String((status && status.last_successful_build_at) || _graphLastBuildAt() || '').trim();
    const controlsCount = Number(status && status.controls_count ? status.controls_count : 0);
    const corpusBCount = Number(status && status.corpus_b_count ? status.corpus_b_count : 0);
    const indexLoaded = !!(status && (status.index_loaded || status.controls_loaded || status.corpus_b_loaded));
    const graphReady = !!(status && status.graph_ready);
    const needsRebuild = !!(status && status.graph_needs_rebuild);
    const stateClass = 'state-' + state.key;

    banner.className = 'graph-build-banner ' + stateClass;
    banner.innerHTML =
      '<div class="graph-build-banner-row">' +
        '<span class="graph-build-pill">' + state.label + '</span>' +
        '<span class="graph-build-meta">Index loaded (Corpus A/B): ' + (indexLoaded ? 'yes' : 'no') + ' (A: ' + controlsCount + ', B: ' + corpusBCount + ')</span>' +
        '<span class="graph-build-meta">Graph ready: ' + (graphReady ? 'yes' : 'no') + '</span>' +
        '<span class="graph-build-meta">Needs rebuild: ' + (needsRebuild ? 'yes' : 'no') + '</span>' +
      '</div>' +
      '<div class="graph-build-banner-row">' +
        '<span class="graph-build-meta">Last successful build: ' + _formatGraphBuildTime(lastBuild) + '</span>' +
      '</div>';
  }

  /**
   * Render the hyper-connected nodes warning based on the provided indicator.
   * @param {Object|null} indicator - The current hyper-connected nodes indicator payload.
   * @returns {void}
   */
  function _renderHyperConnectedWarning(indicator) {
    const el = document.getElementById('gx-hyper-warning');
    if (!el) return;
    if (!indicator || !indicator.detected) {
      el.style.display = 'none';
      el.textContent = '';
      return;
    }
    const ratioPct = Math.round((Number(indicator.hyper_connected_node_ratio || 0) * 10000)) / 100;
    const count = Number(indicator.hyper_connected_node_count || 0);
    const threshold = Number(indicator.threshold_degree || 0);
    const topDegree = Number(indicator.top_node_degree || 0);
    const examples = Array.isArray(indicator.examples) ? indicator.examples.slice(0, 5).join(', ') : '';
    el.style.display = '';
    el.innerHTML =
      '<div class="graph-hyper-warning-title">Warning: Hyper-connected nodes detected</div>' +
      '<div class="graph-hyper-warning-body">'
      + 'Detected ' + count + ' nodes at or above degree threshold ' + threshold + ' (' + ratioPct + '% of node population). '
      + 'Top node degree: ' + topDegree + '. '
      + escHtml(String(indicator.warning || 'Consider adaptive hierarchical depth pruning, forced sub-community partitioning, or map-reduce summarisation for hub neighborhoods.'))
      + '</div>'
      + (examples ? ('<div class="graph-hyper-warning-meta">Examples: ' + escHtml(examples) + '</div>') : '');
  }

  /**
   * Get the count of graph controls.
   * @returns {number} The number of graph controls.
   */
  function _graphControlsCount() {
    const status = _graphStatus();
    return Number(status && status.controls_count ? status.controls_count : 0);
  }

  /**
   * Synchronise the state of the graph console UI elements based on the current graph status. This function enables or disables buttons and input fields according to the graph's readiness, build status, and user permissions. It also updates the graph status display and renders the graph build banner.
   * @returns {void}
   */
  function _syncGraphConsoleState() {
    const status = _graphStatus();
    const buildButtons = [
      document.getElementById('gx-build-btn'),
      document.getElementById('gx-build-ask-btn'),
      document.getElementById('gx-load-snapshot-btn'),
      document.getElementById('gx-load-related-btn')
    ].filter(Boolean);
    const visualiseButtons = [
      document.getElementById('gx-load-snapshot-btn'),
      document.getElementById('gx-load-related-btn'),
      document.getElementById('gx-apply-filters-btn')
    ].filter(Boolean);
    const askGraphEl = document.getElementById('include_graph_expansion');
    const graphDepthEl = document.getElementById('graph_expansion_depth');
    const graphMaxEdgesEl = document.getElementById('graph_expansion_max_edges');
    const buildEnabled = !!(status && status.allow_build);
    const visualisationEnabled = !!(status && status.allow_visualisation);
    const askEnabled = !!(status && status.allow_ask_expansion);

    buildButtons.forEach(function (btn) { btn.disabled = !buildEnabled; });
    visualiseButtons.forEach(function (btn) { btn.disabled = !visualisationEnabled; });
    if (askGraphEl) askGraphEl.disabled = !askEnabled;
    if (graphDepthEl) graphDepthEl.disabled = !askEnabled;
    if (graphMaxEdgesEl) graphMaxEdgesEl.disabled = !askEnabled;

    const gxStatus = document.getElementById('gx-status');
    if (gxStatus) {
      const summary = status ? {
        index_loaded: status.index_loaded,
        index_count: status.index_count,
        controls_loaded: status.controls_loaded,
        controls_count: status.controls_count,
        corpus_b_loaded: status.corpus_b_loaded,
        corpus_b_count: status.corpus_b_count,
        graph_built: status.graph_built,
        graph_ready: status.graph_ready,
        graph_needs_rebuild: status.graph_needs_rebuild,
        allow_build: status.allow_build,
        allow_visualisation: status.allow_visualisation,
        allow_ask_expansion: status.allow_ask_expansion,
        backend: status.backend,
        reason: status.reason,
      } : { reason: 'unknown' };
      gxStatus.textContent = JSON.stringify(summary, null, 2);
    }
    _renderGraphBuildBanner(status);
  }

  /**
   * Refresh the graph status by fetching the latest information from the server. This function retrieves the current graph status, updates the internal state, and synchronises the graph console UI elements accordingly. If an error occurs during the fetch operation, it sets the graph status to an error state.
   * @returns {void}
   */
  function refreshGraphStatus() {
    const token = _currentAuthToken();
    const params = new URLSearchParams();
    if (token) params.set('auth_token', token);
    fetch('/api/graph/status' + (params.toString() ? ('?' + params.toString()) : ''))
      .then(function (r) { return r.json(); })
      .then(function (payload) {
        _setGraphStatus(payload);
      })
      .catch(function (err) {
        _setGraphStatus({ error: String(err) });
      });
  }

  /**
   * Create a payload for the graph build status.
   * @param {string} message - The status message.
   * @param {Object} [extra] - Additional properties to include in the payload.
   * @returns {Object} The graph build status payload.
   */
  function _graphBuildStatusPayload(message, extra) {
    return Object.assign(
      {
        mode: 'graph-build-status',
        message: message,
        controls_loaded: _graphControlsLoaded(),
        graph_ready: _graphReady(),
        graph_needs_rebuild: !!(_graphStatus() && _graphStatus().graph_needs_rebuild),
      },
      extra || {}
    );
  }

  /**
   * Get the value of a graph explorer input element.
   * @param {string} id - The ID of the input element.
   * @param {string} fallback - The fallback value if the input is empty or not found.
   * @returns {string} The value of the input element or the fallback.
   */
  function _graphExplorerValue(id, fallback) {
    const el = document.getElementById(id);
    if (!el) return fallback;
    const val = String(el.value || '').trim();
    return val || fallback;
  }

  /**
   * Populate a select element with options.
   * @param {string} selectId - The ID of the select element.
   * @param {Array<string>} values - The values to populate the select with.
   * @param {string} defaultLabel - The label for the default option.
   * @returns {void}
   */
  function _populateSelectOptions(selectId, values, defaultLabel) {
    const el = document.getElementById(selectId);
    if (!el) return;
    const previous = el.value;
    const unique = Array.from(new Set((values || []).filter(Boolean))).sort();
    el.innerHTML = '';
    const base = document.createElement('option');
    base.value = '';
    base.textContent = defaultLabel;
    el.appendChild(base);
    unique.forEach(function (value) {
      const opt = document.createElement('option');
      opt.value = value;
      opt.textContent = value;
      el.appendChild(opt);
    });
    if (previous && unique.includes(previous)) {
      el.value = previous;
    }
  }

  /**
   * Refresh the graph explorer filter options based on the provided payload.
   * @param {Object} payload - The payload containing nodes and edges information.
   * @returns {void}
   */
  function _refreshGraphExplorerFilterOptions(payload) {
    const nodes = (payload && payload.nodes) || [];
    const edges = (payload && payload.edges) || [];
    _populateSelectOptions(
      'gx-framework-filter',
      nodes.map(function (node) {
        const attrs = node.attributes || {};
        return String(attrs.framework || '').trim();
      }),
      'All frameworks'
    );
    _populateSelectOptions(
      'gx-node-type-filter',
      nodes.map(function (node) { return String(node.node_type || '').trim(); }),
      'All node types'
    );
    _populateSelectOptions(
      'gx-edge-type-filter',
      edges.map(function (edge) { return String(edge.edge_type || '').trim(); }),
      'All edge types'
    );
    const communityValues = Array.from(new Set(nodes.flatMap(function (node) {
      const attrs = node.attributes || {};
      const memberships = Array.isArray(attrs.__communities) ? attrs.__communities : [];
      return memberships.map(function (value) { return String(value || '').trim(); }).filter(Boolean);
    }))).sort();
    _populateSelectOptions(
      'gx-community-filter',
      communityValues,
      'All communities'
    );
    _graphVisualState.selectedCommunities = null;
  }

  /**
   * Compute the communities for the given nodes and edges.
   * @param {Array<Object>} nodes - The nodes in the graph.
   * @param {Array<Object>} edges - The edges in the graph.
   * @returns {Map<string, string>} A map of node IDs to community labels.
   */
  function _computeCommunities(nodes, edges) {
    const adjacency = new Map();
    nodes.forEach(function (n) { adjacency.set(String(n.node_id || ''), new Set()); });
    edges.forEach(function (e) {
      const a = String(e.from_id || '');
      const b = String(e.to_id || '');
      if (!adjacency.has(a) || !adjacency.has(b)) return;
      adjacency.get(a).add(b);
      adjacency.get(b).add(a);
    });

    const communityByNode = new Map();
    let idCounter = 1;
    nodes.forEach(function (n) {
      const nid = String(n.node_id || '');
      if (communityByNode.has(nid)) return;
      const queue = [nid];
      const label = 'Community ' + String(idCounter++);
      while (queue.length) {
        const cur = queue.shift();
        if (communityByNode.has(cur)) continue;
        communityByNode.set(cur, label);
        const next = adjacency.get(cur);
        if (!next) continue;
        next.forEach(function (m) {
          if (!communityByNode.has(m)) queue.push(m);
        });
      }
    });
    return communityByNode;
  }

  /**
   * Get the colour for a given edge type.
   * @param {string} edgeType - The type of the edge.
   * @returns {string} The colour associated with the edge type.
   */
  function _edgeColour(edgeType) {
    const key = String(edgeType || '').trim() || 'edge';
    if (_graphEdgeColorByType.has(key)) return _graphEdgeColorByType.get(key);
    const colour = _GRAPH_EDGE_COLORS[_graphEdgeColorByType.size % _GRAPH_EDGE_COLORS.length];
    _graphEdgeColorByType.set(key, colour);
    return colour;
  }

  /**
   * Render the edge legend for the graph.
   * @param {Array<Object>} edges - The edges in the graph.
   * @returns {void}
   */
  function _renderEdgeLegend(edges) {
    const legend = document.getElementById('gx-edge-legend');
    if (!legend) return;
    const types = Array.from(new Set((edges || []).map(function (e) { return String(e.edge_type || '').trim() || 'edge'; }))).sort();
    if (!types.length) {
      legend.style.display = 'none';
      legend.innerHTML = '';
      return;
    }
    legend.style.display = '';
    legend.innerHTML = '<div class="graph-legend-row">' + types.map(function (t) {
      const colour = _edgeColour(t);
      return '<span class="graph-legend-item"><span class="graph-legend-swatch" style="background:' + colour + '"></span>' + escHtml(t) + '</span>';
    }).join('') + '</div>';
  }

  /**
   * Render the community list for the graph.
   * @param {Array<Object>} nodes - The nodes in the graph.
   * @returns {void}
   */
  function _renderCommunityList(nodes) {
    const el = document.getElementById('gx-community-list');
    if (!el) return;
    const communities = new Map();
    (nodes || []).forEach(function (n) {
      const attrs = n.attributes || {};
      const memberships = Array.isArray(attrs.__communities) ? attrs.__communities : [];
      memberships.forEach(function (value) {
        const c = String(value || '').trim();
        if (!c) return;
        communities.set(c, (communities.get(c) || 0) + 1);
      });
    });
    if (!communities.size) {
      el.style.display = 'none';
      el.innerHTML = '';
      return;
    }
    el.style.display = '';
    const lines = Array.from(communities.entries()).sort(function (a, b) { return a[0].localeCompare(b[0]); });
    const selected = _graphVisualState.selectedCommunities;
    const allSelected = !selected || lines.every(function (entry) { return selected.has(entry[0]); });
    // If all communities are selected, the "Select all" button should show "Unselect all" and vice versa.
    // TODO curently when all are unselected, all communities disappear from view along with the graph and the visible nodes and edges. Consider adding a "Select all" fallback or a message to the user.
    el.innerHTML = '<div class="graph-community-title">Communities</div>' +
      '<div class="graph-community-actions"><button type="button" class="btn-secondary graph-community-toggle-all">' +
      (allSelected ? 'Unselect all' : 'Select all') + '</button></div>' +
      '<div class="graph-community-items">' +
      lines.map(function (entry) {
        const isSelected = allSelected || !selected || selected.has(entry[0]);
        return '<button type="button" class="graph-community-pill' + (isSelected ? ' is-selected' : '') + '" aria-pressed="' + String(isSelected) + '" data-community="' + escHtml(entry[0]) + '">' + escHtml(entry[0]) + ' (' + entry[1] + ')</button>';
      }).join('') +
      '</div>';
    const toggleAll = el.querySelector('.graph-community-toggle-all');
    if (toggleAll) toggleAll.addEventListener('click', toggleAllGraphCommunities);
    el.querySelectorAll('.graph-community-pill').forEach(function (button) {
      button.addEventListener('click', function () {
        toggleGraphCommunity(button.getAttribute('data-community') || '');
      });
    });
  }

  /**
   * Toggle one community in the graph visualisation filter.
   * @param {string} community - The community label to toggle.
   * @returns {void}
   */
  function toggleGraphCommunity(community) {
    const label = String(community || '').trim();
    if (!label) return;
    const available = _graphCommunityLabels(_graphExplorerPayload);
    const selected = _graphVisualState.selectedCommunities
      ? new Set(_graphVisualState.selectedCommunities)
      : new Set(available);
    if (selected.has(label)) selected.delete(label);
    else selected.add(label);
    _graphVisualState.selectedCommunities = selected.size === available.length ? null : selected;
    const dropdown = document.getElementById('gx-community-filter');
    if (dropdown) dropdown.value = selected.size === 1 ? Array.from(selected)[0] : '';
    applyGraphExplorerFilters();
  }

  /**
   * Select or unselect every community in the graph visualisation filter.
   * @returns {void}
   */
  function toggleAllGraphCommunities() {
    const available = _graphCommunityLabels(_graphExplorerPayload);
    const selected = _graphVisualState.selectedCommunities;
    const allSelected = !selected || available.every(function (label) { return selected.has(label); });
    _graphVisualState.selectedCommunities = allSelected ? new Set() : null;
    const dropdown = document.getElementById('gx-community-filter');
    if (dropdown) dropdown.value = '';
    applyGraphExplorerFilters();
  }

  function _graphCommunityLabels(payload) {
    return Array.from(new Set(((payload && payload.nodes) || []).flatMap(function (node) {
      const attrs = node.attributes || {};
      const memberships = Array.isArray(attrs.__communities) ? attrs.__communities : [];
      return memberships.map(function (value) { return String(value || '').trim(); }).filter(Boolean);
    }))).sort();
  }

  /**
   * Render the community summary for the graph.
   * @param {boolean} filtered - Whether the summary is filtered.
   * @returns {void}
   */
  function _renderCommunitySummary(filtered) {
    const el = document.getElementById('gx-community-summary');
    if (!el) return;
    const selectedCommunity = _graphExplorerValue('gx-community-filter', '');
    const payload = _graphExplorerPayload || {};
    const summaries = payload.community_summaries || {};
    if (!selectedCommunity || !summaries[selectedCommunity]) {
      el.style.display = 'none';
      el.innerHTML = '';
      return;
    }
    const entry = summaries[selectedCommunity] || {};
    const summary = String(entry.summary || '').trim();
    const nodeCount = Number(entry.node_count || 0);
    const edgeTypeCounts = entry.edge_type_counts || {};
    const frameworks = entry.framework_counts || {};
    const edgeBreakdown = Object.keys(edgeTypeCounts).length
      ? Object.entries(edgeTypeCounts).map(function (kv) { return kv[0] + ': ' + kv[1]; }).join(', ')
      : 'none';
    const fwBreakdown = Object.keys(frameworks).length
      ? Object.entries(frameworks).map(function (kv) { return kv[0] + ': ' + kv[1]; }).join(', ')
      : 'none';
    el.style.display = '';
    el.innerHTML =
      '<div class="graph-community-summary-title">' + escHtml(selectedCommunity) + '</div>' +
      '<div class="graph-community-summary-body">' + escHtml(summary || 'No summary available.') + '</div>' +
      '<div class="graph-community-summary-meta">Nodes: ' + nodeCount + ' | Frameworks: ' + escHtml(fwBreakdown) + ' | Edge types: ' + escHtml(edgeBreakdown) + '</div>';
  }

  /**
   * Apply the graph filters to the provided payload and return the filtered nodes and edges.
   * @param {Object} payload - The payload containing nodes and edges information.
   * @returns {Object} An object containing the filtered nodes and edges.
   */
  function _applyGraphFilters(payload) {
    const framework = _graphExplorerValue('gx-framework-filter', '');
    const nodeType = _graphExplorerValue('gx-node-type-filter', '');
    const edgeType = _graphExplorerValue('gx-edge-type-filter', '');
    const minConfidence = parseFloat(_graphExplorerValue('gx-min-confidence', '0')) || 0;
    const selectedCommunities = _graphVisualState.selectedCommunities;
    const community = _graphExplorerValue('gx-community-filter', '');
    const allowLargeRenderEl = document.getElementById('gx-allow-large-render');
    const allowLargeRender = !!(allowLargeRenderEl && allowLargeRenderEl.checked);
    const safeMaxEdgesRaw = Number(_graphExplorerValue('gx-safe-max-visible-edges', String(_GRAPH_SAFE_MAX_VISIBLE_EDGES_DEFAULT)));
    const safeMaxEdges = Number.isFinite(safeMaxEdgesRaw) ? Math.max(1, Math.floor(safeMaxEdgesRaw)) : _GRAPH_SAFE_MAX_VISIBLE_EDGES_DEFAULT;
    const nodes = (payload && payload.nodes) || [];
    const edges = (payload && payload.edges) || [];

    const visibleNodes = nodes.filter(function (node) {
      const attrs = node.attributes || {};
      if (framework && String(attrs.framework || '').trim() !== framework) return false;
      if (nodeType && String(node.node_type || '').trim() !== nodeType) return false;
      if (selectedCommunities) {
        const memberships = Array.isArray(attrs.__communities) ? attrs.__communities : [];
        const hasMembership = memberships.some(function (value) { return selectedCommunities.has(String(value || '').trim()); });
        if (!hasMembership) return false;
      } else if (community) {
        const memberships = Array.isArray(attrs.__communities) ? attrs.__communities : [];
        const hasMembership = memberships.some(function (value) { return String(value || '').trim() === community; });
        if (!hasMembership) return false;
      }
      return true;
    });
    const visibleNodeIds = new Set(visibleNodes.map(function (node) { return String(node.node_id || ''); }));

    const candidateEdges = edges.filter(function (edge) {
      const confidence = Number(edge.confidence || 0);
      if (edgeType && String(edge.edge_type || '').trim() !== edgeType) return false;
      if (confidence < minConfidence) return false;
      return visibleNodeIds.has(String(edge.from_id || '')) && visibleNodeIds.has(String(edge.to_id || ''));
    });

    const capped = !allowLargeRender && candidateEdges.length > safeMaxEdges;
    const visibleEdges = capped
      ? candidateEdges
        .slice()
        .sort(function (a, b) { return Number(b.confidence || 0) - Number(a.confidence || 0); })
        .slice(0, safeMaxEdges)
      : candidateEdges;

    return {
      nodes: visibleNodes,
      edges: visibleEdges,
      meta: {
        capped: capped,
        safe_max_visible_edges: safeMaxEdges,
        total_candidate_edges: candidateEdges.length,
      },
    };
  }

  /**
   * Render the graph explorer with the provided payload and filtered data. This function updates the UI elements related to the graph explorer, including status, summary, results, nodes, edges, edge legend, community list, and community summary.
   * @param {Object} payload - The payload containing nodes and edges information.
   * @param {Object} filtered - The filtered nodes and edges based on the applied filters.
   * @returns {void}
   */
  function _renderGraphExplorer(payload, filtered) {
    const els = _graphExplorerElements();
    if (!els.status || !els.summary || !els.results || !els.nodes || !els.edges) return;

    _graphLastFilteredMeta = {
      capped: !!(filtered.meta && filtered.meta.capped),
      safe_max_visible_edges: Number((filtered.meta && filtered.meta.safe_max_visible_edges) || 0),
      total_candidate_edges: Number((filtered.meta && filtered.meta.total_candidate_edges) || 0),
      visible_edges: Number(filtered.edges.length || 0),
    };

    const audit = payload && payload.audit ? payload.audit : {};
    els.status.textContent = JSON.stringify(audit, null, 2);
    els.summary.style.display = '';
    els.results.style.display = '';
    els.summary.textContent = JSON.stringify(
      {
        total_nodes: ((payload && payload.nodes) || []).length,
        total_edges: ((payload && payload.edges) || []).length,
        visible_nodes: filtered.nodes.length,
        visible_edges: filtered.edges.length,
        edge_render_capped: !!(filtered.meta && filtered.meta.capped),
        safe_max_visible_edges: filtered.meta && filtered.meta.safe_max_visible_edges,
        total_candidate_edges: filtered.meta && filtered.meta.total_candidate_edges,
        visible_communities: Array.from(new Set(filtered.nodes.flatMap(function (n) {
          const attrs = n.attributes || {};
          const memberships = Array.isArray(attrs.__communities) ? attrs.__communities : [];
          return memberships.map(function (value) { return String(value || '').trim(); }).filter(Boolean);
        }))).length,
        mode: (audit && audit.operation) || 'graph',
      },
      null,
      2
    );
    _renderEdgeLegend(filtered.edges);
    _renderCommunityList(payload.nodes || []);
    _renderCommunitySummary(filtered);
    _renderGraphCanvas(filtered);
    _renderGraphRenderBudget();
    els.nodes.textContent = JSON.stringify(filtered.nodes, null, 2);
    els.edges.textContent = JSON.stringify(filtered.edges, null, 2);
  }

  /**
   * Get the neighbourhood of a node in the graph.
   * @param {string} nodeId - The ID of the node.
   * @returns {Set<string>} A set of node IDs representing the neighbourhood.
   */
  function _graphNeighbourhood(nodeId) {
    const neighbours = new Set();
    if (!nodeId) return neighbours;
    const adj = _graphVisualState.adjacency.get(nodeId);
    if (!adj) return neighbours;
    adj.forEach(function (id) { neighbours.add(id); });
    neighbours.add(nodeId);
    return neighbours;
  }

  function _graphInteractionContext() {
    const selectedCommunity = _graphExplorerValue('gx-community-filter', '');
    const isolateToggle = document.getElementById('gx-community-isolate');
    const isolateCommunity = !!(isolateToggle && isolateToggle.checked && selectedCommunity);
    const activeNodeId = _graphVisualState.focusedNodeId || _graphVisualState.hoveredNodeId;
    const neighbourhood = _graphNeighbourhood(activeNodeId);
    return {
      selectedCommunity: selectedCommunity,
      isolateCommunity: isolateCommunity,
      activeNodeId: activeNodeId,
      neighbourhood: neighbourhood,
    };
  }

  function _graph3dNodeColor(node) {
    const ctx = _graphInteractionContext();
    const nodeId = String((node && node.id) || '');
    const nodeCommunities = Array.isArray(node && node.communities) ? node.communities : [];
    const isFocus = !!(_graphVisualState.focusedNodeId && nodeId === _graphVisualState.focusedNodeId);
    const isHover = !!(_graphVisualState.hoveredNodeId && nodeId === _graphVisualState.hoveredNodeId);
    const isCommunityDim = !!(ctx.isolateCommunity && nodeCommunities.length && !nodeCommunities.includes(ctx.selectedCommunity));
    const isDim = isCommunityDim || !!(ctx.activeNodeId && !ctx.neighbourhood.has(nodeId));
    if (isFocus) return '#065f46';
    if (isHover) return '#0d9488';
    if (isDim) return 'rgba(15,118,110,0.25)';
    return '#0f766e';
  }

  function _graph3dLinkColor(link) {
    const ctx = _graphInteractionContext();
    const fromId = String((link && link.sourceId) || '');
    const toId = String((link && link.targetId) || '');
    const fromCommunities = Array.isArray(link && link.fromCommunities) ? link.fromCommunities : [];
    const toCommunities = Array.isArray(link && link.toCommunities) ? link.toCommunities : [];
    const isHighlight = !!(ctx.activeNodeId && (fromId === ctx.activeNodeId || toId === ctx.activeNodeId || (ctx.neighbourhood.has(fromId) && ctx.neighbourhood.has(toId))));
    const fromMatches = fromCommunities.includes(ctx.selectedCommunity);
    const toMatches = toCommunities.includes(ctx.selectedCommunity);
    const isCommunityDim = !!(ctx.isolateCommunity && (!fromMatches || !toMatches));
    const isDim = isCommunityDim || !!(ctx.activeNodeId && !isHighlight);
    if (isDim) return 'rgba(148,163,184,0.10)';
    if (isHighlight) return '#0f766e';
    return String((link && link.color) || '#94a3b8');
  }

  function _graph3dLinkWidth(link) {
    const ctx = _graphInteractionContext();
    const fromId = String((link && link.sourceId) || '');
    const toId = String((link && link.targetId) || '');
    const isHighlight = !!(ctx.activeNodeId && (fromId === ctx.activeNodeId || toId === ctx.activeNodeId || (ctx.neighbourhood.has(fromId) && ctx.neighbourhood.has(toId))));
    const base = Number((link && link.widthPx) || 1.0);
    return isHighlight ? Math.max(1.6, base * 1.5) : base;
  }

  /**
   * Update the highlight classes for graph nodes and edges based on the current visual state. This function applies CSS classes to nodes and edges to indicate focus, hover, and dimming based on the neighbourhood of the active node.  
   * @returns {void}
   */
  function _updateGraphHighlightClasses() {
    if (_graph3d) {
      _graph3d.nodeColor(_graph3dNodeColor);
      _graph3d.linkColor(_graph3dLinkColor);
      _graph3d.linkWidth(_graph3dLinkWidth);
      if (typeof _graph3d.refresh === 'function') {
        _graph3d.refresh();
      }
      return;
    }

    if (_graphCy) {
      const ctx = _graphInteractionContext();

      _graphCy.batch(function () {
        _graphCy.nodes().forEach(function (node) {
          const nodeId = String(node.id() || '');
          const nodeCommunities = Array.isArray(node.data('communities')) ? node.data('communities') : [];
          const isFocus = !!(_graphVisualState.focusedNodeId && nodeId === _graphVisualState.focusedNodeId);
          const isHover = !!(_graphVisualState.hoveredNodeId && nodeId === _graphVisualState.hoveredNodeId);
          const isCommunityDim = !!(ctx.isolateCommunity && nodeCommunities.length && !nodeCommunities.includes(ctx.selectedCommunity));
          const isDim = isCommunityDim || !!(ctx.activeNodeId && !ctx.neighbourhood.has(nodeId));
          node.toggleClass('is-focus', isFocus);
          node.toggleClass('is-hover', isHover);
          node.toggleClass('is-dim', isDim);
        });

        _graphCy.edges().forEach(function (edge) {
          const fromId = String(edge.data('source') || '');
          const toId = String(edge.data('target') || '');
          const fromCommunities = Array.isArray(edge.data('fromCommunities')) ? edge.data('fromCommunities') : [];
          const toCommunities = Array.isArray(edge.data('toCommunities')) ? edge.data('toCommunities') : [];
          const isHighlight = !!(ctx.activeNodeId && (fromId === ctx.activeNodeId || toId === ctx.activeNodeId || (ctx.neighbourhood.has(fromId) && ctx.neighbourhood.has(toId))));
          const fromMatches = fromCommunities.includes(ctx.selectedCommunity);
          const toMatches = toCommunities.includes(ctx.selectedCommunity);
          const isCommunityDim = !!(ctx.isolateCommunity && (!fromMatches || !toMatches));
          const isDim = isCommunityDim || !!(ctx.activeNodeId && !isHighlight);
          edge.toggleClass('is-highlight', isHighlight);
          edge.toggleClass('is-community-dim', isCommunityDim);
          edge.toggleClass('is-dim', isDim);
        });
      });
      return;
    }

    const canvas = document.getElementById('gx-canvas');
    if (!canvas) return;
    const ctx = _graphInteractionContext();

    canvas.querySelectorAll('.graph-node').forEach(function (nodeEl) {
      const nodeId = String(nodeEl.getAttribute('data-node-id') || '');
      const nodeCommunities = String(nodeEl.getAttribute('data-communities') || '')
        .split('|')
        .map(function (value) { return String(value || '').trim(); })
        .filter(Boolean);
      const isFocus = !!(_graphVisualState.focusedNodeId && nodeId === _graphVisualState.focusedNodeId);
      const isHover = !!(_graphVisualState.hoveredNodeId && nodeId === _graphVisualState.hoveredNodeId);
      const isCommunityDim = !!(ctx.isolateCommunity && nodeCommunities.length && !nodeCommunities.includes(ctx.selectedCommunity));
      const isDim = isCommunityDim || !!(ctx.activeNodeId && !ctx.neighbourhood.has(nodeId));
      nodeEl.classList.toggle('is-focus', isFocus);
      nodeEl.classList.toggle('is-hover', isHover);
      nodeEl.classList.toggle('is-dim', isDim);
    });

    canvas.querySelectorAll('.graph-edge').forEach(function (edgeEl) {
      const fromId = String(edgeEl.getAttribute('data-from') || '');
      const toId = String(edgeEl.getAttribute('data-to') || '');
      const fromCommunities = String(edgeEl.getAttribute('data-from-communities') || '')
        .split('|')
        .map(function (value) { return String(value || '').trim(); })
        .filter(Boolean);
      const toCommunities = String(edgeEl.getAttribute('data-to-communities') || '')
        .split('|')
        .map(function (value) { return String(value || '').trim(); })
        .filter(Boolean);
      const isHighlight = !!(ctx.activeNodeId && (fromId === ctx.activeNodeId || toId === ctx.activeNodeId || (ctx.neighbourhood.has(fromId) && ctx.neighbourhood.has(toId))));
      const fromMatches = fromCommunities.includes(ctx.selectedCommunity);
      const toMatches = toCommunities.includes(ctx.selectedCommunity);
      const isCommunityDim = !!(ctx.isolateCommunity && (!fromMatches || !toMatches));
      const isDim = isCommunityDim || !!(ctx.activeNodeId && !isHighlight);
      edgeEl.classList.toggle('is-highlight', isHighlight);
      edgeEl.classList.toggle('is-dim', isDim);
    });
  }

  /**
   * Apply the current transform to the graph canvas. This function updates the SVG transform attribute based on the current pan and zoom state.
   * @returns {void}
   */
  function _applyGraphCanvasTransform() {
    if (_graphCy) {
      _graphCy.zoom(_graphCanvasTransform.scale);
      _graphCy.pan({ x: _graphCanvasTransform.tx, y: _graphCanvasTransform.ty });
      return;
    }

    const layer = document.getElementById('gx-panzoom-layer');
    if (!layer) return;
    const t = _graphCanvasTransform;
    layer.setAttribute('transform', 'translate(' + t.tx.toFixed(2) + ',' + t.ty.toFixed(2) + ') scale(' + t.scale.toFixed(3) + ')');
  }

  /**
   * Bind interactions for the graph canvas, including zooming, panning, and click events. This function sets up event listeners for mouse wheel, mouse down, mouse move, mouse up, and click events on the graph canvas. It also manages the state of panning and updates the visual state accordingly.
   * @returns {void}
   */
  function _bindGraphCanvasInteractions() {
    const canvas = document.getElementById('gx-canvas');
    if (!canvas || _graphVisualState.handlersBound) return;

    canvas.addEventListener('wheel', function (evt) {
      evt.preventDefault();
      const delta = evt.deltaY < 0 ? 1.08 : 0.92;
      const nextScale = Math.max(_GRAPH_ZOOM_MIN, Math.min(_GRAPH_ZOOM_MAX, _graphCanvasTransform.scale * delta));
      _graphCanvasTransform.scale = nextScale;
      _applyGraphCanvasTransform();
    }, { passive: false });

    canvas.addEventListener('mousedown', function (evt) {
      if (evt.button !== 0) return;
      _graphPanState.active = true;
      _graphPanState.startX = evt.clientX;
      _graphPanState.startY = evt.clientY;
      _graphPanState.startTx = _graphCanvasTransform.tx;
      _graphPanState.startTy = _graphCanvasTransform.ty;
      canvas.classList.add('is-panning');
    });

    canvas.addEventListener('mousemove', function (evt) {
      if (!_graphPanState.active) return;
      const dx = evt.clientX - _graphPanState.startX;
      const dy = evt.clientY - _graphPanState.startY;
      _graphCanvasTransform.tx = _graphPanState.startTx + dx;
      _graphCanvasTransform.ty = _graphPanState.startTy + dy;
      _applyGraphCanvasTransform();
    });

    const stopPan = function () {
      _graphPanState.active = false;
      canvas.classList.remove('is-panning');
    };
    canvas.addEventListener('mouseup', stopPan);
    canvas.addEventListener('mouseleave', stopPan);

    canvas.addEventListener('click', function (evt) {
      const nodeEl = evt.target && evt.target.closest ? evt.target.closest('.graph-node') : null;
      if (nodeEl) return;
      _graphVisualState.focusedNodeId = '';
      _updateGraphHighlightClasses();
    });

    _graphVisualState.handlersBound = true;
  }

  /**
   * Bind interactions for graph nodes, including hover and click events. This function sets up event listeners for mouse enter, mouse leave, and click events on each graph node element. It updates the visual state to reflect hovered and focused nodes.
   * @returns {void}
   */
  function _bindGraphNodeInteractions() {
    const canvas = document.getElementById('gx-canvas');
    if (!canvas) return;

    canvas.querySelectorAll('.graph-node').forEach(function (nodeEl) {
      const nodeId = String(nodeEl.getAttribute('data-node-id') || '');
      nodeEl.addEventListener('mouseenter', function () {
        _graphVisualState.hoveredNodeId = nodeId;
        _updateGraphHighlightClasses();
      });
      nodeEl.addEventListener('mouseleave', function () {
        if (_graphVisualState.hoveredNodeId === nodeId) {
          _graphVisualState.hoveredNodeId = '';
          _updateGraphHighlightClasses();
        }
      });
      nodeEl.addEventListener('click', function (evt) {
        evt.stopPropagation();
        _graphVisualState.focusedNodeId = (_graphVisualState.focusedNodeId === nodeId) ? '' : nodeId;
        _updateGraphHighlightClasses();
      });
    });
  }

  /**
   * Layout graph points based on the specified mode. This function calculates the positions of nodes in the graph, either using a force-directed layout or a circular layout.
   * @param {Array} nodes - The array of graph nodes.
   * @param {string} mode - The layout mode ('force' or other).
   * @param {number} width - The width of the layout area.
   * @param {number} height - The height of the layout area.
   * @returns {Array} An array of points with x and y coordinates for each node.
   */
  function _layoutGraphPoints(nodes, mode, width, height) {
    const centerX = width / 2;
    const centerY = height / 2;
    if (mode === 'force') {
      const clusters = new Map();
      nodes.forEach(function (node) {
        const attrs = node.attributes || {};
        const memberships = Array.isArray(attrs.__communities) ? attrs.__communities : [];
        const c = String(memberships[0] || '').trim() || 'Unclustered';
        if (!clusters.has(c)) clusters.set(c, []);
        clusters.get(c).push(node);
      });
      const clusterKeys = Array.from(clusters.keys());
      const clusterRadius = Math.max(100, Math.min(180, Math.floor(Math.min(width, height) / 3.3)));
      const points = [];
      clusterKeys.forEach(function (key, clusterIdx) {
        const clusterNodes = clusters.get(key) || [];
        const cAngle = (2 * Math.PI * clusterIdx) / Math.max(1, clusterKeys.length);
        const cx = centerX + clusterRadius * Math.cos(cAngle);
        const cy = centerY + clusterRadius * Math.sin(cAngle);
        const localR = Math.max(40, Math.min(120, 20 + clusterNodes.length * 6));
        clusterNodes.forEach(function (node, idx) {
          const angle = (2 * Math.PI * idx) / Math.max(1, clusterNodes.length);
          points.push({
            id: String(node.node_id || ''),
            label: String(node.label || node.node_id || ''),
            nodeType: String(node.node_type || ''),
            x: cx + localR * Math.cos(angle),
            y: cy + localR * Math.sin(angle),
          });
        });
      });
      return points;
    }

    const radius = Math.max(120, Math.min(220, Math.floor(Math.min(width, height) / 2.5)));
    return nodes.map(function (node, idx) {
      const angle = (2 * Math.PI * idx) / Math.max(1, nodes.length);
      return {
        id: String(node.node_id || ''),
        label: String(node.label || node.node_id || ''),
        nodeType: String(node.node_type || ''),
        x: centerX + radius * Math.cos(angle),
        y: centerY + radius * Math.sin(angle),
      };
    });
  }

  /**
   * Apply an adaptive Cytoscape rendering profile for dense graphs.
   * @param {number} nodeCount - Number of currently visible nodes.
   * @param {number} edgeCount - Number of currently visible edges.
   * @returns {void}
   */
  function _applyGraphCyPerformanceProfile(nodeCount, edgeCount) {
    if (!_graphCy) return;
    const mode = _graphExplorerValue('gx-performance-mode', 'auto');
    const dense = mode === 'dense' || (mode === 'auto' && (nodeCount >= _GRAPH_DENSE_NODE_THRESHOLD || edgeCount >= _GRAPH_DENSE_EDGE_THRESHOLD));
    const minZoomedFontSize = dense ? 22 : 8;
    const edgeCurve = dense ? 'haystack' : 'straight';
    _graphCy.style()
      .selector('node').style('min-zoomed-font-size', minZoomedFontSize)
      .selector('edge').style('curve-style', edgeCurve)
      .update();
  }

  function _graphRenderMode() {
    return _graphExplorerValue('gx-layout-mode', 'radial') === 'force-3d' ? '3d' : '2d';
  }

  function _graphNodeLabelById(nodeId) {
    const id = String(nodeId || '').trim();
    if (!id) return '';
    const payloadNodes = (_graphExplorerPayload && Array.isArray(_graphExplorerPayload.nodes)) ? _graphExplorerPayload.nodes : [];
    const match = payloadNodes.find(function (node) {
      return String(node.node_id || '') === id;
    });
    return String((match && (match.label || match.node_id)) || id);
  }

  function _renderGraphModeHint() {
    const hintEl = document.getElementById('gx-render-mode-hint');
    if (!hintEl) return;
    if (!_graphExplorerPayload) {
      hintEl.style.display = 'none';
      hintEl.textContent = '';
      return;
    }
    const renderer = _graphRenderMode();
    const sensitivity = _graph3dSensitivityPreset();
    hintEl.style.display = '';
    hintEl.textContent = renderer === '3d'
      ? ('3D mode: drag to rotate, scroll to zoom, hover to inspect names, and use Recenter 3D Camera when drifting. Sensitivity: ' + sensitivity + '.')
      : '2D mode: zoom threshold controls label visibility and supports precision pan/zoom navigation.';
  }

  function _setGraphHoverNodeName(name) {
    const el = document.getElementById('gx-hover-node');
    if (!el) return;
    el.style.display = '';
    const text = String(name || '').trim();
    if (!text) {
      el.style.visibility = 'hidden';
      el.textContent = 'Hovered node: -';
      return;
    }
    el.style.visibility = 'visible';
    el.textContent = 'Hovered node: ' + text;
  }

  function _setGraphFocusNodeName(name) {
    const el = document.getElementById('gx-focus-node');
    if (!el) return;
    el.style.display = '';
    const text = String(name || '').trim();
    if (!text) {
      el.style.visibility = 'hidden';
      el.textContent = 'Focused node: -';
      return;
    }
    el.style.visibility = 'visible';
    el.textContent = 'Focused node: ' + text;
  }

  function _destroyGraphCy() {
    if (!_graphCy) return;
    try { _graphCy.destroy(); } catch (_) {}
    _graphCy = null;
  }

  function _destroyGraph3d() {
    if (!_graph3d) return;
    if (typeof _graph3d._destructor === 'function') {
      try { _graph3d._destructor(); } catch (_) {}
    }
    _graph3d = null;
    _graph3dData = { nodes: [], links: [] };
  }

  function _fitGraph3dCamera(durationMs) {
    if (!_graph3d || typeof _graph3d.zoomToFit !== 'function') return;
    try { _graph3d.zoomToFit(durationMs || _GRAPH_3D_CAMERA_FIT_DURATION_MS, _GRAPH_3D_CAMERA_FIT_PADDING_PX); } catch (_) {}
  }

  function _graph3dSensitivityPreset() {
    const raw = _graphExplorerValue('gx-3d-sensitivity', 'standard');
    if (raw === 'calm' || raw === 'fast') return raw;
    return 'standard';
  }

  function _tuneGraph3dControls(dense) {
    if (!_graph3d || typeof _graph3d.controls !== 'function') return;
    const controls = _graph3d.controls();
    if (!controls) return;
    const sensitivity = _graph3dSensitivityPreset();
    const profile = sensitivity === 'calm'
      ? { damping: 0.23, rotate: 0.50, zoom: 0.68, pan: 0.52 }
      : sensitivity === 'fast'
        ? { damping: 0.11, rotate: 1.00, zoom: 1.10, pan: 0.92 }
        : { damping: 0.16, rotate: 0.78, zoom: 0.90, pan: 0.72 };

    const denseScale = dense ? 0.82 : 1.0;
    controls.enableDamping = true;
    controls.dampingFactor = Math.max(0.08, Math.min(0.30, profile.damping + (dense ? 0.03 : 0)));
    controls.rotateSpeed = Math.max(0.35, profile.rotate * denseScale);
    controls.zoomSpeed = Math.max(0.55, profile.zoom * denseScale);
    controls.panSpeed = Math.max(0.45, profile.pan * denseScale);
    controls.minDistance = 18;
    controls.maxDistance = dense ? 3200 : 2600;
  }

  function _graphLabelZoomThreshold() {
    const raw = Number(_graphExplorerValue('gx-label-zoom-threshold', '1'));
    if (!Number.isFinite(raw)) return 1;
    return Math.max(_GRAPH_ZOOM_MIN, Math.min(_GRAPH_ZOOM_MAX, raw));
  }

  function _updateGraphCyLabelLod(forceUpdate) {
    if (!_graphCy) return;
    const threshold = _graphLabelZoomThreshold();
    const showLabels = _graphCy.zoom() >= threshold;
    const previous = _graphCy.scratch('_labelLodVisible');
    if (!forceUpdate && previous === showLabels) return;
    _graphCy.scratch('_labelLodVisible', showLabels);
    _graphCy.style()
      .selector('node').style('label', showLabels ? 'data(shortLabel)' : '')
      .update();

    _renderGraphRenderBudget();
  }

  function _renderGraphRenderBudget() {
    const budgetEl = document.getElementById('gx-render-budget');
    if (!budgetEl) return;
    if (!_graphExplorerPayload) {
      budgetEl.style.display = 'none';
      budgetEl.innerHTML = '';
      return;
    }

    const mode = _graphExplorerValue('gx-performance-mode', 'auto');
    const sensitivity = _graph3dSensitivityPreset();
    const renderer = _graphRenderMode();
    const layoutMode = _graphExplorerValue('gx-layout-mode', 'radial');
    const threshold = _graphLabelZoomThreshold();
    const zoom = _graphCy ? Number(_graphCy.zoom() || 0) : Number(_graphCanvasTransform.scale || 1);
    const labelsVisible = renderer === '3d' ? true : zoom >= threshold;
    const meta = _graphLastFilteredMeta || {};
    const candidateEdges = Number(meta.total_candidate_edges || 0);
    const visibleEdges = _graphCy ? _graphCy.edges().length : (_graph3d ? _graph3dData.links.length : Number(meta.visible_edges || 0));
    const capped = !!meta.capped;
    let zoomLabel = zoom.toFixed(2);
    if (_graph3d && typeof _graph3d.cameraPosition === 'function') {
      const cam = _graph3d.cameraPosition();
      if (cam && Number.isFinite(cam.z)) {
        zoomLabel = 'z=' + Number(cam.z).toFixed(0);
      }
    }

    budgetEl.style.display = '';
    budgetEl.innerHTML =
      '<div class="graph-budget-title">Render Budget</div>' +
      '<div class="graph-budget-row">' +
        '<span class="graph-budget-pill">Renderer: ' + escHtml(renderer.toUpperCase()) + '</span>' +
        '<span class="graph-budget-pill">Layout: ' + escHtml(layoutMode) + '</span>' +
        '<span class="graph-budget-pill">Mode: ' + escHtml(mode) + '</span>' +
        '<span class="graph-budget-pill">3D sensitivity: ' + escHtml(sensitivity) + '</span>' +
        '<span class="graph-budget-pill">Cap: ' + (capped ? 'on' : 'off') + '</span>' +
        '<span class="graph-budget-pill">Edges: ' + String(visibleEdges) + '/' + String(candidateEdges) + '</span>' +
        '<span class="graph-budget-pill">Zoom: ' + zoomLabel + '</span>' +
        '<span class="graph-budget-pill">Labels: ' + (renderer === '3d' ? 'hover-only' : ((labelsVisible ? 'visible' : 'hidden') + ' @ ' + threshold.toFixed(1))) + '</span>' +
      '</div>';

    _renderGraphModeHint();
  }

  /**
   * Render the graph on the canvas. This function updates the SVG elements for nodes and edges based on the filtered data.
   * @param {Object} filtered - The filtered graph data containing nodes and edges.
   * @returns {void}
   */
  function _renderGraphCanvas(filtered) {
    const visual = document.getElementById('gx-visual');
    const canvas = document.getElementById('gx-canvas');
    if (!visual || !canvas) return;

    const nodes = (filtered && filtered.nodes) || [];
    const edges = (filtered && filtered.edges) || [];
    if (!nodes.length) {
      visual.style.display = 'none';
      _setGraphHoverNodeName('');
      if (_graphCy) {
        _graphCy.elements().remove();
      }
      if (_graph3d) {
        _graph3d.graphData({ nodes: [], links: [] });
      }
      if (!_graphCy && !_graph3d) {
        canvas.innerHTML = '';
      }
      return;
    }

    visual.style.display = '';
    const width = 900;
    const height = 520;
    const layoutMode = _graphExplorerValue('gx-layout-mode', 'radial');
    const performanceMode = _graphExplorerValue('gx-performance-mode', 'auto');
    const renderMode = _graphRenderMode();
    const dense = performanceMode === 'dense' || (performanceMode === 'auto' && (nodes.length >= _GRAPH_DENSE_NODE_THRESHOLD || edges.length >= _GRAPH_DENSE_EDGE_THRESHOLD));

    const nodeIndex = new Map();
    const adjacency = new Map();
    nodes.forEach(function (node, idx) {
      nodeIndex.set(String(node.node_id || ''), idx);
      adjacency.set(String(node.node_id || ''), new Set());
    });
    const nodeCommunityById = new Map(
      nodes.map(function (node) {
        const attrs = node && node.attributes && typeof node.attributes === 'object' ? node.attributes : {};
        const memberships = Array.isArray(attrs.__communities) ? attrs.__communities : [];
        const values = memberships.map(function (value) { return String(value || '').trim(); }).filter(Boolean);
        return [String(node.node_id || ''), values];
      })
    );

    if (renderMode === '3d') {
      if (typeof ForceGraph3D === 'undefined') {
        const statusEl = document.getElementById('gx-status');
        if (statusEl) statusEl.textContent = 'Error: 3D force graph library is not available in this browser session.';
        return;
      }

      const hadGraphCy = !!_graphCy;
      _destroyGraphCy();
      if (!_graph3d || hadGraphCy) {
        canvas.innerHTML = '';
      }

      if (!_graph3d) {
        _graph3d = ForceGraph3D()(canvas)
          .backgroundColor('#f8fafc')
          .nodeLabel(function () { return ''; })
          .enableNodeDrag(false)
          .onNodeHover(function (node) {
            const hoveredId = node ? String(node.id || '') : '';
            if (_graphVisualState.hoveredNodeId === hoveredId) return;
            _graphVisualState.hoveredNodeId = hoveredId;
            _setGraphHoverNodeName(node ? String(node.label || node.id || '') : '');
            _updateGraphHighlightClasses();
            _renderGraphRenderBudget();
          })
          .onNodeClick(function (node) {
            const nodeId = node ? String(node.id || '') : '';
            _graphVisualState.focusedNodeId = (_graphVisualState.focusedNodeId === nodeId) ? '' : nodeId;
            _setGraphFocusNodeName(_graphVisualState.focusedNodeId ? _graphNodeLabelById(_graphVisualState.focusedNodeId) : '');
            _updateGraphHighlightClasses();
          });
      }

      _graph3d.width(canvas.clientWidth || width);
      _graph3d.height(Math.max(320, canvas.clientHeight || height));

      const nodeElements3d = nodes.map(function (node) {
        const nodeId = String(node.node_id || '');
        const attrs = node && node.attributes && typeof node.attributes === 'object' ? node.attributes : {};
        const memberships = Array.isArray(attrs.__communities) ? attrs.__communities : [];
        const communities = memberships.map(function (value) { return String(value || '').trim(); }).filter(Boolean);
        const fullLabel = String(node.label || node.node_id || '');
        return {
          id: nodeId,
          label: fullLabel,
          communities: communities,
          nodeType: String(node.node_type || ''),
          displayLabel: _graphVisualState.focusedNodeId === nodeId ? fullLabel : '',
          val: dense ? 3 : 5,
        };
      });

      const edgeElements3d = edges
        .map(function (edge) {
          const fromId = String(edge.from_id || '');
          const toId = String(edge.to_id || '');
          const fromIdx = nodeIndex.get(fromId);
          const toIdx = nodeIndex.get(toId);
          if (fromIdx == null || toIdx == null) return null;
          adjacency.get(fromId)?.add(toId);
          adjacency.get(toId)?.add(fromId);
          const confidence = Number(edge.confidence || 0);
          const opacity = dense
            ? Math.max(0.04, Math.min(0.30, 0.04 + confidence * 0.26))
            : Math.max(0.15, Math.min(0.95, confidence || 0.15));
          const widthPx = dense
            ? Math.max(0.6, Math.min(1.8, 0.6 + confidence * 1.2))
            : Math.max(1, Math.min(4, 1 + confidence * 3));
          const edgeType = String(edge.edge_type || 'edge');
          const color = _edgeColour(edgeType);
          const fromCommunities = (nodeCommunityById.get(fromId) || []);
          const toCommunities = (nodeCommunityById.get(toId) || []);
          return {
            source: fromId,
            target: toId,
            sourceId: fromId,
            targetId: toId,
            edgeType: edgeType,
            confidence: confidence,
            color: color,
            widthPx: Number(widthPx.toFixed(2)),
            opacity: Number(opacity.toFixed(2)),
            fromCommunities: fromCommunities,
            toCommunities: toCommunities,
          };
        })
        .filter(Boolean);

      _graph3dData = { nodes: nodeElements3d, links: edgeElements3d };
      _graph3d
        .graphData(_graph3dData)
        .nodeLabel(function (node) {
          return node && node.displayLabel ? String(node.displayLabel) : '';
        })
        .nodeColor(_graph3dNodeColor)
        .linkColor(_graph3dLinkColor)
        .linkWidth(_graph3dLinkWidth)
        .linkOpacity(dense ? 0.16 : 0.30)
        .cooldownTicks(dense ? 70 : 120)
        .d3AlphaDecay(dense ? 0.14 : 0.08)
        .d3VelocityDecay(dense ? 0.52 : 0.44)
        .numDimensions(3);

      _tuneGraph3dControls(dense);

      _fitGraph3dCamera(_GRAPH_3D_CAMERA_FIT_DURATION_MS);

      _graphVisualState.nodes = nodeElements3d;
      _graphVisualState.edges = edgeElements3d;
      _graphVisualState.adjacency = adjacency;
      _setGraphFocusNodeName(_graphVisualState.focusedNodeId ? _graphNodeLabelById(_graphVisualState.focusedNodeId) : '');
      _updateGraphHighlightClasses();
      _renderGraphRenderBudget();
      return;
    }

    const hadGraph3d = !!_graph3d;
    _destroyGraph3d();
    if (hadGraph3d) {
      canvas.innerHTML = '';
    }
    _setGraphHoverNodeName('');
    _setGraphFocusNodeName(_graphVisualState.focusedNodeId ? _graphNodeLabelById(_graphVisualState.focusedNodeId) : '');

    if (typeof cytoscape === 'undefined') {
      const statusEl = document.getElementById('gx-status');
      if (statusEl) statusEl.textContent = 'Error: Cytoscape is not available in this browser session.';
      return;
    }

    if (!_graphCy || _graphCy.container() !== canvas) {
      _graphCy = cytoscape({
        container: canvas,
        elements: [],
        minZoom: _GRAPH_ZOOM_MIN,
        maxZoom: _GRAPH_ZOOM_MAX,
        wheelSensitivity: 0.2,
        hideEdgesOnViewport: true,
        textureOnViewport: true,
        motionBlur: true,
        motionBlurOpacity: 0.2,
        pixelRatio: 1,
        style: [
          {
            selector: 'node',
            style: {
              'background-color': '#0f766e',
              'border-width': 2,
              'border-color': '#ffffff',
              'label': 'data(shortLabel)',
              'font-size': 10,
              'color': '#1f2937',
              'text-halign': 'right',
              'text-valign': 'center',
              'text-margin-x': 8,
              'text-wrap': 'none',
            },
          },
          {
            selector: 'edge',
            style: {
              'curve-style': 'straight',
              'line-color': 'data(color)',
              'width': 'data(widthPx)',
              'opacity': 'data(opacity)',
            },
          },
          {
            selector: 'node.is-hover',
            style: {
              'background-color': '#0d9488',
              'label': 'data(label)',
            },
          },
          { selector: 'node.is-focus', style: { 'background-color': '#065f46', 'border-color': '#d1fae5', 'border-width': 3 } },
          { selector: 'node.is-dim', style: { 'opacity': 0.25 } },
          { selector: 'edge.is-highlight', style: { 'line-color': '#0f766e', 'opacity': 1 } },
          { selector: 'edge.is-community-dim', style: { 'opacity': 0.1 } },
          { selector: 'edge.is-dim', style: { 'opacity': 0.08 } },
        ],
      });

      _graphCy.on('tap', function (evt) {
        if (evt.target === _graphCy) {
          _graphVisualState.focusedNodeId = '';
          _setGraphFocusNodeName('');
          _updateGraphHighlightClasses();
        }
      });

      _graphCy.on('mouseover', 'node', function (evt) {
        _graphVisualState.hoveredNodeId = String(evt.target.id() || '');
        _setGraphHoverNodeName(String(evt.target.data('label') || evt.target.id() || ''));
        _updateGraphHighlightClasses();
      });

      _graphCy.on('mouseout', 'node', function (evt) {
        const nodeId = String(evt.target.id() || '');
        if (_graphVisualState.hoveredNodeId === nodeId) {
          _graphVisualState.hoveredNodeId = '';
          _setGraphHoverNodeName('');
          _updateGraphHighlightClasses();
        }
      });

      _graphCy.on('tap', 'node', function (evt) {
        const nodeId = String(evt.target.id() || '');
        _graphVisualState.focusedNodeId = (_graphVisualState.focusedNodeId === nodeId) ? '' : nodeId;
        _setGraphFocusNodeName(_graphVisualState.focusedNodeId ? _graphNodeLabelById(_graphVisualState.focusedNodeId) : '');
        _updateGraphHighlightClasses();
      });

      _graphCy.on('zoom', function () {
        _updateGraphCyLabelLod(false);
        _renderGraphRenderBudget();
      });
    }

    const points = _layoutGraphPoints(nodes, layoutMode, width, height);
    const pointById = new Map(points.map(function (point) { return [String(point.id || ''), point]; }));

    const nodeElements = nodes
      .map(function (node) {
        const nodeId = String(node.node_id || '');
        const point = pointById.get(nodeId);
        if (!point) return null;
        const attrs = node && node.attributes && typeof node.attributes === 'object' ? node.attributes : {};
        const memberships = Array.isArray(attrs.__communities) ? attrs.__communities : [];
        const communities = memberships.map(function (value) { return String(value || '').trim(); }).filter(Boolean);
        const fullLabel = String(node.label || node.node_id || '');
        const shortLimit = dense ? 18 : 28;
        const shortLabel = fullLabel.length > shortLimit ? (fullLabel.slice(0, shortLimit - 3) + '...') : fullLabel;
        return {
          data: {
            id: nodeId,
            label: fullLabel,
            shortLabel: shortLabel,
            nodeType: String(node.node_type || ''),
            communities: communities,
          },
          position: {
            x: Number(point.x || 0),
            y: Number(point.y || 0),
          },
        };
      })
      .filter(Boolean);

    const edgeElements = edges
      .map(function (edge, idx) {
        const fromId = String(edge.from_id || '');
        const toId = String(edge.to_id || '');
        const fromIdx = nodeIndex.get(fromId);
        const toIdx = nodeIndex.get(toId);
        if (fromIdx == null || toIdx == null) return null;
        adjacency.get(fromId)?.add(toId);
        adjacency.get(toId)?.add(fromId);
        const confidence = Number(edge.confidence || 0);
        const opacity = dense
          ? Math.max(0.04, Math.min(0.30, 0.04 + confidence * 0.26))
          : Math.max(0.15, Math.min(0.95, confidence || 0.15));
        const widthPx = dense
          ? Math.max(0.6, Math.min(1.8, 0.6 + confidence * 1.2))
          : Math.max(1, Math.min(4, 1 + confidence * 3));
        const edgeType = String(edge.edge_type || 'edge');
        const color = _edgeColour(edgeType);
        const fromCommunities = (nodeCommunityById.get(fromId) || []);
        const toCommunities = (nodeCommunityById.get(toId) || []);
        return {
          data: {
            id: 'e:' + String(idx) + ':' + fromId + ':' + toId + ':' + String(Math.round(confidence * 1000)) + ':' + edgeType,
            source: fromId,
            target: toId,
            edgeType: edgeType,
            confidence: confidence,
            color: color,
            widthPx: Number(widthPx.toFixed(2)),
            opacity: Number(opacity.toFixed(2)),
            fromCommunities: fromCommunities,
            toCommunities: toCommunities,
          },
        };
      })
      .filter(Boolean);

    _graphCy.elements().remove();
    _graphCy.add(nodeElements);
    _graphCy.add(edgeElements);
    _applyGraphCyPerformanceProfile(nodeElements.length, edgeElements.length);
    _graphCy.layout({ name: 'preset', fit: true, padding: 24, animate: false }).run();
    _updateGraphCyLabelLod(true);

    _graphVisualState.nodes = points;
    _graphVisualState.edges = edges;
    _graphVisualState.adjacency = adjacency;
    _graphCanvasTransform = { scale: _graphCy.zoom(), tx: _graphCy.pan().x, ty: _graphCy.pan().y };
    _setGraphFocusNodeName(_graphVisualState.focusedNodeId ? _graphNodeLabelById(_graphVisualState.focusedNodeId) : '');
    _updateGraphHighlightClasses();
  }

  /**
   * Annotate graph nodes with community information. This function computes communities for the given nodes and edges and updates the node attributes accordingly.
   * @param {Object} payload - The graph data containing nodes and edges.
   * @returns {void}
   */
  function _annotateCommunities(payload) {
    const nodes = (payload && payload.nodes) || [];
    const edges = (payload && payload.edges) || [];
    const hasCommunityLabels = nodes.some(function (node) {
      const attrs = node && node.attributes && typeof node.attributes === 'object' ? node.attributes : {};
      const memberships = Array.isArray(attrs.__communities) ? attrs.__communities : [];
      return memberships.some(function (value) { return !!String(value || '').trim(); });
    });

    if (hasCommunityLabels) {
      const communityByNode = new Map();
      nodes.forEach(function (node) {
        const attrs = node && node.attributes && typeof node.attributes === 'object' ? node.attributes : {};
        const nid = String(node.node_id || '').trim();
        const memberships = Array.isArray(attrs.__communities) ? attrs.__communities : [];
        const values = memberships.map(function (value) { return String(value || '').trim(); }).filter(Boolean);
        if (nid && values.length) {
          communityByNode.set(nid, values);
        }
      });
      _graphVisualState.communities = communityByNode;
      return;
    }

    const communities = _computeCommunities(nodes, edges);
    nodes.forEach(function (node) {
      const attrs = node.attributes || {};
      const nid = String(node.node_id || '');
      const community = communities.get(nid) || '';
      attrs.__communities = community ? [community] : [];
      node.attributes = attrs;
    });
    _graphVisualState.communities = communities;
  }

  function _normaliseSeedNodeId(rawValue) {
    const value = String(rawValue || '').trim();
    if (!value) return '';
    const first = value.split(/[\s,;]+/).find(function (part) { return !!part; }) || '';
    return String(first || '').trim();
  }

  function _resetGraphCanvasViewport() {
    _graphCanvasTransform = { scale: 1, tx: 0, ty: 0 };
    if (_graph3d) {
      _fitGraph3dCamera(_GRAPH_3D_CAMERA_FIT_DURATION_MS);
      _renderGraphRenderBudget();
      return;
    }
    if (_graphCy) {
      _graphCy.fit(undefined, 24);
      _graphCanvasTransform = { scale: _graphCy.zoom(), tx: _graphCy.pan().x, ty: _graphCy.pan().y };
      return;
    }
    _applyGraphCanvasTransform();
  }

  function _clearGraphExplorerResults() {
    _graphExplorerPayload = null;
    _graphLastFilteredMeta = null;
    _graphVisualState.nodes = [];
    _graphVisualState.edges = [];
    _graphVisualState.adjacency = new Map();
    _graphVisualState.focusedNodeId = '';
    _graphVisualState.hoveredNodeId = '';
    _graphVisualState.communities = new Map();

    const visual = document.getElementById('gx-visual');
    const summary = document.getElementById('gx-summary');
    const results = document.getElementById('gx-results');
    const nodesEl = document.getElementById('gx-nodes');
    const edgesEl = document.getElementById('gx-edges');
    const legendEl = document.getElementById('gx-edge-legend');
    const communityListEl = document.getElementById('gx-community-list');
    const communitySummaryEl = document.getElementById('gx-community-summary');
    const modeHintEl = document.getElementById('gx-render-mode-hint');
    const renderBudgetEl = document.getElementById('gx-render-budget');
    const hoverNodeEl = document.getElementById('gx-hover-node');
    const focusNodeEl = document.getElementById('gx-focus-node');
    const canvas = document.getElementById('gx-canvas');

    if (visual) visual.style.display = 'none';
    if (summary) {
      summary.style.display = 'none';
      summary.textContent = '';
    }
    if (results) results.style.display = 'none';
    if (nodesEl) nodesEl.textContent = '';
    if (edgesEl) edgesEl.textContent = '';
    if (legendEl) {
      legendEl.style.display = 'none';
      legendEl.innerHTML = '';
    }
    if (communityListEl) {
      communityListEl.style.display = 'none';
      communityListEl.innerHTML = '';
    }
    if (communitySummaryEl) {
      communitySummaryEl.style.display = 'none';
      communitySummaryEl.innerHTML = '';
    }
    if (modeHintEl) {
      modeHintEl.style.display = 'none';
      modeHintEl.textContent = '';
    }
    if (renderBudgetEl) {
      renderBudgetEl.style.display = 'none';
      renderBudgetEl.innerHTML = '';
    }
    if (hoverNodeEl) {
      hoverNodeEl.style.display = 'none';
      hoverNodeEl.textContent = '';
    }
    if (focusNodeEl) {
      focusNodeEl.style.display = 'none';
      focusNodeEl.textContent = '';
    }
    if (_graphCy) {
      _graphCy.elements().remove();
      _graphCy.resize();
    }
    if (_graph3d) {
      _graph3d.graphData({ nodes: [], links: [] });
      _graph3dData = { nodes: [], links: [] };
    }
    if (!_graphCy && !_graph3d && canvas) {
      canvas.innerHTML = '';
    }

    _resetGraphCanvasViewport();
  }

  /**
   * Get the current ask payload from the UI elements. This function collects the values from various input fields and returns an object representing the current state of the ask payload.
   * @returns {Object|null} The current ask payload or null if the question is empty.
   */
  function _currentAskPayload() {
    const question = document.getElementById('question');
    if (!question || !String(question.value || '').trim()) return null;
    const controlsFramework = document.getElementById('controls_framework');
    const controlsComparisonMode = document.getElementById('controls_comparison_mode');
    const evidenceCorporaInclude = document.getElementById('evidence_corpora_include');
    const includeGraphExpansion = document.getElementById('include_graph_expansion');
    const graphExpansionDepth = document.getElementById('graph_expansion_depth');
    const graphExpansionMaxEdges = document.getElementById('graph_expansion_max_edges');
    const selectedCorpora = evidenceCorporaInclude ? Array.from(evidenceCorporaInclude.selectedOptions || []).map(function (opt) { return opt.value; }) : [];
    return {
      question: String(question.value || '').trim(),
      retrieve_k: Number(document.getElementById('retrieve_k')?.value || 5),
      controls_context_cap: Number(document.getElementById('controls_context_cap')?.value || 4),
      temperature: Number(document.getElementById('temperature')?.value || 1),
      top_p: Number(document.getElementById('top_p')?.value || 1),
      max_completion_tokens: Number(document.getElementById('max_completion_tokens')?.value || 0) || null,
      evaluator_max_completion_tokens: Number(document.getElementById('evaluator_max_completion_tokens')?.value || 0) || null,
      controls_semantic: String(document.getElementById('controls_semantic')?.value || '0') === '1',
      controls_framework: String(controlsFramework?.value || '').trim() || null,
      controls_comparison_mode: String(controlsComparisonMode?.value || 'auto-detect'),
      include_graph_expansion: String(includeGraphExpansion?.value || '0') === '1' && _graphReady(),
      graph_expansion_depth: _graphReady() ? (Number(graphExpansionDepth?.value || 0) || null) : null,
      graph_expansion_max_edges: _graphReady() ? (Number(graphExpansionMaxEdges?.value || 0) || null) : null,
      evidence_corpora_include: selectedCorpora.length ? selectedCorpora : null,
      auth_token: _currentAuthToken(),
    };
  }

  /**
   * Load JSON data from a URL with authentication token.
   * @param {string} url - The URL to fetch JSON data from.
   * @returns {Promise<Object>} A promise that resolves to the JSON data.
   */
  function _loadJsonWithAuth(url) {
    const token = _currentAuthToken();
    const params = new URLSearchParams();
    if (token) params.set('auth_token', token);
    const finalUrl = params.toString() ? (url + '?' + params.toString()) : url;
    return fetch(finalUrl).then(function (r) { return r.json(); });
  }

  /**
   * Build the graph payload from the controls index. This function constructs the payload for building the graph based on the loaded controls index.
   * @returns {Object|null} The graph payload or null if the controls are not loaded or the graph status is unavailable.
   */
  function _buildGraphPayloadFromControlsIndex() {
    const status = _graphStatus();
    if (!_graphControlsLoaded() || !status) return null;
    return {
      auth_token: _currentAuthToken() || '',
      controls: [],
      chunks: [],
      persist_store: true,
    };
  }

  /**
   * Load the controls index for graph build. This function fetches the controls and chunks data from the server and constructs the payload for building the graph.
   * @returns {Promise<Object>} A promise that resolves to the graph build payload.
   */
  function _loadControlsIndexForGraphBuild() {
    const graphStatus = _graphStatus() || {};
    const controlsFramework = _graphExplorerValue('gx-framework-filter', '');
    const buildStatusEl = _graphExplorerElements().status;
    const progress = { controls: [], chunks: [] };
    const buildMinCommunitySize = Math.max(2, Number(_graphExplorerValue('gx-build-min-community-size', '3')) || 3);
    const controlsLimit = Math.max(1, Number(graphStatus.controls_count || 0) || 1);
    const corpusBLimit = Math.max(1, Number(graphStatus.corpus_b_count || 0) || 1);

    const controlsPromise = _loadJsonWithAuth('/api/corpus-a/list?limit=' + encodeURIComponent(String(controlsLimit))).then(function (payload) {
      const items = (payload && payload.items) || [];
      progress.controls = items;
      if (controlsFramework) {
        return items.filter(function (item) {
          const framework = String(item.framework || '').trim();
          const key = String(item.requirement_id || '').trim();
          return framework && (framework === controlsFramework || key.toLowerCase().includes(controlsFramework.toLowerCase()));
        });
      }
      return items;
    });

    const chunksPromise = _loadJsonWithAuth('/api/corpus-b/list?limit=' + encodeURIComponent(String(corpusBLimit))).then(function (payload) {
      const items = (payload && payload.items) || [];
      progress.chunks = items;
      return items;
    });

    if (buildStatusEl) buildStatusEl.textContent = JSON.stringify(_graphBuildStatusPayload('Loading controls index for graph build…'), null, 2);

    return Promise.all([controlsPromise, chunksPromise]).then(function ([controls, chunks]) {
      if (!controls.length && !chunks.length) {
        throw new Error('No loaded controls were found in Corpus A or Corpus B.');
      }
      return {
        auth_token: _currentAuthToken() || '',
        controls: controls,
        chunks: chunks,
        persist_store: true,
        min_community_size: buildMinCommunitySize,
      };
    });
  }

  /**
   * Build the graph from the loaded controls index. This function initiates the graph build process by loading the controls index and sending a request to the server to build the graph. It updates the status and handles errors during the process.
   * @returns {void}
   */
  function buildGraphFromAsk() {
    if (!_graphControlsLoaded()) {
      const els = _graphExplorerElements();
      if (els.status) els.status.textContent = 'Graph build is unavailable until controls are loaded.';
      return;
    }
    const els = _graphExplorerElements();
    if (!confirm('Build or rebuild the graph from the loaded controls index data? The previous persisted graph will be replaced on successful build.')) return;

    _clearGraphExplorerResults();
    if (els.status) els.status.textContent = JSON.stringify(_graphBuildStatusPayload('Loading Corpus A/B index data for graph build…'), null, 2);
    _loadControlsIndexForGraphBuild()
      .then(function (body) {
        if (els.status) els.status.textContent = JSON.stringify(_graphBuildStatusPayload('Building graph from loaded controls index…'), null, 2);
        return fetch('/api/graph/build', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
      })
      .then(function (r) { return r.json(); })
      .then(function (resp) {
        if (resp && resp.error) {
          if (els.status) els.status.textContent = JSON.stringify(_graphBuildStatusPayload('Graph build failed.', { error: resp.error }), null, 2);
          _renderHyperConnectedWarning(null);
          return;
        }
        const builtAt = (resp && resp.report && resp.report.built_at) || (new Date()).toISOString();
        _setGraphLastBuildAt(builtAt);
        _setGraphStatus(Object.assign({}, _graphStatus() || {}, { graph_built: true, graph_ready: true, graph_needs_rebuild: false, allow_visualisation: true, allow_ask_expansion: true, allow_build: true, reason: 'ready', controls_loaded: true, last_successful_build_at: builtAt }));
        _renderHyperConnectedWarning(resp && resp.hyper_connected_indicator ? resp.hyper_connected_indicator : null);
        if (els.status) els.status.textContent = JSON.stringify(_graphBuildStatusPayload('Graph build completed. Loading refreshed snapshot…', { response: resp }), null, 2);
        refreshGraphStatus();
        loadGraphSnapshot();
      })
      .catch(function (err) {
        if (els.status) els.status.textContent = JSON.stringify(_graphBuildStatusPayload('Graph build error.', { error: String(err) }), null, 2);
        _renderHyperConnectedWarning(null);
      });
  }

  /**
   * Apply the current graph explorer filters. This function filters the graph data based on the selected filters and updates the graph explorer view.
   * @returns {void}
   */
  function applyGraphExplorerFilters() {
    if (!_graphExplorerPayload) {
      const els = _graphExplorerElements();
      if (els.status) {
        els.status.textContent = JSON.stringify(_graphBuildStatusPayload('Load a graph snapshot or related subgraph before applying filters.'), null, 2);
      }
      return;
    }
    const filtered = _applyGraphFilters(_graphExplorerPayload);
    _renderGraphExplorer(_graphExplorerPayload, filtered);
  }

  /**
   * Jump to a specific node in the graph explorer. This function focuses on the node with the given ID and updates the graph view accordingly.
   * @returns {void}
   */
  function jumpGraphNode() {
    const targetNodeId = _graphExplorerValue('gx-jump-node', '');
    if (!targetNodeId) return;

    if (_graph3d) {
      const node = (_graph3dData.nodes || []).find(function (n) { return String(n.id || '') === targetNodeId; });
      if (!node) {
        const statusEl = document.getElementById('gx-status');
        if (statusEl) statusEl.textContent = 'Node not found in current filtered graph: ' + targetNodeId;
        return;
      }
      _graphVisualState.focusedNodeId = targetNodeId;
      _setGraphHoverNodeName(String(node.label || node.id || ''));
      _setGraphFocusNodeName(_graphNodeLabelById(targetNodeId));
      _updateGraphHighlightClasses();
      if (typeof _graph3d.cameraPosition === 'function') {
        const x = Number(node.x || 0);
        const y = Number(node.y || 0);
        const z = Number(node.z || 0);
        const norm = Math.max(1, Math.sqrt((x * x) + (y * y) + (z * z)));
        const distance = _GRAPH_3D_FOCUS_DISTANCE;
        _graph3d.cameraPosition(
          {
            x: x + (distance * x / norm),
            y: y + (distance * y / norm),
            z: z + (distance * z / norm),
          },
          { x: x, y: y, z: z },
          800
        );
      }
      _renderGraphRenderBudget();
      return;
    }

    if (_graphCy) {
      const nodeEl = _graphCy.getElementById(targetNodeId);
      if (!nodeEl || nodeEl.empty()) {
        const statusEl = document.getElementById('gx-status');
        if (statusEl) statusEl.textContent = 'Node not found in current filtered graph: ' + targetNodeId;
        return;
      }
      _graphVisualState.focusedNodeId = targetNodeId;
      _setGraphFocusNodeName(_graphNodeLabelById(targetNodeId));
      _graphCy.animate(
        {
          center: { eles: nodeEl },
          zoom: Math.min(_GRAPH_ZOOM_MAX, Math.max(_GRAPH_ZOOM_MIN, 1.4)),
        },
        { duration: 240 }
      );
      _graphCanvasTransform = { scale: _graphCy.zoom(), tx: _graphCy.pan().x, ty: _graphCy.pan().y };
      _updateGraphHighlightClasses();
      return;
    }

    const node = (_graphVisualState.nodes || []).find(function (n) { return n.id === targetNodeId; });
    if (!node) {
      const statusEl = document.getElementById('gx-status');
      if (statusEl) statusEl.textContent = 'Node not found in current filtered graph: ' + targetNodeId;
      return;
    }
    _graphVisualState.focusedNodeId = targetNodeId;
    _setGraphFocusNodeName(_graphNodeLabelById(targetNodeId));
    _graphCanvasTransform.tx = 450 - (node.x * _graphCanvasTransform.scale);
    _graphCanvasTransform.ty = 260 - (node.y * _graphCanvasTransform.scale);
    _applyGraphCanvasTransform();
    _updateGraphHighlightClasses();
  }

  function recenterGraph3dCamera() {
    if (_graph3d) {
      _fitGraph3dCamera(_GRAPH_3D_CAMERA_FIT_DURATION_MS);
      _renderGraphRenderBudget();
      return;
    }
    _resetGraphCanvasViewport();
  }

  /**
   * Reset the graph explorer filters. This function clears all filter inputs and reapplies the filters to the graph explorer.
   * @returns {void}
   */
  function resetGraphExplorerFilters() {
    ['gx-framework-filter', 'gx-node-type-filter', 'gx-edge-type-filter', 'gx-community-filter'].forEach(function (id) {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    const seedEl = document.getElementById('gx-node-id');
    if (seedEl) seedEl.value = '';
    const depthEl = document.getElementById('gx-depth');
    if (depthEl) depthEl.value = '2';
    const maxEdgesEl = document.getElementById('gx-max-edges');
    if (maxEdgesEl) maxEdgesEl.value = '200';
    const maxNodesEl = document.getElementById('gx-max-nodes-export');
    if (maxNodesEl) maxNodesEl.value = '500';
    const minCommunitySizeEl = document.getElementById('gx-build-min-community-size');
    if (minCommunitySizeEl) minCommunitySizeEl.value = '3';
    const confidenceEl = document.getElementById('gx-min-confidence');
    if (confidenceEl) confidenceEl.value = '0';
    const layoutEl = document.getElementById('gx-layout-mode');
    if (layoutEl) layoutEl.value = 'radial';
    const performanceModeEl = document.getElementById('gx-performance-mode');
    if (performanceModeEl) performanceModeEl.value = 'auto';
    const sensitivityEl = document.getElementById('gx-3d-sensitivity');
    if (sensitivityEl) sensitivityEl.value = 'standard';
    const labelThresholdEl = document.getElementById('gx-label-zoom-threshold');
    if (labelThresholdEl) labelThresholdEl.value = '1';
    const safeMaxVisibleEdgesEl = document.getElementById('gx-safe-max-visible-edges');
    if (safeMaxVisibleEdgesEl) safeMaxVisibleEdgesEl.value = String(_GRAPH_SAFE_MAX_VISIBLE_EDGES_DEFAULT);
    const isolateEl = document.getElementById('gx-community-isolate');
    if (isolateEl) isolateEl.checked = false;
    _graphVisualState.selectedCommunities = null;
    const allowLargeRenderEl = document.getElementById('gx-allow-large-render');
    if (allowLargeRenderEl) allowLargeRenderEl.checked = false;
    const jumpEl = document.getElementById('gx-jump-node');
    if (jumpEl) jumpEl.value = '';
    _resetGraphCanvasViewport();
    applyGraphExplorerFilters();
  }

  /**
   * Load the graph snapshot from the server. This function fetches the graph data in JSON format and updates the graph explorer view with the loaded data.
   * @returns {void}
   */
  function loadGraphSnapshot() {
    const token = _currentAuthToken();
    const maxNodes = _graphExplorerValue('gx-max-nodes-export', '500');
    const maxEdges = _graphExplorerValue('gx-max-edges', '200');
    const params = new URLSearchParams({ format: 'json', max_nodes: String(maxNodes), max_edges: String(maxEdges) });
    if (token) params.set('auth_token', token);
    const els = _graphExplorerElements();
    if (els.status) els.status.textContent = 'Loading graph snapshot…';
    fetch('/api/graph/export?' + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (payload) {
        if (payload && payload.error) {
          if (els.status) els.status.textContent = 'Error: ' + String(payload.error);
          return;
        }
        if (!payload || !Array.isArray(payload.nodes) || !Array.isArray(payload.edges)) {
          if (els.status) els.status.textContent = 'Error: Invalid graph snapshot payload.';
          return;
        }
        _annotateCommunities(payload);
        _graphExplorerPayload = payload;
        _refreshGraphExplorerFilterOptions(payload);
        _resetGraphCanvasViewport();
        applyGraphExplorerFilters();
      })
      .catch(function (err) {
        if (els.status) els.status.textContent = 'Error: ' + String(err);
      });
  }

  /**
   * Load the related subgraph for a specific node. This function fetches the related subgraph data from the server and updates the graph explorer view.
   * @returns {void}
   */
  function loadGraphRelated() {
    const token = _currentAuthToken();
    const rawNodeId = _graphExplorerValue('gx-node-id', '');
    const nodeId = _normaliseSeedNodeId(rawNodeId);
    const depth = _graphExplorerValue('gx-depth', '2');
    const maxEdges = _graphExplorerValue('gx-max-edges', '200');
    const els = _graphExplorerElements();
    if (!nodeId) {
      if (els.status) els.status.textContent = 'Seed node ID is required to load a related subgraph.';
      return;
    }
    const seedInput = document.getElementById('gx-node-id');
    if (seedInput) seedInput.value = nodeId;
    const params = new URLSearchParams({ node_id: nodeId, depth: String(depth), max_edges: String(maxEdges) });
    if (token) params.set('auth_token', token);
    if (els.status) els.status.textContent = 'Loading related subgraph…';
    fetch('/api/graph/related?' + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (payload) {
        if (payload && payload.error) {
          if (els.status) els.status.textContent = 'Error: ' + String(payload.error);
          return;
        }
        if (!payload || !Array.isArray(payload.nodes) || !Array.isArray(payload.edges)) {
          if (els.status) els.status.textContent = 'Error: Invalid related-subgraph payload.';
          return;
        }
        _annotateCommunities(payload);
        _graphExplorerPayload = payload;
        _refreshGraphExplorerFilterOptions(payload);
        _resetGraphCanvasViewport();
        applyGraphExplorerFilters();
        if (!payload.nodes.length && !payload.edges.length && els.status) {
          els.status.textContent = 'Related subgraph returned no nodes or edges for seed node: ' + nodeId;
        }
      })
      .catch(function (err) {
        if (els.status) els.status.textContent = 'Error: ' + String(err);
      });
  }

  /**
   * Download text content as a file. This function creates a Blob from the provided content and triggers a download with the specified filename and content type.
   * @param {string} filename - The name of the file to be downloaded.
   * @param {string} content - The text content to be saved in the file.
   * @param {string} contentType - The MIME type of the content (e.g., 'text/plain', 'application/json').
   * @returns {void}
   */
  function _downloadText(filename, content, contentType) {
    const blob = new Blob([content], { type: contentType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  /**
   * Download the compliance report in Markdown format. This function triggers the download of the compliance report as a Markdown file.
   * @returns {void}
   */
  function downloadComplianceReportMarkdown() {
    if (!_lastComplianceReport || !_lastComplianceReport.report_markdown) {
      _renderComplianceReport({ error: 'Generate a compliance report first.' });
      return;
    }
    const base = _lastComplianceReport.report_filename_base || 'compliance-report';
    _downloadText(base + '.md', _lastComplianceReport.report_markdown, 'text/markdown;charset=utf-8');
  }

  /**
   * Download the compliance report in JSON format. This function triggers the download of the compliance report as a JSON file.
   * @returns {void}
   */
  function downloadComplianceReportJson() {
    if (!_lastComplianceReport || !_lastComplianceReport.report_structured) {
      _renderComplianceReport({ error: 'Generate a compliance report first.' });
      return;
    }
    const base = _lastComplianceReport.report_filename_base || 'compliance-report';
    _downloadText(
      base + '.json',
      JSON.stringify(_lastComplianceReport.report_structured, null, 2),
      'application/json;charset=utf-8'
    );
  }

  /**
   * Download the compliance findings in CSV format. This function triggers the download of the compliance findings as a CSV file.
   * @returns {void}
   */
  function downloadComplianceFindingsCsv() {
    if (!_lastComplianceReport || !_lastComplianceReport.report_findings_csv) {
      _renderComplianceReport({ error: 'Generate a compliance report first.' });
      return;
    }
    const base = _lastComplianceReport.report_filename_base || 'compliance-report';
    _downloadText(base + '-findings.csv', _lastComplianceReport.report_findings_csv, 'text/csv;charset=utf-8');
  }

  /**
   * Download the Azure compliance report in Markdown format. This function triggers the download of the Azure compliance report as a Markdown file.
   * @returns {void}
   */
  function downloadAzureComplianceReportMarkdown() {
    if (!_lastAzureComplianceReport || !_lastAzureComplianceReport.report_markdown) {
      _renderAzureComplianceReport({ error: 'Generate an Azure compliance report first.' });
      return;
    }
    const base = _lastAzureComplianceReport.report_filename_base || 'azure-compliance-report';
    _downloadText(base + '.md', _lastAzureComplianceReport.report_markdown, 'text/markdown;charset=utf-8');
  }

  /**
   * Download the Azure compliance report in JSON format. This function triggers the download of the Azure compliance report as a JSON file.
   * @returns {void}
   */
  function downloadAzureComplianceReportJson() {
    if (!_lastAzureComplianceReport || !_lastAzureComplianceReport.report_structured) {
      _renderAzureComplianceReport({ error: 'Generate an Azure compliance report first.' });
      return;
    }
    const base = _lastAzureComplianceReport.report_filename_base || 'azure-compliance-report';
    _downloadText(
      base + '.json',
      JSON.stringify(_lastAzureComplianceReport.report_structured, null, 2),
      'application/json;charset=utf-8'
    );
  }

  /**
   * Download the Azure compliance findings in CSV format. This function triggers the download of the Azure compliance findings as a CSV file.
   * @returns
   */
  function downloadAzureComplianceFindingsCsv() {
    if (!_lastAzureComplianceReport || !_lastAzureComplianceReport.report_findings_csv) {
      _renderAzureComplianceReport({ error: 'Generate an Azure compliance report first.' });
      return;
    }
    const base = _lastAzureComplianceReport.report_filename_base || 'azure-compliance-report';
    _downloadText(base + '-findings.csv', _lastAzureComplianceReport.report_findings_csv, 'text/csv;charset=utf-8');
  }

  /**
   * Download the AWS compliance report in Markdown format. This function triggers the download of the AWS compliance report as a Markdown file.
   * @returns {void}
   */
  function downloadAwsComplianceReportMarkdown() {
    if (!_lastAwsComplianceReport || !_lastAwsComplianceReport.report_markdown) {
      _renderAwsComplianceReport({ error: 'Generate an AWS compliance report first.' });
      return;
    }
    const base = _lastAwsComplianceReport.report_filename_base || 'aws-compliance-report';
    _downloadText(base + '.md', _lastAwsComplianceReport.report_markdown, 'text/markdown;charset=utf-8');
  }

  /**
   * Download the AWS compliance report in JSON format. This function triggers the download of the AWS compliance report as a JSON file.
   * @returns {void}
   */
  function downloadAwsComplianceReportJson() {
    if (!_lastAwsComplianceReport || !_lastAwsComplianceReport.report_structured) {
      _renderAwsComplianceReport({ error: 'Generate an AWS compliance report first.' });
      return;
    }
    const base = _lastAwsComplianceReport.report_filename_base || 'aws-compliance-report';
    _downloadText(
      base + '.json',
      JSON.stringify(_lastAwsComplianceReport.report_structured, null, 2),
      'application/json;charset=utf-8'
    );
  }

  /**
   * Download the AWS compliance findings in CSV format. This function triggers the download of the AWS compliance findings as a CSV file.
   * @returns {void}
   */
  function downloadAwsComplianceFindingsCsv() {
    if (!_lastAwsComplianceReport || !_lastAwsComplianceReport.report_findings_csv) {
      _renderAwsComplianceReport({ error: 'Generate an AWS compliance report first.' });
      return;
    }
    const base = _lastAwsComplianceReport.report_filename_base || 'aws-compliance-report';
    _downloadText(base + '-findings.csv', _lastAwsComplianceReport.report_findings_csv, 'text/csv;charset=utf-8');
  }

  /**
   * Refresh the status of Corpus A. This function fetches the current status of Corpus A and updates the UI accordingly.
   * @returns {void}
   */
  function refreshCorpusAStatus() {
    const token = _currentAuthToken();
    const qs = token ? ('?auth_token=' + encodeURIComponent(token)) : '';
    fetch('/api/corpus-a/status' + qs)
      .then(r => r.json())
      .then(data => _renderCorpusAStatus(data))
      .catch(err => _renderCorpusAStatus({ error: String(err) }));
  }

  /**
   * List the indexed items in Corpus A. This function fetches the list of indexed items in Corpus A and updates the UI accordingly.
   * @returns {void}
   */
  function listCorpusAIndexed() {
    const token = _currentAuthToken();
    const frameworks = _selectedCorpusAFrameworks();
    const params = new URLSearchParams();
    if (token) params.set('auth_token', token);
    params.set('limit', '100');

    const hasSingleFramework = frameworks.length === 1 && frameworks[0] !== 'all';
    if (hasSingleFramework) {
      params.set('framework', frameworks[0]);
    }

    const qs = params.toString();
    fetch('/api/corpus-a/list' + (qs ? ('?' + qs) : ''))
      .then(r => r.json())
      .then(data => _renderCorpusAStatus(data))
      .catch(err => _renderCorpusAStatus({ error: String(err) }));
  }

  /**
   * Check the diagnostics of the ingestion job. This function fetches the diagnostics information for the ingestion job and updates the UI accordingly.
   * @returns {void}
   */
  function checkIngestionJobDiagnostics() {
    const token = _currentAuthToken();
    const qs = token ? ('?auth_token=' + encodeURIComponent(token)) : '';
    fetch('/api/ingestion-job/diagnostics' + qs)
      .then(r => r.json())
      .then(data => {
        let html = '<div class="answer"><pre>' + JSON.stringify(data, null, 2) + '</pre></div>';
        if (!data.configured) {
          html = '<div class="answer"><strong>Job Trigger Not Configured</strong><p>' + data.message + '</p></div>';
        } else if (data.recent_executions && data.recent_executions.length === 0) {
          html = '<div class="answer"><strong>No Job Executions Yet</strong><p>' + data.message + '</p></div>';
        }
        const target = document.getElementById('ca-status');
        target.classList.add('markdown');
        target.innerHTML = html;
      })
      .catch(err => _renderCorpusAStatus({ error: String(err) }));
  }

  /**
   * Trigger the ingestion process for Corpus A. This function sends a request to start the ingestion process for Corpus A with the specified options.
   * @returns {void}
   */
  function triggerCorpusAIngest() {
    const token = _currentAuthToken();
    const frameworks = _selectedCorpusAFrameworks();
    const body = {
      frameworks: frameworks,
      replace_existing: document.getElementById('ca-replace-existing').checked,
      dry_run: document.getElementById('ca-dry-run').checked,
      no_guidance: document.getElementById('ca-no-guidance').checked,
      auth_token: token,
    };

    fetch('/api/corpus-a/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(r => r.json())
      .then(data => _renderCorpusAStatus(data))
      .catch(err => _renderCorpusAStatus({ error: String(err) }));
  }

  /**
   * Upload reference documents to Corpus A. This function handles the file upload process for reference documents, including setting options and triggering the ingestion job if requested.
   * @returns {void}
   */
  function uploadCorpusAReferenceDocs() {
    const input = document.getElementById('ca-upload-files');
    const framework = (document.getElementById('ca-upload-framework').value || '').trim();
    if (!input.files || !input.files.length) {
      _renderCorpusAStatus({ error: 'Select the required CIS or PCI source documents first.' });
      return;
    }

    const fd = new FormData();
    for (const file of input.files) {
      fd.append('files', file);
    }
    fd.append('framework', framework);
    fd.append('trigger_job', document.getElementById('ca-upload-trigger-job').checked ? 'true' : 'false');
    fd.append('replace_existing', document.getElementById('ca-replace-existing').checked ? 'true' : 'false');
    fd.append('dry_run', document.getElementById('ca-dry-run').checked ? 'true' : 'false');
    fd.append('no_guidance', document.getElementById('ca-no-guidance').checked ? 'true' : 'false');
    fd.append('auth_token', _currentAuthToken());

    fetch('/api/corpus-a/upload', {
      method: 'POST',
      body: fd,
    })
      .then(r => r.json())
      .then(data => {
        _renderCorpusAStatus(data);
        input.value = '';
        if (data && data.triggered_job && !data.error) {
          const executionName = (data.job && data.job.execution_name) ||
            (data.jobs && data.jobs.length > 0 && data.jobs[0].job && data.jobs[0].job.execution_name) || null;
          const statusEl = document.getElementById('ca-status');
          statusEl.textContent += '\n\n[Job triggered' + (executionName ? ' (' + executionName + ')' : '') + '. Polling job status every 15s, up to 120 checks (~30 min)...]';
          _pollCorpusAIndexStatus(120, 0, executionName, Date.now());
        }
      })
      .catch(err => _renderCorpusAStatus({ error: String(err) }));
  }

  /**
   * Format the elapsed duration since the given start time in milliseconds.
   * @param {number} startedAtMs - The start time in milliseconds.
   * @returns {string} The formatted elapsed duration in "Xm YYs" format.
   */
  function _formatElapsedDuration(startedAtMs) {
    const elapsedMs = Math.max(0, Date.now() - startedAtMs);
    const totalSeconds = Math.floor(elapsedMs / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return minutes + 'm ' + String(seconds).padStart(2, '0') + 's';
  }

  /**
   * Poll the status of the Corpus A index. This function checks the status of the ingestion job and updates the UI accordingly. It continues polling until the job is finished or the maximum number of polls is reached.
   * @param {number} maxPollIntervals - The maximum number of polling intervals to check.
   * @param {number} pollCount - The current count of polling intervals that have been checked.
   * @param {string|null} executionName - The name of the execution to check, if available.
   * @param {number|null} pollStartedAtMs - The timestamp when polling started, in milliseconds.
   * @returns {void}
   */
  function _pollCorpusAIndexStatus(maxPollIntervals, pollCount, executionName, pollStartedAtMs) {
    if (pollCount >= maxPollIntervals) {
      const statusEl = document.getElementById('ca-status');
      const elapsed = _formatElapsedDuration(pollStartedAtMs || Date.now());
      statusEl.textContent += '\n\n[Polling stopped after ' + maxPollIntervals + ' checks (elapsed ' + elapsed + '). Use "Job Diagnostics" or check Azure Portal for final status.]';
      return;
    }
    setTimeout(() => {
      const token = _currentAuthToken();
      const qs = token ? ('?auth_token=' + encodeURIComponent(token)) : '';
      fetch('/api/ingestion-job/diagnostics' + qs)
        .then(r => r.json())
        .then(diagData => {
          const statusEl = document.getElementById('ca-status');
          const now = new Date().toLocaleTimeString();
          let exec = null;
          if (executionName && diagData.recent_executions) {
            exec = diagData.recent_executions.find(
              e => e.id && (e.id.endsWith('/' + executionName) || e.id === executionName)
            );
          }
          if (!exec && diagData.recent_executions && diagData.recent_executions.length > 0) {
            exec = diagData.recent_executions[0];
          }
          if (exec) {
            const st = exec.status || 'Unknown';
            const execShortName = (exec.id || '').split('/').pop();
            const elapsed = _formatElapsedDuration(pollStartedAtMs || Date.now());
            statusEl.textContent = (
              `[Poll ${pollCount + 1}/${maxPollIntervals} at ${now}; elapsed ${elapsed}]\n` +
              `Execution: ${execShortName}\n` +
              `Status: ${st}\n` +
              `Started: ${exec.startTime || 'unknown'}\n` +
              (exec.endTime ? `Ended: ${exec.endTime}\n` : '') +
              JSON.stringify(exec.detailedStatus || {}, null, 2)
            );
            if (st === 'Succeeded' || st === 'Failed') {
              statusEl.textContent += '\n\n[Job finished. Fetching index status...]';
              fetch('/api/corpus-a/status' + qs)
                .then(r => r.json())
                .then(idxData => { statusEl.textContent += '\n' + JSON.stringify(idxData, null, 2); })
                .catch(() => {});
              return;
            }
          } else {
            const elapsed = _formatElapsedDuration(pollStartedAtMs || Date.now());
            statusEl.textContent = `[Poll ${pollCount + 1}/${maxPollIntervals} at ${now}; elapsed ${elapsed}] Waiting for execution to appear...\n${JSON.stringify(diagData, null, 2)}`;
          }
          _pollCorpusAIndexStatus(maxPollIntervals, pollCount + 1, executionName, pollStartedAtMs);
        })
        .catch(() => _pollCorpusAIndexStatus(maxPollIntervals, pollCount + 1, executionName, pollStartedAtMs));
    }, 15000);
  }

  /**
   * Clear the selected controls from Corpus A. This function sends a request to clear the selected controls from the controls index, with an option for a dry run.
   * @returns {void}
   */
  function clearCorpusA() {
    const body = {
      frameworks: _selectedCorpusAFrameworks(),
      dry_run: document.getElementById('ca-clear-dry-run').checked,
      auth_token: _currentAuthToken(),
    };
    if (!confirm((body.dry_run ? 'Preview' : 'Clear') + ' selected Corpus A controls from the controls index?')) return;
    fetch('/api/corpus-a/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(r => r.json())
      .then(data => _renderCorpusAStatus(data))
      .catch(err => _renderCorpusAStatus({ error: String(err) }));
  }

  /**
   * Clear the selected controls from Corpus B. This function sends a request to clear the selected controls from the grounding index, with options for a dry run and blob deletion.
   * @returns {void}
   */
  function clearCorpusB() {
    _cancelCorpusBListRetry();
    const body = {
      dry_run: document.getElementById('cb-clear-dry-run').checked,
      clear_blobs: document.getElementById('cb-clear-blobs').checked,
      auth_token: _currentAuthToken(),
    };
    if (!confirm((body.dry_run ? 'Preview' : 'Clear') + ' Corpus B data from grounding index' + (body.clear_blobs ? ' and delete blobs' : '') + '?')) return;
    fetch('/api/corpus-b/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(r => r.json())
      .then(data => {
        _renderCorpusBStatus(data);
        _refreshComplianceBatchOptions();
      })
      .catch(err => _renderCorpusBStatus({ error: String(err) }));
  }

  /**
   * List the indexed items in Corpus B. This function fetches the list of indexed items in Corpus B and updates the UI accordingly.
   * @param {number} [retryAttempt] - The current retry attempt count.
   * @returns {void}
   */
  function listCorpusBIndexed(retryAttempt) {
    const attempt = Number.isFinite(retryAttempt) ? retryAttempt : 0;
    if (attempt === 0) {
      _cancelCorpusBListRetry();
      _renderCorpusBStatus({
        mode: 'corpus-b-list',
        status: 'Loading Corpus B indexed view...',
        note: 'Indexing can take a few minutes after ingestion. We will auto-refresh briefly if no chunks are visible yet.',
      });
    }

    const token = _currentAuthToken();
    const params = new URLSearchParams();
    if (token) params.set('auth_token', token);
    params.set('limit', '100');

    const batch = (document.getElementById('cr-b-upload-batch').value || '').trim();
    if (batch) params.set('upload_batch', batch);

    const qs = params.toString();
    fetch('/api/corpus-b/list' + (qs ? ('?' + qs) : ''))
      .then(r => r.json())
      .then(data => {
        _cancelCorpusBListRetry();
        _renderCorpusBStatus(data);
        _refreshComplianceBatchOptions();

        const total = data && typeof data.total_count === 'number' ? data.total_count : null;
        const batchSuffix = batch ? (' for upload_batch=' + batch) : '';
        if (total === 0 && attempt < _CORPUS_B_LIST_MAX_RETRIES) {
          const nextAttempt = attempt + 1;
          _appendCorpusBStatusNote(
            '[No Corpus B indexed chunks visible' + batchSuffix +
            ' yet. This can happen for several minutes after ingestion. Auto-refresh ' +
            nextAttempt + '/' + _CORPUS_B_LIST_MAX_RETRIES + ' in ' +
            (_CORPUS_B_LIST_RETRY_MS / 1000) + 's...]'
          );
          _corpusBListRetryTimeout = setTimeout(function () {
            listCorpusBIndexed(nextAttempt);
          }, _CORPUS_B_LIST_RETRY_MS);
        } else if (total === 0) {
          _appendCorpusBStatusNote(
            '[Corpus B still shows 0 indexed chunks' + batchSuffix +
            ' after auto-refresh attempts. Wait a little longer and click "View Corpus B Indexed" again.]'
          );
        } else if (total !== null) {
          _appendCorpusBStatusNote(
            '[Corpus B indexed chunks available: ' + total + '.]'
          );
        }
      })
      .catch(err => {
        _cancelCorpusBListRetry();
        _renderCorpusBStatus({ error: String(err) });
        if (attempt < _CORPUS_B_LIST_MAX_RETRIES) {
          const nextAttempt = attempt + 1;
          _appendCorpusBStatusNote(
            '[Retrying Corpus B indexed view ' + nextAttempt + '/' + _CORPUS_B_LIST_MAX_RETRIES +
            ' in ' + (_CORPUS_B_LIST_RETRY_MS / 1000) + 's after error.]'
          );
          _corpusBListRetryTimeout = setTimeout(function () {
            listCorpusBIndexed(nextAttempt);
          }, _CORPUS_B_LIST_RETRY_MS);
        }
      });
  }

  /**
   * Upload reference documents to Corpus B. This function handles the file upload process for reference documents, including setting options and triggering the ingestion job if requested.
   * @returns {void}
   */
  function uploadCorpusBIngest() {
    _cancelCorpusBListRetry();
    const input = document.getElementById('cb-files');
    if (!input.files || !input.files.length) {
      _renderCorpusBStatus({ error: 'Select at least one file.' });
      return;
    }

    const fd = new FormData();
    for (const file of input.files) {
      fd.append('files', file);
    }
    fd.append('trigger_job', document.getElementById('cb-trigger-job').checked ? 'true' : 'false');
    fd.append('reindex_on_dedupe', document.getElementById('cb-reindex-on-dedupe').checked ? 'true' : 'false');
    fd.append('auth_token', _currentAuthToken());

    fetch('/api/corpus-b/ingest', {
      method: 'POST',
      body: fd,
    })
      .then(r => r.json())
      .then(data => {
        _renderCorpusBStatus(data);
        _refreshComplianceBatchOptions();
        input.value = '';
        if (data && data.triggered_job && !data.error) {
          const executionName = _extractExecutionName(data);
          const statusEl = document.getElementById('cb-status');
          statusEl.textContent += '\n\n[Job triggered' + (executionName ? ' (' + executionName + ')' : '') + '. Polling this execution only every 5s, up to 360 checks (~30 min)...]';
          _pollCorpusBIndexStatus(360, 0, executionName, Date.now());
        }
      })
      .catch(err => _renderCorpusBStatus({ error: String(err) }));
  }

  /**
   * Poll the status of the list and job. This function checks the status of the list and job and updates the UI accordingly. It continues polling until the job is finished or the maximum number of polls is reached.
   * @param {string} listEndpoint - The endpoint to fetch the list of items.
   * @param {string} statusElementId - The ID of the element to update with the status.
   * @param {string} batchFieldId - The ID of the batch field element.
   * @param {number} maxPollIntervals - The maximum number of polling intervals to check.
   * @param {number} pollCount - The current count of polling intervals that have been checked.
   * @param {string|null} executionName - The name of the execution to check, if available.
   * @param {number|null} pollStartedAtMs - The timestamp when polling started, in milliseconds.
   * @returns {void}
   */
  function _pollCorpusListAndJobStatus(listEndpoint, statusElementId, batchFieldId, maxPollIntervals, pollCount, executionName, pollStartedAtMs) {
    if (pollCount >= maxPollIntervals) {
      const statusEl = document.getElementById(statusElementId);
      if (statusEl) {
        const elapsed = _formatElapsedDuration(pollStartedAtMs || Date.now());
        statusEl.textContent += '\n\n[Polling stopped after ' + maxPollIntervals + ' checks (elapsed ' + elapsed + '). Use Job Diagnostics or Azure Portal for final status.]';
      }
      return;
    }
    setTimeout(() => {
      const token = _currentAuthToken();
      const params = new URLSearchParams();
      if (token) params.set('auth_token', token);
      params.set('limit', '100');
      const batchField = document.getElementById(batchFieldId);
      const batch = batchField ? (batchField.value || '').trim() : '';
      if (batch) params.set('upload_batch', batch);
      const qs = params.toString();
      const diagQs = token ? ('?auth_token=' + encodeURIComponent(token)) : '';
      Promise.all([
        fetch(listEndpoint + (qs ? ('?' + qs) : '')).then(r => r.json()),
        fetch('/api/ingestion-job/diagnostics' + diagQs).then(r => r.json()),
      ])
        .then(([data, diagData]) => {
          const statusEl = document.getElementById(statusElementId);
          const total = data && typeof data.total_count === 'number' ? data.total_count : 'unknown';
          const overall = data && typeof data.overall_total_count === 'number' ? data.overall_total_count : null;
          const returned = data && typeof data.returned_count === 'number' ? data.returned_count : 'unknown';
          const exec = _findExecution(diagData, executionName);
          const execShortName = exec && exec.id ? exec.id.split('/').pop() : executionName;
          const jobStatus = exec ? (exec.status || 'unknown') : 'pending lookup';
          const elapsed = _formatElapsedDuration(pollStartedAtMs || Date.now());
          const countLabel = overall === null
            ? `Total count: ${total}; Returned: ${returned}`
            : `Filtered count: ${total}; Overall count: ${overall}; Returned: ${returned}`;
          const pollMsg = `\n[${countLabel}; refreshed at ${new Date().toLocaleTimeString()}; elapsed ${elapsed}] [Ingestion job: ${execShortName || 'unknown'} status=${jobStatus}]\n`;
          statusEl.textContent = pollMsg + JSON.stringify(data, null, 2);
          if (exec && (exec.status === 'Succeeded' || exec.status === 'Failed')) return;
          _pollCorpusListAndJobStatus(listEndpoint, statusElementId, batchFieldId, maxPollIntervals, pollCount + 1, executionName, pollStartedAtMs);
        })
        .catch(() => _pollCorpusListAndJobStatus(listEndpoint, statusElementId, batchFieldId, maxPollIntervals, pollCount + 1, executionName, pollStartedAtMs));
    }, 5000);
  }

  /**
   * Poll the status of the Corpus B index. This function checks the status of the ingestion job and updates the UI accordingly. It continues polling until the job is finished or the maximum number of polls is reached.
   * @param {number} maxPollIntervals - The maximum number of polling intervals to check.
   * @param {number} pollCount - The current count of polling intervals that have been checked.
   * @param {string|null} executionName - The name of the execution to check, if available.
   * @param {number|null} pollStartedAtMs - The timestamp when polling started, in milliseconds.
   * @returns {void}
   */
  function _pollCorpusBIndexStatus(maxPollIntervals, pollCount, executionName, pollStartedAtMs) {
    _pollCorpusListAndJobStatus('/api/corpus-b/list', 'cb-status', 'cr-b-upload-batch', maxPollIntervals, pollCount, executionName, pollStartedAtMs);
  }

  /**
   * Upload reference documents to Corpus C. This function handles the file upload process for reference documents, including setting options and triggering the ingestion job if requested.
   * @returns {void}
   */
  function uploadCorpusCIngest() {
    _cancelCorpusCListRetry();
    const input = document.getElementById('cc-files');
    if (!input.files || !input.files.length) {
      _renderCorpusCStatus({ error: 'Select at least one file.' });
      return;
    }

    const fd = new FormData();
    for (const file of input.files) {
      fd.append('files', file);
    }
    fd.append('trigger_job', document.getElementById('cc-trigger-job').checked ? 'true' : 'false');
    fd.append('reindex_on_dedupe', document.getElementById('cc-reindex-on-dedupe').checked ? 'true' : 'false');
    fd.append('auth_token', _currentAuthToken());

    fetch('/api/corpus-c/ingest', {
      method: 'POST',
      body: fd,
    })
      .then(r => r.json())
      .then(data => {
        _renderCorpusCStatus(data);
        _refreshComplianceBatchOptions();
        input.value = '';
        if (data && data.triggered_job && !data.error) {
          const executionName = _extractExecutionName(data);
          const statusEl = document.getElementById('cc-status');
          statusEl.textContent += '\n\n[Job triggered' + (executionName ? ' (' + executionName + ')' : '') + '. Polling this execution only every 5s, up to 360 checks (~30 min)...]';
          _pollCorpusCIndexStatus(360, 0, executionName, Date.now());
        }
      })
      .catch(err => _renderCorpusCStatus({ error: String(err) }));
  }

  /**
   * Poll the status of the Corpus C index. This function checks the status of the ingestion job and updates the UI accordingly. It continues polling until the job is finished or the maximum number of polls is reached.
   * @param {number} maxPollIntervals - The maximum number of polling intervals to check.
   * @param {number} pollCount - The current count of polling intervals that have been checked.
   * @param {string|null} executionName - The name of the execution to check, if available.
   * @param {number|null} pollStartedAtMs - The timestamp when polling started, in milliseconds.
   * @returns {void}
   */
  function _pollCorpusCIndexStatus(maxPollIntervals, pollCount, executionName, pollStartedAtMs) {
    _pollCorpusListAndJobStatus('/api/corpus-c/list', 'cc-status', 'cr-upload-batch', maxPollIntervals, pollCount, executionName, pollStartedAtMs);
  }

  /**
   * Clear the selected controls from Corpus C. This function sends a request to clear the selected controls from the grounding index, with options for a dry run and blob deletion.
   * @returns {void}
   */
  function clearCorpusC() {
    _cancelCorpusCListRetry();
    const body = {
      dry_run: document.getElementById('cc-clear-dry-run').checked,
      clear_blobs: document.getElementById('cc-clear-blobs').checked,
      auth_token: _currentAuthToken(),
    };
    if (!confirm((body.dry_run ? 'Preview' : 'Clear') + ' Corpus C data from grounding index' + (body.clear_blobs ? ' and delete blobs' : '') + '?')) return;
    fetch('/api/corpus-c/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(r => r.json())
      .then(data => {
        _renderCorpusCStatus(data);
        _refreshComplianceBatchOptions();
      })
      .catch(err => _renderCorpusCStatus({ error: String(err) }));
  }

  /**
   * List the indexed chunks in Corpus C. This function fetches the list of indexed chunks and updates the UI accordingly, with retry logic for cases where no chunks are visible yet.
   * @param {number} retryAttempt - The current retry attempt count.
   * @returns {void}
   */
  function listCorpusCIndexed(retryAttempt) {
    const attempt = Number.isFinite(retryAttempt) ? retryAttempt : 0;
    if (attempt === 0) {
      _cancelCorpusCListRetry();
      _renderCorpusCStatus({
        mode: 'corpus-c-list',
        status: 'Loading Corpus C indexed view...',
        note: 'Indexing can take a few minutes after ingestion. We will auto-refresh briefly if no chunks are visible yet.',
      });
    }

    const token = _currentAuthToken();
    const params = new URLSearchParams();
    if (token) params.set('auth_token', token);
    params.set('limit', '100');

    const batch = (document.getElementById('cr-upload-batch').value || '').trim();
    if (batch) params.set('upload_batch', batch);

    const qs = params.toString();
    fetch('/api/corpus-c/list' + (qs ? ('?' + qs) : ''))
      .then(r => r.json())
      .then(data => {
        _cancelCorpusCListRetry();
        _renderCorpusCStatus(data);
        _refreshComplianceBatchOptions();

        const total = data && typeof data.total_count === 'number' ? data.total_count : null;
        const batchSuffix = batch ? (' for upload_batch=' + batch) : '';
        if (total === 0 && attempt < _CORPUS_C_LIST_MAX_RETRIES) {
          const nextAttempt = attempt + 1;
          _appendCorpusCStatusNote(
            '[No Corpus C indexed chunks visible' + batchSuffix +
            ' yet. This can happen for several minutes after ingestion. Auto-refresh ' +
            nextAttempt + '/' + _CORPUS_C_LIST_MAX_RETRIES + ' in ' +
            (_CORPUS_C_LIST_RETRY_MS / 1000) + 's...]'
          );
          _corpusCListRetryTimeout = setTimeout(function () {
            listCorpusCIndexed(nextAttempt);
          }, _CORPUS_C_LIST_RETRY_MS);
        } else if (total === 0) {
          _appendCorpusCStatusNote(
            '[Corpus C still shows 0 indexed chunks' + batchSuffix +
            ' after auto-refresh attempts. Wait a little longer and click "View Corpus C Indexed" again.]'
          );
        } else if (total !== null) {
          _appendCorpusCStatusNote(
            '[Corpus C indexed chunks available: ' + total + '.]'
          );
        }
      })
      .catch(err => {
        _cancelCorpusCListRetry();
        _renderCorpusCStatus({ error: String(err) });
        if (attempt < _CORPUS_C_LIST_MAX_RETRIES) {
          const nextAttempt = attempt + 1;
          _appendCorpusCStatusNote(
            '[Retrying Corpus C indexed view ' + nextAttempt + '/' + _CORPUS_C_LIST_MAX_RETRIES +
            ' in ' + (_CORPUS_C_LIST_RETRY_MS / 1000) + 's after error.]'
          );
          _corpusCListRetryTimeout = setTimeout(function () {
            listCorpusCIndexed(nextAttempt);
          }, _CORPUS_C_LIST_RETRY_MS);
        }
      });
  }

  /**
   * Poll the status of a compliance job. This function checks the status of the compliance job and updates the UI accordingly. It continues polling until the job is finished or an error occurs.
   * @param {string} jobId - The ID of the compliance job to poll.
   * @param {string} mode - The mode of the compliance report ('azure', 'aws', or default).
   * @returns {void}
   */
  function _pollComplianceJob(jobId, mode) {
    const token = _currentAuthToken();
    const params = new URLSearchParams();
    if (token) params.set('auth_token', token);
    const qs = params.toString();
    const endpoint = '/api/compliance/report/jobs/' + encodeURIComponent(jobId) + (qs ? ('?' + qs) : '');

    const render = mode === 'azure'
      ? _renderAzureComplianceReport
      : (mode === 'aws' ? _renderAwsComplianceReport : _renderComplianceReport);
    const done = render;

    const tick = function () {
      fetch(endpoint)
        .then(r => r.json())
        .then(data => {
          if (data.error) {
            render(data);
            return;
          }

          if (data.state === 'completed') {
            done(data.result || { error: 'Job completed without result payload.' });
            return;
          }
          if (data.state === 'failed') {
            render({ error: data.error || 'Assessment job failed.' });
            return;
          }

          const total = Number(data.total_controls || 0);
          const completed = Number(data.completed_controls || 0);
          const current = data.current_requirement_id ? (' [' + data.current_requirement_id + ']') : '';
          const progressText = total > 0 ? (completed + '/' + total) : (completed > 0 ? String(completed) : '...');
          render({
            status: (data.message || 'Processing') + ' (' + progressText + ')' + current,
            job_id: data.job_id,
            state: data.state,
            completed_controls: completed,
            total_controls: total,
            current_requirement_id: data.current_requirement_id || '',
          });
          setTimeout(tick, 1200);
        })
        .catch(err => render({ error: String(err) }));
    };

    tick();
  }

  /**
   * Generate a compliance report based on the provided question and parameters. This function sends a request to start the compliance report generation process and handles the response, including polling for job status.
   * @returns {void}
   */
  function generateComplianceReport() {
    const question = document.getElementById('cr-question').value.trim();
    const framework = document.getElementById('cr-controls-framework').value || '';
    const strategyEl = document.getElementById('cr-assessment-strategy');
    const strategy = strategyEl ? strategyEl.value : (framework ? 'per_control' : 'single_pass');

    const generateBtn = document.getElementById('cr-generate-btn');
    if (generateBtn && generateBtn.disabled) {
      _renderComplianceReport({
        error: 'Compliance Report is unavailable because there are no Corpus C documents to assess.',
      });
      return;
    }

    const body = {
      question: question,
      retrieve_k: Number(document.getElementById('cr-retrieve-k').value || 5),
      controls_top_k: Math.min(
        Number(document.getElementById('cr-controls-top-k').value || 4),
        document.getElementById('cr-controls-framework').value ? 1500 : 10
      ),
      temperature: Number(document.getElementById('cr-temperature').value || 1),
      top_p: Number(document.getElementById('cr-top-p').value || 1),
      thinking_mode: document.getElementById('cr-thinking-mode').value || 'balanced',
      controls_framework: framework || null,
      assessment_strategy: strategy,
      include_corpus_b: document.getElementById('cr-include-corpus-b').checked,
    max_completion_tokens: (function() {
      const val = document.getElementById('cr-max-completion-tokens').value.trim();
      return val ? Number(val) : null;
    })(),
           evaluator_max_completion_tokens: (function() {
             const val = document.getElementById('cr-evaluator-max-completion-tokens').value.trim();
             return val ? Number(val) : null;
           })(),
      validation_mode: document.getElementById('cr-validation-mode').value || 'hard',
      auth_token: _currentAuthToken(),
    };

    _renderComplianceReport({ status: 'Starting per-control report job...' });
    fetch('/api/compliance/report/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          _renderComplianceReport(data);
          return;
        }
        if (!data.job_id) {
          _renderComplianceReport({ error: 'No job_id returned from compliance job start.' });
          return;
        }
        _pollComplianceJob(data.job_id, 'compliance');
      })
      .catch(err => _renderComplianceReport({ error: String(err) }));
  }

  function refreshComplianceReportAvailability() {
    const questionEl = document.getElementById('cr-question');
    const frameworkEl = document.getElementById('cr-controls-framework');
    const button = document.getElementById('cr-generate-btn');
    if (!questionEl || !frameworkEl || !button) return;
    const requiresQuestion = !frameworkEl.value;
    const hasQuestion = questionEl.value.trim().length > 0;
    button.disabled = requiresQuestion && !hasQuestion;
    button.title = button.disabled
      ? 'Enter an assessment question when Controls framework is Auto.'
      : 'Start a compliance assessment for the selected controls and evidence.';
    const strategyEl = document.getElementById('cr-assessment-strategy');
    if (strategyEl && !strategyEl.dataset.userChanged) {
      strategyEl.value = frameworkEl.value ? 'per_control' : 'single_pass';
    }
  }

  /**
   * Generate an Azure compliance report based on the provided subscription ID, resource group, and resource IDs. This function sends a request to start the Azure compliance report generation process and handles the response, including polling for job status.
   * @returns {void}
   */
  function generateAzureComplianceReport() {
    const subscriptionId = document.getElementById('az-subscription-id').value.trim();
    const resourceGroup = document.getElementById('az-resource-group').value.trim();
    const resourceIds = document.getElementById('az-resource-ids').value
      .split('\n')
      .map(v => v.trim())
      .filter(Boolean);

    if (!subscriptionId) {
      _renderAzureComplianceReport({ error: 'Subscription ID is required.' });
      return;
    }
    if (!resourceGroup && !resourceIds.length) {
      _renderAzureComplianceReport({ error: 'Resource Group is required when no Resource IDs are supplied.' });
      return;
    }

    const body = {
      subscription_id: subscriptionId,
      resource_group: resourceGroup,
      resource_ids: resourceIds,
      controls_framework: document.getElementById('az-controls-framework').value || 'NIST CSF',
      controls_top_k: Number(document.getElementById('az-controls-top-k').value || 4),
      temperature: Number(document.getElementById('az-temperature').value || 1),
      top_p: Number(document.getElementById('az-top-p').value || 1),
      thinking_mode: document.getElementById('az-thinking-mode').value || 'balanced',
      max_completion_tokens: (function() {
        const val = document.getElementById('az-max-completion-tokens').value.trim();
        return val ? Number(val) : null;
      })(),
      evaluator_max_completion_tokens: (function() {
        const val = document.getElementById('az-evaluator-max-completion-tokens').value.trim();
        return val ? Number(val) : null;
      })(),
      assessment_strategy: 'per_control',
      validation_mode: document.getElementById('az-validation-mode').value || 'hard',
      auth_token: _currentAuthToken(),
    };

    _renderAzureComplianceReport({ status: 'Starting Azure assessment job...' });
    fetch('/api/compliance/report/azure/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          _renderAzureComplianceReport(data);
          return;
        }
        if (!data.job_id) {
          _renderAzureComplianceReport({ error: 'No job_id returned from Azure assessment job start.' });
          return;
        }
        _pollComplianceJob(data.job_id, 'azure');
      })
      .catch(err => _renderAzureComplianceReport({ error: String(err) }));
  }

  /**
   * Generate an AWS compliance report based on the provided account ID, region, and resource ARNs. This function sends a request to start the AWS compliance report generation process and handles the response, including polling for job status.
   * @returns {void}
   */
  function generateAwsComplianceReport() {
    const accountId = document.getElementById('aws-account-id').value.trim();
    const region = document.getElementById('aws-region').value.trim();
    const resourceArns = document.getElementById('aws-resource-arns').value
      .split('\n')
      .map(v => v.trim())
      .filter(Boolean);

    if (!accountId) {
      _renderAwsComplianceReport({ error: 'Account ID is required.' });
      return;
    }
    if (!region) {
      _renderAwsComplianceReport({ error: 'Region is required.' });
      return;
    }

    const body = {
      account_id: accountId,
      region: region,
      resource_arns: resourceArns,
      controls_framework: document.getElementById('aws-controls-framework').value || 'NIST CSF',
      controls_top_k: Number(document.getElementById('aws-controls-top-k').value || 4),
      retrieve_k: Number(document.getElementById('aws-retrieve-k').value || 5),
      temperature: Number(document.getElementById('aws-temperature').value || 1),
      top_p: Number(document.getElementById('aws-top-p').value || 1),
      thinking_mode: document.getElementById('aws-thinking-mode').value || 'balanced',
      max_completion_tokens: (function() {
        const val = document.getElementById('aws-max-completion-tokens').value.trim();
        return val ? Number(val) : null;
      })(),
      evaluator_max_completion_tokens: (function() {
        const val = document.getElementById('aws-evaluator-max-completion-tokens').value.trim();
        return val ? Number(val) : null;
      })(),
      assessment_strategy: 'per_control',
      validation_mode: document.getElementById('aws-validation-mode').value || 'hard',
      auth_token: _currentAuthToken(),
    };

    _renderAwsComplianceReport({ status: 'Starting AWS assessment job...' });
    fetch('/api/compliance/report/aws/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          _renderAwsComplianceReport(data);
          return;
        }
        if (!data.job_id) {
          _renderAwsComplianceReport({ error: 'No job_id returned from AWS assessment job start.' });
          return;
        }
        _pollComplianceJob(data.job_id, 'aws');
      })
      .catch(err => _renderAwsComplianceReport({ error: String(err) }));
  }

  /**
   * Generate a cryptographically secure UUID v4 using window.crypto.getRandomValues.
   * @returns {string} A UUID v4 string.
   */
  function _randomUUID() {
    var bytes = window.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant bits
    var hex = Array.from(bytes).map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');
    return hex.slice(0,8)+'-'+hex.slice(8,12)+'-'+hex.slice(12,16)+'-'+hex.slice(16,20)+'-'+hex.slice(20);
  }

  /**
   * Initialise the application once the DOM is fully loaded.
   * @returns {void}
   */
  document.addEventListener('DOMContentLoaded', function () {
    const sd = JSON.parse(document.getElementById('server-data').textContent);
    const pendingKey = 'rag_pending_question';
    const pendingAtKey = 'rag_pending_question_at';

    let session = loadSession();
    if (!session) {
      session = { session_id: _randomUUID(), conversation_id: _randomUUID(), user_id: '', auth_token: '', turns: [] };
      saveSession(session);
    }

    // Persist user_id returned by server after a POST
    if (sd.user_id) {
      session.user_id = sd.user_id;
      saveSession(session);
    }

    // Persist auth token for cross-session API calls
    const authField = document.getElementById('auth_token');
    if (authField) {
      if (authField.value) {
        session.auth_token = authField.value;
        saveSession(session);
      } else if (session.auth_token) {
        authField.value = session.auth_token;
      }
    }

    // Show Past Conversations button once we know who the user is
    if (session.user_id) {
      document.getElementById('show-past-btn').style.display = '';
    }

    document.getElementById('session_id_field').value = session.session_id;
    document.getElementById('conversation_id_field').value = session.conversation_id;

    // If server confirmed the pending question, clear pending marker.
    const pendingQuestion = (sessionStorage.getItem(pendingKey) || '').trim();
    if (pendingQuestion && sd.question && pendingQuestion === sd.question) {
      sessionStorage.removeItem(pendingKey);
      sessionStorage.removeItem(pendingAtKey);
    }

    // Append the server-rendered turn to local history.
    // Always append when a question was submitted so the latest turn is never lost.
    if (sd.question) {
      const renderedAnswer = (sd.answer && sd.answer.trim())
        ? sd.answer
        : ('Request failed: ' + (sd.error || 'No response returned.'));
      const last = session.turns[session.turns.length - 1];
      if (!last || last.q !== sd.question || last.a !== renderedAnswer) {
        session.turns.push({ q: sd.question, a: renderedAnswer });
        saveSession(session);
      }
    }

    renderThread(session.turns);
    if (!session.turns.length) {
      const fallback = document.getElementById('latest-answer-fallback');
      if (fallback) fallback.style.display = '';
    }
    refreshCorpusAStatus();
    _syncCorpusAFrameworkCheckboxes();

    // Restore last-used tab; keep Ask if server rendered a result this load
    var savedTab = '';
    try { savedTab = localStorage.getItem(_TAB_KEY) || ''; } catch (_) {}
    if (sd.question || sd.answer || sd.error) {
      switchTab('ask');
    } else if (savedTab && document.getElementById('tab-' + savedTab)) {
      switchTab(savedTab);
    }

    const askForm = document.getElementById('ask-form');
    const askBtn = document.getElementById('ask-submit-btn');
    const askAdvancedToggle = document.getElementById('advanced_mode');
    const askAdvancedFields = document.getElementById('ask-advanced-fields');
    const askResultsSection = document.getElementById('ask-results-section');
    if (askAdvancedToggle && askAdvancedFields) {
      const refreshAdvancedVisibility = function () {
        const show = askAdvancedToggle.checked ? 'grid' : 'none';
        askAdvancedFields.style.display = show;
      };
      askAdvancedToggle.addEventListener('change', refreshAdvancedVisibility);
      refreshAdvancedVisibility();
    }

    applyRuntimeUiHints();

    // Persist per-request token override fields across page loads
    ['max_completion_tokens', 'evaluator_max_completion_tokens'].forEach(function (id) {
      const el = document.getElementById(id);
      if (!el) return;
      const stored = localStorage.getItem('rag_' + id);
      if (stored !== null && stored !== '') {
        const parsed = parseInt(stored, 10);
        const lo = parseInt(el.getAttribute('min') || '0', 10);
        const hi = parseInt(el.getAttribute('max') || '99999', 10);
        if (!isNaN(parsed) && parsed >= lo && parsed <= hi) el.value = String(parsed);
      }
      el.addEventListener('change', function () {
        try { localStorage.setItem('rag_' + id, el.value); } catch (_) {}
      });
    });
    const askThinkingMode = document.getElementById('thinking_mode');
    if (askThinkingMode) {
      askThinkingMode.addEventListener('change', function () {
        applyAskThinkingModePreset(askThinkingMode.value || 'balanced');
      });
    }
    const complianceThinkingMode = document.getElementById('cr-thinking-mode');
    if (complianceThinkingMode) {
      applyAssessThinkingModePreset('cr', complianceThinkingMode.value || 'balanced');
      complianceThinkingMode.addEventListener('change', function () {
        applyAssessThinkingModePreset('cr', complianceThinkingMode.value || 'balanced');
      });
    }
    const azureThinkingMode = document.getElementById('az-thinking-mode');
    if (azureThinkingMode) {
      applyAssessThinkingModePreset('az', azureThinkingMode.value || 'balanced');
      azureThinkingMode.addEventListener('change', function () {
        applyAssessThinkingModePreset('az', azureThinkingMode.value || 'balanced');
      });
    }
    const awsThinkingMode = document.getElementById('aws-thinking-mode');
    if (awsThinkingMode) {
      applyAssessThinkingModePreset('aws', awsThinkingMode.value || 'balanced');
      awsThinkingMode.addEventListener('change', function () {
        applyAssessThinkingModePreset('aws', awsThinkingMode.value || 'balanced');
      });
    }
    if (askForm && askBtn) {
      askForm.addEventListener('submit', function () {
        const questionField = document.getElementById('question');
        const submitted = questionField && typeof questionField.value === 'string'
          ? questionField.value.trim()
          : '';
        if (submitted) {
          sessionStorage.setItem(pendingKey, submitted);
          sessionStorage.setItem(pendingAtKey, String(Date.now()));
        }
        askBtn.disabled = true;
        askBtn.textContent = 'Asking...';
      });
    }

    const complianceQuestionEl = document.getElementById('cr-question');
    const complianceFrameworkEl = document.getElementById('cr-controls-framework');
    const complianceStrategyEl = document.getElementById('cr-assessment-strategy');
    if (complianceQuestionEl) complianceQuestionEl.addEventListener('input', refreshComplianceReportAvailability);
    if (complianceFrameworkEl) complianceFrameworkEl.addEventListener('change', function () {
      if (complianceStrategyEl) complianceStrategyEl.dataset.userChanged = '';
      refreshComplianceReportAvailability();
    });
    if (complianceStrategyEl) complianceStrategyEl.addEventListener('change', function () {
      complianceStrategyEl.dataset.userChanged = 'true';
    });
    refreshComplianceReportAvailability();

    ['gx-framework-filter', 'gx-node-type-filter', 'gx-edge-type-filter', 'gx-community-filter', 'gx-min-confidence', 'gx-layout-mode'].forEach(function (id) {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('change', function () {
        if (id === 'gx-community-filter') {
          _graphVisualState.selectedCommunities = el.value ? new Set([el.value]) : null;
        }
        applyGraphExplorerFilters();
      });
    });

    const performanceModeEl = document.getElementById('gx-performance-mode');
    if (performanceModeEl) {
      performanceModeEl.addEventListener('change', function () {
        applyGraphExplorerFilters();
      });
    }

    const safeMaxEdgesEl = document.getElementById('gx-safe-max-visible-edges');
    if (safeMaxEdgesEl) {
      safeMaxEdgesEl.addEventListener('change', function () {
        applyGraphExplorerFilters();
      });
    }

    const labelZoomThresholdEl = document.getElementById('gx-label-zoom-threshold');
    if (labelZoomThresholdEl) {
      labelZoomThresholdEl.addEventListener('change', function () {
        _updateGraphCyLabelLod(true);
      });
    }

    const graph3dSensitivityEl = document.getElementById('gx-3d-sensitivity');
    if (graph3dSensitivityEl) {
      // TODO(user-preferences): Persist and restore Graph tab UI preferences via localStorage,
      // including 3D sensitivity, layout mode, performance mode, and label threshold.
      graph3dSensitivityEl.addEventListener('change', function () {
        if (_graph3d) {
          const mode = _graphExplorerValue('gx-performance-mode', 'auto');
          const dense = mode === 'dense' || (mode === 'auto' && (_graph3dData.nodes.length >= _GRAPH_DENSE_NODE_THRESHOLD || _graph3dData.links.length >= _GRAPH_DENSE_EDGE_THRESHOLD));
          _tuneGraph3dControls(dense);
        }
        _renderGraphRenderBudget();
      });
    }

    _refreshComplianceBatchOptions();
    refreshGraphStatus();
  });

  /**
   * Render static answer block (non-session / first load)
   * @returns {void}
   */
  (function () {
    const el = document.getElementById('answer-md');
    if (!el) return;
    const raw = el.getAttribute('data-raw') || '';
    if (!raw) return;
    el.innerHTML = mdRender(raw);
  })();

