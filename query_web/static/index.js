  const _TAB_KEY = 'rag_active_tab';
  const _CORPUS_B_LIST_RETRY_MS = 5000;
  const _CORPUS_B_LIST_MAX_RETRIES = 6;
  const _CORPUS_C_LIST_RETRY_MS = 5000;
  const _CORPUS_C_LIST_MAX_RETRIES = 6;
  let _corpusBListRetryTimeout = null;
  let _corpusCListRetryTimeout = null;

  function _cancelCorpusBListRetry() {
    if (_corpusBListRetryTimeout) {
      clearTimeout(_corpusBListRetryTimeout);
      _corpusBListRetryTimeout = null;
    }
  }

  function _cancelCorpusCListRetry() {
    if (_corpusCListRetryTimeout) {
      clearTimeout(_corpusCListRetryTimeout);
      _corpusCListRetryTimeout = null;
    }
  }

  function _appendCorpusCStatusNote(note) {
    const statusEl = document.getElementById('cc-status');
    if (!statusEl) return;
    statusEl.textContent += '\n\n' + note;
  }

  function _appendCorpusBStatusNote(note) {
    const statusEl = document.getElementById('cb-status');
    if (!statusEl) return;
    statusEl.textContent += '\n\n' + note;
  }

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
    try { localStorage.setItem(_TAB_KEY, name); } catch (_) {}
  }

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

  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function escAttr(s) {
    return String(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function mdFallbackRender(text) {
    const raw = String(text || '');
    const lines = raw.split(/\r?\n/);
    const html = [];
    let listType = '';
    let paragraph = [];

    function flushParagraph() {
      if (!paragraph.length) return;
      html.push('<p>' + paragraph.map(escHtml).join('<br/>') + '</p>');
      paragraph = [];
    }

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

  function loadSession() {
    try { return JSON.parse(localStorage.getItem(SESSION_KEY) || 'null'); }
    catch { return null; }
  }

  function saveSession(s) {
    localStorage.setItem(SESSION_KEY, JSON.stringify(s));
  }

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

  function mdRender(text) {
    if (typeof marked !== 'undefined') return marked.parse(text || '', { breaks: true, gfm: true });
    return mdFallbackRender(text);
  }

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
    setVal('temperature', defaults.temperature);
    setVal('max_completion_tokens', defaults.max_completion_tokens);
    setVal('evaluator_max_completion_tokens', defaults.evaluator_max_completion_tokens);
    setVal('controls_semantic', defaults.controls_semantic);
    setVal('controls_framework', defaults.controls_framework);
    setVal('controls_comparison_mode', defaults.controls_comparison_mode);

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

  function setRating(btn, turnIdx, stars) {
    const widget = document.querySelector('.rating-widget[data-turn="' + turnIdx + '"]');
    if (!widget) return;
    widget.dataset.rating = stars;
    widget.querySelectorAll('.rating-stars button').forEach((b, i) => b.classList.toggle('lit', i < stars));
  }

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

  function togglePastConversations() {
    const panel = document.getElementById('past-conv-panel');
    if (panel.style.display === 'none') {
      panel.style.display = '';
      loadPastConversations();
    } else {
      panel.style.display = 'none';
    }
  }

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

  function _currentAuthToken() {
    const authField = document.getElementById('auth_token');
    if (authField && authField.value) return authField.value;
    const session = loadSession();
    return (session && session.auth_token) ? session.auth_token : '';
  }

  function _extractExecutionName(payload) {
    return (payload && payload.job && payload.job.execution_name) ||
      (payload && payload.jobs && payload.jobs.length > 0 && payload.jobs[0].job && payload.jobs[0].job.execution_name) || null;
  }

  function _findExecution(diagData, executionName) {
    if (!diagData || !diagData.recent_executions || !executionName) return null;
    return diagData.recent_executions.find(
      e => e.id && (e.id.endsWith('/' + executionName) || e.id === executionName)
    ) || null;
  }

  function _selectedCorpusAFrameworks() {
    const allChecked = document.getElementById('ca-all').checked;
    if (allChecked) return ['all'];
    return Array.from(document.querySelectorAll('.ca-fw:checked')).map(el => el.value);
  }

  function _renderCorpusAStatus(payload) {
    const target = document.getElementById('ca-status');
    target.textContent = JSON.stringify(payload, null, 2);
  }

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

  function _renderAzureComplianceReport(payload) {
    const target = document.getElementById('azcr-status');
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

  function downloadComplianceReportMarkdown() {
    if (!_lastComplianceReport || !_lastComplianceReport.report_markdown) {
      _renderComplianceReport({ error: 'Generate a compliance report first.' });
      return;
    }
    const base = _lastComplianceReport.report_filename_base || 'compliance-report';
    _downloadText(base + '.md', _lastComplianceReport.report_markdown, 'text/markdown;charset=utf-8');
  }

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

  function downloadComplianceFindingsCsv() {
    if (!_lastComplianceReport || !_lastComplianceReport.report_findings_csv) {
      _renderComplianceReport({ error: 'Generate a compliance report first.' });
      return;
    }
    const base = _lastComplianceReport.report_filename_base || 'compliance-report';
    _downloadText(base + '-findings.csv', _lastComplianceReport.report_findings_csv, 'text/csv;charset=utf-8');
  }

  function downloadAzureComplianceReportMarkdown() {
    if (!_lastAzureComplianceReport || !_lastAzureComplianceReport.report_markdown) {
      _renderAzureComplianceReport({ error: 'Generate an Azure compliance report first.' });
      return;
    }
    const base = _lastAzureComplianceReport.report_filename_base || 'azure-compliance-report';
    _downloadText(base + '.md', _lastAzureComplianceReport.report_markdown, 'text/markdown;charset=utf-8');
  }

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

  function downloadAzureComplianceFindingsCsv() {
    if (!_lastAzureComplianceReport || !_lastAzureComplianceReport.report_findings_csv) {
      _renderAzureComplianceReport({ error: 'Generate an Azure compliance report first.' });
      return;
    }
    const base = _lastAzureComplianceReport.report_filename_base || 'azure-compliance-report';
    _downloadText(base + '-findings.csv', _lastAzureComplianceReport.report_findings_csv, 'text/csv;charset=utf-8');
  }

  function refreshCorpusAStatus() {
    const token = _currentAuthToken();
    const qs = token ? ('?auth_token=' + encodeURIComponent(token)) : '';
    fetch('/api/corpus-a/status' + qs)
      .then(r => r.json())
      .then(data => _renderCorpusAStatus(data))
      .catch(err => _renderCorpusAStatus({ error: String(err) }));
  }

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

  function _formatElapsedDuration(startedAtMs) {
    const elapsedMs = Math.max(0, Date.now() - startedAtMs);
    const totalSeconds = Math.floor(elapsedMs / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return minutes + 'm ' + String(seconds).padStart(2, '0') + 's';
  }

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

  function _pollCorpusBIndexStatus(maxPollIntervals, pollCount, executionName, pollStartedAtMs) {
    _pollCorpusListAndJobStatus('/api/corpus-b/list', 'cb-status', 'cr-b-upload-batch', maxPollIntervals, pollCount, executionName, pollStartedAtMs);
  }

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

  function _pollCorpusCIndexStatus(maxPollIntervals, pollCount, executionName, pollStartedAtMs) {
    _pollCorpusListAndJobStatus('/api/corpus-c/list', 'cc-status', 'cr-upload-batch', maxPollIntervals, pollCount, executionName, pollStartedAtMs);
  }

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

  function _pollComplianceJob(jobId, isAzure) {
    const token = _currentAuthToken();
    const params = new URLSearchParams();
    if (token) params.set('auth_token', token);
    const qs = params.toString();
    const endpoint = '/api/compliance/report/jobs/' + encodeURIComponent(jobId) + (qs ? ('?' + qs) : '');

    const render = isAzure ? _renderAzureComplianceReport : _renderComplianceReport;
    const done = isAzure ? _renderAzureComplianceReport : _renderComplianceReport;

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

  function generateComplianceReport() {
    const question = document.getElementById('cr-question').value.trim();

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
      controls_top_k: Number(document.getElementById('cr-controls-top-k').value || 4),
      temperature: Number(document.getElementById('cr-temperature').value || 1),
      controls_framework: document.getElementById('cr-controls-framework').value || null,
      controls_comparison_mode: document.getElementById('cr-controls-comparison-mode').value || 'auto-detect',
      corpus_b_upload_batch: document.getElementById('cr-b-upload-batch').value.trim() || null,
      corpus_c_upload_batch: document.getElementById('cr-upload-batch').value.trim() || null,
      assessment_strategy: 'per_control',
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
        _pollComplianceJob(data.job_id, false);
      })
      .catch(err => _renderComplianceReport({ error: String(err) }));
  }

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
        _pollComplianceJob(data.job_id, true);
      })
      .catch(err => _renderAzureComplianceReport({ error: String(err) }));
  }

  // Cryptographically secure UUID v4 using window.crypto.getRandomValues.
  function _randomUUID() {
    var bytes = window.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant bits
    var hex = Array.from(bytes).map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');
    return hex.slice(0,8)+'-'+hex.slice(8,12)+'-'+hex.slice(12,16)+'-'+hex.slice(16,20)+'-'+hex.slice(20);
  }

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

    _refreshComplianceBatchOptions();
  });

  // Render static answer block (non-session / first load)
  (function () {
    const el = document.getElementById('answer-md');
    if (!el) return;
    const raw = el.getAttribute('data-raw') || '';
    if (!raw) return;
    el.innerHTML = mdRender(raw);
  })();

