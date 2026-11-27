const assert = require('node:assert/strict')
const { test } = require('node:test')

test('project routing sees Bot duties and verifies a reused task before skipping delegation', async () => {
  const { registerProjectLeader } = await import('../plugins/dsh-labweave-workspace/host/project-leader.js')
  const { createResearchIntake } = await import('../plugins/dsh-labweave-workspace/host/research-intake.js')
  const row = { scope: 'project', features: [
    { id: 'evidence', label: '证据核对', paradigm: 'notes', hint: '核对论文原文',
      assignments: [{ title: '追踪评估协议' }] },
    { id: 'experiments', label: '实验工程台', paradigm: 'engineering', hint: '维护基线运行' },
  ] }
  const workspace = { id: 'project-workspace', path: '/project' }
  const pending = { id: 'existing', featureId: 'evidence', title: '核对评估协议', status: 'running' }
  const hooks = new Map()
  let guard, modelInput, instructionBot
  let coveredBy = pending.id
  const ctx = {
    on: (name, fn) => hooks.set(name, fn),
    tools: { guard: fn => { guard = fn }, register() {} },
    llm: { async *stream(request) {
      modelInput = JSON.parse(request.messages[0].content[0].text)
      yield { type: 'text-delta', text: JSON.stringify({
        mode: 'MANAGE', research: false, workflow: 'within_scope', missing: [], coveredBy,
      }) }
    } },
  }
  const call = async (name, data) => {
    assert.equal(data.workspace.path, workspace.path)
    if (name === 'tool') return { text: JSON.stringify({ name: '项目' }) }
    if (data.action === 'bot_dispatches') return { tasks: [pending] }
    assert.equal(data.action, 'input')
  }
  const agent = { session: { id: 'project-leader', header: { id: 'project-leader' }, deriveMessages: () => [] } }
  const state = registerProjectLeader(ctx, {
    caller: () => ({ row, workspace }), call, output: {},
    handoff: { match: () => ({ task: '核对评估协议', botId: instructionBot, handoffId: 'handoff' }) },
    intake: createResearchIntake(ctx, call),
  })
  const turn = id => {
    const input = { id: String(id), source: { kind: 'user' }, content: [{ type: 'text', text: '核对评估协议' }] }
    return hooks.get('agent/pre-step')({ agent, turn: id, step: 1, messages: [input], modelSelection: {},
      signal: new AbortController().signal }, async () => ({ messages: [input] }))
  }
  const reused = await turn(1)
  assert.equal(modelInput.forceExecution, true)
  assert.equal(modelInput.routing[0].duties, '核对论文原文')
  assert.deepEqual(modelInput.routing[0].responsibilities, ['追踪评估协议'])
  assert.equal(modelInput.routing[0].tasks[0].status, 'running')
  assert.equal(state(agent).mode, 'MANAGE')
  assert.equal(state(agent).decision.coveredBy, pending.id)
  assert.ok(reused.messages.some(message => message.source.kind === 'labweave-bot-reuse'))
  assert.match(guard({ agent, name: 'labweave_bot_delegate' }), /避免重复分派/)

  coveredBy = 'unknown-task'
  const execution = await turn(2)
  assert.equal(state(agent).mode, 'EXECUTE')
  const routing = execution.messages.find(message => message.source.kind === 'labweave-work-route').content[0].text
  assert.match(routing, /continuation of the same deliverable/)
  assert.match(routing, /open the native ask_user_question card in this project main conversation/)
  assert.equal(guard({ agent, name: 'ask_user_question' }), undefined)
  assert.equal(guard({ agent, name: 'labweave_bot_delegate' }), undefined)

  coveredBy = pending.id
  instructionBot = 'evidence'
  await turn(3)
  assert.equal(modelInput.reuseExisting, false)
  assert.equal(state(agent).mode, 'EXECUTE')
  assert.equal(state(agent).requestedBotId, instructionBot)
})

