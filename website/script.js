const navLinks = [...document.querySelectorAll('.nav-links a[href^="#"]')];
const sections = navLinks.map((link) => document.querySelector(link.getAttribute('href'))).filter(Boolean);

if ('IntersectionObserver' in window && sections.length) {
  const navObserver = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    navLinks.forEach((link) => link.classList.toggle('is-active', link.getAttribute('href') === `#${visible.target.id}`));
  }, { rootMargin: '-28% 0px -62% 0px', threshold: [0, .15, .4] });
  sections.forEach((section) => navObserver.observe(section));
}

const video = document.getElementById('researchos-video');
const playButton = document.getElementById('video-play');
if (video && playButton) {
  const hideOverlay = () => playButton.classList.add('is-hidden');
  const showOverlay = () => playButton.classList.remove('is-hidden');
  playButton.addEventListener('click', async () => {
    try { await video.play(); hideOverlay(); } catch { video.controls = true; }
  });
  video.addEventListener('play', hideOverlay);
  video.addEventListener('ended', () => { video.currentTime = 0; showOverlay(); });
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
