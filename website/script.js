const revealObserver = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting) {
      entry.target.classList.add('is-visible');
      revealObserver.unobserve(entry.target);
    }
  });
}, { threshold: 0.12 });

document.querySelectorAll('.reveal').forEach((el) => revealObserver.observe(el));

const workflow = document.querySelector('[data-workflow]');
if (workflow) {
  const steps = [...workflow.querySelectorAll('[data-step]')];
  const progress = workflow.querySelector('.workflow-line span');
  const workflowObserver = new IntersectionObserver((entries) => {
    if (!entries[0].isIntersecting) return;
    let i = 0;
    const timer = setInterval(() => {
      steps.forEach((step, index) => {
        step.classList.toggle('is-active', index === i);
        step.classList.toggle('is-complete', index < i);
      });
      if (progress) progress.style.width = `${Math.min(100, (i / (steps.length - 1)) * 100)}%`;
      i += 1;
      if (i >= steps.length) clearInterval(timer);
    }, 520);
    workflowObserver.disconnect();
  }, { threshold: 0.35 });
  workflowObserver.observe(workflow);
}

const demoLines = [
  { text: '✓ T1 scope initialized from project.yaml', cls: 'ok', stage: 0, path: 'project.yaml' },
  { text: '→ T2 discovering and verifying literature...', cls: 'dim', stage: 1, path: 'literature/literature_params.json' },
  { text: '✓ Reading queue locked; evidence collection started', cls: 'ok', stage: 1, path: 'literature/deep_read_notes/' },
  { text: '✓ T3.5 synthesis completed', cls: 'ok', stage: 1, path: 'literature/synthesis.md' },
  { text: '→ T4 evolving candidate research directions...', cls: 'dim', stage: 2, path: 'ideation/' },
  { text: '◆ HUMAN GATE · select a candidate direction', cls: 'gate', stage: 2, path: 'ideation/selection_history/' },
  { text: '✓ T4.5 proposal formalized and novelty-audited', cls: 'ok', stage: 3, path: 'ideation/proposal/research_proposal.md' },
  { text: '→ T5 compiling external-executor handoff...', cls: 'dim', stage: 4, path: 'external_executor/handoff_pack.json' },
  { text: '✓ Writer Handoff validated from experimental facts', cls: 'ok', stage: 4, path: 'external_executor/result_pack.json' },
  { text: '→ T8 manuscript generation from verified claims', cls: 'dim', stage: 5, path: 'drafts/' },
  { text: '✓ T9 submission bundle ready', cls: 'ok', stage: 5, path: 'submission/' }
];

const terminalStream = document.getElementById('terminal-stream');
const stageList = document.getElementById('stage-list');
const demoStageLabel = document.getElementById('demo-stage-label');
const artifactPath = document.getElementById('artifact-path');
const labels = ['T1 · Scope', 'T2–T3.5 · Evidence', 'T4 · Ideation', 'T4.5 · Research plan', 'T5 · External execution', 'T8–T9 · Manuscript & submission'];

function setStage(stage, path) {
  if (!stageList) return;
  [...stageList.children].forEach((li, index) => {
    li.classList.toggle('is-complete', index < stage);
    li.classList.toggle('is-current', index === stage);
    li.querySelector('i').textContent = index < stage ? '✓' : index === stage ? '●' : '○';
  });
  demoStageLabel.textContent = labels[stage];
  artifactPath.textContent = path;
}

function runDemo() {
  if (!terminalStream) return;
  terminalStream.innerHTML = '';
  let i = 0;
  const addLine = () => {
    const item = demoLines[i];
    const div = document.createElement('div');
    div.className = `terminal-line ${item.cls}`;
    div.textContent = item.text;
    terminalStream.appendChild(div);
    setStage(item.stage, item.path);
    terminalStream.parentElement.scrollTop = terminalStream.parentElement.scrollHeight;
    i += 1;
    if (i < demoLines.length) {
      setTimeout(addLine, item.cls === 'gate' ? 1250 : 700);
    } else {
      setTimeout(runDemo, 2200);
    }
  };
  setStage(0, 'project.yaml');
  setTimeout(addLine, 700);
}

const demoSection = document.getElementById('demo');
if (demoSection) {
  const demoObserver = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting) {
      runDemo();
      demoObserver.disconnect();
    }
  }, { threshold: 0.25 });
  demoObserver.observe(demoSection);
}

document.querySelectorAll('[data-copy-target]').forEach((button) => {
  button.addEventListener('click', async () => {
    const target = document.getElementById(button.dataset.copyTarget);
    if (!target) return;
    try {
      await navigator.clipboard.writeText(target.innerText);
      const old = button.textContent;
      button.textContent = 'Copied';
      setTimeout(() => button.textContent = old, 1200);
    } catch {
      button.textContent = 'Select & copy';
    }
  });
});
