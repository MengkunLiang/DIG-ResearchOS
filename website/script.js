const header = document.getElementById('site-header');
const primaryNav = document.getElementById('primary-nav');
const menuToggle = document.getElementById('nav-menu-toggle');
const capabilityDropdown = document.querySelector('[data-nav-dropdown]');
const capabilityTrigger = capabilityDropdown?.querySelector('.nav-trigger');
const navAnchors = [...document.querySelectorAll('.nav-links a[href^="#"]')];

const capabilityVisualStyles = `
.capability-row .diagram{display:block!important;visibility:visible!important;opacity:1!important;min-height:440px;overflow:hidden;position:relative;background:#fbfcfe;border:1px solid #d9e2ec;border-radius:18px;box-shadow:0 18px 48px rgba(25,43,68,.075)}
.cap-viz{--accent:#365fde;--tint:#eef3ff;position:relative;display:flex;flex-direction:column;justify-content:center;min-height:440px;padding:30px;background-color:#fbfcfe;background-image:radial-gradient(circle at 1px 1px,#dce4ef 1px,transparent 1.2px);background-size:28px 28px;color:#17263e}
.capability-evidence .cap-viz{--accent:#365fde;--tint:#eef3ff}.capability-ideas .cap-viz{--accent:#7860c4;--tint:#f3effc}.capability-execution .cap-viz{--accent:#168779;--tint:#eaf6f3}.capability-writing .cap-viz{--accent:#a56b24;--tint:#fbf3e7}
.cap-viz *{box-sizing:border-box}.cap-viz-kicker{font-size:12px;font-weight:760;letter-spacing:.1em;text-transform:uppercase;color:#718197}.cap-viz-title{font-size:19px;font-weight:680;line-height:1.25}.cap-viz-sub{margin-top:6px;font-size:13px;line-height:1.45;color:#66778d}.cap-viz-arrow{display:grid;place-items:center;color:var(--accent);font-size:24px;font-weight:400}.cap-viz-card{border:1px solid #d5e0ec;border-radius:13px;background:#fff;padding:17px 18px;box-shadow:0 8px 20px rgba(25,43,68,.035)}.cap-viz-card.is-focus{border-color:var(--accent);background:var(--tint)}.cap-viz-card strong{display:block;font-size:17px;font-weight:670}.cap-viz-card small{display:block;margin-top:6px;font-size:12px;line-height:1.5;color:#66778d}.cap-viz-chip-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:13px}.cap-viz-chip{padding:7px 9px;border:1px solid #d7e1ec;border-radius:8px;background:#fff;font-size:12px;font-weight:620;color:#405169}.cap-viz-chip.is-accent{border-color:color-mix(in srgb,var(--accent) 45%,#d7e1ec);background:var(--tint);color:#27364b}.cap-viz-footer{margin-top:22px;padding-top:16px;border-top:1px solid #dde5ee;display:flex;align-items:center;justify-content:space-between;gap:20px;font-size:12px;color:#687a91}.cap-viz-footer strong{font-size:13px;color:var(--accent);font-weight:700}.cap-viz-flow{display:grid;grid-template-columns:1fr 36px 1.28fr 36px 1fr;gap:8px;align-items:center}.cap-viz-stack{display:grid;gap:10px}.cap-viz-list{display:grid;gap:9px;margin-top:14px}.cap-viz-list span{display:block;padding:10px 12px;border:1px solid #d7e1ec;border-radius:9px;background:#fff;font-size:13px;font-weight:620}.cap-viz-source{position:relative}.cap-viz-source:before,.cap-viz-source:after{content:"";position:absolute;inset:7px -7px -7px 7px;border:1px solid #e1e8f0;border-radius:13px;background:#fff;z-index:-1}.cap-viz-source:after{inset:14px -14px -14px 14px;opacity:.75}.cap-viz-source{z-index:0}
.cap-viz-ideas{display:grid;gap:15px}.cap-viz-opportunity{max-width:370px;margin:0 auto;width:100%;text-align:center}.cap-viz-candidates{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.cap-viz-candidate{text-align:left}.cap-viz-candidate b{display:grid;place-items:center;width:25px;height:25px;margin-bottom:10px;border-radius:7px;background:var(--tint);color:var(--accent);font-size:11px}.cap-viz-evolve{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:8px;padding:12px 14px;border:1px solid color-mix(in srgb,var(--accent) 44%,#d7e1ec);border-radius:10px;background:var(--tint)}.cap-viz-evolve span{font-size:12px;font-weight:650}.cap-viz-evolve i{font-style:normal;color:var(--accent)}.cap-viz-selected{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 16px;border-radius:10px;background:#15253f;color:#fff}.cap-viz-selected span{font-size:12px;color:#aebed4}.cap-viz-selected strong{font-size:15px;font-weight:650}.cap-viz-gate{padding:6px 9px;border-radius:7px;background:#ffffff12;border:1px solid #ffffff26;font-size:11px;color:#dbe6f4;white-space:nowrap}
.cap-viz-execution{display:grid;grid-template-columns:1fr 32px 1.12fr 32px 1fr 32px 1fr;gap:7px;align-items:stretch}.cap-viz-step{display:flex;flex-direction:column;justify-content:center;min-height:200px}.cap-viz-step .cap-viz-kicker{margin-bottom:12px}.cap-viz-contract{margin-top:11px;display:grid;gap:7px}.cap-viz-contract span{padding:8px 9px;border-radius:7px;background:#fff;border:1px solid #d6e1eb;font-size:11px;font-weight:610}.cap-viz-executor-loop{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:12px}.cap-viz-executor-loop span{padding:8px;border-radius:7px;background:#fff;border:1px solid #d6e1eb;text-align:center;font-size:11px;font-weight:610}.cap-viz-return{display:grid;gap:7px;margin-top:12px}.cap-viz-return span{padding:8px 9px;border-left:3px solid var(--accent);background:#fff;border-radius:6px;font-size:11px;font-weight:610}
.cap-viz-writing{display:grid;grid-template-columns:.9fr 30px 1.25fr 30px .9fr;gap:8px;align-items:center}.cap-viz-sources{display:grid;gap:8px}.cap-viz-sources span{padding:10px;border:1px solid #d7e1ec;border-radius:8px;background:#fff;font-size:12px;font-weight:620}.cap-viz-router{padding:18px;border:1px solid var(--accent);border-radius:14px;background:var(--tint)}.cap-viz-router-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:13px}.cap-viz-router-grid span{padding:9px;border-radius:8px;background:#fff;border:1px solid #d7e1ec;text-align:center;font-size:11px;font-weight:620}.cap-viz-manuscript{display:grid;gap:8px}.cap-viz-manuscript span{padding:10px;border:1px solid #d7e1ec;border-radius:8px;background:#fff;text-align:center;font-size:12px;font-weight:620}.cap-viz-manuscript span:last-child{border-color:var(--accent);background:var(--tint);color:#27364b;font-weight:700}
@media(max-width:760px){.capability-row .diagram,.cap-viz{min-height:auto}.cap-viz{padding:22px 18px;background-size:24px 24px}.cap-viz-flow,.cap-viz-writing,.cap-viz-execution{grid-template-columns:1fr;gap:10px}.cap-viz-arrow{height:24px;font-size:0}.cap-viz-arrow:after{content:"↓";font-size:22px}.cap-viz-candidates{grid-template-columns:1fr}.cap-viz-step{min-height:auto}.cap-viz-footer{align-items:flex-start;flex-direction:column;gap:6px}.cap-viz-selected{align-items:flex-start;flex-direction:column}.cap-viz-router-grid{grid-template-columns:1fr 1fr}}
`;

