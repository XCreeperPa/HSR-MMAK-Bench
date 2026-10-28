/* Admin console.
   Read only by design: the only write operations are a management marker and
   voiding the current attempt while issuing a fresh code.

   The data page is a Tabulator grid, vendored locally under
   service/pages/admin/vendor/ (see that directory's README for the version
   lock).  The grid is deliberately read only: no cell editing, no row
   insertion, no paste write back, no score menu.  It refreshes once per
   second and keeps the filters, the sort order, the scroll position, the
   grouping and the selection when the underlying data changes. */
(async () => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const STATUS_LABELS = {
    issued: '未开始', ready: '已就绪', in_progress: '进行中',
    completed: '已完成', voided: '已作废',
  };
  const TYPE_LABELS = { human: '人类', agent: '智能体' };

  const state = {
    view: 'codes',
    revision: -1,
    matrix: null,
    table: null,
    questionTable: null,
    columnIndex: [],
    firstModule: [],
    matrixMode: 'contestant',
    needle: '',
    detailMode: 'one',
    poll: null,
    zoom: 1,
    pan: { x: 0, y: 0 },
  };

  async function call(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      headers: options.body ? { 'Content-Type': 'application/json' } : {},
      ...options,
    });
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) {
      if (response.status === 401) { showLogin(); throw new Error('需要管理员登录'); }
      throw new Error(data.message || `请求失败（${response.status}）`);
    }
    return data;
  }

  function toast(text, kind = 'error') {
    const box = $('#msg');
    box.textContent = text;
    box.className = `msg ${kind}`;
    box.style.display = 'block';
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { box.style.display = 'none'; }, 4200);
  }

  function showLogin() {
    $('#login').hidden = false;
    $('#codes').hidden = true;
    $('#matrix').hidden = true;
    $('#detail').hidden = true;
    $('#views').style.opacity = '.4';
    $('#views').style.pointerEvents = 'none';
  }

  function showConsole() {
    $('#login').hidden = true;
    $('#views').style.opacity = '1';
    $('#views').style.pointerEvents = 'auto';
    switchView(state.view);
  }

  function switchView(view) {
    state.view = view;
    ['codes', 'matrix', 'detail'].forEach((name) => { $(`#${name}`).hidden = name !== view; });
    $('#views').querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
    if (view === 'codes') loadCodes();
    if (view === 'matrix') loadMatrix();
    if (view === 'detail') { /* loaded on demand */ }
  }

  // ------------------------------------------------------------------- auth
  $('#loginBtn').addEventListener('click', async () => {
    try {
      await call('/api/v1/admin/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username: $('#username').value, password: $('#password').value }),
      });
      showConsole();
      toast('已登录', 'ok');
    } catch (error) {
      $('#loginMsg').textContent = error.message;
      $('#loginMsg').className = 'msg error';
    }
  });

  $('#views').querySelectorAll('button').forEach((button) => {
    button.addEventListener('click', () => switchView(button.dataset.view));
  });

  // ------------------------------------------------------------------ codes
  async function copyInvitation(contestantId) {
    // Fetch at click time: completion, voiding or a reset may have changed the code.
    const data = await call('/api/v1/admin/codes');
    const row = data.assessments.find((item) => item.contestant_id === contestantId);
    if (!row) throw new Error('该选手当前没有可用验证码，请刷新后重试。');
    const isAgent = row.contestant_type === 'agent';
    const text = isAgent
      ? `你的身份验证码：${row.code}\n\n任务文档：${data.task_document_url}\n\n按任务文档要求完成整场测试。用 HTTP 直接请求上面的任务文档地址即可，不需要浏览器，也不要打开任何图形界面。`
      : `邀请你参加《崩坏：星穹铁道》多模态知识测试！\n测试入口：${data.entry_url}\n身份验证码：${row.code}\n共 ${row.question_count} 题，限时 ${data.time_budget_minutes} 分钟。打开链接，输入验证码即可开始。`;
    let copied = false;
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(text); copied = true; } catch (_) { /* use HTTP-compatible fallback */ }
    }
    if (!copied) {
      const field = document.createElement('textarea');
      field.value = text;
      field.setAttribute('aria-label', '待复制内容');
      field.style.cssText = 'position:fixed;left:0;top:0;opacity:0;pointer-events:none';
      document.body.appendChild(field);
      field.focus();
      field.select();
      try { copied = document.execCommand('copy'); } catch (_) { /* manual selection below */ }
      field.remove();
    }
    if (copied) {
      toast(`已复制 ${row.contestant_code} ${row.display_name || ''} 的${isAgent ? '启动提示词' : '邀请函'}（含最新验证码）`, 'ok');
      return;
    }
    const dialog = document.createElement('dialog');
    dialog.className = 'copy-dialog';
    dialog.innerHTML = `<h3>${isAgent ? '启动提示词' : '邀请函'} · ${esc(row.contestant_code)}</h3>
      <p>浏览器未允许自动复制，请按 Ctrl+C 复制已选中的内容。</p>
      <textarea aria-label="复制内容" readonly></textarea>
      <button type="button" class="btn quiet">关闭</button>`;
    dialog.querySelector('textarea').value = text;
    dialog.querySelector('button').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => dialog.remove());
    document.body.appendChild(dialog);
    dialog.showModal();
    dialog.querySelector('textarea').select();
  }

  async function loadCodes() {
    const data = await call('/api/v1/admin/codes');
    state.revision = data.revision;
    const needle = ($('#codeSearch').value || '').trim().toLowerCase();
    const rows = data.assessments.filter((row) =>
      !needle || [row.display_name, row.agent_host, row.invocation_route, row.reasoning_effort]
        .filter(Boolean).some((value) => String(value).toLowerCase().includes(needle)));
    $('#codeCount').textContent = `${rows.length} 名选手各有一个可用验证码 · 修订号 ${data.revision}`;
    const header = ['选手', '类型', '思考程度', '接入应用', '模型来源', '测试次', '状态',
      '当前验证码', '已答题', '操作'];
    const body = rows.map((row) => `
      <tr>
        <td class="frozen">${esc(row.display_name || '')}<div class="hint">${esc(row.contestant_code || '')}</div></td>
        <td>${row.contestant_type === 'human' ? '人类' : '智能体'}</td>
        <td>${esc(row.reasoning_effort || '')}</td>
        <td>${esc(row.agent_host || '')}</td>
        <td>${esc(row.invocation_route || '')}</td>
        <td class="num">${row.test_number}</td>
        <td>${esc(row.status)}</td>
        <td class="code">${esc(row.code)}</td>
        <td class="num">${row.answered_count} / ${row.question_count}</td>
        <td>
          <button class="btn quiet" data-copy-invitation="${row.contestant_id}" type="button">${row.contestant_type === 'agent' ? '复制启动提示词' : '复制邀请函'}</button>
          <button class="btn quiet" data-void="${row.assessment_id}" type="button">作废补码</button>
          <button class="btn quiet" data-tag="${row.assessment_id}" type="button">添加标记</button>
        </td>
      </tr>`).join('');
    $('#codeTable').innerHTML =
      `<thead><tr>${header.map((h, i) => `<th class="${i === 0 ? 'frozen' : ''}">${esc(h)}</th>`).join('')}</tr></thead><tbody>${body}</tbody>`;
    $('#codeTable').querySelectorAll('[data-copy-invitation]').forEach((button) => {
      button.addEventListener('click', async () => {
        button.disabled = true;
        try { await copyInvitation(Number(button.dataset.copyInvitation)); }
        catch (error) { toast(error.message); }
        finally { button.disabled = false; }
      });
    });
    $('#codeTable').querySelectorAll('[data-void]').forEach((button) => {
      button.addEventListener('click', async () => {
        const reason = window.prompt('作废原因（会写入审计记录）');
        if (!reason) return;
        try {
          const result = await call(`/api/v1/admin/assessments/${button.dataset.void}/void`, {
            method: 'POST', body: JSON.stringify({ reason }),
          });
          toast(`已作废并补发新码（新的可用验证码 ${result.next_code}）`, 'ok');
          loadCodes();
        } catch (error) { toast(error.message); }
      });
    });
    $('#codeTable').querySelectorAll('[data-tag]').forEach((button) => {
      button.addEventListener('click', async () => {
        const tag = window.prompt('标记名称（例如 弃测 / 环境故障 / 冒烟）', '标记');
        if (!tag) return;
        const note = window.prompt('备注（可选）', '') || '';
        const exclude = window.confirm('是否把这个试次标记为不计入正式统计？');
        try {
          await call('/api/v1/admin/tags', {
            method: 'POST',
            body: JSON.stringify({
              assessment_id: Number(button.dataset.tag), tag, note, exclude_from_statistics: exclude,
            }),
          });
          toast('标记已记录', 'ok');
          loadCodes();
        } catch (error) { toast(error.message); }
      });
    });
  }

  $('#reloadCodes').addEventListener('click', () => loadCodes().catch((e) => toast(e.message)));
  $('#codeSearch').addEventListener('input', () => loadCodes().catch(() => {}));

  // ----------------------------------------------------------------- matrix
  // 表头筛选：文本列「包含」，数值列支持比较符，枚举列支持逗号分隔的任一匹配
  function textFilter(headerValue, rowValue) {
    const expr = String(headerValue ?? '').trim().toLowerCase();
    if (!expr) return true;
    return String(rowValue ?? '').toLowerCase().includes(expr);
  }

  function numFilter(headerValue, rowValue) {
    const expr = String(headerValue ?? '').trim();
    if (!expr) return true;
    if (rowValue === null || rowValue === undefined) return false;
    const matched = /^(>=|<=|>|<|=|<>|!=|~)?\s*(-?\d+(?:\.\d+)?)$/.exec(expr);
    if (!matched) return String(rowValue).includes(expr);
    const op = matched[1] || '=';
    const target = Number(matched[2]);
    if (op === '>') return rowValue > target;
    if (op === '<') return rowValue < target;
    if (op === '>=') return rowValue >= target;
    if (op === '<=') return rowValue <= target;
    if (op === '<>' || op === '!=') return rowValue !== target;
    if (op === '~') return Math.abs(rowValue - target) < 1e-9;
    return rowValue === target;
  }

  function enumFilter(headerValue, rowValue) {
    const expr = String(headerValue ?? '').trim().toLowerCase();
    if (!expr) return true;
    return expr.split(/[,，\s]+/).filter(Boolean)
      .some((part) => String(rowValue ?? '').toLowerCase().includes(part));
  }

  function firstModuleQuestions(matrix) {
    const map = matrix.question_modules || {};
    const first = matrix.modules && matrix.modules[0] ? matrix.modules[0].id : null;
    if (!first) return [];
    return matrix.question_order.filter((qid) => map[qid] === first);
  }

  function toRow(row) {
    const out = {
      _id: row.assessment_id,
      contestant_code: row.contestant_code,
      contestant_type: TYPE_LABELS[row.contestant_type] || row.contestant_type,
      display_name: row.display_name || '',
      reasoning_effort: row.reasoning_effort || '—',
      agent_host: row.agent_host || '—',
      invocation_route: row.invocation_route || '—',
      test_number: row.test_number,
      code: row.code,
      status: STATUS_LABELS[row.status] || row.status,
      answered_count: row.answered_count,
      question_count: row.question_count,
      bank_version: row.bank_version,
      scoring_rules_version: row.scoring_rules_version,
      timing_policy_version: row.timing_policy_version,
      survey_active_days_range: row.survey.active_days_label ?? '不适用',
      survey_familiarity: row.survey.familiarity ?? '不适用',
      module_scores: Object.entries(row.module_scores || {})
        .map(([k, v]) => `${k}:${v}`).join(' ') || '—',
      total_score: row.total_score,
      total_score_max: row.total_score_max,
      score_rate: row.score_rate,
      total_duration_ms: row.total_duration_ms,
      started_at: row.started_at || '—',
      completed_at: row.completed_at || '—',
      anomalies: row.anomalies.join('；'),
      _notstat: !row.include_in_statistics,
      _statistics: isExplicitlyExcluded(row) ? '已排除统计' : '未排除',
      _tags: (row.tags || []).map((tag) => tag.tag).join('、'),
      _raw: row,
    };
    for (const qid of Object.keys(row.questions || {})) {
      out[`score:${qid}`] = row.questions[qid].score;
      out[`time:${qid}`] = row.questions[qid].duration_with_delivery_ms;
    }
    return out;
  }

  function cellTooltip(cell) {
    const field = cell.getField();
    const data = cell.getRow().getData();
    if (!field.startsWith('score:') && !field.startsWith('time:')) return '';
    const qid = field.split(':')[1];
    const item = data._raw.questions[qid];
    if (!item) return '';
    if (item.score === null) {
      if (data._raw.status === 'voided') return `${qid}：该试次已作废`;
      if (data._raw.status === 'completed') return `${qid}：该题未作答`;
      return `${qid}：尚未作答`;
    }
    const parts = [`${qid} 得分 ${item.score}/${item.score_max}`];
    if (item.answer_summary) parts.push(item.answer_summary);
    if (item.duration_with_delivery_ms !== null) parts.push(`用时 ${formatDuration(item.duration_with_delivery_ms)}`);
    if (item.timing_source === 'issued_at_recovery') parts.push('历史计时：按服务器发题时间恢复');
    if (item.abandoned) parts.push('明确放弃');
    if (item.delivery_incomplete) parts.push('交付不完整');
    return parts.join(' · ');
  }

  function formatDuration(ms) {
    if (ms === null || ms === undefined || !Number.isFinite(Number(ms))) return '—';
    const seconds = Math.max(0, Math.round(Number(ms) / 1000));
    return `${Math.floor(seconds / 60)}分${String(seconds % 60).padStart(2, '0')}秒`;
  }

  function scoreMarkup(score, maximum, complete = true, label = score) {
    if (score === null || score === undefined) return '<span class="score-pending">—</span>';
    const tone = Number(score) < Number(maximum) - 1e-9 ? 'score-error'
      : complete ? 'score-perfect' : 'score-pending';
    return `<span class="${tone}">${esc(label)}</span>`;
  }

  function totalScoreMarkup(data, label = data.total_score) {
    const row = data._raw;
    const assessedMaximum = Object.values(row.questions)
      .filter((q) => q.score !== null).reduce((sum, q) => sum + q.score_max, 0);
    return scoreMarkup(data.total_score, row.status === 'completed' ? data.total_score_max : assessedMaximum,
      row.status === 'completed', label);
  }

  function isExplicitlyExcluded(row) {
    return row.status === 'voided' || (row.tags || []).some((tag) => tag.exclude_from_statistics);
  }

  async function excludeAssessment(assessmentId) {
    const reason = await new Promise((resolve) => {
      const dialog = document.createElement('dialog');
      dialog.className = 'copy-dialog';
      dialog.innerHTML = `<form method="dialog">
        <h3>排除本次统计</h3>
        <p>仅排除这一次测试，保留原始答案和记录。</p>
        <label for="excludeReason">原因（可选）</label>
        <textarea id="excludeReason" maxlength="1000" style="min-height:80px">人工排除</textarea>
        <div class="btnrow"><button class="btn quiet" value="cancel">取消</button>
          <button class="btn" value="exclude">确认排除</button></div>
      </form>`;
      dialog.addEventListener('close', () => {
        const value = dialog.returnValue === 'exclude' ? dialog.querySelector('textarea').value : null;
        dialog.remove();
        resolve(value);
      }, { once: true });
      document.body.appendChild(dialog);
      dialog.showModal();
    });
    if (reason === null) return;
    await call('/api/v1/admin/tags', {
      method: 'POST', body: JSON.stringify({
        assessment_id: Number(assessmentId), tag: '排除统计',
        note: reason.trim(), exclude_from_statistics: true,
      }),
    });
    await loadMatrix();
    const row = state.matrix.rows.find((item) => item.assessment_id === Number(assessmentId));
    if ($('#mDetail').dataset.assessmentId === String(assessmentId)) {
      if (row) showRowDetail(toRow(row)); else $('#mDetail').hidden = true;
    }
    toast('该次测试已排除统计，原始答案和记录保留。', 'ok');
  }

  function matrixColumns(matrix) {
    const qids = matrix.question_order;
    const meta = matrix.question_meta || {};
    const index = [];
    const push = (group, field, title, extra) => {
      index.push({ group, field, title });
      return { title, field, ...extra };
    };
    const text = (extra = {}) => ({
      headerFilter: 'input', headerFilterLiveFilter: false, headerFilterFunc: textFilter,
      width: 130, ...extra,
    });
    const number = (extra = {}) => ({
      headerFilter: 'input', headerFilterLiveFilter: false, headerFilterFunc: numFilter,
      width: 84, hozAlign: 'right', sorter: 'number', ...extra,
    });
    const list = (extra = {}) => ({
      headerFilter: 'input', headerFilterLiveFilter: false, headerFilterFunc: enumFilter,
      width: 110, ...extra,
    });

    const identity = [
      push('身份', 'contestant_code', '选手代号', text({ width: 100 })),
      push('身份', 'display_name', '姓名/模型', text({ width: 150 })),
    ];

    const access = [
      push('接入', 'contestant_type', '类型', list({ width: 78 })),
      push('接入', 'reasoning_effort', '思考程度', list({ width: 92 })),
      push('接入', 'agent_host', '接入应用', text({ width: 132 })),
      push('接入', 'invocation_route', '模型来源', text({ width: 112 })),
    ];

    const test = [
      push('测试', 'test_number', '测试次', number({ width: 84 })),
      push('测试', 'code', '身份验证码', text({ width: 104, cssClass: 'code' })),
      push('测试', 'status', '状态', list({ width: 90 })),
      push('测试', 'answered_count', '已答题', number({ width: 84 })),
      push('测试', '_statistics', '统计操作', {
        width: 122, headerSort: false,
        formatter: (cell) => {
          const data = cell.getRow().getData();
          return isExplicitlyExcluded(data._raw) ? '<span class="hint">已排除统计</span>'
            : `<button type="button" class="btn quiet" data-exclude="${data._id}">排除统计</button>`;
        },
      }),
      push('测试', '_annotation', '标注', {
        headerFilter: false, width: 100, headerSort: false,
        formatter: (cell) => {
          const data = cell.getRow().getData();
          const answered = state.firstModule.some(
            (qid) => data._raw.questions[qid] && data._raw.questions[qid].score !== null);
          if (!answered) return '<span class="hint">—</span>';
          return `<a href="#" class="annlink" data-detail="${data._id}">查看该模块</a>`;
        },
      }),
      push('测试', 'bank_version', '题库版本', text({ width: 190 })),
      push('测试', 'scoring_rules_version', '规则版本', text({ width: 150 })),
      push('测试', 'timing_policy_version', '计时版本', text({ width: 150 })),
    ];

    const survey = [
      push('问卷', 'survey_active_days_range', '活跃天数区间', list({ width: 155 })),
      push('问卷', 'survey_familiarity', '熟悉程度', list({ width: 110 })),
    ];

    const scores = qids.map((qid) => push('得分', `score:${qid}`, qid, number({
      width: 74,
      formatter: (cell) => scoreMarkup(cell.getValue(), cell.getRow().getData()._raw.questions[qid].score_max),
      headerTooltip: `${qid} · ${meta[qid] ? meta[qid].module_name : ''} · 满分 ${meta[qid] ? meta[qid].score : '—'}\n`
        + `${meta[qid] ? meta[qid].prompt : ''}${state.firstModule.includes(qid) ? '\n（点得分看标注对比）' : ''}`,
      tooltip: (cell) => cellTooltip(cell),
      cellClick: (event, cell) => {
        if (state.firstModule.includes(qid)) openQuestion(cell.getRow().getData(), qid);
        else showRowDetail(cell.getRow().getData());
      },
    })));

    const times = qids.map((qid) => push('用时', `time:${qid}`, qid, number({
      width: 108, formatter: (cell) => formatDuration(cell.getValue()),
      headerTooltip: `${qid} 的含交付用时（分秒显示；数值筛选单位为毫秒）`,
      tooltip: (cell) => cellTooltip(cell),
    })));

    const summary = [
      push('汇总', 'module_scores', '模块分', text({ width: 140, formatter: (cell) => {
        const data = cell.getRow().getData();
        return Object.entries(data._raw.module_scores || {}).map(([module, value]) => {
          const questions = qids.filter((qid) => String(matrix.question_modules[qid]).split('_').pop() === String(module));
          const maximum = questions.reduce((sum, qid) => sum + (data._raw.status === 'completed'
            || data._raw.questions[qid].score !== null ? data._raw.questions[qid].score_max : 0), 0);
          return `${esc(module)}:${scoreMarkup(value, maximum, data._raw.status === 'completed')}`;
        }).join(' ') || '—';
      } })),
      push('汇总', 'total_score', '总分', number({ width: 84,
        formatter: (cell) => totalScoreMarkup(cell.getRow().getData()) })),
      push('汇总', 'total_score_max', '满分', number({ width: 78 })),
      push('汇总', 'score_rate', '得分率', number({ width: 88, formatter: (cell) => {
        const value = cell.getValue();
        return value === null || value === undefined ? ''
          : totalScoreMarkup(cell.getRow().getData(), `${(value * 100).toFixed(1)}%`);
      } })),
      push('汇总', 'total_duration_ms', '总用时', number({ width: 120, formatter: (cell) => formatDuration(cell.getValue()) })),
      push('汇总', 'started_at', '开始时刻', text({ width: 190 })),
      push('汇总', 'completed_at', '完成时刻', text({ width: 190 })),
      push('汇总', 'anomalies', '异常标记', text({ width: 220, cssClass: 'flag' })),
    ];

    const tree = [
      // 冻结要落在列组上：Tabulator 6.6 只认 group 的 frozen，
      // 给子列写 frozen 会直接被忽略并打警告。
      { title: '身份', frozen: true, columns: identity },
      { title: '接入', columns: access },
      { title: '测试', columns: test },
      { title: '问卷', columns: survey },
      { title: '得分', columns: scores },
      { title: '用时', columns: times },
      { title: '汇总', columns: summary },
    ];
    return { tree, index };
  }

  function groupHeader(title, value, data) {
    const rows = Array.isArray(data) ? data : [];
    const included = rows.filter((row) => row._raw.include_in_statistics && row.total_score !== null);
    const perContestant = new Map();
    included.forEach((row) => {
      const key = row._raw.contestant_id;
      if (!perContestant.has(key)) perContestant.set(key, []);
      perContestant.get(key).push(row.total_score);
    });
    const scores = state.matrix.mode === 'by_contestant'
      ? [...perContestant.values()].map((values) => values.reduce((a, b) => a + b, 0) / values.length)
      : included.map((row) => row.total_score);
    const mean = scores.length
      ? (scores.reduce((sum, item) => sum + item, 0) / scores.length).toFixed(2) : '—';
    return `<div class="grouphdr"><b>${esc(title)}</b>`
      + `<span class="hint">${rows.length} 次测试 · 纳入 ${included.length} 次 · 正式均分 ${mean}</span></div>`;
  }

  function captureScroll(table) {
    const holder = table.element && table.element.querySelector('.tabulator-tableholder');
    return holder ? { top: holder.scrollTop, left: holder.scrollLeft } : null;
  }

  function restoreScroll(table, position) {
    if (!position || !table.element) return;
    const holder = table.element.querySelector('.tabulator-tableholder');
    if (!holder) return;
    holder.scrollTop = position.top;
    holder.scrollLeft = position.left;
  }

  function filteredRows(rawRows) {
    const needle = state.needle.toLowerCase();
    const status = $('#mStatus').value;
    const qualify = $('#mQualify').value;
    return rawRows.filter((row) => {
      if (status && row.status !== status) return false;
      if (qualify === 'yes' && !row.include_in_statistics) return false;
      if (qualify === 'no' && row.include_in_statistics) return false;
      if (!needle) return true;
      const hay = [row.contestant_code, row.display_name, row.agent_host, row.invocation_route,
        row.reasoning_effort, row.code, String(row.test_number), row.status];
      return hay.some((value) => String(value ?? '').toLowerCase().includes(needle));
    });
  }

  function buildTable(matrix) {
    const { tree, index } = matrixColumns(matrix);
    state.columnIndex = index;
    const table = new Tabulator('#matrixTable', {
      data: [],
      columns: tree,
      index: '_id',
      layout: 'fitData',
      height: '68vh',
      placeholder: '没有符合条件的行',
      // 只读：没有编辑器、没有行增删；clipboard 只开复制（不注册 paste 监听）
      editable: false,
      selectableRows: true,
      clipboard: 'copy',
      clipboardCopyRowRange: 'selected',
      // 多列排序、列可拖动、虚拟行渲染是 Tabulator 的内建行为
      sortMode: 'multiple',
      movableColumns: true,
      headerFilterLiveFilterDelay: 400,
      // 偏好存在当前浏览器：列宽、列显隐、列序、排序、表头筛选、分组
      // 注意 Tabulator 6.6 的 persistence.columns 是「要留存的列属性名数组」，
      // 传对象会让持久化模块在初始化时对非数组调 forEach 直接崩。
      persistence: {
        columns: ['title', 'width', 'visible'],
        sort: true,
        headerFilter: true,
        group: true,
      },
      persistenceID: `hsr-admin-matrix-v2-${matrix.bank_version}`,
      persistenceMode: true,
      groupBy: $('#mGroup').value || false,
      groupStartOpen: true,
      groupHeader,
      rowFormatter: (row) => {
        if (row.getData()._notstat) row.getElement().classList.add('notstat');
      },
    });
    state.table = table;
    table.on('rowClick', (event, row) => {
      if (event.target.closest('a, button') || event.target.closest('[tabulator-field^="score:"]')) return;
      showRowDetail(row.getData());
    });
    // dataFiltered fires before the row manager replaces its active-row list.
    table.on('dataFiltered', (_filters, rows) => updateOverview(rows.length));
    table.on('tableBuilt', () => {
      applyData();
      renderColumnPanel();
    });
    $('#matrixTable').addEventListener('click', (event) => {
      const exclude = event.target.closest('button[data-exclude]');
      if (exclude) {
        event.stopPropagation();
        exclude.disabled = true;
        excludeAssessment(exclude.dataset.exclude).catch((error) => toast(error.message))
          .finally(() => { exclude.disabled = false; });
        return;
      }
      const link = event.target.closest('a.annlink');
      if (!link) return;
      event.preventDefault();
      event.stopPropagation();
      openModule(link.dataset.detail);
    });
  }

  function applyData() {
    const table = state.table;
    if (!table || !state.matrix) return;
    const rows = filteredRows(state.matrix.rows).map(toRow);
    const scroll = captureScroll(table);
    const selected = (table.getSelectedRows() || []).map((row) => row.getData()._id);
    table.replaceData(rows).then(() => {
    if (selected.length) {
      const current = table.getRows().map((row) => row.getData()._id);
      selected.filter((id) => current.includes(id)).forEach((id) => {
        const row = table.getRows().find((item) => item.getData()._id === id);
        if (row) row.select();
      });
    }
    restoreScroll(table, scroll);
    updateOverview();
    }).catch((error) => toast(error.message));
  }

  function updateSummary() {
    const matrix = state.matrix;
    if (!matrix) return;
    const summary = matrix.summaries;
    $('#mStat').textContent =
      `样本单位：${summary.sample_unit} · 纳入 ${summary.all.tests} 次完整测试 / ` +
      `${summary.all.contestants ?? summary.all.tests} 名选手 · 全体平均总分 ` +
      `${summary.all.mean_total ?? '—'} · 修订号 ${matrix.revision}`;
  }

  function updateOverview(shownCount) {
    const matrix = state.matrix;
    if (!matrix) return;
    const rows = matrix.rows;
    const count = (predicate) => rows.filter(predicate).length;
    const shown = Number.isInteger(shownCount) ? shownCount
      : (state.table ? state.table.getData('active').length : 0);
    const items = [
      ['当前视图', `${shown} / ${rows.length} 次测试`],
      ['已完成', count((row) => row.status === 'completed')],
      ['进行中', count((row) => row.status === 'in_progress' || row.status === 'ready')],
      ['未开始', count((row) => row.status === 'issued')],
      ['已作废', count((row) => row.status === 'voided')],
      ['不计入统计', count((row) => !row.include_in_statistics)],
      ['有异常标记', count((row) => row.anomalies.length > 0)],
    ];
    $('#mOverview').innerHTML = items
      .map(([label, value]) => `<span class="ov"><b>${esc(value)}</b>${esc(label)}</span>`).join('');
  }

  async function loadMatrix() {
    const query = new URLSearchParams({
      mode: $('#mMode').value,
      include_non_statistics: $('#mExtra').checked ? 'true' : 'false',
    });
    if ($('#mType').value) query.set('contestant_type', $('#mType').value);
    if ($('#mBank').value) query.set('bank_version', $('#mBank').value);
    const matrix = await call(`/api/v1/admin/matrix?${query}`);
    $('#mBank').innerHTML = (matrix.bank_versions || [{version:matrix.bank_version}])
      .map((bank) => `<option value="${esc(bank.version)}" ${bank.version === matrix.bank_version ? 'selected' : ''}>${esc(bank.version)}</option>`).join('');
    const rebuilt = state.matrix && state.matrix.bank_version !== matrix.bank_version;
    state.matrix = matrix;
    state.revision = matrix.revision;
    state.firstModule = firstModuleQuestions(matrix);
    updateSummary();
    if (!state.table || rebuilt) {
      if (state.table) { state.table.destroy(); state.table = null; }
      buildTable(matrix);
    } else {
      applyData();
    }
    if (state.matrixMode === 'question') buildQuestionTable(matrix);
  }

  // --------------------------------------------------- by question (pivot)
  function questionRows(matrix) {
    const summary = matrix.summaries;
    const meta = matrix.question_meta || {};
    const pick = (group, qid) => (group.answered_by_question || {})[qid] || 0;
    const mean = (group, qid) => (group.mean_by_question || {})[qid];
    return matrix.question_order.map((qid) => {
      const info = meta[qid] || {};
      const all = summary.all;
      const human = summary.human || {};
      const agent = summary.agent || {};
      const meanAll = mean(all, qid);
      return {
        qid,
        module: info.module_name || info.module || '',
        type: info.type === 'point_identification' ? '图片标注' : '单选',
        prompt: info.prompt || '',
        score_max: info.score,
        answered_all: pick(all, qid),
        answered_human: pick(human, qid),
        answered_agent: pick(agent, qid),
        mean_all: meanAll === undefined ? null : meanAll,
        mean_human: mean(human, qid) === undefined ? null : mean(human, qid),
        mean_agent: mean(agent, qid) === undefined ? null : mean(agent, qid),
        rate_all: meanAll === null || meanAll === undefined || !info.score
          ? null : meanAll / info.score,
      };
    });
  }

  function buildQuestionTable(matrix) {
    const text = (extra = {}) => ({
      headerFilter: 'input', headerFilterLiveFilter: false, headerFilterFunc: textFilter,
      width: 130, ...extra,
    });
    const number = (extra = {}) => ({
      headerFilter: 'input', headerFilterLiveFilter: false, headerFilterFunc: numFilter,
      width: 110, hozAlign: 'right', sorter: 'number', ...extra,
    });
    const percent = (extra = {}) => number({
      formatter: (cell) => {
        const value = cell.getValue();
        return value === null || value === undefined ? '' : `${(value * 100).toFixed(1)}%`;
      },
      ...extra,
    });
    if (state.questionTable) { state.questionTable.destroy(); state.questionTable = null; }
    state.questionTable = new Tabulator('#qTable', {
      data: questionRows(matrix),
      index: 'qid',
      layout: 'fitData',
      height: '68vh',
      placeholder: '没有数据',
      editable: false,
      selectableRows: true,
      clipboard: 'copy',
      sortMode: 'multiple',
      persistence: { sort: true, headerFilter: true },
      persistenceID: `hsr-admin-questions-v2-${matrix.bank_version}`,
      persistenceMode: true,
      columns: [
        { title: '题号', field: 'qid', frozen: true, width: 80,
          headerFilter: 'input', headerFilterLiveFilter: false, headerFilterFunc: textFilter },
        { title: '模块', field: 'module', ...text({ width: 120 }) },
        { title: '类型', field: 'type', ...text({ width: 100 }) },
        { title: '满分', field: 'score_max', ...number({ width: 80 }) },
        { title: '作答数·全体', field: 'answered_all', ...number({ width: 110 }) },
        { title: '均分·全体', field: 'mean_all', ...number({ width: 110,
          formatter: (cell) => scoreMarkup(cell.getValue(), cell.getRow().getData().score_max) }) },
        { title: '得分率·全体', field: 'rate_all', ...percent({ width: 120, formatter: (cell) =>
          scoreMarkup(cell.getValue(), 1, true, `${(cell.getValue() * 100).toFixed(1)}%`) }) },
        { title: '作答数·人类', field: 'answered_human', ...number({ width: 110 }) },
        { title: '均分·人类', field: 'mean_human', ...number({ width: 110,
          formatter: (cell) => scoreMarkup(cell.getValue(), cell.getRow().getData().score_max) }) },
        { title: '作答数·智能体', field: 'answered_agent', ...number({ width: 110 }) },
        { title: '均分·智能体', field: 'mean_agent', ...number({ width: 110,
          formatter: (cell) => scoreMarkup(cell.getValue(), cell.getRow().getData().score_max) }) },
        { title: '题面', field: 'prompt', ...text({ width: 380 }) },
      ],
    });
  }

  // ------------------------------------------------------------- row detail
  function showRowDetail(data) {
    const matrix = state.matrix;
    const qids = matrix.question_order;
    const meta = matrix.question_meta || {};
    const body = qids.map((qid) => {
      const item = data._raw.questions[qid];
      const info = meta[qid] || {};
      const flags = [
        item.abandoned ? '<span class="miss">明确放弃</span>' : '',
        item.delivery_incomplete ? '<span class="miss">交付不完整</span>' : '',
      ].filter(Boolean).join(' ');
      return `<tr>
        <td class="code">${esc(qid)}</td>
        <td>${esc(info.module_name || '')}</td>
        <td class="num">${scoreMarkup(item.score, item.score_max)}</td>
        <td class="num">${item.score_max}</td>
        <td class="num" title="${item.timing_source === 'issued_at_recovery' ? '历史计时：按服务器发题时间恢复' : '从服务器发送题目至收到答案'}">${formatDuration(item.duration_with_delivery_ms)}</td>
        <td>${esc(item.answer_summary ?? '—')}</td>
        <td>${flags}</td>
        <td>${state.firstModule.includes(qid) ? `<a href="#" data-q="${esc(qid)}" data-id="${data._id}">看标注</a>` : '—'}</td>
      </tr>`;
    }).join('');
    $('#mDetail').hidden = false;
    $('#mDetail').dataset.assessmentId = String(data._id);
    $('#mDetail').innerHTML = `
      <div class="rowdetail-head">
        <b>试次明细 · ${esc(data.contestant_code)} ${esc(data.display_name)}</b>
        <span class="hint">第 ${data.test_number} 次 · 验证码 ${esc(data.code)} · ${esc(data.status)} ·
          总分 ${totalScoreMarkup(data)}/${data.total_score_max} · 修订号 ${matrix.revision}</span>
        <button class="btn quiet" id="mDetailClose" type="button">收起</button>
        ${isExplicitlyExcluded(data._raw) ? '<span class="miss">已排除统计</span>' : '<button class="btn quiet" id="mDetailExclude" type="button">排除本次统计</button>'}
      </div>
      <div class="scroll" style="max-height:38vh"><table class="grid">
        <thead><tr><th>题号</th><th>模块</th><th>得分</th><th>满分</th><th>用时</th>
          <th>答案摘要</th><th>标记</th><th>标注</th></tr></thead>
        <tbody>${body}</tbody></table></div>`;
    $('#mDetailClose').addEventListener('click', () => { $('#mDetail').hidden = true; });
    if ($('#mDetailExclude')) $('#mDetailExclude').addEventListener('click', async (event) => {
      event.target.disabled = true;
      try { await excludeAssessment(data._id); }
      catch (error) { toast(error.message); event.target.disabled = false; }
    });
    $('#mDetail').querySelectorAll('a[data-q]').forEach((link) => {
      link.addEventListener('click', (event) => {
        event.preventDefault();
        openQuestionById(link.dataset.id, link.dataset.q);
      });
    });
  }

  // ----------------------------------------------------------- column panel
  function setSectionVisibility(group, visible) {
    for (const item of state.columnIndex) {
      if (group && item.group !== group) continue;
      const column = state.table.getColumn(item.field);
      if (column) { if (visible) column.show(); else column.hide(); }
    }
    renderColumnPanel();
  }

  function renderSectionToggles() {
    const groups = [...new Set(state.columnIndex.map((item) => item.group))];
    const counts = new Map(groups.map((group) => {
      const items = state.columnIndex.filter((item) => item.group === group);
      return [group, {total:items.length, visible:items.filter((item) => state.table.getColumn(item.field)?.isVisible()).length}];
    }));
    $('#columnSections').innerHTML = '<b>显示分区</b>' + groups.map((group) => {
      const count = counts.get(group);
      return `<label><input type="checkbox" data-section="${esc(group)}" aria-label="显示${esc(group)}列" ${count.visible === count.total ? 'checked' : ''}>${esc(group)} <span class="hint">${count.visible}/${count.total}</span></label>`;
    }).join('') + '<button type="button" class="btn quiet" id="showAllSections">恢复全部列</button>';
    $('#columnSections').querySelectorAll('[data-section]').forEach((box) => {
      const count = counts.get(box.dataset.section);
      box.indeterminate = count.visible > 0 && count.visible < count.total;
      box.addEventListener('change', () => setSectionVisibility(box.dataset.section, box.checked));
    });
    $('#showAllSections').addEventListener('click', () => setSectionVisibility(null, true));
  }

  function renderColumnPanel() {
    const table = state.table;
    if (!table) return;
    renderSectionToggles();
    const needle = ($('#colSearch').value || '').trim().toLowerCase();
    const groups = new Map();
    for (const item of state.columnIndex) {
      if (needle && !item.title.toLowerCase().includes(needle)
          && !item.group.toLowerCase().includes(needle)) continue;
      if (!groups.has(item.group)) groups.set(item.group, []);
      groups.get(item.group).push(item);
    }
    const blocks = [];
    for (const [group, items] of groups) {
      blocks.push(`<div class="colgroup">
        <div class="colgroup-head">
          <b>${esc(group)}</b>
          <button class="btn quiet" data-all="${esc(group)}" type="button">全显</button>
          <button class="btn quiet" data-none="${esc(group)}" type="button">全隐</button>
        </div>
        ${items.map((item) => {
          const column = table.getColumn(item.field);
          const visible = column && typeof column.isVisible === 'function' ? column.isVisible() : true;
          return `<label class="colitem">
            <input type="checkbox" data-field="${esc(item.field)}"${visible ? ' checked' : ''}>
            <span>${esc(item.title)}</span></label>`;
        }).join('')}
      </div>`);
    }
    $('#colTree').innerHTML = blocks.join('') || '<p class="hint">没有匹配的列</p>';
    $('#colTree').querySelectorAll('input[data-field]').forEach((box) => {
      box.addEventListener('change', () => {
        const column = table.getColumn(box.dataset.field);
        if (!column) return;
        if (box.checked) column.show(); else column.hide();
        renderColumnPanel();
      });
    });
    $('#colTree').querySelectorAll('button[data-all]').forEach((button) => {
      button.addEventListener('click', () => {
        for (const item of state.columnIndex) {
          if (item.group !== button.dataset.all) continue;
          const column = table.getColumn(item.field);
          if (column) column.show();
        }
        renderColumnPanel();
      });
    });
    $('#colTree').querySelectorAll('button[data-none]').forEach((button) => {
      button.addEventListener('click', () => {
        for (const item of state.columnIndex) {
          if (item.group !== button.dataset.none) continue;
          const column = table.getColumn(item.field);
          if (column) column.hide();
        }
        renderColumnPanel();
      });
    });
  }

  $('#mColsBtn').addEventListener('click', () => {
    const panel = $('#colPanel');
    panel.hidden = !panel.hidden;
    if (!panel.hidden) renderColumnPanel();
  });
  $('#colClose').addEventListener('click', () => { $('#colPanel').hidden = true; });
  $('#colSearch').addEventListener('input', () => renderColumnPanel());

  // --------------------------------------------------------------- exports
  function downloadAllRecords() {
    const query = new URLSearchParams({
      format: $('#dlAllFormat').value,
      scope: 'all',
      mode: $('#mMode').value,
      include_non_statistics: 'true',
    });
    if ($('#mBank').value) query.set('bank_version', $('#mBank').value);
    window.location.href = `/api/v1/admin/export?${query}`;
  }

  function exportMeta() {
    const matrix = state.matrix;
    const rows = matrix.rows;
    return {
      generated_at: new Date().toISOString(),
      revision: matrix.revision,
      bank_version: matrix.bank_version,
      scoring_rules_version: [...new Set(rows.map((row) => row.scoring_rules_version))].join('、'),
      timing_policy_version: [...new Set(rows.map((row) => row.timing_policy_version))].join('、'),
      filters: '管理控制台当前视图：全局搜索 + 状态 + 统计资格 + 各列表头筛选 + 分组',
      sample_unit: matrix.summaries.sample_unit,
      mode: matrix.mode,
      included_tests: matrix.summaries.all.tests,
      included_contestants: matrix.summaries.all.contestants,
      note: '本文件由管理控制台按当前视图导出，统计口径与服务端汇总一致。',
    };
  }

  function downloadCurrentView() {
    const format = $('#dlViewFormat').value;
    const table = state.matrixMode === 'question' ? state.questionTable : state.table;
    if (!table || !state.matrix) { toast('总表还没载入'); return; }
    const columns = table.getColumns().filter((column) => column.getField() && column.isVisible());
    const header = columns.map((column) => `${column.getDefinition().title} [${column.getField()}]`);
    const rows = table.getData('active').map((row) => columns.map((column) => {
      const value = row[column.getField()];
      return value === null || value === undefined ? '' : value;
    }));
    const meta = exportMeta();
    const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    if (format === 'json') {
      const body = JSON.stringify({
        meta,
        columns: header,
        rows: rows.map((line) => Object.fromEntries(header.map((title, i) => [title, line[i]]))),
      }, null, 2);
      const blob = new Blob([body], { type: 'application/json;charset=utf-8' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `hsr-mmak-current-view-${stamp}.json`;
      link.click();
      URL.revokeObjectURL(link.href);
      return;
    }
    const escapeCell = (value) => {
      const text = String(value ?? '');
      return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    };
    const lines = [header.map(escapeCell).join(',')];
    for (const line of rows) lines.push(line.map(escapeCell).join(','));
    lines.push('');
    lines.push('导出说明');
    for (const [key, value] of Object.entries(meta)) {
      if (value === undefined) continue;
      lines.push(`${escapeCell(key)},${escapeCell(value)}`);
    }
    const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `hsr-mmak-current-view-${stamp}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  $('#dlAllBtn').addEventListener('click', () => downloadAllRecords());
  $('#dlViewBtn').addEventListener('click', () => downloadCurrentView());

  // ------------------------------------------------------------- toolbar
  $('#mSearch').addEventListener('input', (event) => {
    state.needle = (event.target.value || '').trim();
    applyData();
  });
  $('#mStatus').addEventListener('change', () => applyData());
  $('#mQualify').addEventListener('change', () => applyData());
    ['mType', 'mMode', 'mExtra', 'mBank'].forEach((id) => {
    $(`#${id}`).addEventListener('change', () => loadMatrix().catch((e) => toast(e.message)));
  });
  $('#mGroup').addEventListener('change', (event) => {
    if (state.table) state.table.setGroupBy(event.target.value || false);
  });
  $('#mView').querySelectorAll('button').forEach((button) => {
    button.addEventListener('click', () => {
      state.matrixMode = button.dataset.mode;
      $('#mView').querySelectorAll('button')
        .forEach((item) => item.classList.toggle('active', item === button));
      $('#matrixTable').hidden = state.matrixMode !== 'contestant';
      $('#qTable').hidden = state.matrixMode !== 'question';
      $('#columnSections').hidden = state.matrixMode !== 'contestant';
      $('#mColsBtn').disabled = state.matrixMode !== 'contestant';
      if (state.matrixMode === 'question') $('#colPanel').hidden = true;
      $('#mDetail').hidden = true;
      if (state.matrixMode === 'question' && state.matrix) buildQuestionTable(state.matrix);
    });
  });

  // ----------------------------------------------------------------- detail
  // One question and the whole first module share the same two pieces of
  // markup: the question image with the standard answer polygons drawn on it,
  // and the list of the points the contestant actually submitted.
  function annotationStage(payload) {
    const width = payload.question.image_width;
    const height = payload.question.image_height;
    const polygons = payload.targets.map((target) => {
      const points = target.head_region.map((p) => `${p[0]},${p[1]}`).join(' ');
      const title = `<title>${esc(target.name || target.character_id)}（标准答案判定框）</title>`;
      return `<polygon class="${$('#dTargets').checked ? '' : 'hidden'}" points="${points}">${title}</polygon>`;
    }).join('');
    const points = payload.submitted.map((item) => {
      const cx = item.x;
      const cy = item.y;
      const title = `<title>${esc(item.character_id)} (${item.x}, ${item.y})`
        + `${item.score ? ' 命中' : ' 未命中'}</title>`;
      return `<circle class="${item.score ? 'hit' : ''}" cx="${cx}" cy="${cy}" r="${width * 0.006}">${title}</circle>`;
    }).join('');
    return `
      <div class="stageinner">
        <img alt="题图" src="${esc(payload.asset.url)}">
        <svg class="svglayer" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">${polygons}${points}</svg>
      </div>`;
  }

  function annotationPoints(payload) {
    return payload.submitted.length
      ? payload.submitted.map((item) => `
        <li>
          <span class="mark">${esc(item.character_id)}</span>
          <span class="${item.score ? 'ok' : 'miss'}">${item.score ? '命中' : '未命中'}</span>
          ${item.duplicate ? '<span class="miss">重复</span>' : ''}
          <div class="raw">(${item.x}, ${item.y})${item.matched_target_id ? ' → ' + esc(item.matched_target_id) : ''}</div>
          ${item.reason ? `<div class="hint">${esc(item.reason)}</div>` : ''}
        </li>`).join('')
      : '<li class="hint">该题没有提交记录。</li>';
  }

  async function loadDetail() {
    const assessmentId = $('#dAssessment').value;
    const questionId = $('#dQuestion').value;
    if (!assessmentId) { toast('请填写测试ID'); return; }
    const data = await call(
      `/api/v1/admin/assessments/${assessmentId}/questions/${questionId}/annotation`);
    state.detailMode = 'one';
    $('#dStat').textContent =
      `${data.contestant_code} ${data.display_name} · 验证码 ${data.code} · ` +
      `题库 ${data.bank_version} · 得分 ${data.score ?? '—'} / ${data.score_max}`;
    $('#dStat').innerHTML = esc($('#dStat').textContent).replace(
      `得分 ${data.score ?? '—'} / ${data.score_max}`,
      `得分 ${scoreMarkup(data.score, data.score_max)} / ${data.score_max}`);
    $('#dOne').hidden = false;
    $('#dAll').hidden = true;
    $('#dStage').innerHTML = annotationStage(data);
    $('#dPointsList').innerHTML = annotationPoints(data);
    applyZoom();
  }

  // The whole first module at once: one request, every annotation question of
  // the module stacked up, so the reader does not type a question id four times.
  async function loadAll() {
    const assessmentId = $('#dAssessment').value;
    if (!assessmentId) { toast('请填写测试ID'); return; }
    const data = await call(`/api/v1/admin/assessments/${assessmentId}/annotations`);
    state.detailMode = 'all';
    const hits = data.questions.reduce(
      (sum, item) => sum + item.submitted.filter((point) => point.score).length, 0);
    const points = data.questions.reduce((sum, item) => sum + item.submitted.length, 0);
    const score = data.questions.reduce((sum, item) => sum + (item.score ?? 0), 0);
    const max = data.questions.reduce((sum, item) => sum + item.score_max, 0);
    $('#dStat').textContent =
      `${data.contestant_code} ${data.display_name} · 验证码 ${data.code} · ` +
      `题库 ${data.bank_version} · ${data.module.name || data.module.id} ` +
      `${data.questions.length} 题 · 命中 ${hits}/${points} · 得分 ${score} / ${max}`;
    $('#dOne').hidden = true;
    $('#dAll').hidden = false;
    $('#dAll').innerHTML = data.questions.map((item) => `
      <div class="annocard">
        <div class="cardhead">
          <span class="qid">${esc(item.question.id)}</span>
          <span>得分 ${scoreMarkup(item.score, item.score_max)} / ${item.score_max}</span>
          <span class="hint">命中 ${item.submitted.filter((point) => point.score).length}/${item.submitted.length}</span>
          ${item.answered ? '' : '<span class="hint">未作答</span>'}
        </div>
        <div class="detail">
          <div class="stagewrap">${annotationStage(item)}</div>
          <div>
            <h3>提交点明细</h3>
            <ul class="pointlist">${annotationPoints(item)}</ul>
          </div>
        </div>
      </div>`).join('');
    applyZoom();
  }

  function redrawDetail() {
    return (state.detailMode === 'all' ? loadAll() : loadDetail()).catch(() => {});
  }

  function openQuestionById(assessmentId, questionId) {
    $('#dAssessment').value = assessmentId;
    $('#dQuestion').value = questionId;
    switchView('detail');
    loadDetail().catch((error) => toast(error.message));
  }

  function openQuestion(data, questionId) {
    openQuestionById(data._id, questionId);
  }

  function openModule(assessmentId) {
    $('#dAssessment').value = assessmentId;
    switchView('detail');
    loadAll().catch((error) => toast(error.message));
  }

  $('#dLoad').addEventListener('click', () => loadDetail().catch((e) => toast(e.message)));
  $('#dLoadAll').addEventListener('click', () => loadAll().catch((e) => toast(e.message)));
  $('#dTargets').addEventListener('change', () => redrawDetail());
  $('#dPoints').addEventListener('change', () => redrawDetail());

  // -------------------------------------------------------------- zoom/pan
  function applyZoom() {
    document.querySelectorAll('#detail .stagewrap').forEach((wrap) => {
      wrap.style.setProperty('--zoom', String(state.zoom));
      wrap.style.setProperty('--panx', `${state.pan.x}px`);
      wrap.style.setProperty('--pany', `${state.pan.y}px`);
    });
    $('#dZoomLabel').textContent = `${Math.round(state.zoom * 100)}%`;
  }

  function setZoom(next) {
    state.zoom = Math.min(6, Math.max(1, Number(next.toFixed(2))));
    if (state.zoom === 1) state.pan = { x: 0, y: 0 };
    applyZoom();
  }

  $('#dZoomIn').addEventListener('click', () => setZoom(state.zoom * 1.25));
  $('#dZoomOut').addEventListener('click', () => setZoom(state.zoom / 1.25));
  $('#dZoomReset').addEventListener('click', () => { state.zoom = 1; state.pan = { x: 0, y: 0 }; applyZoom(); });

  let dragging = null;
  $('#detail').addEventListener('pointerdown', (event) => {
    const wrap = event.target.closest('.stagewrap');
    if (!wrap || state.zoom <= 1) return;
    dragging = { x: event.clientX - state.pan.x, y: event.clientY - state.pan.y };
    wrap.setPointerCapture(event.pointerId);
    wrap.classList.add('grabbing');
  });
  $('#detail').addEventListener('pointermove', (event) => {
    if (!dragging) return;
    state.pan = { x: event.clientX - dragging.x, y: event.clientY - dragging.y };
    applyZoom();
  });
  $('#detail').addEventListener('pointerup', (event) => {
    if (!dragging) return;
    const wrap = event.target.closest('.stagewrap');
    if (wrap) wrap.classList.remove('grabbing');
    dragging = null;
  });

  // ------------------------------------------------------- incremental refresh
  async function poll() {
    try {
      const data = await call('/api/v1/admin/revision');
      $('#status').textContent = `修订号 ${data.revision} · ${new Date().toLocaleTimeString('zh-CN')}`;
      if (data.revision !== state.revision) {
        if (state.view === 'codes') await loadCodes();
        else if (state.view === 'matrix') await loadMatrix();
      }
    } catch (error) {
      $('#status').textContent = `刷新失败：${error.message}`;
    }
  }

  try {
    await call('/api/v1/admin/session');
    showConsole();
  } catch (_) {
    showLogin();
  }
  await poll();
  state.poll = setInterval(poll, 1000);
})().catch((error) => {
  const box = document.querySelector('#msg');
  if (box) { box.textContent = error.message; box.className = 'msg error'; box.style.display = 'block'; }
});
