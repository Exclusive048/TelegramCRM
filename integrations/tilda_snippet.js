/**
 * CRM Bot — Интеграция с любым сайтом
 * 
 * УСТАНОВКА:
 * 1. Вставьте этот код на страницу (в тег <script> или в менеджер тегов)
 * 2. Замените CRM_WEBHOOK_URL на ваш URL
 * 3. Замените CRM_API_KEY на ваш ключ
 * 
 * Для Tilda:
 *   Настройки сайта → Формы → После отправки → Webhook
 *   Вставьте URL: https://your-domain.com/api/v1/leads/tilda
 *   И добавьте заголовок: X-API-Key: YOUR_KEY
 *   Пример:
 *   fetch('https://your-domain.com/api/v1/leads/tilda', {
 *     method: 'POST',
 *     headers: { 'X-API-Key': 'YOUR_KEY' },
 *     body: formData
 *   })
 * 
 * Для других сайтов — используйте код ниже:
 */

const CRM_WEBHOOK_URL = 'https://your-domain.com/api/v1/leads';
const CRM_API_KEY = 'your_api_secret_key';

/**
 * Отправить лид в CRM
 * @param {Object} leadData - данные клиента
 */
async function sendLeadToCRM(leadData) {
  try {
    const response = await fetch(CRM_WEBHOOK_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': CRM_API_KEY,
      },
      body: JSON.stringify({
        name:         leadData.name    || '—',
        phone:        leadData.phone   || '—',
        source:       leadData.source  || 'website',
        comment:      leadData.comment || 'Заявка с сайта',
        service:      leadData.service || null,
        utm_campaign: getUTM('utm_campaign'),
        utm_source:   getUTM('utm_source'),
        extra:        leadData.extra   || null,
      }),
    });

    if (!response.ok) {
      console.error('CRM error:', await response.text());
      return false;
    }
    return true;
  } catch (e) {
    console.error('CRM send failed:', e);
    return false;
  }
}

/** Получить UTM-метку из URL */
function getUTM(key) {
  return new URLSearchParams(window.location.search).get(key) || null;
}

// ─── Примеры подключения к популярным формам ──────────

// 1. Обычная HTML-форма
document.addEventListener('DOMContentLoaded', function() {
  const form = document.querySelector('#contact-form'); // замените на ваш селектор
  if (!form) return;

  form.addEventListener('submit', async function(e) {
    e.preventDefault();
    const data = new FormData(form);

    const ok = await sendLeadToCRM({
      name:    data.get('name'),
      phone:   data.get('phone'),
      comment: data.get('message') || 'Заявка с сайта',
      service: data.get('service'),
      source:  'website',
    });

    if (ok) {
      // Показать успех
      form.innerHTML = '<p>✅ Спасибо! Мы свяжемся с вами.</p>';
    }
  });
});

// 2. WordPress Contact Form 7
// document.addEventListener('wpcf7mailsent', function(e) {
//   sendLeadToCRM({
//     name:    e.detail.inputs.find(i => i.name === 'your-name')?.value,
//     phone:   e.detail.inputs.find(i => i.name === 'your-phone')?.value,
//     comment: e.detail.inputs.find(i => i.name === 'your-message')?.value,
//     source:  'website_cf7',
//   });
// });