const capabilityVisuals = {
  evidence: `
    <div class="cap-viz" role="img" aria-label="Verified literature is converted into typed paper cards and synthesized into a research space.">
      <div class="cap-viz-flow">
        <div class="cap-viz-card cap-viz-source">
          <span class="cap-viz-kicker">Verified literature</span>
          <div class="cap-viz-title" style="margin-top:10px">Paper pool</div>
          <div class="cap-viz-sub">identity · access · reading coverage</div>
          <div class="cap-viz-chip-row"><span class="cap-viz-chip">Paper</span><span class="cap-viz-chip">Paper</span><span class="cap-viz-chip">Paper</span></div>
        </div>
        <div class="cap-viz-arrow">→</div>
        <div class="cap-viz-card is-focus">
          <span class="cap-viz-kicker">Typed reading</span>
          <div class="cap-viz-title" style="margin-top:10px">Paper card</div>
          <div class="cap-viz-sub">what this source can actually support</div>
          <div class="cap-viz-list"><span>Mechanism</span><span>Claims ↔ evidence</span><span>Reading coverage</span></div>
        </div>
        <div class="cap-viz-arrow">→</div>
        <div class="cap-viz-card">
          <span class="cap-viz-kicker">Cross-paper synthesis</span>
          <div class="cap-viz-title" style="margin-top:10px">Research space</div>
          <div class="cap-viz-list"><span>Mechanisms</span><span>Tensions</span><span>Synthesis</span><span>Opportunities</span></div>
        </div>
      </div>
      <div class="cap-viz-footer"><span>Source traceability survives every synthesis step.</span><strong>Literature → Research space</strong></div>
    </div>`,
  ideas: `
    <div class="cap-viz" role="img" aria-label="Evidence-led opportunities become multiple research candidates, which are scored, evolved, and selected by the researcher.">
      <div class="cap-viz-ideas">
        <div class="cap-viz-card is-focus cap-viz-opportunity"><span class="cap-viz-kicker">Evidence routing</span><div class="cap-viz-title" style="margin-top:8px">Opportunity map</div><div class="cap-viz-sub">problems · tensions · transfers · constraints</div></div>
        <div class="cap-viz-arrow" style="height:18px">↓</div>
        <div class="cap-viz-candidates">
          <div class="cap-viz-card cap-viz-candidate"><b>A</b><strong>Candidate</strong><small>thesis · mechanism · test</small></div>
          <div class="cap-viz-card cap-viz-candidate"><b>B</b><strong>Candidate</strong><small>thesis · mechanism · test</small></div>
          <div class="cap-viz-card cap-viz-candidate"><b>C</b><strong>Candidate</strong><small>thesis · mechanism · test</small></div>
        </div>
        <div class="cap-viz-evolve"><span>Independent score</span><i>→</i><span>Mutate</span><i>→</i><span>Cross over</span><i>→</i><span>Survive</span></div>
        <div class="cap-viz-selected"><div><span>Portfolio</span><strong>Researcher-selected direction</strong></div><div class="cap-viz-gate">HUMAN GATE</div></div>
      </div>
      <div class="cap-viz-footer"><span>Generation, scoring, and selection remain separate decisions.</span><strong>Research space → Direction</strong></div>
    </div>`,
  execution: `
    <div class="cap-viz" role="img" aria-label="An accepted research blueprint is compiled into a bounded execution handoff for an external executor and returns as a validated result package.">
      <div class="cap-viz-execution">
        <div class="cap-viz-card cap-viz-step"><span class="cap-viz-kicker">Accepted plan</span><strong>Research blueprint</strong><small>mechanism · baselines · evaluation</small><div class="cap-viz-contract"><span>Claim boundary</span><span>Resource limits</span></div></div>
        <div class="cap-viz-arrow">→</div>
        <div class="cap-viz-card is-focus cap-viz-step"><span class="cap-viz-kicker">ResearchOS</span><strong>Compile handoff</strong><small>specialize Skills and execution contract</small><div class="cap-viz-contract"><span>Project Skills</span><span>Execution protocol</span></div></div>
        <div class="cap-viz-arrow">→</div>
        <div class="cap-viz-card cap-viz-step"><span class="cap-viz-kicker">External executor</span><strong>Run & diagnose</strong><small>Codex · Claude Code · Human</small><div class="cap-viz-executor-loop"><span>Build</span><span>Run</span><span>Fix</span><span>Analyze</span></div></div>
        <div class="cap-viz-arrow">→</div>
        <div class="cap-viz-card cap-viz-step"><span class="cap-viz-kicker">Return</span><strong>Writer handoff</strong><div class="cap-viz-return"><span>Research report</span><span>Result pack</span><span>Run manifest</span></div></div>
      </div>
      <div class="cap-viz-footer"><span>The experiment contract travels with the work.</span><strong>Plan → Validated execution</strong></div>
    </div>`,
  writing: `
    <div class="cap-viz" role="img" aria-label="Accepted literature and experiment evidence is routed to manuscript sections, then reviewed and claim-audited before submission.">
      <div class="cap-viz-writing">
        <div><span class="cap-viz-kicker">Accepted evidence</span><div class="cap-viz-sources" style="margin-top:13px"><span>Paper cards</span><span>Experiment facts</span><span>Figures & tables</span><span>Claim boundary</span></div></div>
        <div class="cap-viz-arrow">→</div>
        <div class="cap-viz-router"><span class="cap-viz-kicker">Evidence router</span><div class="cap-viz-title" style="margin-top:8px">Section-level context</div><div class="cap-viz-router-grid"><span>Introduction</span><span>Related Work</span><span>Method</span><span>Experiments</span><span>Analysis</span><span>Discussion</span></div></div>
        <div class="cap-viz-arrow">→</div>
        <div><span class="cap-viz-kicker">Manuscript pipeline</span><div class="cap-viz-manuscript" style="margin-top:13px"><span>Draft</span><span>Review</span><span>Revise</span><span>Claim audit</span></div></div>
      </div>
      <div class="cap-viz-footer"><span>Every major claim stays attached to accepted evidence.</span><strong>Evidence → Audited paper</strong></div>
    </div>`
};

