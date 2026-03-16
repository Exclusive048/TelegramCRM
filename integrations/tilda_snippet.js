/**
 * TelegramCRM + Tilda integration (secure mode)
 *
 * IMPORTANT:
 * - Do NOT place `X-API-Key` in browser JavaScript.
 * - Do NOT call TelegramCRM ingest API directly from browser.
 * - Browser should only call YOUR backend proxy endpoint (same-origin).
 *
 * Safe options:
 * 1) Preferred for Tilda: configure Tilda server-side Webhook in Tilda admin.
 *    Tilda admin stores secret; browser never sees the key.
 * 2) Generic websites: browser -> your backend proxy -> TelegramCRM ingest API.
 *
 * This file contains ONLY browser-side proxy call example (no secrets).
 */

// Browser sends lead data only to your backend endpoint (same-origin recommended).
const CRM_PROXY_URL = '/crm/ingest/lead';

/**
 * Send lead to your backend proxy.
 * Your backend must attach secret X-API-Key and forward to TelegramCRM.
 */
async function sendLeadToProxy(leadData) {
  try {
    const response = await fetch(CRM_PROXY_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        name: leadData.name || '—',
        phone: leadData.phone || '—',
        source: leadData.source || 'website',
        comment: leadData.comment || 'Заявка с сайта',
        service: leadData.service || null,
        utm_campaign: getUTM('utm_campaign'),
        utm_source: getUTM('utm_source'),
        extra: leadData.extra || null,
      }),
      credentials: 'same-origin',
    });

    if (!response.ok) {
      console.error('Proxy error:', await response.text());
      return false;
    }

    return true;
  } catch (error) {
    console.error('Proxy send failed:', error);
    return false;
  }
}

function getUTM(key) {
  return new URLSearchParams(window.location.search).get(key) || null;
}

// Example: plain HTML form -> backend proxy.
document.addEventListener('DOMContentLoaded', function () {
  const form = document.querySelector('#contact-form');
  if (!form) return;

  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    const data = new FormData(form);

    const ok = await sendLeadToProxy({
      name: data.get('name'),
      phone: data.get('phone'),
      comment: data.get('message') || 'Заявка с сайта',
      service: data.get('service'),
      source: 'website',
    });

    if (ok) {
      form.innerHTML = '<p>✅ Спасибо! Мы свяжемся с вами.</p>';
    }
  });
});
