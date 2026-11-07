// Lune booking widget (inlined into the workshop page by server.py).
(function () {
  var form = document.getElementById('booking-form');
  if (!form) return;
  var price = Number(form.dataset.price);
  var MIN = 1, MAX = Number(form.dataset.maxSeats) || 6;
  function $(id) { return document.getElementById(id); }
  function field(name) { return form.elements.namedItem(name); }
  function money(n) { return '€' + n; }

  function render() {
    var seats = Number(field('seats').value) || 1;
    var chip = document.querySelector('[data-date].is-selected');
    $('seat-count').textContent = seats;
    $('sum-date').textContent = chip ? chip.dataset.label : 'Choose a date';
    $('sum-date').classList.toggle('is-empty', !chip);
    $('sum-line').textContent = money(price) + ' × ' + seats + (seats > 1 ? ' seats' : ' seat');
    $('sum-subtotal').textContent = money(price * seats);
    $('sum-total').textContent = money(price * seats);
  }

  window.pickDate = function (el) {
    document.querySelectorAll('[data-date]').forEach(function (c) {
      c.classList.toggle('is-selected', c === el);
    });
    field('date').value = el.dataset.date;
    $('reserve-error').hidden = true;
    render();
  };

  window.stepSeats = function (delta) {
    var s = Math.max(MIN, Math.min(MAX, (Number(field('seats').value) || 1) + delta));
    field('seats').value = s;
    render();
  };

  window.reserve = function () {
    if (!field('date').value) {
      $('reserve-error').hidden = false;
      return;
    }
    form.submit();
  };

  render();
})();
