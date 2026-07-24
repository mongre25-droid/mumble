(() => {
  const sections = [...document.querySelectorAll("main section[id]")];
  const links = [...document.querySelectorAll(".section-nav a")];
  if (sections.length && links.length && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;
      links.forEach((link) => link.classList.toggle(
        "current", link.getAttribute("href") === `#${visible.target.id}`));
    }, { rootMargin: "-20% 0px -68%", threshold: [0, .2, .6] });
    sections.forEach((section) => observer.observe(section));
  }

  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.querySelector(button.dataset.copyTarget);
      if (!target) return;
      try {
        await navigator.clipboard.writeText(target.innerText);
        const old = button.textContent;
        button.textContent = "Copied";
        setTimeout(() => { button.textContent = old; }, 1400);
      } catch (_) {
        button.textContent = "Select + copy";
      }
    });
  });
})();
