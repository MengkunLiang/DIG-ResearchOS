const header = document.getElementById('site-header');
const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const updateHeader = () => {
  if (!header) return;
  header.classList.toggle('is-scrolled', window.scrollY > 18);
};
updateHeader();
window.addEventListener('scroll', updateHeader, { passive: true });

// V3 copy and structure refinements. Keeping these changes here lets the redesign remain
// reversible while the public page is reviewed in PR form.
const heroKicker = document.querySelector('.hero-kicker');
const heroTitle = document.querySelector('.hero h1');
const heroLede = document.querySelector('.hero-lede');
if (heroKicker) heroKicker.innerHTML = '<span class="status-dot"></span>DIG · Open-source research infrastructure';
if (heroTitle) {
  heroTitle.textContent = 'ResearchOS';
  if (!document.querySelector('.hero-runtime')) {
    heroTitle.insertAdjacentHTML('afterend', '<div class="hero-runtime">Research Compilation Runtime</div><p class="hero-statement">Compile research rigor into the system — not into a prompt.</p>');
  }
}
if (heroLede) {
  heroLede.textContent = 'ResearchOS turns long-horizon research into a persistent, auditable workflow. Literature evidence, idea evolution, experiment handoffs, and manuscript claims are represented as durable artifacts and governed by explicit runtime contracts.';
}

const compilerStages = [...document.querySelectorAll('.compiler-stage')];
if (compilerStages[2]) {
  const label = compilerStages[2].querySelector('strong');
  if (label) label.textContent = 'Evolved Direction';
}
compilerStages.forEach((stage) => stage.classList.remove('is-active', 'is-complete'));

const principleLabels = [
  'Typed evidence',
  'Explicit contracts',
  'Resume & rollback',
  'Human-gated authority',
  'Executor-agnostic'
];
document.querySelectorAll('.hero-principles strong').forEach((node, index) => {
  if (principleLabels[index]) node.textContent = principleLabels[index];
});

const demoHeading = document.querySelector('#demo .section-heading h2');
const demoCopy = document.querySelector('#demo .section-heading p');
const demoDisclaimer = document.querySelector('.demo-disclaimer');
if (demoHeading) demoHeading.textContent = 'See the actual system move from papers toward a research direction.';
if (demoCopy) demoCopy.textContent = 'A 36-second recording from the real CLI workflow: paper intake, structured reading, research-space formation, and candidate direction generation.';
if (demoDisclaimer) demoDisclaimer.textContent = 'This excerpt covers the literature-to-idea path. External execution and manuscript production continue under separate runtime contracts.';

const whyHeading = document.querySelector('.why-copy h2');
const whyCopy = document.querySelector('.why-copy p');
if (whyHeading) whyHeading.textContent = 'A research project outlives any model call.';
if (whyCopy) whyCopy.textContent = 'Long-running research needs durable state, typed evidence, explicit decision authority, and safe recovery after interruption. ResearchOS encodes those requirements in the runtime instead of relying on a model to remember them from conversation history.';

const capabilityHeading = document.querySelector('#capabilities .section-heading h2');
const capabilityCopy = document.querySelector('#capabilities .section-heading p');
if (capabilityHeading) capabilityHeading.textContent = 'Four research transitions, each with an explicit contract.';
if (capabilityCopy) capabilityCopy.textContent = 'ResearchOS does not collapse reading, ideation, experimentation, and writing into one agent. Each transition produces inspectable artifacts, bounded authority, and a validated handoff.';

const runtimeHeading = document.querySelector('#runtime .section-heading h2');
const runtimeCopy = document.querySelector('#runtime .section-heading p');
if (runtimeHeading) runtimeHeading.textContent = 'The runtime governs what can happen next — and who is allowed to decide.';
if (runtimeCopy) runtimeCopy.textContent = 'ResearchOS separates the research information flow, the capabilities that transform it, and the runtime foundation that governs state, validation, authority, provenance, and recovery.';

const workflowCopy = document.querySelector('#workflow .section-heading p');
if (workflowCopy) workflowCopy.textContent = 'The stages are automated where safe and gated where a decision changes the meaning or authority of the research.';

const relatedCopy = document.querySelector('.related-copy p');
if (relatedCopy) relatedCopy.textContent = 'A separate, task-oriented repository that curates public resources for AI-assisted research: Skills, MCPs, research agents, prompts, benchmarks, tools, templates, and workflow references. It is maintained alongside ResearchOS and is not part of the ResearchOS runtime.';

