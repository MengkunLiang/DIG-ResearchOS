const header = document.getElementById('site-header');
const revealNodes = document.querySelectorAll('.reveal');

const onScroll = () => {
  if (!header) return;
  header.classList.toggle('is-scrolled', window.scrollY > 24);
};
window.addEventListener('scroll', onScroll, { passive: true });
onScroll();

const revealObserver = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (!entry.isIntersecting) return;
    entry.target.classList.add('is-visible');
    revealObserver.unobserve(entry.target);
  });
}, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });

revealNodes.forEach((node) => revealObserver.observe(node));

const video = document.getElementById('researchos-video');
const playButton = document.getElementById('video-play');

if (video && playButton) {
  const hideOverlay = () => playButton.classList.add('is-hidden');
  const showOverlay = () => {
    if (video.paused && video.currentTime < 0.2) playButton.classList.remove('is-hidden');
  };

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
  video.addEventListener('loadedmetadata', showOverlay);
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
if (compilerStages.length) {
  let activeStage = 0;
  setInterval(() => {
    compilerStages.forEach((stage, index) => stage.classList.toggle('is-active', index === activeStage));
    activeStage = (activeStage + 1) % compilerStages.length;
  }, 1900);
}
