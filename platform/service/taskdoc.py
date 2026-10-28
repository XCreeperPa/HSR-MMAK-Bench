"""The public task document served at ``/task``.

The document has to be self sufficient: an agent that only receives the
six digit code and this URL must be able to complete the whole test. It
therefore spells out the API root, every contestant endpoint, the exact field
names, the request and response shapes, the media rules, the scoring rules,
the retry semantics and the completion condition.

It never contains an answer, a reference answer, a source citation or an
admin route.
"""
from __future__ import annotations

import json
from html import escape

from .bank import build_snapshot
from .config import settings

DOC_VERSION = settings().task_document_version


def _base_url(request) -> str:
    configured = settings().public_base_url
    if configured:
        return configured
    return str(request.base_url).rstrip('/')


def task_document_html(request) -> str:
    base = _base_url(request)
    snapshot = build_snapshot()
    questions = snapshot.public['questions']
    point_questions = [q for q in questions if q['type'] == 'point_identification']
    choice_questions = [q for q in questions if q['type'] != 'point_identification']
    roster = snapshot.public['characters']['characters']
    example_question = point_questions[0] if point_questions else questions[0]
    example_choice = choice_questions[0] if choice_questions else questions[0]

    roster_rows = ''.join(
        f'<tr><td>{escape(c["id"])}</td><td>{escape(c.get("name", ""))}</td>'
        f'<td>{escape(c.get("pinyin", ""))}</td></tr>'
        for c in roster
    )

    # The outline of the test: modules, questions and scores. It is derived
    # from the frozen bank snapshot, so it can never drift from the real test.
    numerals = ['一', '二', '三', '四', '五', '六', '七', '八']
    type_labels = {'point_identification': '图片标注', 'single_choice': '单选',
                   'multi_choice': '多选'}
    media_hints = {'1': '静态截图（标注角色头部）', '3': '四张候选图'}
    module_rows = ''
    question_rows = ''
    for index, module in enumerate(snapshot.public.get('modules', [])):
        number = str(module.get('id', '')).split('_')[-1]
        members = [q for q in questions if str(q.get('module')) == number]
        numeral = numerals[index] if index < len(numerals) else str(index + 1)
        label = f'{numeral} {module.get("name", "")}'
        per_question = ' / '.join(str(q['score']) for q in members) \
            or str(module.get('score_per_question', '—'))
        scores = {q['score'] for q in members}
        if len(scores) == 1:
            # Every question of the module is worth the same; say it once.
            per_question = str(next(iter(scores)))
        elif not members:
            per_question = str(module.get('score_per_question', '—'))
        media = media_hints.get(number, str(module.get('media', '') or '—'))
        module_rows += (
            f'<tr><td>{escape(label)}</td><td>{len(members)}</td><td>{escape(per_question)}</td>'
            f'<td>{int(module.get("score_max", 0))}</td>'
            f'<td>{escape(type_labels.get(str(module.get("question_type")), "—"))}</td>'
            f'<td>{escape(media)}</td></tr>'
        )
        for question in members:
            question_rows += (
                f'<tr><td>{escape(str(question["id"]))}</td><td>{escape(label)}</td>'
                f'<td>{escape(type_labels.get(str(question["type"]), str(question["type"])))}</td>'
                f'<td>{question["score"]}</td></tr>'
            )
    module_count = len(snapshot.public.get('modules', []))
    point_count = len(point_questions)
    choice_count = len(choice_questions)

    sample_answer = {
        'accepted': True,
        'receipt': {
            'submission_id': 's-unique-001',
            'question_id': example_question['id'],
            'answer_received_at': '2026-10-25T12:00:00.123Z',
        },
        'done': False,
        'next_question': {
            'question_id': questions[1]['id'] if len(questions) > 1 else questions[0]['id'],
            'delivery_id': 'd-unique-002',
            'order': 2,
            'question_count': len(questions),
            'type': questions[1]['type'] if len(questions) > 1 else questions[0]['type'],
            'prompt': '……',
            'score': questions[1]['score'] if len(questions) > 1 else questions[0]['score'],
            'options': [],
            'assets': [],
        },
    }

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HSR-MMAK-Bench 任务文档 · {escape(DOC_VERSION)}</title>
<style>
:root{{color-scheme:light}}
body{{font:16px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
max-width:880px;margin:0 auto;padding:40px 22px 80px;color:#1d232b;background:#fbfbf9}}
h1{{font-size:26px;margin:0 0 6px}} h2{{font-size:20px;margin:38px 0 10px;border-bottom:1px solid #e3e1dc;padding-bottom:6px}}
h3{{font-size:16px;margin:22px 0 6px}} p,li{{color:#38414d}}
code,kbd{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:14px;
background:#f1efe9;padding:1px 5px;border-radius:4px}}
pre{{background:#f4f2ec;border:1px solid #e3e1dc;border-radius:8px;padding:14px 16px;overflow:auto}}
pre code{{background:none;padding:0;font-size:13px}}
table{{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}}
th,td{{border:1px solid #e3e1dc;padding:6px 9px;text-align:left;vertical-align:top}}
th{{background:#f4f2ec;font-weight:600}}
.lead{{color:#5a6472;font-size:15px;margin-top:0}}
.note{{border-left:3px solid #b8862b;background:#fdf8ee;padding:10px 14px;margin:16px 0}}
</style>
</head>
<body>
<h1>HSR-MMAK-Bench 任务文档</h1>
<p class="lead">版本 {escape(DOC_VERSION)} · 题库 {escape(snapshot.version)} ·
{len(questions)} 题 / {int(snapshot.score_max)} 分 · {module_count} 个模块 ·
本文档只描述接口与规则，不含任何答案。</p>
<div class="note"><strong>你不需要浏览器。</strong>用普通的 HTTP 客户端请求本文档地址即可，
不要打开图形界面，也不要用无头浏览器或截图。</div>

<h2>1. 本次测试大纲</h2>

<h3>1.1 总览</h3>
<table>
<tr><th>项目</th><th>值</th></tr>
<tr><td>满分</td><td>{int(snapshot.score_max)} 分</td></tr>
<tr><td>题量</td><td>{len(questions)} 题</td></tr>
<tr><td>模块</td><td>{module_count} 个</td></tr>
<tr><td>题型</td><td>图片标注 {point_count} 题 + 单选 {choice_count} 题</td></tr>
<tr><td>全场时长</td><td>{settings().time_budget_seconds // 60} 分钟（统一预算，从第一次有效 <code>start</code> 起算）</td></tr>
<tr><td>单题时长</td><td><strong>无单题时限</strong>，只有全场统一预算</td></tr>
<tr><td>提交方式</td><td>顺序作答，提交当前题即得到下一题</td></tr>
</table>

<h3>1.2 模块结构</h3>
<table>
<tr><th>模块</th><th>题数</th><th>每题分值</th><th>模块满分</th><th>题型</th><th>素材</th></tr>
{module_rows}
<tr><td><strong>合计</strong></td><td><strong>{len(questions)}</strong></td>
<td>—</td><td><strong>{int(snapshot.score_max)}</strong></td><td>—</td><td>—</td></tr>
</table>

<h3>1.3 逐题分值</h3>
<table>
<tr><th>题号</th><th>模块</th><th>题型</th><th>分值</th></tr>
{question_rows}
<tr><td><strong>合计</strong></td><td>—</td><td>—</td><td><strong>{int(snapshot.score_max)}</strong></td></tr>
</table>

<h3>1.4 作答限制</h3>
<div class="note"><strong>禁止使用任何本地或在线资料。</strong>
唯一允许的信息来源是：本文档本身、通过下方 API 端点取得的题目与素材，以及你自己的判断。</div>
<ul>
<li>禁止联网检索、网页浏览、搜索、在线问答，或调用任何在线 API 去获取答案。</li>
<li>禁止读取、引用或依赖任何本地文件、缓存、历史记录、记忆库或既有资料库中的现成答案。</li>
<li>禁止调用外部工具、外部服务或插件来完成题目。</li>
<li><strong>禁止启用多智能体</strong>：不得把题目、素材或草稿分派给其他智能体、子代理或协作角色，
  不得与其他智能体协商、投票或合并答案。你一个人作答。</li>
<li>禁止把题目、素材、验证码或令牌转发给其他模型、平台或人。</li>
</ul>
<p>允许你使用自己的推理与既有认知；上面禁止的是在本次测试过程中<strong>去获取外部现成答案</strong>。
违反上述限制的测试由管理员在后台打管理标记并排除出统计。</p>

<h3>1.5 安全规范</h3>
<ul>
<li>身份验证码与令牌只用于本次测试；不要写进日志、截图、URL 查询串或任何第三方服务。</li>
<li>不要把验证码或令牌分享给任何人，也不要向管理员索取第二个凭据。</li>
<li>只访问本文档列出的地址：不要猜测路径，不要访问未发题的资源，不要访问管理端路由。</li>
<li>下载到的素材只用于本次作答，不再分发、不外传。</li>
<li>提交后不可修改；网络错误时用同一个 <code>submission_id</code> 与相同内容重试。</li>
<li>不要尝试修改服务、数据库或题库。</li>
</ul>

<h3>1.6 API 端点</h3>
<p>接口前缀 <code>/api/v1</code>，根地址 <code>{escape(base)}</code>。标「是」的行都要带上
<code>Authorization: Bearer &lt;credential.token&gt;</code>。字段、示例与重试语义见第 3—7 节。</p>
<table>
<tr><th>方法</th><th>路径</th><th>用途</th><th>令牌</th></tr>
<tr><td>POST</td><td>/api/v1/session</td><td>用六位码登录，取回令牌与当前状态</td><td>否</td></tr>
<tr><td>GET</td><td>/api/v1/session</td><td>查询状态、已答题数与时间预算</td><td>是</td></tr>
<tr><td>POST</td><td>/api/v1/start</td><td>开始测试并取第 1 题</td><td>是</td></tr>
<tr><td>GET</td><td>/api/v1/current</td><td>恢复当前题与原 delivery_id</td><td>是</td></tr>
<tr><td>GET</td><td>/api/v1/assets/{{asset_id}}</td><td>下载本题素材，支持 Range</td><td>是</td></tr>
<tr><td>POST</td><td>/api/v1/events/materials-ready</td><td>报告本题材料已可用（可选）</td><td>是</td></tr>
<tr><td>POST</td><td>/api/v1/answers</td><td>提交答案，响应里带下一题</td><td>是</td></tr>
<tr><td>GET</td><td>/task</td><td>本文档</td><td>否</td></tr>
</table>

<h2>2. 你需要准备什么</h2>
<ul>
<li>一个六位数字身份验证码（由管理员发放，允许前导零，例如 <code>012345</code>）。</li>
<li>能够发起 HTTP 请求并处理 JSON 的工具。</li>
<li>能够下载并查看图片、播放音频与视频的能力。</li>
</ul>
<div class="note">身份验证码与本文档地址就是全部输入。不要注册账号，不要设置密码，不要向管理员索取第二个凭据。</div>

<h2>3. 接口根地址与登录</h2>
<p>所有接口都在同一个根地址下：<code>{escape(base)}</code>。接口前缀为 <code>/api/v1</code>。</p>
<h3>3.1 登录</h3>
<pre><code>POST {escape(base)}/api/v1/session
Content-Type: application/json

{{"code": "012345"}}</code></pre>
<p>成功返回（节选）：</p>
<pre><code>{{
  "accepted": true,
  "assessment_id": 42,
  "contestant_type": "agent",
  "status": "ready",
  "question_count": {len(questions)},
  "answered_count": 0,
  "survey_required": false,
  "time_budget": {{"budget_seconds": {settings().time_budget_seconds}, "remaining_seconds": {settings().time_budget_seconds}, "expired": false}},
  "credential": {{"type": "bearer", "token": "……", "expires_in": {settings().session_ttl_seconds}}},
  "task_document": {{"version": "{escape(DOC_VERSION)}", "url": "{escape(base)}/task"}}
}}</code></pre>
<p>之后每个请求都要带上登录返回的令牌：</p>
<pre><code>Authorization: Bearer &lt;credential.token&gt;</code></pre>
<p>令牌有效期 {settings().session_ttl_seconds} 秒。令牌过期后重新用同一个验证码登录即可，
服务器会恢复原来的测试进度，<strong>不会重新发题，也不会重置时间</strong>。</p>
<p>连续多次输错验证码会被限速，返回 <code>429</code>。登录错误返回 <code>401</code>。</p>

<h3>3.2 查询当前状态</h3>
<pre><code>GET {escape(base)}/api/v1/session
Authorization: Bearer &lt;credential.token&gt;</code></pre>
<p>返回当前状态、已答题数、时间预算与问卷要求。它不会发放新题，不会改变进度，也不会重置时间。
换设备或重新登录之后，可以用它确认自己走到哪一步了。</p>

<h2>4. 作答流程</h2>
<pre><code>登录 → POST /api/v1/start（拿到第 1 题）
    → GET /api/v1/assets/{{asset_id}}（下载本题媒体）
    → POST /api/v1/answers（提交第 1 题，响应里直接带第 2 题）
    → …… 重复 ……
    → 提交第 {len(questions)} 题：响应 done=true，给出完成回执</code></pre>
<p>题目严格按顺序发放。提交当前题之后，响应体里会直接包含下一题，不需要再取一次题。
在提交之前，服务端不会提供任何后续题目。</p>

<h3>4.1 开始</h3>
<pre><code>POST {escape(base)}/api/v1/start</code></pre>
<p>重复调用不会产生第二次开始，只会恢复当前题。智能体不需要填问卷；人类测试者需要先提交
<code>PUT {escape(base)}/api/v1/survey</code>，请求体为
<code>{{"active_days_range": "50-199", "familiarity": 4}}</code>（<code>active_days_range</code>
为字符串，四个可选值为 <code>0-49</code>、<code>50-199</code>、<code>200-500</code>、<code>over-500</code>，
分别对应 0–49 天、50–199 天、200–500 天、500 天以上；<code>familiarity</code> 为 1—5 的整数）。智能体调用问卷接口会返回 <code>409</code>，
因此智能体不要调用它。</p>

<h3>4.2 恢复当前题</h3>
<pre><code>GET {escape(base)}/api/v1/current</code></pre>
<p>返回当前题与最初的 <code>delivery_id</code>。刷新页面、换设备或重新登录之后用这个接口恢复。
首次必须先调用 <code>start</code> 开始计时；未开始时取题或下载素材返回
<code>409 assessment_not_started</code>。超时后的首次状态检查会结束试次；
取题、开始或新提交返回 <code>408</code>，随后恢复接口返回 <code>done=true</code>。
已成功提交的同内容重试仍可获取回执。题目响应中的 <code>time_budget</code> 给出剩余秒数。</p>

<h2>5. 题目响应</h2>
<pre><code>{{
  "question_id": "{example_question['id']}",
  "delivery_id": "d-unique-001",
  "order": 1,
  "question_count": {len(questions)},
  "module": {example_question.get('module')},
  "type": "{example_question['type']}",
  "prompt": "……",
  "score": {example_question['score']},
  "options": [{{"id": "A", "label": "……", "content_id": "……", "assets": []}}],
  "assets": [{{"asset_id": "a0001", "mime": "image/webp", "bytes": 375458,
               "sha256": "……", "url": "{escape(base)}/api/v1/assets/a0001",
               "width": 1920, "height": 1080}}]
}}</code></pre>
<p>标注题额外带 <code>characters</code>（可用角色名单）、<code>image_width</code>、
<code>image_height</code> 与 <code>coordinate_space</code>。</p>
<div class="note">媒体 URL 必须使用响应里给出的地址，不要按编号推断内容，也不要访问未发题的资源。
题目响应里不包含正确选项、答案数量、判定区域或任何来源信息。</div>

<h2>6. 媒体下载</h2>
<ul>
<li>每个媒体条目给出 <code>url</code>、<code>mime</code>、<code>bytes</code>、<code>sha256</code>；图片还给出原始宽高。</li>
<li>媒体下载同样要带上 <code>Authorization: Bearer &lt;credential.token&gt;</code>；不带令牌的请求返回 <code>401</code>。</li>
<li>下载后请校验 <code>sha256</code>；不一致说明文件不完整或被改动。</li>
<li>支持 HTTP Range，可以分段下载；服务端会合并成功传输的区间来判断整份素材是否送达。</li>
<li>同一份素材可以重复请求、并行请求；重复请求不会重置任何计时。</li>
<li>可以选地报告"本题材料已可用"，接口如下。这只是一个客户端声明，服务端不会把它当作不可伪造的思考起点。</li>
</ul>
<pre><code>POST {escape(base)}/api/v1/events/materials-ready
Content-Type: application/json
Authorization: Bearer &lt;credential.token&gt;

{{"question_id": "{example_question['id']}", "delivery_id": "d-unique-001"}}</code></pre>
<p>成功返回 <code>{{"accepted": true, "received_at": "2026-10-25T12:00:00.123Z"}}</code>。
<code>delivery_id</code> 可以省略；给了就必须与当前题一致，否则返回 <code>409</code>。
同一题重复上报不会覆盖第一次上报的时刻。</p>
<p>音频为各地区原声音乐的前 10 秒，视频为官方 PV 节选（保留原音轨与字幕）。</p>

<h2>7. 提交答案</h2>
<pre><code>POST {escape(base)}/api/v1/answers
Content-Type: application/json

{{
  "question_id": "{example_question['id']}",
  "submission_id": "s-unique-001",
  "delivery_id": "d-unique-001",
  "selected_options": ["A"],
  "reasons": {{"A": "……", "B": "……", "C": "……", "D": "……"}}
}}</code></pre>
<p>标注题改为提交 <code>annotations</code>：</p>
<pre><code>{{
  "question_id": "{example_question['id']}",
  "submission_id": "s-unique-002",
  "delivery_id": "d-unique-001",
  "annotations": [
    {{"character_id": "角色ID", "x": 288.0, "y": 398.5, "reason": "头部与服饰识别依据"}}
  ]
}}</code></pre>
<ul>
<li><code>x</code>/<code>y</code> 是原图像素坐标，左上角为原点，范围 0—<code>image_width</code>/<code>image_height</code>。</li>
<li>选择题只能选 0 或 1 项；提交多于 1 项返回 <code>422</code>，服务端不会替你截断成第一项。</li>
<li>空的 <code>selected_options</code> 或空的 <code>annotations</code> 表示明确放弃，记 0 分。</li>
<li>理由：选择题的 A、B、C、D 都要写简短理由，每条不超过 10000 字符；
  <strong>第四道标注题</strong>的每条标注都要写识别与定位依据，前三道标注题写了也会被接受。</li>
<li>标注题只提交 <code>annotations</code>，不要提交 <code>selected_options</code>；选择题只提交
  <code>selected_options</code>，不要提交 <code>annotations</code>。两类字段同时提交会返回 <code>422</code>。</li>
<li>提交后不可修改，也不可回退到上一题。</li>
</ul>
<p>成功响应：</p>
<pre><code>{json.dumps(sample_answer, ensure_ascii=False, indent=2)}</code></pre>

<h2>8. 计分</h2>
<ul>
<li><strong>图片标注</strong>：每位可操控角色 1 分，姓名与头部位置同时正确才得分，不归一化，同一目标不重复计分。</li>
<li><strong>音乐辨识 / 图片综合 / 视频综合</strong>：单选，选对得该题满分，选错得 0 分。</li>
<li>满分合计 {int(snapshot.score_max)} 分。理由只用于研究审查，不额外计分。</li>
</ul>

<h2>9. 幂等与重试</h2>
<ul>
<li>每次提交都要带唯一的 <code>submission_id</code>。</li>
<li>网络超时后用<strong>同一个</strong> <code>submission_id</code> 和<strong>相同内容</strong>重试，
  会返回原提交回执与同一道下一题，不会重复计分、不会覆盖原答案、不会重置下一题的时间。</li>
<li>同一个 <code>submission_id</code> 配不同内容返回 <code>409</code>。</li>
<li>对已经提交过的题换一个 <code>submission_id</code> 再交，返回 <code>409</code>。</li>
<li>不要把网络错误当成"提交失败"而换一个 ID 重复交卷。</li>
</ul>

<h2>10. 时长预算与计时</h2>
<p>全场统一时长预算 <strong>{settings().time_budget_seconds // 60} 分钟</strong>，从第一次有效
<code>start</code> 开始计算。<strong>没有单题时限</strong>，时间只在全场层面统一。
超过预算后 <code>POST /api/v1/answers</code> 返回 <code>408</code>，
不再接受新的提交，测试以已提交的内容结束。关闭页面、切换后台、断网、重新登录都不会暂停或重置计时。</p>
<p>计时以服务器时刻为准：服务端记录发题、媒体传输、答案接收与入库的原始时刻，
用时按所选口径从这些原始记录派生。请不要自行上报耗时字段。</p>

<h2>11. 错误码</h2>
<table><tr><th>状态码</th><th>code</th><th>含义</th></tr>
<tr><td>401</td><td>unauthorized</td><td>未登录、验证码错误或令牌失效</td></tr>
<tr><td>403</td><td>forbidden</td><td>测试已作废，或没有该素材的权限</td></tr>
<tr><td>408</td><td>time_budget_exceeded</td><td>超过全场时长预算</td></tr>
<tr><td>409</td><td>conflict 系列</td><td>题目顺序冲突、重复提交、submission_id 内容不一致</td></tr>
<tr><td>422</td><td>invalid_request</td><td>字段格式错误（选项超量、坐标越界、角色不在名单、理由缺失等）</td></tr>
<tr><td>429</td><td>rate_limited</td><td>请求过于频繁，请退避后重试</td></tr>
</table>
<p>失败不会推进题目。遇到 5xx 或网络中断时，用同一 <code>submission_id</code> 重试即可。</p>

<h2>12. 怎么算完成</h2>
<p>提交最后一题后，响应里 <code>done</code> 为 <code>true</code> 且 <code>next_question</code> 为
<code>null</code>，同时给出最终回执。此时不要再提交任何答案。整个测试结束后旧验证码立即失效，
管理员会为你自动准备下一场的验证码。</p>

<h2>13. 角色名单（标注题使用）</h2>
<p>共 {len(roster)} 个可用角色。<code>character_id</code> 直接使用下表第一列。</p>
<table><tr><th>character_id</th><th>名称</th><th>拼音</th></tr>
{roster_rows}
</table>
</body>
</html>"""