test('example-first chats keep the example in the current conversation and release the scope on the next turn', async () => {
  const { registerProjectLeader } = await import('../plugins/dsh-labweave-workspace/host/project-leader.js')
  const { createResearchIntake } = await import('../plugins/dsh-labweave-workspace/host/research-intake.js')
  for (const role of ['global', 'project', 'bot']) {
    const row = { scope: role === 'global' ? 'global' : 'project', botSessionIds: ['bot'],
      features: [{ id: 'notes', label: '笔记', paradigm: 'notes', botSessionId: 'bot' }] }
    const workspace = { id: row.scope, path: '/' + row.scope }
    const history = [], hooks = new Map(), actions = []
    const agent = { session: { id: role, header: { id: role }, deriveMessages: () => history } }
    let decision, question, guard, progressTool
    const ctx = {
      on: (name, fn) => hooks.set(name, fn),
      tools: { guard: fn => { guard = fn }, register(tool) { if (tool.name === 'labweave_project_progress') progressTool = tool } },
      llm: { async *stream(request) {
        if (request.purpose === 'weave-text-reply') {
          assert.deepEqual(request.tools, [])
          assert.equal(request.messages[0].content[0].text, '什么是置信区间？')
          yield { type: 'text-delta', text: '定义答复' }
          return
        }
        question = JSON.parse(request.messages[0].content[0].text)
        yield { type: 'text-delta', text: JSON.stringify({ workflow: 'within_scope', missing: [], ...decision }) }
      } },
    }
    const call = async (name, data) => {
      actions.push(data.action || data.tool)
      if (name === 'tool') return { text: '{}' }
      if (data.action === 'saved-review') return [{ bot: '独立复核', status: 'completed', id: 'record', title: '复核', summary: '81.25%', verified: true, sources: [] }]
      if (data.action === 'input') return { hasPinnedRules: false }
      if (data.action === 'bot_dispatches') return { tasks: [] }
      if (data.action === 'read') return { events: [] }
    }
    const state = registerProjectLeader(ctx, { caller: () => ({ row, workspace }), call,
      output: {}, handoff: { match() {} }, intake: createResearchIntake(ctx, call) })
    const turn = (id, text, source = 'user') => {
      const message = { id: String(id), role: 'user', source: { kind: source }, content: [{ type: 'text', text }] }
      return hooks.get('agent/pre-step')({ agent, turn: id, step: 1, messages: [message], modelSelection: {},
        signal: new AbortController().signal }, async () => ({ messages: [message] }))
    }
    decision = { mode: 'MANAGE', research: true, reply: 'example', workflow: 'confirm', missing: ['workflow_direction'] }
    const result = await turn(1, '实验结果表怎么排？')
    assert.deepEqual(result.messages.map(item => item.source.kind), ['user', 'labweave-example-first'], role)
    assert.match(result.messages[1].content[0].text, /同一条回复末尾问一个具体的问题/)
    assert.equal(actions.includes('read'), false)
    for (const name of ['labweave_paper_search', 'labweave_bot_delegate', 'labweave_project_handoff', 'ask_user_question']) {
      assert.match(guard({ agent, name }), /先在本条回复中给出示例/)
    }

    const offer = '要我按你的模板整理所有任务的数据吗？'
    history.push({ role: 'assistant', content: [{ type: 'text', text: '示例内容'.repeat(400) + offer }] })
    decision = { mode: 'EXECUTE', research: true }
    const continued = await turn(2, '继续')
    assert.ok(question.recent.at(-1).text.endsWith(offer))
    assert.equal(state(agent).mode, 'EXECUTE')
    assert.equal(continued.messages.some(item => item.source.kind === 'labweave-example-first'), false)
    assert.equal(guard({ agent, name: 'labweave_bot_delegate' }), undefined)

    decision = { mode: 'EXECUTE', research: true, reply: 'example' }
    const verified = await turn(3, '先从指定论文核实一个样例')
    assert.ok(verified.messages.some(item => item.source.kind === 'labweave-example-first'))
    assert.equal(verified.messages.some(item => item.source.kind === 'labweave-work-route'), role === 'project')
    assert.equal(state(agent).mode, 'EXECUTE')
    assert.equal(guard({ agent, name: 'labweave_paper_read' }), undefined)
    const update = { title: '先核对原文', summary: '按表 2 核对召回率', nextStep: '读取第 2 页' }
    if (role === 'project') {
      assert.equal(guard({ agent, name: 'labweave_project_progress' }), undefined)
      assert.equal(await progressTool.execute(update, { agent }), '已更新项目对话。')
      assert.equal(actions.at(-1), 'coordination')
    } else {
      await assert.rejects(progressTool.execute(update, { agent }), /项目主对话/)
    }

    decision = { mode: 'MANAGE', directAnswer: '80%，即 8 ÷ 10。' }
    await turn(4, '8 个真阳性、2 个假阴性，召回率是多少？')
    assert.equal(Boolean(state(agent).decision.directAnswer), role === 'project')
    if (role === 'project') {
      ctx.agents = { currentInitiator: () => agent }
      const chunks = []
      for await (const chunk of hooks.get('llm/stream')({ sessionId: role }, () => assert.fail('unexpected second model call'))) chunks.push(chunk)
      assert.equal(chunks.find(chunk => chunk.type === 'text-delta').text, decision.directAnswer)
    }
    if (role === 'project') {
      const previous = question
      await turn(5, '什么是置信区间？')
      assert.equal(question, previous)
      assert.equal(state(agent).decision.textOnly, true)
      assert.match(guard({ agent, name: 'labweave_bot_delegate' }), /仅使用对话/)
      const request = Object.freeze({ sessionId: role, tools: [{ name: 'read' }] })
      const stream = hooks.get('llm/stream')(request, () => assert.fail('unexpected tool-enabled generation'))
      const output = []
      for await (const chunk of stream) output.push(chunk.text)
      assert.deepEqual(output, ['定义答复'])
      await turn(6, '请查阅项目记录，再说明置信区间的定义。')
      assert.notEqual(question, previous)
      assert.equal(state(agent).decision.textOnly, undefined)
    }
    if (role === 'project') {
      for (const phrase of ['再验收一次', '请核对本次两位 Bot 的实际分派和保存记录。若仍缺独立复核，请交给「独立复核」完成，再给我最终数值、页码和所引用的原始摘录记录。']) {
        const before = question
        const n = actions.length
        await turn(8, phrase)
        assert.equal(question, before)
        assert.deepEqual(actions.slice(n), ['input', 'saved-review'])
        assert.match(state(agent).decision.directAnswer, /81.25%/)
      }
    }
    decision = { mode: 'MANAGE', research: false, reply: 'example' }
    await turn(7, '同步例行任务', 'schedule')
    assert.equal(state(agent).decision.exampleFirst, undefined)
  }
})