const mountCapabilityVisuals = () => {
  if (!document.getElementById('capability-visual-styles')) {
    const style = document.createElement('style');
    style.id = 'capability-visual-styles';
    style.textContent = capabilityVisualStyles;
    document.head.appendChild(style);
  }

  const targets = [
    ['.capability-evidence .diagram', capabilityVisuals.evidence],
    ['.capability-ideas .diagram', capabilityVisuals.ideas],
    ['.capability-execution .diagram', capabilityVisuals.execution],
    ['.capability-writing .diagram', capabilityVisuals.writing]
  ];

  targets.forEach(([selector, markup]) => {
    const target = document.querySelector(selector);
    if (!target) return;
    target.innerHTML = markup;
    target.classList.add('diagram-mounted');
  });
};

mountCapabilityVisuals();

const closeCapabilityMenu = () => {
  if (!capabilityDropdown || !capabilityTrigger) return;
  capabilityDropdown.classList.remove('is-open');
  capabilityTrigger.setAttribute('aria-expanded', 'false');
};

const closeMobileNav = () => {
  if (!primaryNav || !menuToggle) return;
  primaryNav.classList.remove('is-open');
  menuToggle.classList.remove('is-open');
  menuToggle.setAttribute('aria-expanded', 'false');
  menuToggle.setAttribute('aria-label', 'Open navigation');
};

