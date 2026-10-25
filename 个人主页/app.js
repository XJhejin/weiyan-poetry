'use strict';
document.querySelectorAll('[data-copy]').forEach(button => {
  button.addEventListener('click', async () => {
    const status = document.querySelector('.copy-status');
    const original = button.innerHTML;
    button.disabled = true;
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = '已复制';
      status.textContent = '已复制：' + button.dataset.copy;
    } catch {
      status.textContent = '请长按或选中号码复制。';
    } finally {
      setTimeout(() => { button.innerHTML = original; button.disabled = false; }, 1800);
    }
  });
});
