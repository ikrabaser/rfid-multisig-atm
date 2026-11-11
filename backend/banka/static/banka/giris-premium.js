'use strict';

(() => {
  const input = document.getElementById('parola');
  const toggle = document.querySelector('.password-toggle');
  const form = document.getElementById('login-form');
  if (input && toggle) {
    toggle.addEventListener('click', () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      toggle.setAttribute('aria-pressed', String(show));
      toggle.setAttribute('aria-label', show ? 'Parolayı gizle' : 'Parolayı göster');
      toggle.title = show ? 'Parolayı gizle' : 'Parolayı göster';
      input.focus({ preventScroll: true });
    });
  }
  if (form) {
    form.addEventListener('submit', () => {
      const button = form.querySelector('.submit-button');
      if (button && form.checkValidity()) {
        button.classList.add('is-submitting');
        button.querySelector('span').textContent = 'Giriş yapılıyor…';
        // Do not disable the submit button, to preserve standard POST behavior.
      }
    });
  }
})();