test('intake accepts one grounded blocker and a ready response without an empty checklist', async () => {
  const { createResearchIntake } = await import('../plugins/dsh-labweave-workspace/host/research-intake.js')
  const replies = [
    { mode: 'CLARIFY', research: true, workflow: 'confirm',
      blocker: { id: 'claim_or_source', question: 'audit.pdf 中的表 2 需要原文。可以上传 PDF 吗？' } },
    { mode: 'EXECUTE', research: true, workflow: 'within_scope' },
    { mode: 'EXECUTE', research: true, workflow: 'within_scope',
      creation: { kind: 'research', name: 'Daquan Zhou 最新科研', named_only: true } },
  ]
  const asked = []
  const ctx = {
    llm: { async *stream(request) {
      if (replies.length === 2) assert.match(request.messages[0].content[0].text, /已上传 audit.pdf/)
      yield { type: 'block-end', block: { type: 'text', text: JSON.stringify(replies.shift()) } }
    } },
    userQuestions: { async ask({ questions }) {
      asked.push(...questions)
      return { answers: [{ id: 'claim_or_source', custom: '已上传 audit.pdf' }] }
    } },
  }
  const intake = createResearchIntake(ctx, async () => null)
  const options = { agent: { session: { deriveMessages: () => [] } }, interactive: true,
    signal: new AbortController().signal, modelSelection: {} }
  const result = await intake.resolve({ ...options, latest: '核对 audit.pdf 表 2' })
  assert.deepEqual(asked.map(item => item.id), ['claim_or_source'])
  assert.match(asked[0].question, /audit.pdf/)
  assert.equal(result.mode, 'EXECUTE')
  const creation = await intake.resolve({ ...options, onlyResearch: true, latest: '帮我创建一个项目追踪 Daquan Zhou' })
  assert.equal(creation.mode, 'MANAGE')
  assert.equal(creation.creation.kind, 'named_only')
  assert.equal(asked.length, 1)
})
