const assert = require('node:assert/strict')
const fs = require('node:fs')
const { chromium } = require('playwright')

async function main() {
  const { url, stateUrl } = JSON.parse(fs.readFileSync(0, 'utf8'))
  const channel = process.env.PLAYWRIGHT_CHANNEL || (process.platform === 'win32' ? 'msedge' : 'chromium')
  const browser = await chromium.launch({ channel, headless: true })
  const page = await browser.newPage({ locale: 'zh-CN', viewport: { width: 1280, height: 820 } })
  page.setDefaultTimeout(45000)
  page.on('pageerror', error => console.error(error.message))
  const button = name => page.getByRole('button', { name, exact: true })
  const chat = page.locator('[data-conversation-content][data-conversation-session]:visible').last()
  const question = chat.locator('[data-question-key]:visible')
  const send = async text => {
    await chat.locator('[contenteditable=true]').last().fill(text)
    await button('发送消息').click()
  }
  const state = async () => (await fetch(stateUrl)).json()
  const pendingHandoffs = () => chat.evaluate(async el => {
    const result = await fetch('/api/labweave/project-handoffs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: el.dataset.conversationSession }),
    })
    if (!result.ok) throw Error('Cannot read current handoffs')
    return result.json()
  })
  const newGlobalChat = async () => {
    await button('打开助手').click()
    const newChat = button('新建全局对话')
    await newChat.waitFor()
    const previous = await chat.getAttribute('data-conversation-session')
    await newChat.click()
    await page.waitForFunction(id => {
      const visible = [...document.querySelectorAll('[data-conversation-content][data-conversation-session]')]
        .filter(element => element.getClientRects().length)
      return visible.at(-1)?.dataset.conversationSession && visible.at(-1).dataset.conversationSession !== id
    }, previous)
    await newChat.waitFor({ state: 'hidden' })
  }
  let step = 'open'
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded' })
    await button('打开助手').click()
    step = 'broad project creation'
    await send('创建跟 Agent 相关的项目')
    await question.getByRole('heading', { name: /想聚焦哪个研究方向/ }).waitFor()
    await button('Agent 研究').waitFor()
    assert.equal((await state()).jobs, 0)
    assert.deepEqual(await pendingHandoffs(), [])
    const input = question.getByPlaceholder('输入你的答案')
    await input.fill('Agent 记忆可靠性')
    await input.press('Enter')
    await question.getByRole('heading', { name: '本轮希望推进到哪一步？', exact: true }).waitFor()
    assert.equal((await state()).jobs, 0)
    assert.deepEqual(await pendingHandoffs(), [])
    await question.getByRole('radio', { name: '先做一轮文献摸底' }).click()
    if (await question.isVisible()) await question.getByRole('button', { name: /提交|完成/ }).click()
    step = 'confirmed handoff'
    await chat.getByText('文献摸底完成', { exact: true }).waitFor({ timeout: 75000 })
    assert.equal((await state()).jobs, 1)
    step = 'explicit delegation'
    await newGlobalChat()
    await send('创建一个跟 Agent 相关的项目，帮我选一个方向并开始')
    await chat.getByText('文献摸底完成', { exact: true }).waitFor({ timeout: 75000 })
    assert.equal(await question.count(), 0)
    assert.equal((await state()).jobs, 2)
  } catch (error) {
    console.error(step, (await page.locator('body').innerText()).slice(-1800))
    throw error
  } finally {
    await browser.close()
  }
  console.log('宽泛领域建项、提问卡确认、确认后转交与分派、明确授权直接推进通过。')
}

main().catch(error => { console.error(error.message); process.exitCode = 1 })
