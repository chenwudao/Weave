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
