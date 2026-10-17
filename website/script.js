const header = document.getElementById('site-header');
const primaryNav = document.getElementById('primary-nav');
const menuToggle = document.getElementById('nav-menu-toggle');
const capabilityDropdown = document.querySelector('[data-nav-dropdown]');
const capabilityTrigger = capabilityDropdown?.querySelector('.nav-trigger');
const navAnchors = [...document.querySelectorAll('.nav-links a[href^="#"]')];

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
