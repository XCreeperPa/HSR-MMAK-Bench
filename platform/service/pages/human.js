/* Human test flow: authentication, survey, answering and the completion receipt.
   The page never receives a score, a correct option or a reference answer. */
(async () => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  async function call(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      headers: options.body ? { 'Content-Type': 'application/json' } : {},
      ...options,
    });
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) {
      const error = new Error(data.message || `请求失败（${response.status}）`);
      error.status = response.status;
      error.code = data.code;
      throw error;
    }
    return data;
  }

  function showMessage(id, text, kind = 'error') {
    const box = $(id) || $('#pageMsg');
    if (!box) return;
    box.textContent = text;
    // An empty message must not leave an empty coloured box on the page.
    box.className = text ? `msg ${kind}` : 'msg';
  }

  let draftGeneration = '0';
  function draftKey(assessmentId) { return `hsr-draft-${draftGeneration}-${assessmentId}`; }
  function loadDraft(assessmentId, questionId) {
    try {
      const all = JSON.parse(localStorage.getItem(draftKey(assessmentId)) || '{}');
      return all[questionId] || null;
    } catch (_) { return null; }
  }
  function saveDraft(assessmentId, questionId, value) {
    try {
      const key = draftKey(assessmentId);
      const all = JSON.parse(localStorage.getItem(key) || '{}');
      all[questionId] = value;
      localStorage.setItem(key, JSON.stringify(all));
    } catch (_) { /* a private mode browser may refuse to store */ }
  }
  function clearDraft(assessmentId, questionId) {
    try {
      const key = draftKey(assessmentId);
      const all = JSON.parse(localStorage.getItem(key) || '{}');
      delete all[questionId];
      localStorage.setItem(key, JSON.stringify(all));
    } catch (_) { /* ignore */ }
  }

  const page = document.body.dataset.page;

  // ------------------------------------------------------------------ login
  if (page === 'login') {
    const form = $('#codeForm');
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const code = $('#code').value.trim();
      if (!/^[0-9]{6}$/.test(code)) {
        showMessage('#msg', '请输入六位数字身份验证码。');
        return;
      }
      const button = $('#enter');
      button.disabled = true;
      try {
        const data = await call('/api/v1/session', { method: 'POST', body: JSON.stringify({ code }) });
        if (data.contestant_type === 'human' && data.status === 'survey_required') {
          location.href = '/survey';
        } else if (data.status === 'completed') {
          location.href = '/done';
        } else {
          location.href = '/play';
        }
      } catch (error) {
        showMessage('#msg', error.message);
        button.disabled = false;
      }
    });
  }

  // ----------------------------------------------------------------- survey
  if (page === 'survey') {
    const status = await call('/api/v1/session');
    if (status.status === 'completed') { location.href = '/done'; }
    if (status.status !== 'survey_required') { location.href = '/play'; }
    if (status.survey?.active_days_range) $('#activeDays').value = status.survey.active_days_range;
    if (status.survey?.familiarity) $('#familiarity').value = status.survey.familiarity;
    const submit = async (event) => {
      event.preventDefault();
      const activeDays = $('#activeDays').value;
      const familiarity = Number($('#familiarity').value);
      if (!['0-49', '50-199', '200-500', 'over-500'].includes(activeDays)) {
        showMessage('#msg', '请选择活跃天数区间。'); return;
      }
      try {
        await call('/api/v1/survey', {
          method: 'PUT',
          body: JSON.stringify({ active_days_range: activeDays, familiarity }),
        });
        location.href = '/play';
      } catch (error) { showMessage('#msg', error.message); }
    };
    $('#surveyForm').addEventListener('submit', submit);
  }

  // ------------------------------------------------------------------- play
  if (page === 'play') {
    const state = { question: null, assessmentId: null, activeCharacter: null,
                    pendingPoint: null, points: [] };
    let deadline = null;
    let pendingSubmission = null;

    function syncBudget(budget) {
      if (budget && budget.remaining_seconds !== null) deadline = Date.now() + budget.remaining_seconds * 1000;
    }

    function saveCurrentDraft() {
      if (!state.question) return;
      saveDraft(state.assessmentId, state.question.question_id, {
        annotations: state.points.map((p) => ({ ...p })),
        pending_point: state.pendingPoint ? { ...state.pendingPoint } : null,
        selected_options: [...document.querySelectorAll('input[name=answer]:checked')].map((i) => i.value),
        pending_submission: pendingSubmission,
      });
    }

    const header = $('#head');
    // The canvas, the stage and the roster are created while the question is
    // rendered, so they are resolved there and not at load time.
    let stage = null;
    let canvas = null;
    let rosterList = null;
    let placedList = null;
    let rosterSearch = null;

    async function boot() {
      const status = await call('/api/v1/session');
      state.assessmentId = status.assessment_id;
      draftGeneration = status.state_generation || '0';
      syncBudget(status.time_budget);
      if (status.status === 'completed') { location.href = '/done'; return; }
      if (status.status === 'survey_required') { location.href = '/survey'; return; }
      if (status.status === 'issued' || status.status === 'ready') {
        const started = await call('/api/v1/start', { method: 'POST' });
        if (started.done) { location.href = '/done'; return; }
        render(started);
        return;
      }
      const current = await call('/api/v1/current');
      if (current.done) { location.href = '/done'; return; }
      render(current);
    }

    function render(payload) {
      state.question = payload;
      pendingSubmission = null;
      syncBudget(payload.time_budget);
      const question = payload;
      document.body.classList.toggle('annotation-page', question.type === 'point_identification');
      const progress = Math.round(((question.order - 1) / question.question_count) * 100);
      header.innerHTML = `
        <div class="progress">
          <span class="order">第 ${question.order} / ${question.question_count} 题</span>
          <span class="module">${esc(question.module_name || '')}</span>
          <span class="order">${question.score} 分</span>
        </div>
        <div class="meter"><div style="width:${progress}%"></div></div>
        <h2 class="prompt">${esc(question.prompt)}</h2>
        ${question.instructions ? `<p class="instructions">${esc(question.instructions)}</p>` : ''}`;

      const body = $('#body');
      body.innerHTML = '';
      state.points = [];
      state.activeCharacter = null;
      state.pendingPoint = null;

      if (question.type === 'point_identification') {
        buildPicker(question, body);
      } else {
        buildChoice(question, body);
      }
      buildMedia(question, body);
      buildFooter(question, body);
      restoreDraft(question);
      showMessage('#pageMsg', '');
    }

    function buildMedia(question, body) {
      const wrap = document.createElement('div');
      wrap.className = 'media';
      const image = question.assets.find((a) => a.mime.startsWith('image/'));
      const audio = question.assets.find((a) => a.mime.startsWith('audio/'));
      const video = question.assets.find((a) => a.mime.startsWith('video/'));

      if (question.type === 'point_identification' && image && stage && canvas) {
        canvas.hidden = false;
        stage.innerHTML = `<img alt="题图" src="${esc(image.url)}">`;
        const img = stage.querySelector('img');
        img.addEventListener('load', () => { fitStage(); drawPoints(); });
        img.addEventListener('click', onCanvasClick);
        if (img.complete && img.naturalWidth) { fitStage(); drawPoints(); }
      } else if (canvas) {
        canvas.hidden = true;
      }
      if (audio) {
        const element = document.createElement('audio');
        element.controls = true;
        // Metadata only: the clip length is useful for the contestant and the
        // file is validated early, without pulling the whole clip before play.
        element.preload = 'metadata';
        element.src = audio.url;
        wrap.appendChild(element);
      }
      if (video) {
        const element = document.createElement('video');
        element.controls = true;
        element.preload = 'metadata';
        element.playsInline = true;
        element.src = video.url;
        wrap.appendChild(element);
      }
      body.appendChild(wrap);
      if (question.type === 'point_identification') {
        const note = document.createElement('p');
        note.className = 'hint';
        note.textContent = '先点击头部位置，再搜索并选择角色。标注完成后再点下一个位置；提交前可删除或重新标注。';
        body.appendChild(note);
      }
    }

    // ------------------------------------------------------------- point picker
    function buildPicker(question, body) {
      const picker = document.createElement('div');
      picker.className = 'picker';
      picker.innerHTML = `
        <div>
          <div class="canvas" id="canvasBox"><div class="stage" id="stage"></div></div>
        </div>
        <div>
          <label for="rosterSearch">选择角色<span class="sub" id="pointStatus">请先点击题图中的头部位置</span></label>
          <input type="search" id="rosterSearch" placeholder="角色名 / 全拼 / 首字母（如 yl）" autocomplete="off" aria-label="搜索角色" disabled>
          <div class="roster" id="roster"></div>
          <h3>已标注</h3>
          <ul class="placed" id="placed"></ul>
        </div>`;
      body.appendChild(picker);

      canvas = picker.querySelector('#canvasBox');
      stage = picker.querySelector('#stage');
      rosterList = picker.querySelector('#roster');
      placedList = picker.querySelector('#placed');
      rosterSearch = picker.querySelector('#rosterSearch');

      rosterSearch.addEventListener('input', renderRoster);
      // Esc 取消「先点图再选角色」时记下的那个位置
      document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || !state.pendingPoint) return;
        state.pendingPoint = null;
        saveCurrentDraft();
        renderRoster();
        showMessage('#msg', '');
        drawPoints();
      });
      renderRoster();
      renderPlaced();
    }

    function characterName(characterId) {
      const character = (state.question.characters || [])
        .find((item) => item.id === characterId);
      return character ? (character.name || character.id) : characterId;
    }

    function renderRoster() {
      rosterSearch.disabled = !state.pendingPoint;
      $('#pointStatus').textContent = state.pendingPoint
        ? '位置已选，请搜索并选择这个位置的角色'
        : '请先点击题图中的下一个头部位置';
      if (!state.pendingPoint) {
        rosterSearch.value = '';
        rosterList.innerHTML = '<p class="roster-empty">点选位置后，在这里选择角色。</p>';
        return;
      }
      const characters = (state.question.characters || []);
      const normalize = (value) => String(value || '').normalize('NFKC').toLowerCase().replace(/\s+/g, '');
      const needle = normalize(rosterSearch.value);
      const matches = characters.filter((c) =>
        !needle || [c.id, c.name, c.pinyin, c.initials, ...(c.aliases || [])]
          .some((value) => normalize(value).includes(needle)));
      const placedIds = new Set(state.points.map((point) => point.character_id));
      rosterList.innerHTML = matches.map((c) => {
        const done = placedIds.has(c.id);
        const classes = [state.activeCharacter === c.id ? 'active' : '', done ? 'done' : '']
          .filter(Boolean).join(' ');
        return `<button type="button" data-character="${esc(c.id)}" class="${classes}">
          ${esc(c.name || c.id)}${done ? '<span class="tick">已标注</span>' : ''}</button>`;
      }).join('') || '<p class="roster-empty">没有匹配的角色，请试试角色名、全拼或首字母。</p>';
      rosterList.querySelectorAll('[data-character]').forEach((button) => {
        button.addEventListener('click', () => {
          const characterId = button.dataset.character;
          if (state.pendingPoint) {
            placePoint(characterId, state.pendingPoint.x, state.pendingPoint.y);
            state.pendingPoint = null;
            state.activeCharacter = null;
            saveCurrentDraft();
            showMessage('#msg', `已把该位置标注为「${characterName(characterId)}」。`, 'ok');
          }
          renderRoster();
          renderPlaced();
          drawPoints();
        });
      });
    }

    // 一个角色一个位置：给已经标注过的角色再点一次是改位置，
    // 不是再叠一个点。同一角色标两次会被判成重复标记、不得分。
    function placePoint(characterId, x, y) {
      const point = {
        character_id: characterId,
        x: Math.round(x * 100) / 100,
        y: Math.round(y * 100) / 100,
      };
      const index = state.points.findIndex((item) => item.character_id === characterId);
      if (index >= 0) state.points[index] = point;
      else state.points.push(point);
      saveCurrentDraft();
    }

    function fitStage() {
      const img = stage.querySelector('img');
      if (!img || !img.naturalWidth) return;
      const available = canvas.clientWidth || 600;
      const width = available;
      const height = (img.naturalHeight / img.naturalWidth) * width;
      stage.style.width = `${width}px`;
      stage.style.height = `${height}px`;
      drawPoints();
    }

    function onCanvasClick(event) {
      const question = state.question;
      if (!question) return;
      const img = stage.querySelector('img');
      const rect = img.getBoundingClientRect();
      const x = (event.clientX - rect.left) * (img.naturalWidth / rect.width);
      const y = (event.clientY - rect.top) * (img.naturalHeight / rect.height);
      if (x < 0 || y < 0 || x > question.image_width || y > question.image_height) {
        showMessage('#msg', '点击位置超出题图范围。');
        return;
      }
      if (state.pendingPoint) {
        showMessage('#msg', '这个位置还没选择角色。请先选择角色，或按 Esc 取消这个点。');
        rosterSearch.focus();
        return;
      }
      state.activeCharacter = null;
      state.pendingPoint = { x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100 };
      rosterSearch.value = '';
      saveCurrentDraft();
      renderRoster();
      rosterSearch.focus();
      showMessage('#msg', '位置已选，请选择对应角色。按 Esc 可取消这个点。', 'ok');
      drawPoints();
    }

    function renderPlaced() {
      const characters = state.question.characters || [];
      const names = new Map(characters.map((c) => [c.id, c.name || c.id]));
      placedList.innerHTML = state.points.map((point, index) =>
        `<li><span>${index + 1}. ${esc(names.get(point.character_id) || point.character_id)}
          <span class="hint">(${point.x}, ${point.y})</span></span>
          <button type="button" class="rm" data-remove="${index}">删除</button></li>`).join('');
      placedList.querySelectorAll('[data-remove]').forEach((button) => {
        button.addEventListener('click', () => {
          state.points.splice(Number(button.dataset.remove), 1);
          saveCurrentDraft();
          renderPlaced();
          renderRoster();
        });
      });
      drawPoints();
    }

    function drawPoints() {
      stage.querySelectorAll('.pin').forEach((pin) => pin.remove());
      const img = stage.querySelector('img');
      if (!img || !img.naturalWidth) return;
      const rect = img.getBoundingClientRect();
      const width = rect.width / img.naturalWidth;
      const height = rect.height / img.naturalHeight;
      state.points.forEach((point, index) => {
        const pin = document.createElement('div');
        pin.className = 'pin';
        pin.style.left = `${point.x * width}px`;
        pin.style.top = `${point.y * height}px`;
        pin.textContent = String(index + 1);
        pin.title = `${index + 1}. ${characterName(point.character_id)} (${point.x}, ${point.y})`;
        stage.appendChild(pin);
      });
      // 还没绑到角色上的那个位置：画成虚线环，和已经落定的点区分开
      if (state.pendingPoint) {
        const pin = document.createElement('div');
        pin.className = 'pin pending';
        pin.style.left = `${state.pendingPoint.x * width}px`;
        pin.style.top = `${state.pendingPoint.y * height}px`;
        stage.appendChild(pin);
      }
    }

    // ---------------------------------------------------------------- choice
    function buildChoice(question, body) {
      const list = document.createElement('ul');
      list.className = 'options';
      list.id = 'options';
      question.options.forEach((option) => {
        const image = (option.assets || [])
          .map((id) => (question.assets || []).find((a) => a.asset_id === id))
          .find(Boolean);
        const item = document.createElement('li');
        item.innerHTML = `
          <label class="option" for="opt-${esc(option.id)}">
          <input type="${question.type === 'multi_choice' ? 'checkbox' : 'radio'}" name="answer" value="${esc(option.id)}" id="opt-${esc(option.id)}">
          <span class="letter">${esc(option.id)}</span>
          <span class="text">
            ${option.label ? `<span>${esc(option.label)}</span>` : '<span class="hint">（见下方对应图片）</span>'}
            ${image ? `<img alt="选项 ${esc(option.id)} 对应图像" src="${esc(image.url)}" width="${Number(image.width) || 1920}" height="${Number(image.height) || 1080}" loading="eager">` : ''}
          </span></label>`;
        item.querySelector('input').addEventListener('change', saveCurrentDraft);
        list.appendChild(item);
      });
      body.appendChild(list);
      const clear = document.createElement('button');
      clear.type = 'button';
      clear.className = 'btn quiet';
      clear.textContent = '清空选择';
      clear.addEventListener('click', () => {
        list.querySelectorAll('input').forEach((input) => { input.checked = false; });
        saveCurrentDraft();
      });
      body.appendChild(clear);
    }

    function buildFooter(question, body) {
      const footer = document.createElement('div');
      footer.innerHTML = `
        <div class="btnrow">
          <button type="button" class="btn" id="submit">提交并进入下一题</button>
          <span class="hint">提交后不可修改，也不能回退。</span>
        </div>
        <div class="msg" id="msg"></div>`;
      body.appendChild(footer);
      footer.querySelector('#submit').addEventListener('click', () => submitAnswer(question));
    }

    function restoreDraft(question) {
      const draft = loadDraft(state.assessmentId, question.question_id);
      if (!draft) return;
      pendingSubmission = draft.pending_submission || null;
      if (question.type === 'point_identification' && Array.isArray(draft.annotations)) {
        state.points = draft.annotations;
        state.pendingPoint = draft.pending_point || null;
        renderPlaced();
        renderRoster();
      }
      if (question.type !== 'point_identification' && Array.isArray(draft.selected_options)) {
        draft.selected_options.forEach((value) => {
          const input = [...document.querySelectorAll('input[name=answer]')].find((i) => i.value === value);
          if (input) input.checked = true;
        });
      }
    }

    async function submitAnswer(question) {
      if (question.type === 'point_identification' && state.pendingPoint) {
        showMessage('#msg', '还有一个点没有选择角色，请先补全，或按 Esc 取消这个点。');
        rosterSearch.focus();
        return;
      }
      const button = $('#submit');
      button.disabled = true;
      const payload = {
        question_id: question.question_id,
        submission_id: `${state.assessmentId}-${question.question_id}-${Date.now()}`,
        delivery_id: question.delivery_id,
      };
      if (question.type === 'point_identification') {
        payload.annotations = state.points.map((point) => ({ ...point }));
      } else {
        const selected = [...document.querySelectorAll('input[name=answer]:checked')].map((i) => i.value);
        payload.selected_options = selected;
      }
      // Reuse the exact identifier after a lost response, including after reload.
      const content = JSON.stringify({ ...payload, submission_id: undefined });
      if (pendingSubmission && pendingSubmission.content === content) {
        payload.submission_id = pendingSubmission.id;
      }
      pendingSubmission = { content, id: payload.submission_id };
      saveCurrentDraft();
      try {
        const result = await call('/api/v1/answers', { method: 'POST', body: JSON.stringify(payload) });
        clearDraft(state.assessmentId, question.question_id);
        if (result.done) { location.href = '/done'; return; }
        render(result.next_question);
        window.scrollTo({ top: 0, behavior: 'instant' });
      } catch (error) {
        if (error.status === 408) { location.href = '/done'; return; }
        if (!error.status || error.status === 409) {
          try {
            const current = await call('/api/v1/current');
            if (current.done) { location.href = '/done'; return; }
            if (current.question_id !== question.question_id) {
              clearDraft(state.assessmentId, question.question_id);
              render(current);
              showMessage('#msg', '进度已恢复，上一题已成功接收。', 'ok');
              return;
            }
          } catch (_) { /* retain the draft and allow an exact retry */ }
        }
        showMessage('#msg', error.message);
        button.disabled = false;
      }
    }

    boot().catch((error) => showMessage('#msg', `无法载入题目：${error.message}`));
    window.addEventListener('resize', () => { if (stage && state.question?.type === 'point_identification') fitStage(); });
    setInterval(() => {
      if (deadline === null) return;
      const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      $('#timeRemaining').textContent = `剩余 ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    }, 1000);
    setInterval(async () => {
      if (!state.assessmentId) return;
      try {
        const status = await call('/api/v1/session');
        syncBudget(status.time_budget);
        if (status.status === 'completed') location.href = '/done';
      } catch (error) { showMessage('#msg', `状态同步失败：${error.message}。草稿已保留，可重新输入验证码恢复。`); }
    }, 15000);
  }

  // ------------------------------------------------------------------- done
  if (page === 'done') {
    call('/api/v1/session').then((status) => {
      $('#receipt').innerHTML = `
        <dl>
          <dt>测试编号</dt><dd>${esc(status.assessment_id)}</dd>
          <dt>状态</dt><dd>${esc(status.status)}</dd>
          <dt>已提交题目</dt><dd>${esc(status.answered_count)} / ${esc(status.question_count)}</dd>
        </dl>`;
      if (status.status === 'completed') {
        $('#doneNote').textContent = '本次测试已提交完成。成绩与详细解析由管理员统一整理后另行公布。';
      } else {
        $('#doneNote').textContent = '本次测试仍在进行中。你可以重新用身份验证码进入并继续作答。';
      }
    }).catch((error) => {
      $('#doneNote').textContent = `无法读取提交状态：${error.message}`;
    });
  }
})().catch((error) => {
  const box = document.querySelector('#msg');
  if (box) { box.textContent = error.message; box.className = 'msg error'; }
});
