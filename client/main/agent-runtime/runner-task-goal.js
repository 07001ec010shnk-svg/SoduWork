'use strict';

// The Runner owns execution and continuation. The desktop only sends explicit
// intent and projects its durable receipt; observing a terminal never resends.
const goalContract = require('../conversation/goal-contract');
const STATUSES = new Set(['working', 'reviewing', 'completed', 'paused', 'blocked', 'disabled']);
const SUPPORTED = new Set(['codex', 'codex-harness', 'opencode', 'dsh', 'kimi', 'qwen']);
const clean = value => String(value || '').trim();
const count = value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;

function explicitContinuation(value) {
  if (/(?:以下|下面|这段|如下)[^\n]*(?:提示词|示例|引用|文本|指令)[^\n]*[：:]\s*\n/.test(clean(value))) return false;
  const text = clean(value)
    .replace(/```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)/g, '')
    .replace(/^\s*>.*$/gm, '')
    .replace(/`[^`\n]*`|“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"|'[^'\n]*'/g, '');
  return (text.match(/[^\n。！？.!?；;]+[。！？.!?；;]?/g) || []).some(raw => {
    if (/[？?]$/.test(raw)) return false;
    const sentence = raw.replace(/[。！.!；;]$/, '');
    // Quotations, examples, questions and negation cannot opt a chat into an
    // unattended loop. This deliberately accepts only a narrow imperative.
    if (/(?:不要|不必|不用|无需|别|停止|取消|是否|如何|怎么|怎样|为什么|提示词|示例|引用|翻译|解释)|\b(?:not|don't|never|stop|cancel|example|quote|translate|explain|whether|how)\b/i.test(sentence)) return false;
    return [sentence, ...sentence.split(/[，,]/)].some(part => {
      const directive = part.trim().replace(/^(?:[-*]|\d+[)．、])\s*/, '');
      return /^(?:请(?:你|帮我)?|帮我)?(?:持续|继续)(?:自主|自动)?完成(?:整个|全部)(?:任务|工作)$/.test(directive) ||
        /^(?:请(?:你|帮我)?|帮我)?(?:持续|继续)(?:自主|自动)?(?:推进|执行|工作)[，,\s]*(?:直到|直至)(?:整个|全部)?(?:任务|工作)?(?:全部)?完成$/.test(directive) ||
        /^(?:please\s+)?(?:keep\s+(?:working|going)|continue\s+(?:working|executing))(?:\s+autonomously)?\s+until\s+(?:(?:the\s+)?(?:entire\s+|whole\s+)?task\s+is\s+|everything\s+is\s+)(?:complete|completed|done)$/i.test(directive);
    });
  });
}

function fromOptions(options) {
  const goal = goalContract.normalize(options);
  return goal.intent === 'goal' ? {
    schemaVersion: 1, objective: goal.goal, acceptance: goal.acceptance,
    budgetMinutes: goal.budgetMinutes, maxSteps: goal.maxSteps,
  } : null;
}

function fromText(value) {
  const objective = clean(value);
  return objective && objective.length <= 50000 && explicitContinuation(objective) ? {
    schemaVersion: 1, objective, acceptance: '',
    budgetMinutes: goalContract.DEFAULT_BUDGET_MINUTES, maxSteps: goalContract.DEFAULT_MAX_STEPS,
  } : null;
}

function activate(state, goal) {
  if (!goal) return;
  state.taskGoalContract = { ...goal };
  state.taskGoal = { status: 'working', reason: '', elapsedMs: 0, steps: 0,
    budgetMs: goal.budgetMinutes * 60000, maxSteps: goal.maxSteps, continuations: 0 };
  state.goalVisible = true;
  state.intent = 'goal';
  state.goal = goal.objective;
  state.acceptance = goal.acceptance;
  state.budgetMinutes = goal.budgetMinutes;
  state.maxSteps = goal.maxSteps;
  state.goalPaused = false;
  state.goalLimit = null;
}

function receipt(value) {
  if (!value || !STATUSES.has(value.status)) return null;
  return {
    status: value.status, reason: clean(value.reason).slice(0, 2000),
    elapsedMs: count(value.elapsedMs), steps: count(value.steps),
    budgetMs: count(value.budgetMs), maxSteps: count(value.maxSteps),
    continuations: count(value.continuations),
    ...(typeof value.objective === 'string' ? { objective: value.objective } : {}),
    ...(typeof value.acceptance === 'string' ? { acceptance: value.acceptance } : {}),
  };
}

function exhausted(goal) {
  return Boolean(goal && (goal.budgetMs > 0 && goal.elapsedMs >= goal.budgetMs ||
    goal.maxSteps > 0 && goal.steps >= goal.maxSteps));
}

function observe(state, value, terminal) {
  const goal = receipt(value);
  if (!goal || state.pendingTaskGoalControl) return null;
  state.taskGoal = goal;
  state.goalVisible = goal.status !== 'disabled';
  state.intent = state.goalVisible ? 'goal' : '';
  if (typeof goal.objective === 'string') state.goal = goal.objective;
  if (typeof goal.acceptance === 'string') state.acceptance = goal.acceptance;
  if (goal.budgetMs) state.budgetMinutes = goal.budgetMs / 60000;
  if (goal.maxSteps) state.maxSteps = goal.maxSteps;
  state.goalPaused = Boolean(terminal && ['paused', 'blocked'].includes(goal.status));
  const timeLimit = goal.budgetMs > 0 && goal.elapsedMs >= goal.budgetMs;
  state.goalLimit = state.goalPaused ? {
    kind: exhausted(goal) ? 'budget' : 'task-goal', reason: goal.reason || '任务已暂停，进度已保留',
    used: timeLimit ? Math.ceil(goal.elapsedMs / 60000) : goal.steps,
    limit: timeLimit ? goal.budgetMs / 60000 : goal.maxSteps,
    unit: timeLimit ? '分钟' : '个执行步骤', canContinue: !exhausted(goal),
  } : null;
  return goal;
}

module.exports = { supported: id => SUPPORTED.has(id), explicitContinuation, fromOptions, fromText, activate, receipt, observe, exhausted };
