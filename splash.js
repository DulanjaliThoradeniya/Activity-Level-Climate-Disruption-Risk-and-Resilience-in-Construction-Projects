(() => {
  const splash = document.getElementById('appSplash');
  const mark = splash?.querySelector('.splash-mark');
  const brand = document.querySelector('.brand');
  if (!splash || !mark || !brand) {
    document.body.classList.remove('splash-active');
    splash?.remove();
    return;
  }

  const dismiss = () => {
    const source = mark.getBoundingClientRect();
    const target = brand.getBoundingClientRect();
    const scale = Math.max(.2, Math.min(.48, target.width / source.width, target.height / source.height));
    const x = target.left + target.width / 2 - (source.left + source.width / 2);
    const y = target.top + target.height / 2 - (source.top + source.height / 2);

    mark.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
    splash.classList.add('leaving');
    window.setTimeout(() => {
      document.body.classList.remove('splash-active');
      splash.remove();
    }, 1180);
  };

  document.addEventListener('DOMContentLoaded', () => {
    window.setTimeout(dismiss, 1500);
  }, { once: true });
})();
