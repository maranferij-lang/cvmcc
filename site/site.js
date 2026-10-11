// The launch is free. APP_URL is the address of the app (the owner sets it when the app is open to everyone).
// While it is null, every "Try it free" button leads to the email section ("Get one email when something new ships").
// When it is set, the buttons open the app and pass the traffic source along.
var APP_URL = null; // e.g. "https://app.getcvmax.com"

(function () {
  // Sample CV: before / with red pen
  var sheet = document.getElementById('sheet');
  var before = document.getElementById('t-before');
  var after = document.getElementById('t-after');
  if (sheet && before && after) {
    var set = function (marked) {
      sheet.classList.toggle('marked', marked);
      after.setAttribute('aria-pressed', String(marked));
      before.setAttribute('aria-pressed', String(!marked));
    };
    before.addEventListener('click', function () { set(false); });
    after.addEventListener('click', function () { set(true); });
  }

  // Where the visitor came from (?ref=dou or ?utm_source=youtube), kept for this visit only.
  var params = new URLSearchParams(location.search);
  var source = (params.get('utm_source') || params.get('ref') || '').toLowerCase().replace(/[^a-z0-9_.-]/g, '').slice(0, 60);
  try {
    if (source) sessionStorage.setItem('src', source);
    else source = sessionStorage.getItem('src') || '';
  } catch (e) {}
  if (!source && document.referrer) {
    try { source = new URL(document.referrer).hostname.replace(/^www\./, '').slice(0, 60); } catch (e) {}
  }

  // "Try it free" buttons
  document.querySelectorAll('a[data-cta]').forEach(function (a) {
    if (APP_URL) {
      a.href = APP_URL + (source ? '/?src=' + encodeURIComponent(source) : '/');
      return;
    }
    a.addEventListener('click', function () {
      setTimeout(function () {
        var email = document.getElementById('wl-email');
        if (email) email.focus({ preventScroll: true });
      }, 400);
    });
  });

  // Email list: one email when something new ships
  var form = document.getElementById('wl-form');
  if (!form) return;
  var msg = document.getElementById('wl-msg');
  var btn = document.getElementById('wl-submit');
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var email = form.email.value.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) {
      msg.className = 'msg err';
      msg.textContent = 'Check your email address: it looks incomplete.';
      return;
    }
    btn.disabled = true;
    msg.className = 'msg';
    msg.textContent = 'Saving…';
    fetch('/api/waitlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: email,
        plan: 'full', // kept so the database function needs no change
        source: source,
        company: form.company.value
      })
    }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      msg.className = 'msg ok';
      msg.textContent = "You're on the list. We'll email you once when something new ships.";
      form.email.value = '';
    }).catch(function () {
      msg.className = 'msg err';
      msg.textContent = "Couldn't save it. Try again in a minute, or email hello@getcvmax.com.";
    }).finally(function () {
      btn.disabled = false;
    });
  });
})();