const relatedVisual = document.querySelector('.related-visual');
if (relatedVisual) {
  relatedVisual.removeAttribute('aria-hidden');
  relatedVisual.innerHTML = `
    <div class="hub-browse">
      <span class="hub-browse-label">Browse by research task</span>
      <a href="https://github.com/MengkunLiang/DIG-Research-Hub/tree/main/01-%E8%AF%BE%E9%A2%98%E6%89%AB%E6%8F%8F%E4%B8%8E%E9%80%89%E9%A2%98" target="_blank" rel="noreferrer">Topic & direction scanning <b>↗</b></a>
      <a href="https://github.com/MengkunLiang/DIG-Research-Hub/tree/main/02-%E6%96%87%E7%8C%AE%E6%A3%80%E7%B4%A2%E4%B8%8E%E5%8D%95%E7%AF%87%E7%B2%BE%E8%AF%BB" target="_blank" rel="noreferrer">Literature search & reading <b>↗</b></a>
      <a href="https://github.com/MengkunLiang/DIG-Research-Hub/tree/main/05-%E5%AE%9E%E9%AA%8C%E6%89%A7%E8%A1%8C%E3%80%81%E5%A4%8D%E7%8E%B0%E4%B8%8E%E8%AF%84%E6%B5%8B" target="_blank" rel="noreferrer">Experiments & evaluation <b>↗</b></a>
      <a href="https://github.com/MengkunLiang/DIG-Research-Hub/tree/main/06-%E5%86%99%E4%BD%9C%E3%80%81%E5%AE%A1%E7%A8%BF%E4%B8%8E%E6%8A%95%E7%A8%BF" target="_blank" rel="noreferrer">Writing, review & submission <b>↗</b></a>
    </div>
    <div class="hub-browse">
      <span class="hub-browse-label">Browse by resource type</span>
      <a href="https://github.com/MengkunLiang/DIG-Research-Hub/tree/main/91-Skills" target="_blank" rel="noreferrer">Skills <b>↗</b></a>
      <a href="https://github.com/MengkunLiang/DIG-Research-Hub/tree/main/92-MCP" target="_blank" rel="noreferrer">MCP <b>↗</b></a>
      <a href="https://github.com/MengkunLiang/DIG-Research-Hub/tree/main/93-%E7%A7%91%E7%A0%94%E5%B7%A5%E4%BD%9C%E6%B5%81%E4%B8%8EAgent" target="_blank" rel="noreferrer">Research agents & workflows <b>↗</b></a>
      <a href="https://github.com/MengkunLiang/DIG-Research-Hub/tree/main/94-Prompt%E4%B8%8E%E5%86%99%E4%BD%9C" target="_blank" rel="noreferrer">Prompts & writing <b>↗</b></a>
    </div>`;
}

const closingHeading = document.querySelector('.closing-inner h2');
if (closingHeading) closingHeading.textContent = 'Move faster without losing the research record.';

// Keep the navigation state informative, not animated for decoration.
const navLinks = [...document.querySelectorAll('.nav-links a[href^="#"]')];
const navTargets = navLinks
  .map((link) => document.querySelector(link.getAttribute('href')))
  .filter(Boolean);
if ('IntersectionObserver' in window && navTargets.length) {
  const navObserver = new IntersectionObserver((entries) => {
    const current = entries
      .filter((entry) => entry.isIntersecting)
      .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!current) return;
    navLinks.forEach((link) => {
      link.classList.toggle('is-active', link.getAttribute('href') === `#${current.target.id}`);
    });
  }, { rootMargin: '-30% 0px -58% 0px', threshold: [0, .15, .4] });
  navTargets.forEach((target) => navObserver.observe(target));
}

const video = document.getElementById('researchos-video');
const playButton = document.getElementById('video-play');
if (video) {
  const hasMp4 = [...video.querySelectorAll('source')].some((source) => source.type === 'video/mp4');
  if (!hasMp4) {
    const mp4 = document.createElement('source');
    mp4.src = './assets/researchos-main-flow.mp4';
    mp4.type = 'video/mp4';
    video.appendChild(mp4);
  }
}
if (video && playButton) {
  const playTitle = playButton.querySelector('strong');
  const playMeta = playButton.querySelector('small');
  const hideOverlay = () => playButton.classList.add('is-hidden');

  playButton.addEventListener('click', async () => {
    if (playButton.disabled) return;
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
  video.addEventListener('error', () => {
    playButton.classList.remove('is-hidden');
    playButton.disabled = true;
    if (playTitle) playTitle.textContent = 'Demo media not uploaded yet';
    if (playMeta) playMeta.textContent = 'Add MP4/WebM under website/assets/';
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

if (prefersReducedMotion) {
  document.documentElement.style.scrollBehavior = 'auto';
}