if (menuToggle && primaryNav) {
  menuToggle.addEventListener('click', () => {
    const willOpen = !primaryNav.classList.contains('is-open');
    primaryNav.classList.toggle('is-open', willOpen);
    menuToggle.classList.toggle('is-open', willOpen);
    menuToggle.setAttribute('aria-expanded', String(willOpen));
    menuToggle.setAttribute('aria-label', willOpen ? 'Close navigation' : 'Open navigation');
    if (!willOpen) closeCapabilityMenu();
  });
}

if (capabilityTrigger && capabilityDropdown) {
  capabilityTrigger.addEventListener('click', () => {
    const willOpen = !capabilityDropdown.classList.contains('is-open');
    capabilityDropdown.classList.toggle('is-open', willOpen);
    capabilityTrigger.setAttribute('aria-expanded', String(willOpen));
  });
}

navAnchors.forEach((link) => {
  link.addEventListener('click', () => {
    closeCapabilityMenu();
    closeMobileNav();
  });
});

document.addEventListener('click', (event) => {
  if (header && !header.contains(event.target)) {
    closeCapabilityMenu();
    closeMobileNav();
  }
});

document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  closeCapabilityMenu();
  closeMobileNav();
});

const navTargets = [
  'demo',
  'capabilities',
  'capability-evidence',
  'capability-ideas',
  'capability-execution',
  'capability-writing',
  'runtime',
  'workflow',
  'more-from-dig',
  'quickstart'
].map((id) => document.getElementById(id)).filter(Boolean);

const setActiveNav = (sectionId) => {
  navAnchors.forEach((link) => link.classList.remove('is-active'));
  capabilityTrigger?.classList.remove('is-active');

  if (sectionId === 'capabilities' || sectionId.startsWith('capability-')) {
    capabilityTrigger?.classList.add('is-active');
    const exact = document.querySelector(`.nav-dropdown-menu a[href="#${sectionId}"]`);
    exact?.classList.add('is-active');
    return;
  }

  const activeLink = document.querySelector(`.nav-links > a[href="#${sectionId}"]`);
  activeLink?.classList.add('is-active');
};

if ('IntersectionObserver' in window && navTargets.length) {
  const navObserver = new IntersectionObserver((entries) => {
    const visible = entries
      .filter((entry) => entry.isIntersecting)
      .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (visible) setActiveNav(visible.target.id);
  }, { rootMargin: '-30% 0px -58% 0px', threshold: [0, .08, .25, .5] });
  navTargets.forEach((section) => navObserver.observe(section));
}

const video = document.getElementById('researchos-video');
const videoStage = document.getElementById('video-stage');
const playButton = document.getElementById('video-play');

if (video && videoStage && playButton) {
  playButton.addEventListener('click', async () => {
    try {
      await video.play();
      videoStage.classList.add('is-playing');
    } catch {
      video.controls = true;
    }
  });

  video.addEventListener('play', () => videoStage.classList.add('is-playing'));
  video.addEventListener('ended', () => {
    video.currentTime = 0;
    videoStage.classList.remove('is-playing');
  });
}

const copyButton = document.getElementById('copy-quickstart');
const quickstart = document.getElementById('quickstart-code');

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
