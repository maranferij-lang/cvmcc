// Checkout page: initialises Paddle.js, which opens the overlay for ?_ptxn=<transaction id>.
// The client-side token and environment are public values set as data- attributes on #pay.
(function () {
  var root = document.getElementById('pay');
  var msg = document.getElementById('pay-msg');
  if (!root || !msg) return;
  var token = (root.getAttribute('data-paddle-token') || '').trim();
  var env = (root.getAttribute('data-paddle-env') || 'sandbox').trim().toLowerCase();
  if (env !== 'live') env = 'sandbox';

  var hasToken = token && token.indexOf('REPLACE') !== 0 && /^[a-z0-9_]{10,}$/i.test(token);
  if (!hasToken) {
    msg.textContent = 'Checkout is not open yet. Please come back soon, or write to hello@getcvmax.com.';
    return;
  }
  if (!window.Paddle) {
    msg.textContent = 'The checkout could not load. Check your connection or ad blocker, then reload this page.';
    return;
  }
  if (!/[?&]_ptxn=/.test(location.search)) {
    msg.innerHTML = 'There is nothing to pay for here. Start from your review in <a href="https://app.getcvmax.com/">GetCVmax</a>.';
    return;
  }
  // Send the buyer back with the transaction id, so the app can finish from any tab or browser.
  // The id is used only when it has the expected shape.
  var successUrl = 'https://app.getcvmax.com/?paid=1';
  try {
    var ptxn = new URLSearchParams(location.search).get('_ptxn') || '';
    if (/^txn_[a-z0-9]{10,64}$/.test(ptxn)) successUrl += '&txn=' + ptxn;
  } catch (e) { /* keep the plain return URL */ }
  try {
    if (env === 'sandbox') window.Paddle.Environment.set('sandbox');
    window.Paddle.Initialize({
      token: token,
      checkout: {
        settings: {
          displayMode: 'overlay',
          successUrl: successUrl
        }
      }
    });
  } catch (e) {
    msg.textContent = 'The checkout could not start. Please reload this page.';
  }
})();
