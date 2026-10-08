const refinement = document.createElement('link');
refinement.rel = 'stylesheet';
refinement.href = './refinement.css';
document.head.appendChild(refinement);

const setText = (selector, text) => {
  const node = document.querySelector(selector);
  if (node) node.textContent = text;
};

setText('.hero-lede', 'ResearchOS carries literature evidence, evolving research directions, experiment handoffs, and manuscript claims through one persistent workspace with typed artifacts, explicit contracts, and recoverable state.');
setText('#demo .section-intro p', 'A 36-second CLI run showing paper intake, structured reading, cross-paper synthesis, and candidate direction generation.');
setText('.foundation-intro .body-large', 'Long-horizon research depends on persistent state, evidence boundaries, explicit decision authority, and safe recovery. Each major transition leaves an inspectable record in the workspace.');
setText('#capabilities .section-intro h2', 'Four research transitions, each governed by a clear contract.');
setText('#capabilities .section-intro p', 'Each stage has explicit inputs, outputs, authority, and validation, keeping the research record coherent from evidence to manuscript.');

const capabilityCopy = document.querySelectorAll('.capability-copy > p');
const capabilityText = [
  'Verified papers become structured evidence records. Cross-paper synthesis maps mechanisms, design choices, empirical support, tensions, boundary conditions, and transfer opportunities into a research space.',
  'Evidence routing seeds multiple research routes. Independent generation, scoring, mutation, crossover, and survival produce a compact candidate portfolio for researcher selection.',
  'The accepted research blueprint becomes project-specific Skills, protocol constraints, resource requirements, and a structured handoff for Codex, Claude Code, or a human executor.',
  'Literature evidence, experimental facts, figures, tables, and claim boundaries are indexed before drafting, then routed section by section through review, revision, and claim audit.'
];
capabilityCopy.forEach((node, index) => {
  if (capabilityText[index]) node.textContent = capabilityText[index];
});

setText('#runtime .section-intro h2', 'The runtime keeps state, evidence, authority, and recovery legible.');
setText('#runtime .section-intro p', 'Three layers connect the research record, the capabilities that transform it, and the runtime controls that govern each transition.');
setText('.authority-copy h3', 'Scientific authority has an explicit owner at every transition.');
setText('.authority-copy p', 'Proposal, execution, validation, writing, and human decisions remain attributable throughout the project.');
setText('#workflow .section-intro p', 'Automation advances routine transitions. Human gates appear when a decision changes research meaning or authority.');
setText('.related-copy p', 'DIG Research Hub curates public resources for AI-assisted research across Skills, MCPs, research agents, prompts, benchmarks, tools, templates, and workflow references.');
setText('.closing-inner h2', 'Accelerate the workflow. Preserve the research record.');

const video = document.getElementById('researchos-video');
const playButton = document.getElementById('video-play');

if (video) {
  const posterProbe = new Image();
  posterProbe.onerror = () => video.setAttribute('poster', './assets/researchos-demo-poster.svg');
  posterProbe.src = './assets/researchos-demo-poster.webp';
}

if (video && playButton) {
  const hideOverlay = () => playButton.classList.add('is-hidden');
  playButton.addEventListener('click', async () => {
    try {
      await video.play();
      hideOverlay();
    } catch {
      video.controls = true;
    }
  });
  video.addEventListener('play', hideOverlay);
  video.addEventListener('ended', () => {
    video.currentTime = 0;
    playButton.classList.remove('is-hidden');
  });
}

const copyButton = document.getElementById('copy-quickstart');
const quickstart = document.getElementById('quickstart');
if (copyButton && quickstart) {
  copyButton.addEventListener('click', async () => {
    const original = copyButton.textContent;
    try {
      await navigator.clipboard.writeText(quickstart.innerText);
      copyButton.textContent = 'Copied';
    } catch {
      copyButton.textContent = 'Select & copy';
    }
    window.setTimeout(() => { copyButton.textContent = original; }, 1200);
  });
}
