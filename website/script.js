const header = document.getElementById('site-header');
const revealNodes = document.querySelectorAll('.reveal');
const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const onScroll = () => {
  if (!header) return;
  header.classList.toggle('is-scrolled', window.scrollY > 24);
};
window.addEventListener('scroll', onScroll, { passive: true });
onScroll();

if (prefersReducedMotion) {
  revealNodes.forEach((node) => node.classList.add('is-visible'));
} else {
  const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-visible');
      revealObserver.unobserve(entry.target);
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });

  revealNodes.forEach((node) => revealObserver.observe(node));
}

const video = document.getElementById('researchos-video');
const playButton = document.getElementById('video-play');

if (video && playButton) {
  const playTitle = playButton.querySelector('strong');
  const playMeta = playButton.querySelector('small');
  const hideOverlay = () => playButton.classList.add('is-hidden');
  const showOverlay = () => {
    if (video.paused && video.currentTime < 0.2) playButton.classList.remove('is-hidden');
  };

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
  video.addEventListener('loadedmetadata', showOverlay);
  video.addEventListener('error', () => {
    playButton.classList.remove('is-hidden');
    playButton.disabled = true;
    if (playTitle) playTitle.textContent = 'Real demo media pending';
    if (playMeta) playMeta.textContent = 'Add the recording under website/assets/';
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
    setTimeout(() => { copyButton.textContent = original; }, 1400);
  });
}

const compilerStages = [...document.querySelectorAll('.compiler-stage')];
if (compilerStages.length && !prefersReducedMotion) {
  let activeStage = 0;
  setInterval(() => {
    compilerStages.forEach((stage, index) => stage.classList.toggle('is-active', index === activeStage));
    activeStage = (activeStage + 1) % compilerStages.length;
  }, 1900);
}
